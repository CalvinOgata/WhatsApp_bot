#!/usr/bin/env python3
"""Assistente do WhatsApp - ponto de entrada.

Roda um unico processo, um unico thread:

    loop principal -> agendador (mensagens do config.json)
                   -> varredura de mencoes (@Nome) -> notificacao do Windows

Tudo em espaco de usuario: nenhuma etapa pede permissao de Administrador.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import random
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import FrameType

from src.config import Config, ConfigError, ScheduledMessage, default_data_dir, load_config
from src.notifier import Notifier
from src.scheduler import MessageScheduler
from src.whatsapp_driver import DriverError, LoggedOutError, WhatsAppDriver

APP_NAME = "Assistente do WhatsApp"
LOG = logging.getLogger("assistente")

# Espera entre tentativas quando o navegador cai (segundos).
RESTART_BACKOFF = (15, 30, 60, 120, 300)

# Pausa entre conversas ao enviar a mesma mensagem: uma pessoa nao dispara
# textos para varios grupos no mesmo segundo.
BETWEEN_CHATS_SECONDS = (8.0, 25.0)

_stop_requested = False
_stop_file: Path | None = None


# --------------------------------------------------------------------- infra
def _request_stop(signum: int, _frame: FrameType | None) -> None:
    global _stop_requested
    LOG.info("Sinal %s recebido: encerrando.", signum)
    _stop_requested = True


def discard_stale_request(path: Path) -> None:
    """Apaga um pedido esquecido no disco por uma execucao anterior."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        LOG.warning("Nao consegui apagar o pedido antigo (%s).", path.name, exc_info=True)


def arm_stop_file(path: Path) -> None:
    """Passa a observar o arquivo de parada que o stop.bat cria.

    Encerrar por arquivo (e nao por taskkill) e' o que garante uma saida limpa: o
    navegador e' fechado pelo Playwright, a sessao do WhatsApp fica intacta e a
    trava de instancia e' liberada. Um taskkill no pythonw.exe deixaria o Chrome
    orfao segurando o perfil, e o proximo start nao conseguiria usa-lo.
    """
    global _stop_file
    _stop_file = path
    # Um pedido esquecido de ontem nao pode derrubar a execucao de agora.
    discard_stale_request(path)


def write_send_request(path: Path, name: str) -> None:
    """Pede um envio a instancia que ja esta rodando (mesma ideia do stop.request).

    Dois processos nao podem abrir o mesmo perfil do navegador ao mesmo tempo, entao
    `--send-now` com o assistente ligado nao tem como subir um Chrome proprio: ele
    deixa o pedido aqui e quem executa e' o loop principal, que ja esta logado.
    """
    path.write_text(name, encoding="utf-8")


def take_send_request(path: Path) -> str | None:
    """Le e consome um pedido de envio deixado pelo `--send-now`."""
    try:
        wanted = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        LOG.warning("Nao consegui ler o pedido de envio.", exc_info=True)
        return None
    # Consumir sempre, inclusive quando o arquivo veio vazio: um pedido ilegivel
    # que ficasse no disco seria relido em todo ciclo do loop.
    discard_stale_request(path)
    return wanted or None


def should_stop() -> bool:
    """True se pediram para encerrar: sinal do sistema ou stop.bat."""
    global _stop_requested
    if _stop_requested:
        return True
    if _stop_file is not None and _stop_file.exists():
        LOG.info("Pedido de encerramento recebido (%s).", _stop_file.name)
        _stop_requested = True
        try:
            _stop_file.unlink()
        except OSError:
            LOG.debug("Nao consegui remover o pedido de parada.", exc_info=True)
        return True
    return False


def install_signal_handlers() -> None:
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _request_stop)
            except (ValueError, OSError):
                pass  # nem todo sinal existe em todo host


