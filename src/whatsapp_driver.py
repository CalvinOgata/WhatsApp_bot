"""Automacao do WhatsApp Web via Playwright, com foco em seguranca da conta.

Principios que guiam este modulo:

* **Sessao persistente** - `launch_persistent_context` num perfil proprio, para que
  o QR code seja escaneado uma unica vez.
* **Navegador real** - usamos o Edge/Chrome instalado na maquina (`channel`). So
  caimos no Chromium do Playwright se o canal pedido nao existir.
* **Nunca headless** - o WhatsApp Web funciona mal em headless e o fingerprint
  headless e' trivial de detectar. Para "rodar em segundo plano" posicionamos a
  janela fora da area visivel da tela.
* **Janela do tamanho da tela real** - `no_viewport=True` + ajuste por CDP. Ver
  o comentario em `_fit_window` para o motivo (isso corrige a UI embolada).
* **Digitacao humana** - `type_human_like` bate tecla por tecla com atraso variavel.
  Nunca usamos `fill()` nem injecao de valor no DOM.
* **DOM passivo** - a varredura de mencoes e' um unico `evaluate()` por ciclo, sem
  refresh de pagina e sem abrir conversas.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Iterable, Sequence

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from .config import Config

LOG = logging.getLogger(__name__)

WHATSAPP_URL = "https://web.whatsapp.com/"

# Dimensao desejada da janela: 1920x1080 e' padrao e nao chama atencao, mas nunca
# passamos da area util da tela (ver _fit_window). O minimo garante que o WhatsApp
# Web mostre a lista de conversas junto com a conversa aberta.
PREFERRED_WINDOW = (1920, 1080)
MIN_WINDOW = (1024, 700)

# Posicao usada no modo oculto. O proprio Windows coloca janelas minimizadas em
# (-32000, -32000), entao e' um valor que o sistema trata bem.
OFFSCREEN_POSITION = (-32000, -32000)

# --------------------------------------------------------------------------
# Seletores. O WhatsApp Web muda o markup com frequencia, por isso cada alvo
# tem uma lista de candidatos (do mais estavel para o mais especifico) e o
# codigo usa o primeiro que estiver visivel. Se um dia tudo falhar, e' aqui
# que se mexe.
# --------------------------------------------------------------------------
CHAT_LIST_SELECTORS = (
    "#pane-side",
    'div[aria-label="Chat list"]',
    'div[aria-label="Lista de conversas"]',
    '[data-testid="chat-list"]',
)

QR_SELECTORS = (
    'canvas[aria-label*="Scan" i]',
    'canvas[aria-label*="digitaliz" i]',
    "div[data-ref]",
    '[data-testid="qrcode"]',
)

SEARCH_SELECTORS = (
    'div[contenteditable="true"][data-tab="3"]',
    'div[aria-label="Search input textbox"]',
    'div[contenteditable="true"][aria-label*="esquis" i]',
    'div[contenteditable="true"][aria-label*="earch" i]',
    '[data-testid="chat-list-search"]',
)

MESSAGE_BOX_SELECTORS = (
    'footer div[contenteditable="true"][data-tab="10"]',
    'footer div[contenteditable="true"]',
    'div[aria-label*="Digite uma mensagem" i]',
    'div[aria-label*="Type a message" i]',
    '[data-testid="conversation-compose-box-input"]',
)

CHAT_HEADER_SELECTORS = (
    "#main header span[title]",
    "header span[title]",
)

# Texto que aparece quando a sessao expira / foi desconectada pelo celular.
LOGGED_OUT_HINTS = (
    "log in to whatsapp web",
    "entrar no whatsapp web",
    "use o whatsapp no seu computador",
)

# Preview da propria usuaria ("Você: ...") nao deve gerar notificacao de mencao.
_OWN_MESSAGE_PREFIXES = ("você:", "voce:", "you:")

# Enfeites no fim da linha da lista: contador de nao lidas, "@" do icone de
# mencao, separadores. Um "@" so e' removido quando esta sozinho no final, para
# nao mutilar um preview que termina em "@Maria". Numeros no fim sao ambiguos
# (badge ou texto): perder um "as 8" no preview e' melhor que exibir o contador.
_TRAILING_JUNK_RE = re.compile(r"(?:\s*\b\d{1,3}\b|\s*@|[\s\-–—·]+)$")

# JS de varredura da lista de conversas: uma unica passada, sem abrir nada.
_SCAN_JS = """
() => {
  const pane = document.querySelector('#pane-side');
  if (!pane) return null;
  let rows = pane.querySelectorAll('div[role="listitem"]');
  if (!rows.length) rows = pane.querySelectorAll('div[role="row"]');
  const out = [];
  rows.forEach((row) => {
    const titleEl = row.querySelector('span[title]');
    if (!titleEl) return;
    const title = (titleEl.getAttribute('title') || '').trim();
    if (!title) return;
    const text = (row.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 400);
    let unread = false;
    row.querySelectorAll('span[aria-label]').forEach((span) => {
      const label = span.getAttribute('aria-label') || '';
      if (/unread|nao lida|não lida|no le/i.test(label)) unread = true;
    });
    if (row.querySelector('span[data-icon="unread-count"], span[data-icon="status-unread"]')) {
      unread = true;
    }
    const mention = !!row.querySelector(
      'span[data-icon="mention"], span[data-icon="mentioned"], span[data-icon="group-mention"]'
    );
    out.push({ title, text, unread, mention });
  });
  return out;
}
"""

# Mascara de automacao aplicada antes de qualquer script da pagina rodar.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
if (!window.chrome) {
  window.chrome = { runtime: {} };
}
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5],
});
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) =>
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters);
}
"""

