"""Validacao do config.json: o que o usuario final vai errar."""

from __future__ import annotations

import pytest

from src.config import ALL_DAYS, ConfigError, load_config


# --------------------------------------------------------------- formato novo
def test_carrega_formato_novo(write_config, valid_payload):
    config = load_config(write_config(valid_payload))
    assert [m.name for m in config.messages] == ["bom dia", "boa noite"]
    assert [m.at for m in config.messages] == ["08:30", "21:30"]
    assert [m.message for m in config.messages] == ["Bom dia!", "Boa noite!"]
    # sem "chats" proprio, cada mensagem herda target_chats
    assert all(m.chats == ("Familia", "Amigas") for m in config.messages)
    assert all(m.days == ALL_DAYS for m in config.messages)


def test_aceita_muitas_mensagens(write_config, valid_payload):
    valid_payload["scheduled_messages"] = [
        {"name": f"aviso {hora}", "time": f"{hora:02d}:00", "message": f"São {hora}h"}
        for hora in (7, 12, 15, 18, 22)
    ]
    config = load_config(write_config(valid_payload))
    assert len(config.messages) == 5
    assert [m.at for m in config.messages] == ["07:00", "12:00", "15:00", "18:00", "22:00"]


def test_chats_por_mensagem_sobrepoem_o_padrao(write_config, valid_payload):
    valid_payload["scheduled_messages"] = [
        {"name": "bom dia", "time": "08:30", "message": "Bom dia!"},
        {"name": "aviso", "time": "12:00", "message": "Almoço!", "chats": ["Trabalho"]},
    ]
    config = load_config(write_config(valid_payload))
    assert config.messages[0].chats == ("Familia", "Amigas")
    assert config.messages[1].chats == ("Trabalho",)
    assert config.all_chats == ("Familia", "Amigas", "Trabalho")


def test_chats_pode_ser_um_texto_simples(write_config, valid_payload):
    valid_payload["scheduled_messages"] = [
        {"time": "08:30", "message": "Bom dia!", "chats": "Só a Familia"}
    ]
    config = load_config(write_config(valid_payload))
    assert config.messages[0].chats == ("Só a Familia",)


def test_nome_e_opcional_e_cai_no_texto(write_config, valid_payload):
    valid_payload["scheduled_messages"] = [{"time": "08:30", "message": "Bom dia!"}]
    config = load_config(write_config(valid_payload))
    assert config.messages[0].name == "Bom dia!"
    assert config.messages[0].key == "bom-dia"


def test_nomes_repetidos_geram_chaves_distintas(write_config, valid_payload):
    valid_payload["scheduled_messages"] = [
        {"name": "aviso", "time": "08:30", "message": "Um"},
        {"name": "aviso", "time": "20:30", "message": "Dois"},
    ]
    config = load_config(write_config(valid_payload))
    chaves = [m.key for m in config.messages]
    assert len(set(chaves)) == 2, chaves


def test_enabled_false_desativa_a_mensagem(write_config, valid_payload):
    valid_payload["scheduled_messages"][1]["enabled"] = False
    config = load_config(write_config(valid_payload))
    assert [m.name for m in config.messages] == ["bom dia"]


def test_todas_desativadas_e_erro(write_config, valid_payload):
    for entry in valid_payload["scheduled_messages"]:
        entry["enabled"] = False
    with pytest.raises(ConfigError, match="desativadas"):
        load_config(write_config(valid_payload))


# ------------------------------------------------------------- dias da semana
@pytest.mark.parametrize(
    "days, esperado",
    [
        ("dias uteis", {0, 1, 2, 3, 4}),
        ("dias úteis", {0, 1, 2, 3, 4}),
        ("fim de semana", {5, 6}),
        ("todos", set(range(7))),
        (["segunda", "sexta"], {0, 4}),
        (["sábado", "domingo"], {5, 6}),
        (["seg", "qua", "sex"], {0, 2, 4}),
        (["mon", "friday"], {0, 4}),
        (["terça-feira"], {1}),
    ],
)
def test_dias_da_semana(write_config, valid_payload, days, esperado):
    valid_payload["scheduled_messages"] = [
        {"time": "08:30", "message": "Bom dia!", "days": days}
    ]
    config = load_config(write_config(valid_payload))
    assert set(config.messages[0].days) == esperado


