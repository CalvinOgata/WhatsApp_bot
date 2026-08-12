#!/usr/bin/env python3
"""Assistente do WhatsApp - ponto de entrada.

Roda um unico processo, um unico thread:

    loop principal -> agendador (bom dia / boa noite)
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

from src.config import Config, ConfigError, default_data_dir, load_config
from src.notifier import Notifier
from src.scheduler import EVENING, MORNING, GreetingScheduler
from src.whatsapp_driver import DriverError, LoggedOutError, WhatsAppDriver

APP_NAME = "Assistente do WhatsApp"
LOG = logging.getLogger("assistente")

# Espera entre tentativas quando o navegador cai (segundos).
RESTART_BACKOFF = (15, 30, 60, 120, 300)

# Pausa entre conversas ao enviar a mesma saudacao: uma pessoa nao dispara
# mensagens para varios grupos no mesmo segundo.
BETWEEN_CHATS_SECONDS = (8.0, 25.0)

_stop_requested = False


# --------------------------------------------------------------------- infra
def _request_stop(signum: int, _frame: FrameType | None) -> None:
    global _stop_requested
    LOG.info("Sinal %s recebido: encerrando.", signum)
    _stop_requested = True


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
    while not _stop_requested and time.monotonic() < deadline:
        time.sleep(min(0.5, deadline - time.monotonic()))


# ----------------------------------------------------------------- aplicacao
def make_send_callback(driver: WhatsAppDriver, notifier: Notifier, config: Config):
    """Fabrica o callback que o agendador usa para enviar uma saudacao."""

    def send(job_key: str, message: str) -> int:
        enviados = 0
        for index, chat in enumerate(config.target_chats):
            if _stop_requested:
                LOG.warning("Encerrando no meio da saudacao '%s'.", job_key)
                break
            if index:
                interruptible_sleep(random.uniform(*BETWEEN_CHATS_SECONDS))
            if driver.send_message(chat, message):
                enviados += 1
        faltando = len(config.target_chats) - enviados
        if faltando:
            notifier.notify(
                APP_NAME,
                f"Nao consegui enviar \"{message}\" para {faltando} conversa(s). "
                "Confira os nomes no config.json.",
            )
        return enviados

    return send


def main_loop(
    driver: WhatsAppDriver, scheduler: GreetingScheduler, config: Config, notifier: Notifier
) -> None:
    LOG.info("Monitorando mencoes a %s a cada %ss.", config.mention_names, config.check_interval_seconds)
    while not _stop_requested:
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


def run(config: Config, notifier: Notifier, test_job: str | None) -> int:
    driver = WhatsAppDriver(config)
    scheduler = GreetingScheduler(config, make_send_callback(driver, notifier, config))
    LOG.info("Agenda: %s", scheduler.describe())

    attempt = 0
    try:
        while not _stop_requested:
            try:
                driver.start()
                attempt = 0
                if test_job:
                    scheduler.trigger_now(test_job)
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
        description="Envia bom dia / boa noite e avisa quando você e' mencionada.",
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
        choices=(MORNING, EVENING),
        help="envia a saudacao agora, para testar os nomes das conversas",
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
        notifier.notify(APP_NAME, str(exc))
        return 2

    if args.visible:
        config.hide_window = False

    lock = SingleInstanceLock(config.lock_path)
    if not lock.acquire():
        notifier.notify(
            APP_NAME, "O assistente ja esta rodando. Nao e' preciso abrir de novo."
        )
        return 3

    try:
        return run(config, notifier, args.send_now)
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