# UA usado apenas no fallback para o Chromium empacotado. Com Edge/Chrome real
# nao tocamos no UA: um UA que nao combina com o navegador e' mais suspeito do
# que o UA verdadeiro.
_FALLBACK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class DriverError(Exception):
    """Falha recuperavel na automacao (a app tenta reiniciar)."""


class LoggedOutError(DriverError):
    """A sessao do WhatsApp expirou: precisa de QR code novamente."""


@dataclass(frozen=True)
class Mention:
    chat: str
    preview: str


def human_pause(minimum: float = 1.2, maximum: float = 3.5) -> None:
    """Pausa aleatoria, como uma pessoa lendo a tela antes de agir."""
    time.sleep(random.uniform(minimum, maximum))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().casefold()


class WhatsAppDriver:
    """Encapsula o navegador e as operacoes de alto nivel no WhatsApp Web."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._playwright = None
        self._context = None
        self.page: Page | None = None
        self._notified_cache: dict[str, float] = {}
        self._cache_ttl = 1800.0  # 30 min: evita re-notificar a mesma mencao

    # ------------------------------------------------------------ ciclo de vida
    @property
    def _live_page(self) -> Page:
        """Pagina ativa, ou DriverError se o navegador nao esta mais de pe.

        Ponto unico de verificacao: todo metodo que toca no DOM comeca por aqui,
        em vez de repetir "checa is_running + assert page is not None".
        """
        page = self.page
        if page is None or page.is_closed():
            raise DriverError("A janela do navegador foi fechada.")
        return page

    @property
    def is_running(self) -> bool:
        """Mesma checagem de _live_page, em forma de booleano."""
        return self.page is not None and not self.page.is_closed()

    def start(self) -> None:
        """Sobe o navegador e garante que estamos logados.

        Primeiro tenta em segundo plano (janela fora da tela). Se aparecer QR code,
        reabre com a janela visivel para a usuaria escanear.
        """
        hidden = self.config.hide_window and self._has_existing_profile()
        self._launch(hidden=hidden)

        if self._wait_for_chat_list(timeout_ms=45_000):
            LOG.info("Sessao do WhatsApp Web pronta%s.", " (janela oculta)" if hidden else "")
            return

        if not self._qr_visible():
            raise DriverError(
                "O WhatsApp Web nao carregou a lista de conversas. "
                "Verifique a conexao com a internet."
            )

        if hidden:
            LOG.warning("Sessao expirou: reabrindo a janela para escanear o QR code.")
            self._shutdown_browser()
            self._launch(hidden=False)
        else:
            LOG.warning("Aguardando leitura do QR code.")

        self.bring_to_front()
        # 5 minutos: tempo confortavel para pegar o celular e escanear.
        if not self._wait_for_chat_list(timeout_ms=300_000):
            raise LoggedOutError(
                "O QR code nao foi escaneado. Abra o programa novamente quando puder escanear."
            )
        LOG.info("Login concluido e sessao salva no perfil local.")

    def stop(self) -> None:
        self._shutdown_browser()

    def _has_existing_profile(self) -> bool:
        """True se o perfil ja tem dados (ou seja: nao e' o primeiro uso)."""
        profile = self.config.profile_dir
        try:
            return any(profile.iterdir())
        except OSError:
            return False

    def _launch(self, hidden: bool) -> None:
        # Tamanho e posicao NAO vem por flag: `--window-size`/`--window-position`
        # sao ignorados por alguns gerenciadores de janela e, pior, o Chrome
        # restaura o tamanho salvo no perfil por cima deles. Fazemos isso por CDP
        # em _fit_window, que e' deterministico.
        args = [
            # Exigencia do spec: remove o sinal mais obvio de automacao.
            "--disable-blink-features=AutomationControlled",
            # Mantem a pagina viva mesmo com a janela fora da tela / sem foco.
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--no-first-run",
            "--no-default-browser-check",
        ]

        self._playwright = sync_playwright().start()
        channel = self.config.browser_channel
        for attempt_channel in self._channel_candidates(channel):
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.config.profile_dir),
                    channel=attempt_channel,
                    headless=False,
                    args=args,
                    # Playwright injeta --enable-automation por padrao; removemos.
                    ignore_default_args=["--enable-automation"],
                    # A pagina usa o tamanho real da janela. Ver _fit_window.
                    no_viewport=True,
                    locale=self.config.locale,
                    user_agent=None if attempt_channel else _FALLBACK_USER_AGENT,
                )
                if attempt_channel != channel:
                    LOG.warning(
                        "Canal '%s' indisponivel; usando %s.",
                        channel,
                        attempt_channel or "Chromium do Playwright",
                    )
                break
            except PlaywrightError as exc:
                LOG.warning("Falha ao abrir o navegador (%s): %s", attempt_channel, exc)
                self._context = None
        if self._context is None:
            self._shutdown_browser()
            raise DriverError(
                "Nao foi possivel abrir o Edge, o Chrome nem o Chromium. "
                "Rode setup.bat novamente."
            )

        self._context.add_init_script(_STEALTH_JS)
        self._context.set_default_timeout(20_000)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()

        # Ajustar ANTES de carregar o WhatsApp: ele calcula o layout no primeiro
        # render, e redimensionar depois deixa a interface torta.
        self._fit_window(hidden=hidden)
        self.page.goto(WHATSAPP_URL, wait_until="domcontentloaded", timeout=90_000)

    def _fit_window(self, hidden: bool) -> None:
        """Ajusta a janela para caber na tela, via CDP.

        Por que isto existe: antes passavamos `viewport={"width": 1920, "height":
        1080}`. Em modo headful o Playwright honra o viewport redimensionando a
        *janela* para que a area de conteudo tenha exatamente essa medida - o que
        da uma janela de ~1928x1211. Em telas 1920x1080 (ou em qualquer tela com
        escalonamento do Windows a 125%/150%, onde a area util logica e' bem
        menor) essa janela nao cabe: sobra fora do monitor, e o Chrome comprime a
        pagina dentro do que restou. Resultado: interface minuscula e embolada,
        com a caixa de mensagem abaixo da borda da tela.

        Agora a pagina usa o tamanho real da janela (`no_viewport=True`) e a
        janela e' dimensionada a partir da area util informada pelo proprio
        sistema. Como o Chrome guarda o tamanho da janela no perfil, aplicar isso
        em todo lancamento tambem conserta perfis que ficaram com a medida ruim.
        """
        page = self.page
        if page is None or self._context is None:
            return
        try:
            avail = page.evaluate("() => [screen.availWidth, screen.availHeight]")
            width = min(int(avail[0] * 0.95), PREFERRED_WINDOW[0])
            height = min(int(avail[1] * 0.92), PREFERRED_WINDOW[1])
            # Nao encolher abaixo do utilizavel - desde que a tela permita.
            width = max(width, min(MIN_WINDOW[0], avail[0]))
            height = max(height, min(MIN_WINDOW[1], avail[1]))

            if hidden:
                left, top = OFFSCREEN_POSITION
            else:
                left = max(0, (avail[0] - width) // 2)
                top = max(0, (avail[1] - height) // 2)

            cdp = self._context.new_cdp_session(page)
            target = cdp.send("Browser.getWindowForTarget")
            cdp.send(
                "Browser.setWindowBounds",
                {
                    "windowId": target["windowId"],
                    "bounds": {
                        "left": left,
                        "top": top,
                        "width": width,
                        "height": height,
                        "windowState": "normal",
                    },
                },
            )
            LOG.info(
                "Janela em %sx%s na posicao (%s,%s); area util da tela: %sx%s.",
                width,
                height,
                left,
                top,
                avail[0],
                avail[1],
            )
        except Exception:
            # Sem isso o programa ainda funciona: a janela fica no tamanho que o
            # Chrome escolher. Nao vale derrubar a sessao por causa disso.
            LOG.warning("Nao consegui ajustar o tamanho da janela.", exc_info=True)

    def _channel_candidates(self, channel: str) -> list[str | None]:
        """Canal pedido primeiro, depois alternativas, e por fim o Chromium local."""
        order = [channel]
        for extra in ("msedge", "chrome"):
            if extra not in order:
                order.append(extra)
        if "chromium" in order:
            order.remove("chromium")
        # `None` = Chromium baixado pelo Playwright em espaco de usuario.
        return [*order, None]

    def _shutdown_browser(self) -> None:
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception:
                LOG.debug("Erro ao fechar o navegador.", exc_info=True)
        self._context = None
        self._playwright = None
        self.page = None

    def bring_to_front(self) -> None:
        """Traz a janela para a frente (usado no login por QR code)."""
        try:
            self._live_page.bring_to_front()
        except Exception:
            LOG.debug("Nao foi possivel focar a janela.", exc_info=True)

    # ------------------------------------------------------------------ estado
    def _match_selector(
        self, selectors: Sequence[str], timeout_ms: int = 4000
    ) -> tuple[str | None, Locator | None]:
        """Primeiro seletor visivel da lista, junto com o proprio seletor.

        Saber *qual* candidato funcionou e' o que o modo --diagnose precisa quando
        o WhatsApp muda o HTML.
        """
        page = self._live_page
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=timeout_ms)
                return selector, locator
            except (PlaywrightTimeout, PlaywrightError):
                continue
        return None, None

    def _first_visible(self, selectors: Sequence[str], timeout_ms: int = 4000) -> Locator | None:
        return self._match_selector(selectors, timeout_ms)[1]

    def _wait_for_chat_list(self, timeout_ms: int) -> bool:
        """Espera a lista de conversas, checando QR code em paralelo."""
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            # _first_visible ja levanta DriverError se a janela foi fechada.
            if self._first_visible(CHAT_LIST_SELECTORS, timeout_ms=1500) is not None:
                return True
            if self._qr_visible():
                return False
            time.sleep(1.0)
        return self._first_visible(CHAT_LIST_SELECTORS, timeout_ms=1500) is not None

    def _qr_visible(self) -> bool:
        try:
            return self._first_visible(QR_SELECTORS, timeout_ms=800) is not None
        except DriverError:
            return False

    def health_check(self) -> None:
        """Levanta erro se o navegador morreu ou a sessao caiu."""
        if self._first_visible(CHAT_LIST_SELECTORS, timeout_ms=2000) is not None:
            return
        if self._qr_visible():
            raise LoggedOutError("A sessao do WhatsApp Web foi desconectada.")
        try:
            body = _normalize(self._live_page.inner_text("body", timeout=3000))
        except (PlaywrightTimeout, PlaywrightError):
            raise DriverError("A pagina do WhatsApp Web nao esta respondendo.") from None
        if any(hint in body for hint in LOGGED_OUT_HINTS):
            raise LoggedOutError("A sessao do WhatsApp Web foi desconectada.")
        raise DriverError("A lista de conversas desapareceu da pagina.")

    # -------------------------------------------------------------- digitacao
    def type_human_like(self, element: Locator | None, text: str) -> None:
        """Digita tecla por tecla com atraso variavel (50-180 ms).

        Nunca usar `fill()` ou injecao de valor: o WhatsApp observa os eventos de
        teclado, e um campo que muda de vazio para o texto completo num unico tick
        e' o padrao classico de bot.
        """
        page = self._live_page
        if element is not None:
            element.click()
            human_pause(0.3, 0.9)
        for index, char in enumerate(text):
            page.keyboard.type(char)
            time.sleep(random.uniform(0.05, 0.18))
            # De vez em quando uma pausa mais longa, como quem pensa no meio da frase.
            if char == " " and random.random() < 0.15:
                time.sleep(random.uniform(0.25, 0.7))
            elif index and index % random.randint(12, 20) == 0:
                time.sleep(random.uniform(0.2, 0.5))

    def _clear_field(self) -> None:
        page = self._live_page
        page.keyboard.press("Control+A")
        time.sleep(random.uniform(0.1, 0.25))
        page.keyboard.press("Backspace")
        time.sleep(random.uniform(0.2, 0.5))

    def _clear_search_text(self) -> None:
        """Esvazia a busca para que a lista de conversas volte a mostrar tudo.

        Importante para a deteccao de mencoes: com um filtro esquecido no campo de
        busca, `scan_chat_list` leria apenas as linhas que casam com aquele texto e
        as mencoes das outras conversas passariam batido. O Escape normalmente
        limpa, mas nao dependemos disso.
        """
        search = self._first_visible(SEARCH_SELECTORS, timeout_ms=3000)
        if search is None:
            return
        try:
            if not (search.inner_text() or "").strip():
                return
            search.click()
            human_pause(0.2, 0.5)
            self._clear_field()
        except PlaywrightError:
            LOG.debug("Nao consegui limpar o campo de busca.", exc_info=True)

    def _press_escape(self, times: int = 1) -> None:
        """Fecha busca/conversa aberta. E' sempre limpeza, entao falha em silencio."""
        try:
            page = self._live_page
            for _ in range(times):
                page.keyboard.press("Escape")
                time.sleep(random.uniform(0.2, 0.5))
        except (DriverError, PlaywrightError):
            LOG.debug("Escape ignorado: pagina indisponivel.", exc_info=True)

    # ----------------------------------------------------------------- envio
    def open_chat(self, chat_name: str) -> Locator | None:
        """Busca a conversa e abre. Devolve a caixa de mensagem, ou None se falhou.

        So devolve a caixa depois de confirmar, pelo titulo no cabecalho, que a
        conversa aberta e' exatamente a pedida - mandar "Bom dia!" para a conversa
        errada e' pior do que nao mandar. Nao digita nada: o modo --diagnose usa
        este metodo para checar os nomes sem enviar mensagem.
        """
        self._press_escape(2)

        search = self._first_visible(SEARCH_SELECTORS, timeout_ms=8000)
        if search is None:
            raise DriverError("Campo de busca do WhatsApp nao encontrado.")

        search.click()
        human_pause(0.4, 1.1)
        self._clear_field()
        self.type_human_like(None, chat_name)

        # Pausa exigida pelo spec antes de clicar no resultado da busca.
        human_pause(1.2, 3.5)

        row = self._find_chat_row(chat_name)
        if row is None:
            LOG.error(
                "Conversa '%s' nao encontrada na busca. Confira se o nome no config.json "
                "e' identico ao nome que aparece no WhatsApp.",
                chat_name,
            )
            self._press_escape(2)
            return None

        row.click()
        human_pause(0.8, 2.0)

        box = self._first_visible(MESSAGE_BOX_SELECTORS, timeout_ms=10_000)
        if box is None:
            LOG.error("Caixa de mensagem nao apareceu para '%s'.", chat_name)
            self._press_escape(2)
            return None

        opened = self._opened_chat_title()
        if _normalize(opened) != _normalize(chat_name):
            LOG.error(
                "Conversa aberta e' '%s', esperava '%s'. Nada sera digitado.", opened, chat_name
            )
            self._press_escape(2)
            return None

        # A conversa certa esta aberta: tira o filtro da busca para nao cegar a
        # varredura de mencoes depois. Nao fecha a conversa.
        self._clear_search_text()
        return box

    def send_message(self, chat_name: str, text: str) -> bool:
        """Procura a conversa, abre, digita e envia. Retorna True se enviou."""
        page = self._live_page

        LOG.info("Preparando envio para '%s'.", chat_name)
        box = self.open_chat(chat_name)
        if box is None:
            return False

        self.type_human_like(box, text)
        human_pause(1.2, 3.5)
        page.keyboard.press("Enter")
        human_pause(1.0, 2.2)

        confirmed = self._last_outgoing_contains(text)
        if confirmed:
            LOG.info("Mensagem enviada para '%s': %s", chat_name, text)
        else:
            # Nao reenviamos: um duplicado e' pior do que um log de aviso.
            LOG.warning(
                "Nao consegui confirmar visualmente o envio para '%s' (pode ter sido enviada).",
                chat_name,
            )
        self._press_escape(2)
        return confirmed

    def _find_chat_row(self, chat_name: str) -> Locator | None:
        """Localiza a linha do resultado de busca com titulo exatamente igual."""
        titles = self._live_page.locator("#pane-side span[title]")
        try:
            count = min(titles.count(), 40)
        except PlaywrightError:
            return None

        wanted = _normalize(chat_name)
        fallback: Locator | None = None
        for index in range(count):
            span = titles.nth(index)
            try:
                title = span.get_attribute("title") or ""
            except PlaywrightError:
                continue
            if _normalize(title) != wanted:
                continue
            row = span.locator('xpath=ancestor::div[@role="listitem"][1]')
            try:
                if row.count() > 0:
                    return row.first
            except PlaywrightError:
                pass
            fallback = fallback or span
        return fallback

    def _opened_chat_title(self) -> str:
        header = self._first_visible(CHAT_HEADER_SELECTORS, timeout_ms=5000)
        if header is None:
            return ""
        try:
            return header.get_attribute("title") or header.inner_text()
        except PlaywrightError:
            return ""

    def _last_outgoing_contains(self, text: str) -> bool:
        page = self._live_page
        for selector in ("#main div.message-out", '#main div[data-id*="true_"]'):
            bubbles = page.locator(selector)
            try:
                count = bubbles.count()
                if not count:
                    continue
                content = bubbles.nth(count - 1).inner_text(timeout=3000)
            except (PlaywrightError, PlaywrightTimeout):
                continue
            if _normalize(text) in _normalize(content):
                return True
        return False

    # ----------------------------------------------------------- diagnostico
    def diagnose(self, chats: Sequence[str]) -> dict:
        """Relatorio do estado da pagina, sem enviar nada.

        Responde as tres perguntas que aparecem quando o programa "para de
        funcionar": a sessao esta de pe? qual seletor casou com cada elemento
        (ou seja, o WhatsApp mudou o HTML)? cada conversa do config.json existe?
        """
        page = self._live_page
        selectors: dict[str, str | None] = {"message_box": None}
        selectors["chat_list"], _ = self._match_selector(CHAT_LIST_SELECTORS, 3000)
        selectors["qr_code"], _ = self._match_selector(QR_SELECTORS, 800)
        selectors["search"], _ = self._match_selector(SEARCH_SELECTORS, 3000)

        report: dict = {
            "url": page.url,
            "user_agent": page.evaluate("() => navigator.userAgent"),
            "webdriver_flag": page.evaluate("() => navigator.webdriver"),
            # Se a janela nao couber na tela ou a pagina for maior que a janela, a
            # interface aparece cortada/comprimida - foi o bug do viewport forcado.
            "window": page.evaluate(
                """() => ({
                    avail: [screen.availWidth, screen.availHeight],
                    outer: [window.outerWidth, window.outerHeight],
                    inner: [window.innerWidth, window.innerHeight],
                    dpr: window.devicePixelRatio,
                })"""
            ),
            "logged_in": selectors["chat_list"] is not None and selectors["qr_code"] is None,
            "selectors": selectors,
            "rows": [],
            "chats": {},
        }

        # A lista precisa estar sem filtro para o relatorio refletir a realidade.
        self._clear_search_text()
        try:
            report["rows"] = self.scan_chat_list()
        except DriverError as exc:
            report["scan_error"] = str(exc)

        for chat in chats:
            box = self.open_chat(chat)
            report["chats"][chat] = box is not None
            if box is not None and selectors["message_box"] is None:
                selectors["message_box"], _ = self._match_selector(MESSAGE_BOX_SELECTORS, 5000)
            self._press_escape(2)
            human_pause(0.5, 1.5)
        return report

    # -------------------------------------------------------------- mencoes
    def scan_chat_list(self) -> list[dict]:
        """Le a lista de conversas com um unico evaluate (DOM passivo)."""
        try:
            rows = self._live_page.evaluate(_SCAN_JS)
        except (PlaywrightError, PlaywrightTimeout) as exc:
            raise DriverError(f"Falha ao ler a lista de conversas: {exc}") from exc
        if rows is None:
            raise DriverError("Lista de conversas nao esta na pagina (sessao pode ter caido).")
        return [row for row in rows if isinstance(row, dict)]

    def find_mentions(self, names: Iterable[str]) -> list[Mention]:
        """Retorna mencoes novas a qualquer um dos nomes informados."""
        patterns = [f"@{name.casefold()}" for name in names if name]
        if not patterns:
            return []

        found: list[Mention] = []
        now = time.monotonic()
        self._prune_cache(now)

        for row in self.scan_chat_list():
            title = str(row.get("title") or "").strip()
            text = str(row.get("text") or "")
            if not title:
                continue
            if not (row.get("unread") or row.get("mention")):
                continue

            preview = self._extract_preview(title, text)
            if _normalize(preview).startswith(_OWN_MESSAGE_PREFIXES):
                continue  # mencao feita pela propria usuaria

            lowered = text.casefold()
            if not (row.get("mention") or any(pattern in lowered for pattern in patterns)):
                continue
            # O icone de mencao sozinho ja basta, mas se ha texto queremos o @nome.
            if row.get("mention") and patterns and "@" in lowered:
                if not any(pattern in lowered for pattern in patterns):
                    continue

            key = f"{title}||{preview}"
            if key in self._notified_cache:
                continue
            self._notified_cache[key] = now
            found.append(Mention(chat=title, preview=preview))

        return found

    def _extract_preview(self, title: str, text: str) -> str:
        """Tira o titulo, o horario e os adornos do innerText da linha."""
        cleaned = text
        if cleaned.startswith(title):
            cleaned = cleaned[len(title) :]
        cleaned = re.sub(r"^\s*\d{1,2}:\d{2}\s*(AM|PM)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        # O fim da linha carrega enfeites em ordem variavel: contador de nao lidas,
        # icone de mencao, check de entrega. Removemos em loop ate sobrar o texto.
        while True:
            stripped = _TRAILING_JUNK_RE.sub("", cleaned)
            if stripped == cleaned:
                return cleaned.strip()
            cleaned = stripped

    def _prune_cache(self, now: float) -> None:
        expired = [key for key, stamp in self._notified_cache.items() if now - stamp > self._cache_ttl]
        for key in expired:
            self._notified_cache.pop(key, None)
        if len(self._notified_cache) > 500:  # trava de seguranca contra crescimento
            for key in list(self._notified_cache)[:250]:
                self._notified_cache.pop(key, None)