def test_dia_invalido_explica_as_opcoes(write_config, valid_payload):
    valid_payload["scheduled_messages"] = [
        {"time": "08:30", "message": "Bom dia!", "days": ["quartada"]}
    ]
    with pytest.raises(ConfigError, match="quartada"):
        load_config(write_config(valid_payload))


def test_descricao_dos_dias(write_config, valid_payload):
    valid_payload["scheduled_messages"] = [
        {"time": "08:30", "message": "a", "days": "dias uteis"},
        {"time": "09:30", "message": "b", "days": "fim de semana"},
        {"time": "10:30", "message": "c"},
        {"time": "11:30", "message": "d", "days": ["segunda"]},
    ]
    config = load_config(write_config(valid_payload))
    assert [m.describe_days() for m in config.messages] == [
        "dias uteis",
        "fim de semana",
        "todos os dias",
        "segunda",
    ]


# --------------------------------------------------------------- erros comuns
@pytest.mark.parametrize("hora", ["8:30", "25:30", "08:60", "830", "oito e meia", ""])
def test_horario_invalido(write_config, valid_payload, hora):
    valid_payload["scheduled_messages"] = [{"time": hora, "message": "Bom dia!"}]
    with pytest.raises(ConfigError, match="HH:MM|horario"):
        load_config(write_config(valid_payload))


def test_mensagem_sem_texto(write_config, valid_payload):
    valid_payload["scheduled_messages"] = [{"time": "08:30", "message": "  "}]
    with pytest.raises(ConfigError, match="message"):
        load_config(write_config(valid_payload))


def test_mensagem_sem_conversa_alguma(write_config, valid_payload):
    del valid_payload["target_chats"]
    valid_payload["scheduled_messages"] = [{"time": "08:30", "message": "Bom dia!"}]
    with pytest.raises(ConfigError, match="target_chats"):
        load_config(write_config(valid_payload))


def test_scheduled_messages_vazia(write_config, valid_payload):
    valid_payload["scheduled_messages"] = []
    with pytest.raises(ConfigError, match="pelo menos uma mensagem"):
        load_config(write_config(valid_payload))


def test_entrada_que_nao_e_objeto(write_config, valid_payload):
    valid_payload["scheduled_messages"] = ["08:30 Bom dia"]
    with pytest.raises(ConfigError, match="entre chaves"):
        load_config(write_config(valid_payload))


def test_sem_nome_do_usuario(write_config, valid_payload):
    valid_payload["user_name"] = ""
    with pytest.raises(ConfigError, match="user_name"):
        load_config(write_config(valid_payload))


def test_json_quebrado_aponta_a_linha(write_config):
    caminho = write_config('{\n  "user_name": "Maria",\n  "target_chats": ["A",]\n}')
    with pytest.raises(ConfigError, match="linha 3"):
        load_config(caminho)


def test_json_que_nao_e_objeto(write_config):
    with pytest.raises(ConfigError, match="objeto JSON"):
        load_config(write_config("[1, 2, 3]"))


# ------------------------------------------------------------- textos exemplo
def test_placeholders_sao_recusados(write_config):
    from src.config import TEMPLATE

    with pytest.raises(ConfigError, match="textos de exemplo"):
        load_config(write_config(dict(TEMPLATE)))


def test_placeholder_parcial_tambem_e_recusado(write_config):
    from src.config import TEMPLATE

    payload = dict(TEMPLATE)
    payload["user_name"] = "Maria Silva"  # so o nome preenchido
    with pytest.raises(ConfigError, match="PRIMEIRA CONVERSA"):
        load_config(write_config(payload))


