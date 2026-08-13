"""Modo oculto: a janela minimizada continua utilizavel pela automacao?

Este modulo existe por causa de um bug real: antes o modo oculto teleportava a
janela para (-32000,-32000). Ela ficava invisivel, mas a *geometria restaurada*
tambem ficava fora da tela - quando a usuaria clicava na barra de tarefas ou no
botao de maximizar, nada aparecia.

A correcao e' minimizar de verdade. Isso so vale se o Chrome mantiver a pagina
com layout e interativa enquanto minimizada, e e' exatamente o que se verifica
aqui: varredura da lista, abrir conversa, digitar e enviar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import whatsapp_driver
from src.config import Config, ScheduledMessage
from src.whatsapp_driver import WhatsAppDriver

pytestmark = pytest.mark.dom

FAKE_PAGE = (Path(__file__).parent / "fake_whatsapp.html").as_uri()


@pytest.fixture(scope="module")
def hidden_driver(tmp_path_factory):
    """Driver com hide_window=True e perfil ja existente => sobe minimizado."""
    data_dir = tmp_path_factory.mktemp("oculto")
    profile = data_dir / "browser_profile"
    profile.mkdir()
    # _has_existing_profile() precisa ver algo aqui para o modo oculto valer.
    (profile / "First Run").write_text("", encoding="utf-8")

    config = Config(
        user_name="Maria",
        messages=(
            ScheduledMessage(
                key="bom-dia",
                name="bom dia",
                at="08:30",
                message="Bom dia!",
                chats=("Familia",),
            ),
        ),
        check_interval_seconds=12,
        browser_channel="msedge",
        target_chats=("Familia",),
        mention_names=["Maria"],
        data_dir=data_dir,
        hide_window=True,
    )

    original_url = whatsapp_driver.WHATSAPP_URL
    whatsapp_driver.WHATSAPP_URL = FAKE_PAGE
    instance = WhatsAppDriver(config)
    try:
        instance.start()
    except Exception as exc:
        instance.stop()
        whatsapp_driver.WHATSAPP_URL = original_url
        pytest.skip(f"navegador indisponivel para o teste oculto: {exc}")

    yield instance

    instance.stop()
    whatsapp_driver.WHATSAPP_URL = original_url


def _bounds(driver) -> dict:
    """Geometria da janela segundo o proprio Chrome."""
    cdp = driver._context.new_cdp_session(driver.page)
    target = cdp.send("Browser.getWindowForTarget")
    return cdp.send("Browser.getWindowBounds", {"windowId": target["windowId"]})["bounds"]


def _exige_minimizada(driver) -> None:
    """Pula o teste onde o gerenciador de janelas nao respeita minimizar.

    Em Wayland/compositores de tiling (Hyprland, sway) a janela e' forcada a
    preencher o seu espaco e o Chrome relata 'maximized' mesmo depois do pedido
    de minimizar. Nao e' um defeito do codigo, mas tambem nao da para afirmar que
    passou: melhor pular do que fingir cobertura.
    """
    estado = _bounds(driver)["windowState"]
    if estado != "minimized":
        pytest.skip(
            f"o gerenciador de janelas impos '{estado}'; minimizar nao pode ser "
            "verificado neste ambiente (esperado no Windows)"
        )


def test_sobe_minimizada(hidden_driver):
    _exige_minimizada(hidden_driver)
    assert _bounds(hidden_driver)["windowState"] == "minimized"


def test_geometria_restaurada_fica_na_tela(hidden_driver):
    """O bug antigo: restaurar mostrava a janela fora do monitor.

    A geometria guardada precisa ser visivel - e' ela que o Windows usa quando a
    usuaria clica na barra de tarefas ou maximiza.
    """
    cdp = hidden_driver._context.new_cdp_session(hidden_driver.page)
    target = cdp.send("Browser.getWindowForTarget")
    # Restaurar como o usuario faria, e conferir onde a janela foi parar.
    cdp.send(
        "Browser.setWindowBounds",
        {"windowId": target["windowId"], "bounds": {"windowState": "normal"}},
    )
    b = cdp.send("Browser.getWindowBounds", {"windowId": target["windowId"]})["bounds"]
    tela = hidden_driver.page.evaluate("() => [screen.availWidth, screen.availHeight]")

    assert b["left"] >= 0, b
    assert b["top"] >= 0, b
    assert b["left"] < tela[0], b
    assert b["top"] < tela[1], b

    # Devolve ao estado minimizado para os testes seguintes.
    cdp.send(
        "Browser.setWindowBounds",
        {"windowId": target["windowId"], "bounds": {"windowState": "minimized"}},
    )


def test_le_a_lista_minimizada(hidden_driver):
    linhas = hidden_driver.scan_chat_list()
    assert [linha["title"] for linha in linhas] == [
        "Familia",
        "Amigas",
        "Trabalho",
        "Ciladas",
    ]


def test_detecta_mencoes_minimizada(hidden_driver):
    hidden_driver._notified_cache.clear()
    mencoes = hidden_driver.find_mentions(["Maria"])
    assert [m.chat for m in mencoes] == ["Familia"]


def test_envia_mensagem_minimizada(hidden_driver):
    """O caso que importa: o "Bom dia!" das 08:30 com a janela escondida."""
    assert hidden_driver.send_message("Familia", "Bom dia!") is True
    enviadas = hidden_driver.page.evaluate("() => window.__sent || []")
    assert enviadas[-1] == {"chat": "Familia", "text": "Bom dia!"}


def test_recusa_conversa_errada_minimizada(hidden_driver):
    antes = len(hidden_driver.page.evaluate("() => window.__sent || []"))
    assert hidden_driver.send_message("Ciladas", "Bom dia!") is False
    assert len(hidden_driver.page.evaluate("() => window.__sent || []")) == antes


def test_continua_minimizada_depois_de_trabalhar(hidden_driver):
    """Enviar nao pode trazer a janela para a frente e assustar a usuaria."""
    _exige_minimizada(hidden_driver)
    assert _bounds(hidden_driver)["windowState"] == "minimized"


def test_bring_to_front_desminimiza(hidden_driver):
    """Sessao expirada precisa mostrar o QR code: minimizada nao serve."""
    hidden_driver.bring_to_front()
    assert _bounds(hidden_driver)["windowState"] != "minimized"
