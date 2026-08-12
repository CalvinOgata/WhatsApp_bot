"""Leitura e validacao do config.json.

Tudo aqui e' desenhado para que um erro de digitacao do usuario final gere uma
mensagem clara em vez de um traceback: o app roda sem console (pythonw), portanto
qualquer problema precisa acabar no log e numa notificacao.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Canais aceitos. "chromium" usa o navegador que o Playwright baixa em espaco de
# usuario (fallback quando Edge/Chrome nao estao instalados).
VALID_CHANNELS = ("msedge", "chrome", "chrome-beta", "msedge-beta", "chromium")

# O spec pede polling nao mais rapido que 10-15s. Limitamos para que ninguem
# consiga configurar algo agressivo o suficiente para chamar atencao.
MIN_CHECK_INTERVAL = 10
MAX_CHECK_INTERVAL = 300

TEMPLATE = {
    "user_name": "SEU NOME AQUI",
    "morning_time": "08:30",
    "evening_time": "21:30",
    "target_chats": ["NOME EXATO DA PRIMEIRA CONVERSA", "NOME EXATO DA SEGUNDA CONVERSA"],
    "check_interval_seconds": 12,
    "browser_channel": "msedge",
}

# Textos de exemplo do TEMPLATE. Se algum deles sobreviver no config.json, o
# usuario nao preencheu o arquivo: melhor recusar com uma instrucao clara do que
# procurar por uma conversa chamada "NOME EXATO DA PRIMEIRA CONVERSA".
PLACEHOLDER_VALUES = (
    TEMPLATE["user_name"],
    *TEMPLATE["target_chats"],
)


class ConfigError(Exception):
    """Configuracao ausente ou invalida."""


@dataclass
class Config:
    """Configuracao efetiva do app (valores ja validados e normalizados)."""

    user_name: str
    morning_time: str
    evening_time: str
    target_chats: list[str]
    check_interval_seconds: int
    browser_channel: str

    # --- opcionais (nao precisam existir no config.json) ---
    morning_message: str = "Bom dia!"
    evening_message: str = "Boa noite!"
    jitter_min_seconds: int = 10
    jitter_max_seconds: int = 300
    max_catch_up_minutes: int = 90
    hide_window: bool = True
    locale: str = "pt-BR"
    mention_names: list[str] = field(default_factory=list)
    data_dir: Path = field(default_factory=lambda: default_data_dir())

    @property
    def profile_dir(self) -> Path:
        """Perfil do navegador (cookies/localStorage da sessao do WhatsApp)."""
        return self.data_dir / "browser_profile"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "assistente.log"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "assistente.lock"


def default_data_dir() -> Path:
    """Diretorio de dados - sempre em espaco de usuario, nunca precisa de UAC."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "WhatsAppBotData"
    return PROJECT_ROOT / "user_data"


