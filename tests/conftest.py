"""Fixtures compartilhadas pelos testes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import ALL_DAYS, Config, ScheduledMessage  # noqa: E402


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Diretorio de dados isolado (state.json, trava, perfil do navegador)."""
    path = tmp_path / "dados"
    (path / "browser_profile").mkdir(parents=True)
    return path


@pytest.fixture
def make_message():
    """Fabrica de ScheduledMessage com valores razoaveis."""

    def factory(**kw) -> ScheduledMessage:
        params = dict(
            key="bom-dia",
            name="bom dia",
            at="08:30",
            message="Bom dia!",
            chats=("Familia",),
            days=ALL_DAYS,
        )
        params.update(kw)
        params["chats"] = tuple(params["chats"])
        return ScheduledMessage(**params)

    return factory


@pytest.fixture
def make_config(data_dir: Path, make_message):
    """Fabrica de Config ja validada, sem passar pelo arquivo."""

    def factory(**kw) -> Config:
        params = dict(
            user_name="Maria Silva",
            messages=(make_message(),),
            check_interval_seconds=12,
            browser_channel="msedge",
            target_chats=("Familia",),
            mention_names=["Maria Silva", "Maria"],
            data_dir=data_dir,
        )
        params.update(kw)
        params["messages"] = tuple(params["messages"])
        return Config(**params)

    return factory


@pytest.fixture
def write_config(tmp_path: Path):
    """Escreve um config.json de teste e devolve o caminho."""

    def factory(payload: dict | str, name: str = "config.json") -> Path:
        path = tmp_path / name
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    return factory


@pytest.fixture
def valid_payload() -> dict:
    """config.json minimo e valido no formato novo."""
    return {
        "user_name": "Maria Silva",
        "target_chats": ["Familia", "Amigas"],
        "scheduled_messages": [
            {"name": "bom dia", "time": "08:30", "message": "Bom dia!"},
            {"name": "boa noite", "time": "21:30", "message": "Boa noite!"},
        ],
        "check_interval_seconds": 12,
        "browser_channel": "msedge",
    }
