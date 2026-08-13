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
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        LOG.warning("Nao consegui apagar o pedido de parada antigo.", exc_info=True)


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


def setup_logging(verbose: bool) -> Path:
    """Log em arquivo rotativo (o app roda sem console, o log e' a unica janela)."""
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "assistente.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

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

    def _clear_if_stale(self) -> bool:
        try:
            pid = int((self.path.read_text(encoding="utf-8") or "0").strip() or 0)
        except (OSError, ValueError):
            pid = 0
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


def run(config: Config, notifier: Notifier, args: argparse.Namespace) -> int:
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
                if args.diagnose:
                    print_diagnosis(driver, scheduler, config)
                    return 0
                if args.send_now:
                    scheduler.trigger_now(args.send_now)
                    return 0
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log_path = setup_logging(args.verbose)
    install_signal_handlers()
    LOG.info("=== %s iniciando (log em %s) ===", APP_NAME, log_path)

    notifier = Notifier(APP_NAME)
    if args.test_notification:
        notifier.notify(APP_NAME, "Se você esta vendo isso, as notificacoes funcionam!")
        return 0

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

    if args.visible or args.diagnose:
        config.hide_window = False

    arm_stop_file(config.stop_request_path)

    lock = SingleInstanceLock(config.lock_path)
    if not lock.acquire():
        _emit("O assistente ja esta rodando. Feche a copia anterior antes de abrir outra.")
        notifier.notify(
            APP_NAME, "O assistente ja esta rodando. Nao e' preciso abrir de novo."
        )
        return 3

    try:
        return run(config, notifier, args)
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
