"""Encerramento limpo, trava de instancia unica e argumentos de linha de comando.

O stop.bat nao pode ser testado aqui (e' Windows), mas o mecanismo que ele usa
pode: ele so cria um arquivo, e todo o comportamento esta neste modulo.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import pytest

import main as mainmod
from src.scheduler import MessageScheduler


@pytest.fixture(autouse=True)
def estado_limpo():
    """Cada teste comeca sem pedido de parada pendente."""
    mainmod._stop_requested = False
    mainmod._stop_file = None
    yield
    mainmod._stop_requested = False
    mainmod._stop_file = None


class NotifierFalso:
    """Guarda os avisos em vez de chamar o Windows."""

    def __init__(self) -> None:
        self.avisos: list[tuple[str, str]] = []

    def notify(self, title: str, body: str) -> None:
        self.avisos.append((title, body))

    def notify_mention(self, chat: str, preview: str) -> None:
        self.avisos.append((chat, preview))


# ------------------------------------------------------------ pedido de parada
def test_sem_arquivo_nao_para(tmp_path):
    mainmod.arm_stop_file(tmp_path / "stop.request")
    assert mainmod.should_stop() is False


def test_arquivo_criado_para_o_programa(tmp_path):
    pedido = tmp_path / "stop.request"
    mainmod.arm_stop_file(pedido)
    pedido.write_text("", encoding="utf-8")  # e' o que o stop.bat faz
    assert mainmod.should_stop() is True


def test_pedido_e_consumido(tmp_path):
    """O arquivo e' apagado ao ser lido, para nao sobrar sujeira."""
    pedido = tmp_path / "stop.request"
    mainmod.arm_stop_file(pedido)
    pedido.write_text("", encoding="utf-8")
    assert mainmod.should_stop() is True
    assert not pedido.exists()
    # continua parado depois de consumir o arquivo
    assert mainmod.should_stop() is True


def test_pedido_antigo_nao_derruba_execucao_nova(tmp_path):
    """Cenario real: o programa foi morto a forca e o pedido ficou no disco.

    Sem essa limpeza, o proximo start.bat subiria e morreria na hora.
    """
    pedido = tmp_path / "stop.request"
    pedido.write_text("", encoding="utf-8")
    mainmod.arm_stop_file(pedido)
    assert not pedido.exists()
    assert mainmod.should_stop() is False


def test_sinal_do_sistema_para(tmp_path):
    mainmod.arm_stop_file(tmp_path / "stop.request")
    mainmod._request_stop(15, None)
    assert mainmod.should_stop() is True


def test_interruptible_sleep_sai_no_pedido(tmp_path):
    """A saida tem que ser rapida, nao esperar o intervalo inteiro."""
    import time

    pedido = tmp_path / "stop.request"
    mainmod.arm_stop_file(pedido)
    pedido.write_text("", encoding="utf-8")
    inicio = time.monotonic()
    mainmod.interruptible_sleep(30)
    assert time.monotonic() - inicio < 1.0


def test_stop_request_fica_no_data_dir(make_config):
    config = make_config()
    assert config.stop_request_path.parent == config.data_dir
    assert config.stop_request_path.name == "stop.request"


# ---------------------------------------------------------- trava de instancia
def test_trava_impede_segunda_copia(tmp_path):
    caminho = tmp_path / "assistente.lock"
    primeira = mainmod.SingleInstanceLock(caminho)
    segunda = mainmod.SingleInstanceLock(caminho)
    assert primeira.acquire() is True
    assert segunda.acquire() is False
    primeira.release()
    assert not caminho.exists()


def test_trava_guarda_o_pid(tmp_path):
    """O stop.bat le esse arquivo para saber quem encerrar."""
    import os

    caminho = tmp_path / "assistente.lock"
    trava = mainmod.SingleInstanceLock(caminho)
    assert trava.acquire() is True
    assert caminho.read_text(encoding="utf-8").strip() == str(os.getpid())
    trava.release()


def test_trava_antiga_e_reaproveitada(tmp_path):
    caminho = tmp_path / "assistente.lock"
    caminho.write_text("999999", encoding="utf-8")  # processo que nao existe
    trava = mainmod.SingleInstanceLock(caminho)
    assert trava.acquire() is True
    trava.release()


def test_trava_ilegivel_nao_travar_para_sempre(tmp_path):
    caminho = tmp_path / "assistente.lock"
    caminho.write_text("nao e um numero", encoding="utf-8")
    trava = mainmod.SingleInstanceLock(caminho)
    assert trava.acquire() is True
    trava.release()


def test_release_sem_acquire_nao_estoura(tmp_path):
    mainmod.SingleInstanceLock(tmp_path / "assistente.lock").release()


# ------------------------------------------------------------------ argumentos
@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--visible"],
        ["--verbose"],
        ["--test-notification"],
        ["--diagnose"],
        ["--send-now", "bom dia"],
    ],
)
def test_argumentos_validos(argv):
    mainmod.parse_args(argv)


