"""Testes do driver contra uma pagina local que imita o WhatsApp Web.

Estes testes rodam o Playwright de verdade (navegador visivel, janela fora da
tela). Sem navegador instalado ou sem display, o modulo inteiro e' pulado - a
suite continua util em qualquer maquina.

A pagina `fake_whatsapp.html` reproduz apenas o que o driver toca: #pane-side com
as linhas, o campo de busca, o cabecalho da conversa e a caixa de mensagem. Uma
das linhas ("Ciladas") abre um cabecalho com OUTRO nome, para provar que o driver
se recusa a digitar na conversa errada.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src import whatsapp_driver
from src.config import Config, ScheduledMessage
from src.whatsapp_driver import DriverError, WhatsAppDriver

pytestmark = pytest.mark.dom

FAKE_PAGE = (Path(__file__).parent / "fake_whatsapp.html").as_uri()


@pytest.fixture(scope="module")
def driver(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("dom")
    (data_dir / "browser_profile").mkdir()
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
        browser_channel="msedge",  # ausente no Linux: exercita a cadeia de fallback
        target_chats=("Familia", "Amigas"),
        mention_names=["Maria"],
        data_dir=data_dir,
    )

    original_url = whatsapp_driver.WHATSAPP_URL
    whatsapp_driver.WHATSAPP_URL = FAKE_PAGE
    instance = WhatsAppDriver(config)
    try:
        instance.start()
    except Exception as exc:  # sem display, sem navegador, sem sandbox...
        instance.stop()
        whatsapp_driver.WHATSAPP_URL = original_url
        pytest.skip(f"navegador indisponivel para o teste de DOM: {exc}")

    yield instance

    instance.stop()
    whatsapp_driver.WHATSAPP_URL = original_url


def enviadas(driver) -> list[dict]:
    """O que a pagina falsa registrou como mensagem enviada."""
    return driver.page.evaluate("() => window.__sent || []")


# ------------------------------------------------------------------- sessao
def test_sessao_detectada(driver):
    assert driver.is_running
    driver.health_check()  # nao levanta


def test_mascara_de_automacao(driver):
    assert driver.page.evaluate("() => navigator.webdriver") is False
    assert driver.page.evaluate("() => !!window.chrome")


def test_user_agent_nao_denuncia_headless(driver):
    assert "Headless" not in driver.page.evaluate("() => navigator.userAgent")


def _metricas(driver) -> dict:
    return driver.page.evaluate(
        """() => ({
            avail: [screen.availWidth, screen.availHeight],
            outer: [window.outerWidth, window.outerHeight],
            inner: [window.innerWidth, window.innerHeight],
        })"""
    )


def test_janela_cabe_na_tela(driver):
    """A janela nunca pode passar da area util: no Windows ela sai do monitor."""
    m = _metricas(driver)
    assert m["outer"][0] <= m["avail"][0], m
    assert m["outer"][1] <= m["avail"][1], m


def test_pagina_usa_o_tamanho_real_da_janela(driver):
    """Regressao: `viewport` forcado emulava 1920x1080 dentro de uma janela menor.

    O Chrome comprimia a pagina para caber, e a interface do WhatsApp aparecia
    minuscula e embolada. Sem emulacao, a diferenca entre janela e pagina e'
    apenas a moldura do navegador.
    """
    m = _metricas(driver)
    assert m["inner"][0] <= m["outer"][0], m
    assert m["inner"][1] <= m["outer"][1], m
    assert m["outer"][0] - m["inner"][0] <= 40, m  # bordas laterais
    assert m["outer"][1] - m["inner"][1] <= 200, m  # barra de titulo + abas


def test_largura_suficiente_para_o_whatsapp(driver):
    """Estreito demais e o WhatsApp esconde a lista de conversas."""
    m = _metricas(driver)
    assert m["inner"][0] >= min(900, m["avail"][0] - 120), m


# --------------------------------------------------------------- leitura DOM
def test_scan_le_todas_as_linhas(driver):
    linhas = driver.scan_chat_list()
    assert [linha["title"] for linha in linhas] == ["Familia", "Amigas", "Trabalho", "Ciladas"]


def test_scan_le_badge_e_icone(driver):
    por_titulo = {linha["title"]: linha for linha in driver.scan_chat_list()}
    assert por_titulo["Familia"]["unread"] is True
    assert por_titulo["Familia"]["mention"] is True
    assert por_titulo["Trabalho"]["unread"] is False  # sem badge na pagina falsa
    assert por_titulo["Amigas"]["mention"] is False


def test_mencoes_no_dom_real(driver):
    driver._notified_cache.clear()
    mencoes = driver.find_mentions(["Maria"])
    assert [m.chat for m in mencoes] == ["Familia"]
    assert mencoes[0].preview == "Joao: @Maria bom dia!"


# ---------------------------------------------------------------- digitacao
def test_digitacao_e_tecla_por_tecla(driver):
    # A caixa de mensagem so existe com uma conversa aberta.
    caixa = driver.open_chat("Familia")
    assert caixa is not None

    inicio = time.monotonic()
    driver.type_human_like(caixa, "teste humano")
    decorrido = time.monotonic() - inicio

    assert driver.page.evaluate("() => document.getElementById('composer').innerText").strip() == (
        "teste humano"
    )
    # 12 caracteres a 50-180ms cada, mais a pausa do clique
    assert decorrido >= 0.9
    driver.page.evaluate("() => { document.getElementById('composer').innerText = ''; }")


@pytest.mark.parametrize(
    "texto",
    [
        "Bom dia! ☀️",  # emoji fora do BMP e seletor de variacao
        "Hora do remédio! 💊",
        "Bom domingo, família! ❤️",
        "Reunião às 9h — não esqueça",  # acentos e travessao
    ],
)
def test_digita_acentos_e_emojis(driver, texto):
    """O README sugere mensagens com emoji: a digitacao precisa dar conta."""
    caixa = driver.open_chat("Familia")
    assert caixa is not None
    driver.type_human_like(caixa, texto)
    digitado = driver.page.evaluate(
        "() => document.getElementById('composer').innerText"
    )
    assert digitado.strip() == texto
    driver.page.evaluate("() => { document.getElementById('composer').innerText = ''; }")


# -------------------------------------------------------------------- envio
def test_envia_para_a_conversa_certa(driver):
    antes = len(enviadas(driver))
    assert driver.send_message("Familia", "Bom dia!") is True
    depois = enviadas(driver)
    assert len(depois) == antes + 1
    assert depois[-1] == {"chat": "Familia", "text": "Bom dia!"}


def test_conversa_inexistente_nao_envia(driver):
    antes = len(enviadas(driver))
    assert driver.send_message("Grupo Que Nao Existe", "Bom dia!") is False
    assert len(enviadas(driver)) == antes


def test_nao_digita_na_conversa_errada(driver):
    """A linha "Ciladas" abre um cabecalho chamado "Outra Conversa"."""
    antes = len(enviadas(driver))
    assert driver.send_message("Ciladas", "Bom dia!") is False
    assert len(enviadas(driver)) == antes


def test_open_chat_devolve_a_caixa_sem_digitar(driver):
    antes = len(enviadas(driver))
    caixa = driver.open_chat("Amigas")
    assert caixa is not None
    assert len(enviadas(driver)) == antes  # abrir nao envia nada


def test_open_chat_nao_deixa_a_busca_filtrando(driver):
    """Filtro esquecido na busca cegaria a varredura de mencoes depois do envio."""
    assert driver.open_chat("Familia") is not None
    assert len(driver.scan_chat_list()) == 4


def test_open_chat_recusa_cabecalho_diferente(driver):
    assert driver.open_chat("Ciladas") is None


# --------------------------------------------------------------- diagnostico
def test_diagnose_reporta_seletores_e_conversas(driver):
    antes = len(enviadas(driver))
    report = driver.diagnose(["Familia", "Amigas"])

    assert report["logged_in"] is True
    assert report["webdriver_flag"] is False
    assert report["chats"] == {"Familia": True, "Amigas": True}
    assert report["selectors"]["chat_list"] == "#pane-side"
    assert report["selectors"]["search"] is not None
    assert report["selectors"]["message_box"] is not None
    assert report["selectors"]["qr_code"] is None  # nao ha QR: estamos "logados"
    assert len(report["rows"]) == 4
    assert len(enviadas(driver)) == antes, "diagnose nao pode enviar mensagem"


def test_diagnose_marca_conversa_que_nao_existe(driver):
    report = driver.diagnose(["Familia", "Fantasma"])
    assert report["chats"] == {"Familia": True, "Fantasma": False}


# ------------------------------------------------------------------- falhas
def test_erro_quando_a_lista_desaparece(driver):
    driver.page.evaluate("() => document.getElementById('pane-side').remove()")
    with pytest.raises(DriverError):
        driver.health_check()
    with pytest.raises(DriverError):
        driver.scan_chat_list()
    driver.page.reload()  # devolve a pagina para os testes seguintes
    assert driver._first_visible(whatsapp_driver.CHAT_LIST_SELECTORS, 5000) is not None


def test_driver_fechado_levanta_erro(make_config):
    parado = WhatsAppDriver(make_config())
    assert parado.is_running is False
    with pytest.raises(DriverError):
        parado.scan_chat_list()
