"""Agendador: horario + jitter, dias da semana, e a garantia de nao duplicar."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from src.scheduler import MessageScheduler, _ArmedJob


@pytest.fixture
def enviados() -> list:
    return []


@pytest.fixture
def make_scheduler(make_config, enviados):
    """Agendador com um callback que so registra o que seria enviado."""

    def factory(**kw) -> MessageScheduler:
        config = kw.pop("config", None) or make_config(**kw)
        return MessageScheduler(
            config, lambda message: enviados.append(message.name) or len(message.chats)
        )

    return factory


def _force_due(
    scheduler: MessageScheduler, message, minutos_de_atraso: float = 1.0
) -> None:
    """Coloca a mensagem como vencida, sem esperar o horario real."""
    agora = dt.datetime.now()
    scheduler._armed[message.key] = _ArmedJob(
        message=message,
        base_at=agora - dt.timedelta(minutes=minutos_de_atraso),
        due_at=agora - dt.timedelta(seconds=1),
    )


# ------------------------------------------------------------------- jitter
def test_arm_aplica_jitter_no_futuro(make_config, make_scheduler):
    config = make_config()
    scheduler = make_scheduler(config=config)
    mensagem = config.messages[0]

    antes = dt.datetime.now()
    scheduler._arm(mensagem)
    job = scheduler._armed[mensagem.key]

    atraso = (job.due_at - antes).total_seconds()
    assert config.jitter_min_seconds - 1 <= atraso <= config.jitter_max_seconds + 1
    assert (job.base_at.hour, job.base_at.minute) == (8, 30)


def test_jitter_respeita_faixa_configurada(make_config, make_scheduler):
    config = make_config(jitter_min_seconds=30, jitter_max_seconds=31)
    scheduler = make_scheduler(config=config)
    scheduler._arm(config.messages[0])
    atraso = (scheduler._armed["bom-dia"].due_at - dt.datetime.now()).total_seconds()
    assert 28 <= atraso <= 32


def test_nao_envia_antes_da_hora(make_config, make_scheduler, enviados):
    config = make_config()
    scheduler = make_scheduler(config=config)
    scheduler._arm(config.messages[0])
    scheduler.tick()
    assert enviados == []
    assert scheduler.armed_keys == ("bom-dia",)


# -------------------------------------------------------------------- envio
def test_envia_quando_vence(make_config, make_scheduler, enviados):
    config = make_config()
    scheduler = make_scheduler(config=config)
    _force_due(scheduler, config.messages[0])
    scheduler.tick()
    assert enviados == ["bom dia"]
    assert config.state_path.exists()
    assert scheduler.sent_today("bom-dia")


def test_nao_envia_duas_vezes_no_mesmo_dia(make_config, make_scheduler, enviados):
    config = make_config()
    scheduler = make_scheduler(config=config)
    _force_due(scheduler, config.messages[0])
    scheduler.tick()
    _force_due(scheduler, config.messages[0])
    scheduler.tick()
    assert enviados == ["bom dia"]


def test_estado_sobrevive_a_reinicio(make_config, enviados):
    config = make_config()
    primeiro = MessageScheduler(config, lambda m: enviados.append(m.name) or 1)
    _force_due(primeiro, config.messages[0])
    primeiro.tick()

    segundo = MessageScheduler(config, lambda m: enviados.append("de novo") or 1)
    _force_due(segundo, config.messages[0])
    segundo.tick()
    assert enviados == ["bom dia"]


def test_state_json_corrompido_nao_derruba(make_config, make_scheduler, enviados):
    config = make_config()
    config.state_path.write_text("{isso nao e json", encoding="utf-8")
    scheduler = make_scheduler(config=config)
    _force_due(scheduler, config.messages[0])
    scheduler.tick()
    assert enviados == ["bom dia"]


def test_state_json_tem_formato_esperado(make_config, make_scheduler):
    config = make_config()
    scheduler = make_scheduler(config=config)
    _force_due(scheduler, config.messages[0])
    scheduler.tick()
    dados = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert dados["last_sent"]["bom-dia"] == dt.date.today().isoformat()


# ----------------------------------------------------- computador suspenso
def test_pula_mensagem_muito_atrasada(make_config, make_scheduler, enviados):
    config = make_config(max_catch_up_minutes=90)
    scheduler = make_scheduler(config=config)
    _force_due(scheduler, config.messages[0], minutos_de_atraso=5 * 60)
    scheduler.tick()
    assert enviados == []
    # marcada como resolvida hoje, para nao ficar tentando de novo
    assert scheduler.sent_today("bom-dia")


def test_atraso_dentro_do_limite_ainda_envia(make_config, make_scheduler, enviados):
    config = make_config(max_catch_up_minutes=90)
    scheduler = make_scheduler(config=config)
    _force_due(scheduler, config.messages[0], minutos_de_atraso=89)
    scheduler.tick()
    assert enviados == ["bom dia"]


# ------------------------------------------------------------ dia da semana
def test_nao_envia_em_dia_fora_da_lista(make_config, make_message, make_scheduler, enviados):
    outro_dia = (dt.date.today().weekday() + 1) % 7
    mensagem = make_message(days=frozenset({outro_dia}))
    config = make_config(messages=(mensagem,))
    scheduler = make_scheduler(config=config)
    _force_due(scheduler, mensagem)
    scheduler.tick()
    assert enviados == []
    # nao marca o dia: semana que vem, no dia certo, precisa enviar
    assert not scheduler.sent_today(mensagem.key)


def test_envia_no_dia_da_lista(make_config, make_message, make_scheduler, enviados):
    hoje = dt.date.today().weekday()
    mensagem = make_message(days=frozenset({hoje}))
    config = make_config(messages=(mensagem,))
    scheduler = make_scheduler(config=config)
    _force_due(scheduler, mensagem)
    scheduler.tick()
    assert enviados == ["bom dia"]


# ------------------------------------------------------- varias mensagens
def test_mensagens_sao_independentes(make_config, make_message, make_scheduler, enviados):
    mensagens = (
        make_message(key="cafe", name="cafe", at="07:00", message="Cafe!"),
        make_message(key="almoco", name="almoco", at="12:00", message="Almoco!"),
        make_message(key="boa-noite", name="boa noite", at="22:00", message="Boa noite!"),
    )
    config = make_config(messages=mensagens)
    scheduler = make_scheduler(config=config)

    _force_due(scheduler, mensagens[1])
    scheduler.tick()
    assert enviados == ["almoco"]
    assert not scheduler.sent_today("cafe")
    assert not scheduler.sent_today("boa-noite")

    _force_due(scheduler, mensagens[2])
    scheduler.tick()
    assert enviados == ["almoco", "boa noite"]


def test_conta_conversas_enviadas(make_config, make_message):
    mensagem = make_message(chats=("Familia", "Amigas", "Trabalho"))
    config = make_config(messages=(mensagem,))
    recebidas = []
    scheduler = MessageScheduler(
        config, lambda m: recebidas.append(m.chats) or len(m.chats)
    )
    _force_due(scheduler, mensagem)
    scheduler.tick()
    assert recebidas == [("Familia", "Amigas", "Trabalho")]


# -------------------------------------------------------------- envio manual
def test_trigger_now_por_nome(make_config, make_scheduler, enviados):
    scheduler = make_scheduler(config=make_config())
    assert scheduler.trigger_now("bom dia") == 1
    assert enviados == ["bom dia"]


def test_trigger_now_por_chave(make_config, make_scheduler, enviados):
    scheduler = make_scheduler(config=make_config())
    scheduler.trigger_now("bom-dia")
    assert enviados == ["bom dia"]


def test_trigger_now_ignora_o_estado_do_dia(make_config, make_scheduler, enviados):
    config = make_config()
    scheduler = make_scheduler(config=config)
    _force_due(scheduler, config.messages[0])
    scheduler.tick()
    scheduler.trigger_now("bom dia")  # teste manual deve funcionar mesmo assim
    assert enviados == ["bom dia", "bom dia"]


def test_trigger_now_nome_errado_lista_as_opcoes(make_config, make_scheduler):
    scheduler = make_scheduler(config=make_config())
    with pytest.raises(KeyError, match="bom dia"):
        scheduler.trigger_now("cafe da manha")


# ------------------------------------------------------------------ resumo
def test_describe_lista_todas_as_mensagens(make_config, make_message, make_scheduler):
    config = make_config(
        messages=(
            make_message(),
            make_message(key="boa-noite", name="boa noite", at="21:30", message="Boa noite!"),
        )
    )
    linhas = make_scheduler(config=config).describe()
    assert len(linhas) == 2
    assert "bom dia" in linhas[0] and "08:30" in linhas[0]
    assert "boa noite" in linhas[1] and "21:30" in linhas[1]


def test_next_run_existe_para_cada_mensagem(make_config, make_scheduler):
    config = make_config()
    scheduler = make_scheduler(config=config)
    assert scheduler.next_run("bom-dia") is not None
    assert scheduler.next_run("inexistente") is None