def write_template(path: Path = CONFIG_PATH) -> None:
    """Cria um config.json de exemplo para o usuario editar."""
    path.write_text(
        json.dumps(TEMPLATE, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _require_str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f'"{key}" precisa ser um texto entre aspas no config.json.')
    return value.strip()


def _require_time(raw: dict, key: str) -> str:
    value = _require_str(raw, key)
    if not _TIME_RE.match(value):
        raise ConfigError(
            f'"{key}" precisa estar no formato "HH:MM" (24 horas). Recebido: "{value}".'
        )
    return value


def _placeholders_left(user_name: str, target_chats: list[str]) -> list[str]:
    """Valores de exemplo que o usuario ainda nao trocou."""
    known = {value.casefold() for value in PLACEHOLDER_VALUES}
    return [value for value in (user_name, *target_chats) if value.casefold() in known]


def _mention_names(user_name: str, extra: object) -> list[str]:
    """Nomes que contam como mencao a usuaria: nome completo, primeiro nome e apelidos."""
    names: list[str] = []

    def add(candidate: object) -> None:
        if isinstance(candidate, str):
            cleaned = candidate.strip().lstrip("@").strip()
            if len(cleaned) >= 2 and cleaned.casefold() not in [n.casefold() for n in names]:
                names.append(cleaned)

    add(user_name)
    first = user_name.split()[0] if user_name.split() else ""
    add(first)
    if isinstance(extra, list):
        for item in extra:
            add(item)
    return names


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Carrega e valida o config.json.

    Se o arquivo nao existir, cria um modelo e levanta ConfigError pedindo que o
    usuario preencha (nunca assumimos nomes de conversas - mandar mensagem para a
    conversa errada e' pior do que nao mandar nada).
    """
    if not path.exists():
        write_template(path)
        raise ConfigError(
            f"Criei um arquivo de configuracao em {path}. "
            "Abra ele, preencha seu nome, horarios e as conversas, e rode o programa de novo."
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"O arquivo {path.name} tem um erro de formatacao na linha {exc.lineno}: {exc.msg}. "
            "Verifique se todas as virgulas e aspas estao no lugar."
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} deveria conter um objeto JSON (entre chaves).")

    user_name = _require_str(raw, "user_name")
    morning_time = _require_time(raw, "morning_time")
    evening_time = _require_time(raw, "evening_time")

    targets_raw = raw.get("target_chats")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise ConfigError(
            '"target_chats" precisa ser uma lista com pelo menos uma conversa. '
            'Exemplo: "target_chats": ["Familia", "Amigas"]'
        )
    target_chats: list[str] = []
    for item in targets_raw:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError('Todos os itens de "target_chats" precisam ser textos entre aspas.')
        name = item.strip()
        if name not in target_chats:
            target_chats.append(name)

    pendentes = _placeholders_left(user_name, target_chats)
    if pendentes:
        raise ConfigError(
            "O arquivo config.json ainda esta com os textos de exemplo: "
            + ", ".join(f'"{value}"' for value in pendentes)
            + ". Abra o arquivo e troque pelo seu nome e pelos nomes exatos das "
            "conversas, como eles aparecem no WhatsApp."
        )

    interval_raw = raw.get("check_interval_seconds", 12)
    try:
        interval = int(interval_raw)
    except (TypeError, ValueError):
        raise ConfigError('"check_interval_seconds" precisa ser um numero.') from None
    clamped = max(MIN_CHECK_INTERVAL, min(MAX_CHECK_INTERVAL, interval))
    if clamped != interval:
        LOG.warning(
            "check_interval_seconds=%s fora do permitido; usando %s segundos.", interval, clamped
        )

    channel = str(raw.get("browser_channel", "msedge")).strip().lower()
    if channel not in VALID_CHANNELS:
        LOG.warning(
            'browser_channel "%s" desconhecido; usando "msedge". Opcoes: %s',
            channel,
            ", ".join(VALID_CHANNELS),
        )
        channel = "msedge"

    jitter_min = max(0, int(raw.get("jitter_min_seconds", 10)))
    jitter_max = max(jitter_min, int(raw.get("jitter_max_seconds", 300)))

    config = Config(
        user_name=user_name,
        morning_time=morning_time,
        evening_time=evening_time,
        target_chats=target_chats,
        check_interval_seconds=clamped,
        browser_channel=channel,
        morning_message=str(raw.get("morning_message", "Bom dia!")).strip() or "Bom dia!",
        evening_message=str(raw.get("evening_message", "Boa noite!")).strip() or "Boa noite!",
        jitter_min_seconds=jitter_min,
        jitter_max_seconds=jitter_max,
        max_catch_up_minutes=max(0, int(raw.get("max_catch_up_minutes", 90))),
        hide_window=bool(raw.get("hide_window", True)),
        locale=str(raw.get("locale", "pt-BR")).strip() or "pt-BR",
        mention_names=_mention_names(user_name, raw.get("mention_names")),
        data_dir=default_data_dir(),
    )

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.profile_dir.mkdir(parents=True, exist_ok=True)
    return config