def test_placeholder_em_chats_de_mensagem(write_config, valid_payload):
    valid_payload["scheduled_messages"][0]["chats"] = ["NOME EXATO DA PRIMEIRA CONVERSA"]
    with pytest.raises(ConfigError, match="textos de exemplo"):
        load_config(write_config(valid_payload))


def test_arquivo_ausente_cria_modelo(tmp_path):
    caminho = tmp_path / "novo.json"
    with pytest.raises(ConfigError, match="Criei um arquivo"):
        load_config(caminho)
    assert caminho.exists()
    # o modelo recem-criado tambem e' recusado, por conter os textos de exemplo
    with pytest.raises(ConfigError, match="textos de exemplo"):
        load_config(caminho)


# ----------------------------------------------------------- formato antigo
def test_formato_antigo_ainda_funciona(write_config):
    config = load_config(
        write_config(
            {
                "user_name": "Maria",
                "morning_time": "07:15",
                "evening_time": "22:45",
                "target_chats": ["Familia"],
                "check_interval_seconds": 12,
                "browser_channel": "msedge",
            }
        )
    )
    assert [(m.key, m.at, m.message) for m in config.messages] == [
        ("morning", "07:15", "Bom dia!"),
        ("evening", "22:45", "Boa noite!"),
    ]


def test_formato_antigo_com_textos_proprios(write_config):
    config = load_config(
        write_config(
            {
                "user_name": "Maria",
                "morning_time": "07:15",
                "morning_message": "Bom dia, familia!",
                "target_chats": ["Familia"],
            }
        )
    )
    assert len(config.messages) == 1
    assert config.messages[0].message == "Bom dia, familia!"


def test_sem_horario_nenhum_pede_scheduled_messages(write_config):
    with pytest.raises(ConfigError, match="scheduled_messages"):
        load_config(write_config({"user_name": "Maria", "target_chats": ["Familia"]}))


# ------------------------------------------------------------------- extras
def test_intervalo_e_limitado(write_config, valid_payload):
    valid_payload["check_interval_seconds"] = 2
    assert load_config(write_config(valid_payload)).check_interval_seconds == 10
    valid_payload["check_interval_seconds"] = 9999
    assert load_config(write_config(valid_payload)).check_interval_seconds == 300


def test_canal_desconhecido_cai_no_msedge(write_config, valid_payload):
    valid_payload["browser_channel"] = "netscape"
    assert load_config(write_config(valid_payload)).browser_channel == "msedge"


def test_conversas_repetidas_sao_removidas(write_config, valid_payload):
    valid_payload["target_chats"] = ["Familia", "Familia", "Amigas"]
    assert load_config(write_config(valid_payload)).target_chats == ("Familia", "Amigas")


def test_nomes_de_mencao(write_config, valid_payload):
    valid_payload["mention_names"] = ["Mari", "@Mari", "x"]
    config = load_config(write_config(valid_payload))
    assert config.mention_names[:2] == ["Maria Silva", "Maria"]
    assert "Mari" in config.mention_names
    assert "x" not in config.mention_names  # curto demais para ser confiavel


@pytest.mark.parametrize("busca", ["bom dia", "BOM DIA", "bom-dia", "Bom Dia"])
def test_find_message_ignora_caixa_e_acento(write_config, valid_payload, busca):
    config = load_config(write_config(valid_payload))
    encontrada = config.find_message(busca)
    assert encontrada is not None and encontrada.at == "08:30"


def test_find_message_inexistente(write_config, valid_payload):
    assert load_config(write_config(valid_payload)).find_message("almoço") is None


def test_jitter_maximo_nunca_menor_que_minimo(write_config, valid_payload):
    valid_payload["jitter_min_seconds"] = 200
    valid_payload["jitter_max_seconds"] = 50
    config = load_config(write_config(valid_payload))
    assert config.jitter_max_seconds >= config.jitter_min_seconds