def test_send_now_aceita_nome_com_espaco():
    assert mainmod.parse_args(["--send-now", "hora do remedio"]).send_now == (
        "hora do remedio"
    )


def test_argumento_desconhecido_e_recusado():
    with pytest.raises(SystemExit):
        mainmod.parse_args(["--modo-turbo"])


# --------------------------------------------------------- pedido de envio
# Cenario que motivou tudo isto: o assistente esta ligado (avisando das mencoes) e
# a pessoa roda `--send-now "bom dia"` no Prompt. O segundo processo nao pode abrir
# o mesmo perfil do navegador, entao ele deixa o pedido em um arquivo.
def test_pedido_de_envio_e_consumido(tmp_path):
    pedido = tmp_path / "send.request"
    mainmod.write_send_request(pedido, "bom dia")
    assert mainmod.take_send_request(pedido) == "bom dia"
    assert not pedido.exists()
    assert mainmod.take_send_request(pedido) is None


def test_pedido_de_envio_vazio_e_descartado(tmp_path):
    """Um arquivo ilegivel nao pode ficar no disco: seria relido a cada ciclo."""
    pedido = tmp_path / "send.request"
    pedido.write_text("   \n", encoding="utf-8")
    assert mainmod.take_send_request(pedido) is None
    assert not pedido.exists()


def test_send_request_fica_no_data_dir(make_config):
    config = make_config()
    assert config.send_request_path.parent == config.data_dir
    assert config.send_request_path.name == "send.request"


def _scheduler_espiao(config):
    """Agendador de verdade, com o envio trocado por um registrador."""
    enviadas: list[str] = []

    def envia(message) -> int:
        enviadas.append(message.name)
        return len(message.chats)

    return MessageScheduler(config, envia), enviadas


def test_loop_atende_o_pedido_de_envio(make_config):
    config = make_config()
    scheduler, enviadas = _scheduler_espiao(config)
    mainmod.write_send_request(config.send_request_path, "bom dia")

    mainmod.serve_send_request(scheduler, config, NotifierFalso())

    assert enviadas == ["bom dia"]
    assert not config.send_request_path.exists()


def test_loop_sem_pedido_nao_envia_nada(make_config):
    config = make_config()
    scheduler, enviadas = _scheduler_espiao(config)
    mainmod.serve_send_request(scheduler, config, NotifierFalso())
    assert enviadas == []


def test_pedido_com_nome_errado_avisa_e_nao_derruba_o_loop(make_config):
    config = make_config()
    scheduler, enviadas = _scheduler_espiao(config)
    notifier = NotifierFalso()
    mainmod.write_send_request(config.send_request_path, "cafe da tarde")

    mainmod.serve_send_request(scheduler, config, notifier)

    assert enviadas == []
    assert not config.send_request_path.exists()
    assert notifier.avisos, "o usuario precisa saber que o nome nao existe"


def test_send_now_com_assistente_ligado_vira_pedido(make_config):
    config = make_config()
    args = mainmod.parse_args(["--send-now", "bom dia"])
    assert mainmod.delegate_to_running_instance(config, NotifierFalso(), args, 4321) == 0
    assert config.send_request_path.read_text(encoding="utf-8") == "bom dia"


def test_diagnose_com_assistente_ligado_nao_vira_pedido(make_config):
    """--diagnose precisa do navegador so para ele: nao da para delegar."""
    config = make_config()
    args = mainmod.parse_args(["--diagnose"])
    assert mainmod.delegate_to_running_instance(config, NotifierFalso(), args, 4321) == 3
    assert not config.send_request_path.exists()


# ------------------------------------------------------------------- log
@pytest.fixture
def root_limpo():
    """Isola o logger raiz: setup_logging mexe em estado global."""
    root = logging.getLogger()
    antes, nivel = root.handlers[:], root.level
    root.handlers[:] = []
    yield root
    for handler in root.handlers:
        handler.close()
    root.handlers[:] = antes
    root.setLevel(nivel)


def test_instancia_secundaria_nao_abre_o_log_compartilhado(root_limpo, monkeypatch, tmp_path):
    """Dois RotatingFileHandler no mesmo arquivo embaralham o log no Windows.

    Era o que acontecia ao rodar `--send-now` com o assistente ligado: as linhas se
    misturavam e a rotacao falhava, porque o outro processo ainda segurava o arquivo.
    """
    monkeypatch.setattr(mainmod, "default_data_dir", lambda: tmp_path)
    assert mainmod.setup_logging(False, to_file=False) is None
    assert not [h for h in root_limpo.handlers if isinstance(h, RotatingFileHandler)]
    assert not (tmp_path / "assistente.log").exists()


def test_instancia_dona_escreve_no_log(root_limpo, monkeypatch, tmp_path):
    monkeypatch.setattr(mainmod, "default_data_dir", lambda: tmp_path)
    assert mainmod.setup_logging(False, to_file=True) == tmp_path / "assistente.log"
    assert len([h for h in root_limpo.handlers if isinstance(h, RotatingFileHandler)]) == 1
