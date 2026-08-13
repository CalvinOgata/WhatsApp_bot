"""Deteccao de @mencoes a partir da lista de conversas (sem navegador)."""

from __future__ import annotations

import pytest

from src.whatsapp_driver import WhatsAppDriver


def row(title, text, unread=True, mention=False) -> dict:
    return {"title": title, "text": text, "unread": unread, "mention": mention}


@pytest.fixture
def driver(make_config):
    """Driver sem navegador: scan_chat_list e' substituido em cada teste."""
    return WhatsAppDriver(make_config())


def scanning(driver, rows):
    driver.scan_chat_list = lambda: rows
    return driver


NOMES = ["Maria Silva", "Maria"]


# ------------------------------------------------------------------ acertos
def test_detecta_mencao_ao_primeiro_nome(driver):
    scanning(driver, [row("Familia", "Familia 10:32 Joao: @Maria bom dia! 2")])
    assert [m.chat for m in driver.find_mentions(NOMES)] == ["Familia"]


def test_detecta_mencao_ao_nome_completo(driver):
    scanning(driver, [row("Grupo", "Grupo 12:00 Bia: @Maria Silva confirma? 3")])
    assert [m.chat for m in driver.find_mentions(NOMES)] == ["Grupo"]


def test_icone_de_mencao_sem_texto_visivel(driver):
    # Mensagem longa: o @nome pode nao aparecer no preview, mas o icone aparece.
    scanning(driver, [row("Grupo", "Grupo 12:00 Bia: mensagem longa...", mention=True)])
    assert [m.chat for m in driver.find_mentions(NOMES)] == ["Grupo"]


def test_apelido_extra_configurado(driver):
    scanning(driver, [row("Grupo", "Grupo 12:00 Bia: @Mari vem?")])
    assert [m.chat for m in driver.find_mentions([*NOMES, "Mari"])] == ["Grupo"]


# ------------------------------------------------------------------- recusas
def test_ignora_conversa_sem_mencao(driver):
    scanning(driver, [row("Amigas", "Amigas 09:10 Ana: alguem viu o filme? 1")])
    assert driver.find_mentions(NOMES) == []


def test_ignora_conversa_ja_lida(driver):
    scanning(driver, [row("Lidas", "Lidas 08:00 Ana: @Maria oi", unread=False)])
    assert driver.find_mentions(NOMES) == []


def test_ignora_mencao_feita_pela_propria_usuaria(driver):
    scanning(driver, [row("Trabalho", "Trabalho 11:00 Você: @Joao me liga", mention=True)])
    assert driver.find_mentions(NOMES) == []


def test_ignora_you_em_ingles(driver):
    scanning(driver, [row("Work", "Work 11:00 You: @John call me", mention=True)])
    assert driver.find_mentions(NOMES) == []


def test_ignora_mencao_a_outra_pessoa(driver):
    scanning(driver, [row("Grupo", "Grupo 11:00 Ana: @Joao vem? 1", mention=True)])
    assert driver.find_mentions(NOMES) == []


def test_sem_nomes_configurados_nao_notifica(driver):
    scanning(driver, [row("Familia", "Familia 10:32 Joao: @Maria bom dia! 2", mention=True)])
    assert driver.find_mentions([]) == []


# ---------------------------------------------------------------- repeticao
def test_nao_notifica_a_mesma_mencao_duas_vezes(driver):
    scanning(driver, [row("Familia", "Familia 10:32 Joao: @Maria bom dia! 2")])
    assert len(driver.find_mentions(NOMES)) == 1
    assert driver.find_mentions(NOMES) == []


def test_mensagem_nova_notifica_de_novo(driver):
    rows = [row("Familia", "Familia 10:32 Joao: @Maria bom dia! 2")]
    scanning(driver, rows)
    driver.find_mentions(NOMES)
    rows[0]["text"] = "Familia 10:40 Joao: @Maria voce viu? 3"
    assert [m.chat for m in driver.find_mentions(NOMES)] == ["Familia"]


def test_cache_expira(driver):
    scanning(driver, [row("Familia", "Familia 10:32 Joao: @Maria bom dia! 2")])
    driver.find_mentions(NOMES)
    driver._cache_ttl = 0  # simula a passagem do tempo
    assert len(driver.find_mentions(NOMES)) == 1


def test_cache_nao_cresce_sem_limite(driver):
    for indice in range(600):
        scanning(driver, [row(f"Grupo {indice}", f"Grupo {indice} 10:32 Ana: @Maria oi")])
        driver.find_mentions(NOMES)
    assert len(driver._notified_cache) <= 500


# ------------------------------------------------------------------ preview
@pytest.mark.parametrize(
    "titulo, texto, esperado",
    [
        ("Familia", "Familia 10:32 Joao: @Maria bom dia! @ 2", "Joao: @Maria bom dia!"),
        ("Familia", "Familia 10:32 Joao: @Maria bom dia! 2 @", "Joao: @Maria bom dia!"),
        ("Amigas", "Amigas 09:10 Ana: viu o filme? 1", "Ana: viu o filme?"),
        ("Grupo", "Grupo 11:00 Bia: oi @Maria", "Bia: oi @Maria"),
        ("X", "X 1:05 PM Ana: ola", "Ana: ola"),
        ("Y", "Y 23:59 Ana: bom dia", "Ana: bom dia"),
    ],
)
def test_limpeza_do_preview(driver, titulo, texto, esperado):
    assert driver._extract_preview(titulo, texto) == esperado


def test_preview_chega_limpo_na_mencao(driver):
    scanning(driver, [row("Familia", "Familia 10:32 Joao: @Maria bom dia! @ 2", mention=True)])
    assert driver.find_mentions(NOMES)[0].preview == "Joao: @Maria bom dia!"


# --------------------------------------------------------------- varias linhas
def test_varias_conversas_de_uma_vez(driver):
    scanning(
        driver,
        [
            row("Familia", "Familia 10:32 Joao: @Maria bom dia! 2", mention=True),
            row("Amigas", "Amigas 09:10 Ana: viu o filme? 1"),
            row("Trabalho", "Trabalho 11:00 Você: @Joao me liga", mention=True),
            row("Grupo X", "Grupo X 12:00 Bia: @Maria Silva confirma? 3"),
            row("Lidas", "Lidas 08:00 Ana: @Maria oi", unread=False),
        ],
    )
    assert sorted(m.chat for m in driver.find_mentions(NOMES)) == ["Familia", "Grupo X"]


def test_linha_sem_titulo_e_ignorada(driver):
    scanning(driver, [row("", "10:32 Ana: @Maria oi")])
    assert driver.find_mentions(NOMES) == []
