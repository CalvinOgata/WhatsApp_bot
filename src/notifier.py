"""Notificacoes nativas do Windows (toast + som padrao do sistema).

Backends tentados em ordem:
  1. windows-toasts  -> toast nativo do Windows 10/11 (com som padrao)
  2. plyer           -> balao de notificacao (som via winsound)
  3. log             -> ultimo recurso: registra no arquivo de log

A API do windows-toasts mudou entre versoes maiores, por isso a deteccao tenta as
duas formas conhecidas em vez de fixar uma versao.
"""

from __future__ import annotations

import logging
import sys

LOG = logging.getLogger(__name__)

MENTION_TITLE = "Você foi mencionada no WhatsApp!"
PREVIEW_LIMIT = 160


def _beep() -> None:
    """Som padrao do sistema (usado quando o backend nao toca som sozinho)."""
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:  # pragma: no cover - som e' um extra, nunca deve quebrar o app
        LOG.debug("Nao foi possivel tocar o som de notificacao.", exc_info=True)


class Notifier:
    """Envia notificacoes de desktop, escolhendo o melhor backend disponivel."""

    def __init__(self, app_name: str = "Assistente do WhatsApp") -> None:
        self.app_name = app_name
        self._backend = "log"
        self._toaster = None
        self._toast_cls = None
        self._plyer_notify = None
        self._needs_beep = False
        if sys.platform == "win32":
            self._setup_windows_backend()
        else:
            LOG.info("Sistema nao-Windows: notificacoes serao apenas registradas no log.")

    # ------------------------------------------------------------------ setup
    def _setup_windows_backend(self) -> None:
        if self._try_windows_toasts():
            return
        if self._try_plyer():
            return
        LOG.warning(
            "Nenhuma biblioteca de notificacao disponivel. "
            "Rode setup.bat novamente para instalar 'windows-toasts'."
        )

    def _try_windows_toasts(self) -> bool:
        try:
            import windows_toasts
        except Exception:
            LOG.debug("windows-toasts indisponivel.", exc_info=True)
            return False

        try:
            self._toaster = windows_toasts.WindowsToaster(self.app_name)
        except Exception:
            LOG.debug("Falha ao inicializar WindowsToaster.", exc_info=True)
            return False

        # API moderna (>= 1.1): Toast() com text_fields.
        toast_cls = getattr(windows_toasts, "Toast", None)
        if toast_cls is not None:
            self._toast_cls = toast_cls
            self._backend = "windows_toasts"
        else:
            # API antiga (1.0.x): ToastText2 com SetHeadline/SetBody.
            toast_cls = getattr(windows_toasts, "ToastText2", None)
            if toast_cls is None:
                return False
            self._toast_cls = toast_cls
            self._backend = "windows_toasts_legacy"

        LOG.info("Notificacoes via %s.", self._backend)
        return True

    def _try_plyer(self) -> bool:
        try:
            from plyer import notification
        except Exception:
            LOG.debug("plyer indisponivel.", exc_info=True)
            return False
        self._plyer_notify = notification.notify
        self._backend = "plyer"
        self._needs_beep = True
        LOG.info("Notificacoes via plyer (fallback).")
        return True

    # ------------------------------------------------------------------ envio
    def notify(self, title: str, body: str) -> None:
        """Mostra uma notificacao. Nunca levanta excecao."""
        body = (body or "").strip()
        if len(body) > PREVIEW_LIMIT:
            body = body[: PREVIEW_LIMIT - 1].rstrip() + "…"

        LOG.info("Notificacao: %s | %s", title, body)
        try:
            if self._backend == "windows_toasts":
                self._show_modern(title, body)
            elif self._backend == "windows_toasts_legacy":
                self._show_legacy(title, body)
            elif self._backend == "plyer":
                self._show_plyer(title, body)
        except Exception:
            LOG.warning("Falha ao exibir a notificacao no Windows.", exc_info=True)
            _beep()

    def notify_mention(self, chat: str, preview: str) -> None:
        """Notificacao especifica de mencao (@Nome)."""
        body = f"{chat}: {preview}" if preview else chat
        self.notify(MENTION_TITLE, body)

    # -------------------------------------------------------------- backends
    def _show_modern(self, title: str, body: str) -> None:
        toast = self._toast_cls()  # type: ignore[misc]
        toast.text_fields = [title, body]
        try:  # som padrao explicito; se a versao nao expuser, o default ja toca
            from windows_toasts import AudioSource, ToastAudio

            toast.audio = ToastAudio(AudioSource.Default)
        except Exception:
            LOG.debug("ToastAudio indisponivel; usando som padrao.", exc_info=True)
        self._toaster.show_toast(toast)  # type: ignore[union-attr]

    def _show_legacy(self, title: str, body: str) -> None:
        toast = self._toast_cls()  # type: ignore[misc]
        toast.SetHeadline(title)
        toast.SetBody(body)
        self._toaster.show_toast(toast)  # type: ignore[union-attr]

    def _show_plyer(self, title: str, body: str) -> None:
        self._plyer_notify(
            title=title,
            message=body,
            app_name=self.app_name,
            timeout=15,
        )
        if self._needs_beep:
            _beep()