def setup_logging(verbose: bool, to_file: bool = True) -> Path | None:
    """Log em arquivo rotativo (o app roda sem console, o log e' a unica janela).

    `to_file=False` para quem NAO e' dono da trava de instancia. Dois processos com
    um RotatingFileHandler no mesmo arquivo se atrapalham no Windows: as linhas se
    misturam e, na hora de rotacionar, o rename falha porque o outro processo ainda
    segura o arquivo - o log some ou aparece pela metade. Comandos auxiliares
    (`--send-now`, `--diagnose`) rodam no Prompt de Comando, entao o console basta.
    """
    log_path: Path | None = None
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if to_file:
        data_dir = default_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path = data_dir / "assistente.log"
        file_handler = RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Com pythonw.exe nao existe stdout; adicionar StreamHandler quebraria o log.
    if sys.stdout is not None and sys.stdout.isatty():
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)

    logging.getLogger("schedule").setLevel(logging.WARNING)
    return log_path


def _emit(text: str = "") -> None:
    """Mostra no console quando ha um (modos --diagnose e afins) e sempre no log."""
    if sys.stdout is not None:
        print(text)
    if text:
        LOG.info(text)


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


class SingleInstanceLock:
    """Impede duas copias do programa no mesmo perfil de navegador.

    Dois processos no mesmo `user_data_dir` corrompem o perfil do Chrome e derrubam
    a sessao do WhatsApp - e' o erro mais facil de cometer clicando duas vezes no
    start.bat.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._acquired = False

    def acquire(self) -> bool:
        for attempt in (1, 2):
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == 2:
                    return False
                if self._clear_if_stale():
                    continue
                return False
            except OSError:
                LOG.warning("Nao foi possivel criar o arquivo de trava.", exc_info=True)
                return True  # nao vale bloquear o app por causa da trava
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
            self._acquired = True
            return True
        return False

    def owner_pid(self) -> int:
        """Processo anotado no arquivo de trava (0 se nao der para ler)."""
        try:
            return int((self.path.read_text(encoding="utf-8") or "0").strip() or 0)
        except (OSError, ValueError):
            return 0

    def _clear_if_stale(self) -> bool:
        pid = self.owner_pid()
        if _process_alive(pid):
            LOG.error("O assistente ja esta rodando (processo %s).", pid)
            return False
        LOG.warning("Removendo trava antiga do processo %s.", pid)
        try:
            self.path.unlink()
        except OSError:
            return False
        return True

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            self.path.unlink()
        except OSError:
            LOG.debug("Falha ao remover o arquivo de trava.", exc_info=True)
        self._acquired = False


def interruptible_sleep(seconds: float) -> None:
    """Dorme em fatias para reagir rapido a um pedido de encerramento."""
    deadline = time.monotonic() + seconds
    while not should_stop() and time.monotonic() < deadline:
        time.sleep(min(0.5, deadline - time.monotonic()))


# ----------------------------------------------------------------- aplicacao
def make_send_callback(driver: WhatsAppDriver, notifier: Notifier, config: Config):
    """Fabrica o callback que o agendador usa para enviar uma mensagem."""

    def send(message: ScheduledMessage) -> int:
        enviados = 0
        for index, chat in enumerate(message.chats):
            if should_stop():
                LOG.warning("Encerrando no meio da mensagem '%s'.", message.name)
                break
            if index:
                interruptible_sleep(random.uniform(*BETWEEN_CHATS_SECONDS))
            if driver.send_message(chat, message.message):
                enviados += 1
        faltando = len(message.chats) - enviados
        if faltando:
            notifier.notify(
                APP_NAME,
                f'Nao consegui enviar "{message.message}" para {faltando} conversa(s). '
                "Confira os nomes no config.json.",
            )
        return enviados

    return send


def serve_send_request(
    scheduler: MessageScheduler, config: Config, notifier: Notifier
) -> None:
    """Executa um `--send-now` pedido por outro processo, se houver um esperando."""
    wanted = take_send_request(config.send_request_path)
    if wanted is None:
        return
    LOG.info("Pedido de envio imediato recebido: '%s'.", wanted)
    try:
        scheduler.trigger_now(wanted)
    except KeyError as exc:
        LOG.error("%s", exc)
        notifier.notify(APP_NAME, str(exc))


def main_loop(
    driver: WhatsAppDriver, scheduler: MessageScheduler, config: Config, notifier: Notifier
) -> None:
    LOG.info(
        "Monitorando mencoes a %s a cada %ss.",
        ", ".join(config.mention_names),
        config.check_interval_seconds,
    )
    while not should_stop():
        scheduler.tick()
        serve_send_request(scheduler, config, notifier)
        try:
            mentions = driver.find_mentions(config.mention_names)
        except DriverError:
            # Classifica a falha: sessao caida vira LoggedOutError e pede QR code.
            driver.health_check()
            raise
        for mention in mentions:
            notifier.notify_mention(mention.chat, mention.preview)
        interruptible_sleep(config.check_interval_seconds)


def print_diagnosis(driver: WhatsAppDriver, scheduler: MessageScheduler, config: Config) -> None:
    """Relatorio para descobrir *por que* algo parou de funcionar."""
    _emit(f"=== Diagnostico: {APP_NAME} ===")
    _emit(f"Log        : {config.log_path}")
    _emit(f"Dados      : {config.data_dir}")
    _emit(f"Navegador  : canal '{config.browser_channel}'")
    _emit(f"Mencoes    : {', '.join(config.mention_names)}")
    _emit(f"Intervalo  : {config.check_interval_seconds}s")
    _emit("")
    _emit("Agenda configurada:")
    for message in config.messages:
        quando = scheduler.next_run(message.key)
        proximo = quando.strftime("%d/%m %H:%M") if quando else "?"
        marca = " (ja enviada hoje)" if scheduler.sent_today(message.key) else ""
        _emit(f"  {message.name}: {message.describe()} | proximo: {proximo}{marca}")
    _emit("")

    report = driver.diagnose(config.all_chats)
    _emit(f"Pagina     : {report['url']}")
    _emit(f"Sessao     : {'conectada' if report['logged_in'] else 'DESCONECTADA (precisa de QR)'}")
    _emit(f"webdriver  : {report['webdriver_flag']} (esperado: False)")
    _emit(f"User-Agent : {report['user_agent']}")

    janela = report["window"]
    _emit(
        f"Estado     : janela {report['window_state']} "
        f"(minimized = rodando escondida, normal = visivel)"
    )
    _emit(
        f"Janela     : {janela['outer'][0]}x{janela['outer'][1]} | "
        f"pagina {janela['inner'][0]}x{janela['inner'][1]} | "
        f"tela util {janela['avail'][0]}x{janela['avail'][1]} | zoom {janela['dpr']}"
    )
    if (
        janela["outer"][0] > janela["avail"][0]
        or janela["outer"][1] > janela["avail"][1]
    ):
        _emit("  AVISO: a janela e maior que a tela; parte dela fica fora do monitor.")
    if janela["inner"][0] > janela["outer"][0]:
        _emit("  AVISO: a pagina e maior que a janela; a interface aparece comprimida.")
    _emit("")
    _emit("Seletores que casaram (se algum estiver vazio, o WhatsApp mudou o HTML):")
    for nome, selector in report["selectors"].items():
        _emit(f"  {nome:<12}: {selector or '(nenhum)'}")
    if report.get("scan_error"):
        _emit(f"  ERRO na leitura da lista: {report['scan_error']}")
    _emit("")

    _emit("Conversas do config.json:")
    for chat, encontrada in report["chats"].items():
        _emit(f"  {'OK   ' if encontrada else 'FALHA'} {chat}")
    if not all(report["chats"].values()):
        _emit("  -> Um nome com FALHA nao existe na busca do WhatsApp.")
        _emit("     Copie o nome exatamente como aparece na lista de conversas.")
    _emit("")

    rows = report["rows"]
    _emit(f"Lista de conversas vista agora ({len(rows)} linhas, mostrando ate 15):")
    for row in rows[:15]:
        marcas = "".join(
            (
                "[nao lida]" if row.get("unread") else "",
                "[mencao]" if row.get("mention") else "",
            )
        )
        _emit(f"  {marcas or '[lida]'} {row.get('title')} | {row.get('text', '')[:70]}")
    _emit("")
    _emit("Fim do diagnostico. Nenhuma mensagem foi enviada.")


def send_now(scheduler: MessageScheduler, config: Config, wanted: str) -> int:
    """Envia uma mensagem agora e conta o resultado na tela (nao so no log).

    O usuario roda isso no Prompt de Comando para conferir os nomes das conversas:
    terminar em silencio, sempre com codigo 0, esconde justamente o que ele veio ver.
    """
    message = config.find_message(wanted)
    if message is None:  # ja validado em main(), mas o modo nunca deve enviar as cegas
        _emit(f'Nao existe mensagem "{wanted}".')
        return 2

    enviados = scheduler.trigger_now(wanted)
    total = len(message.chats)
    if enviados == total:
        _emit(f'Mensagem "{message.name}" enviada para {total} conversa(s).')
        return 0
    _emit(f'Enviei "{message.name}" para {enviados} de {total} conversa(s).')
    _emit("Rode --diagnose para ver quais nomes de conversa nao foram encontrados.")
    _emit(f"Detalhes no log: {config.log_path}")
    return 1


def run_once(config: Config, notifier: Notifier, args: argparse.Namespace) -> int:
    """Modos de uma tacada so (`--diagnose`, `--send-now`), rodados no Prompt.

    Sem o laco de reinicio do modo normal, de proposito: a pessoa esta esperando na
    frente do console, e repetir um envio que estourou no meio arriscaria mandar a
    mesma mensagem duas vezes para as conversas que ja receberam.
    """
    driver = WhatsAppDriver(config)
    scheduler = MessageScheduler(config, make_send_callback(driver, notifier, config))
    try:
        driver.start()
        if args.diagnose:
            print_diagnosis(driver, scheduler, config)
            return 0
        return send_now(scheduler, config, args.send_now)
    except LoggedOutError as exc:
        _emit(f"A sessao do WhatsApp caiu: {exc}")
        _emit("Rode start.bat, escaneie o QR code e tente de novo.")
        return 4
    except DriverError as exc:
        _emit(f"Nao consegui usar o WhatsApp Web: {exc}")
        _emit(f"Detalhes no log: {config.log_path}")
        return 4
    except Exception:
        LOG.exception("Erro inesperado no modo de linha de comando.")
        _emit(f"Erro inesperado. O detalhe tecnico esta no log: {config.log_path}")
        return 4
    finally:
        driver.stop()


def run(config: Config, notifier: Notifier, args: argparse.Namespace) -> int:
    if args.diagnose or args.send_now:
        return run_once(config, notifier, args)

    driver = WhatsAppDriver(config)
    scheduler = MessageScheduler(config, make_send_callback(driver, notifier, config))
    for linha in scheduler.describe():
        LOG.info("Agenda: %s", linha)

    attempt = 0
    try:
        while not should_stop():
            try:
                driver.start()
                attempt = 0
                main_loop(driver, scheduler, config, notifier)
                return 0
            except LoggedOutError as exc:
                LOG.warning("Sessao desconectada: %s", exc)
                notifier.notify(
                    APP_NAME,
                    "A conexao com o WhatsApp caiu. Vou abrir a janela para você escanear o QR code.",
                )
                driver.stop()
                interruptible_sleep(30)
            except DriverError as exc:
                LOG.warning("Problema na automacao: %s", exc)
                driver.stop()
                delay = RESTART_BACKOFF[min(attempt, len(RESTART_BACKOFF) - 1)]
                attempt += 1
                LOG.info("Tentando novamente em %ss (tentativa %s).", delay, attempt)
                interruptible_sleep(delay)
            except Exception:
                LOG.exception("Erro inesperado no loop principal.")
                driver.stop()
                delay = RESTART_BACKOFF[min(attempt, len(RESTART_BACKOFF) - 1)]
                attempt += 1
                interruptible_sleep(delay)
    finally:
        driver.stop()
        LOG.info("Assistente encerrado.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="Assistente do WhatsApp",
        description="Envia mensagens nos horarios configurados e avisa quando você e' mencionada.",
    )
    parser.add_argument(
        "--visible", action="store_true", help="mantem a janela do navegador visivel"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="log detalhado (para diagnostico)"
    )
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="mostra uma notificacao de teste e sai",
    )
    parser.add_argument(
        "--send-now",
        metavar="NOME",
        help='envia agora a mensagem com esse nome (ex.: --send-now "bom dia")',
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="checa sessao, seletores e nomes das conversas sem enviar nada",
    )
    return parser.parse_args(argv)


def delegate_to_running_instance(
    config: Config, notifier: Notifier, args: argparse.Namespace, running_pid: int
) -> int:
    """Decide o que fazer quando o assistente ja esta ligado.

    O perfil do navegador so aceita um dono: subir um segundo Chrome no mesmo
    `user_data_dir` corromperia a sessao do WhatsApp. Entao `--send-now` vira um
    pedido para quem ja esta rodando, e os outros modos explicam como proceder.
    """
    quem = f" (processo {running_pid})" if running_pid else ""
    if args.send_now:
        write_send_request(config.send_request_path, args.send_now)
        _emit(f"O assistente ja esta rodando{quem}; passei o pedido para ele.")
        _emit(
            f'"{args.send_now}" sai em ate {config.check_interval_seconds} segundos. '
            f"O resultado fica no log: {config.log_path}"
        )
        return 0

    _emit(f"O assistente ja esta rodando{quem}.")
    if args.diagnose:
        _emit("Clique duas vezes em stop.bat, rode o --diagnose e depois start.bat de novo.")
        return 3

    # Duplo clique no start.bat: roda com pythonw, sem console e sem log proprio.
    # A notificacao e' a unica forma de dizer que esta tudo bem.
    _emit("Nao e' preciso abrir de novo. Para desligar, use o stop.bat.")
    notifier.notify(APP_NAME, "O assistente ja esta rodando. Nao e' preciso abrir de novo.")
    return 3


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # --test-notification nao abre navegador nem le config: nao disputa a trava.
    if args.test_notification:
        setup_logging(args.verbose, to_file=False)
        Notifier(APP_NAME).notify(
            APP_NAME, "Se você esta vendo isso, as notificacoes funcionam!"
        )
        return 0

    # A trava vem antes do log de proposito: quem nao e' o dono nao pode abrir um
    # segundo handler no assistente.log (ver setup_logging).
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = SingleInstanceLock(data_dir / "assistente.lock")
    owner = lock.acquire()
    running_pid = 0 if owner else lock.owner_pid()

    try:
        log_path = setup_logging(args.verbose, to_file=owner)
        install_signal_handlers()
        LOG.info("=== %s iniciando (log em %s) ===", APP_NAME, log_path)

        notifier = Notifier(APP_NAME)
        try:
            config = load_config()
        except ConfigError as exc:
            LOG.error("Configuracao invalida: %s", exc)
            _emit(f"Configuracao invalida: {exc}")
            notifier.notify(APP_NAME, str(exc))
            return 2

        # Validar o nome ANTES de abrir o navegador: erro de digitacao nao merece
        # esperar a sessao subir para so entao reclamar.
        if args.send_now and config.find_message(args.send_now) is None:
            disponiveis = ", ".join(f'"{m.name}"' for m in config.messages)
            _emit(f'Nao existe mensagem "{args.send_now}". Disponiveis: {disponiveis}')
            return 2

        if not owner:
            return delegate_to_running_instance(config, notifier, args, running_pid)

        # Os modos de console existem para a pessoa ver o que esta acontecendo.
        if args.visible or args.diagnose or args.send_now:
            config.hide_window = False

        arm_stop_file(config.stop_request_path)
        discard_stale_request(config.send_request_path)
        return run(config, notifier, args)
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
