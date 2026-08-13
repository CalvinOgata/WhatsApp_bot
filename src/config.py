"""Leitura e validacao do config.json.

Tudo aqui e' desenhado para que um erro de digitacao do usuario final gere uma
mensagem clara em vez de um traceback: o app roda sem console (pythonw), portanto
qualquer problema precisa acabar no log e numa notificacao.

O agendamento e' uma lista livre (`scheduled_messages`): a usuaria pode ter duas
saudacoes, cinco, ou uma mensagem so na sexta-feira. O formato antigo
(`morning_time`/`evening_time`) continua funcionando e e' convertido para a lista.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import unicodedata
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
    "target_chats": ["NOME EXATO DA PRIMEIRA CONVERSA", "NOME EXATO DA SEGUNDA CONVERSA"],
    "scheduled_messages": [
        {"name": "bom dia", "time": "08:30", "message": "Bom dia!"},
        {"name": "boa noite", "time": "21:30", "message": "Boa noite!"},
    ],
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

# Dias da semana aceitos, em portugues e ingles (com ou sem acento/abreviacao).
# 0 = segunda-feira, como em datetime.weekday().
WEEKDAY_ALIASES = {
    "segunda": 0, "segunda-feira": 0, "seg": 0, "monday": 0, "mon": 0,
    "terca": 1, "terca-feira": 1, "ter": 1, "tuesday": 1, "tue": 1,
    "quarta": 2, "quarta-feira": 2, "qua": 2, "wednesday": 2, "wed": 2,
    "quinta": 3, "quinta-feira": 3, "qui": 3, "thursday": 3, "thu": 3,
    "sexta": 4, "sexta-feira": 4, "sex": 4, "friday": 4, "fri": 4,
    "sabado": 5, "sab": 5, "saturday": 5, "sat": 5,
    "domingo": 6, "dom": 6, "sunday": 6, "sun": 6,
}
WEEKDAY_GROUPS = {
    "todos": frozenset(range(7)),
    "todos os dias": frozenset(range(7)),
    "diario": frozenset(range(7)),
    "all": frozenset(range(7)),
    "daily": frozenset(range(7)),
    "dias uteis": frozenset({0, 1, 2, 3, 4}),
    "semana": frozenset({0, 1, 2, 3, 4}),
    "weekdays": frozenset({0, 1, 2, 3, 4}),
    "fim de semana": frozenset({5, 6}),
    "fds": frozenset({5, 6}),
    "weekend": frozenset({5, 6}),
}
WEEKDAY_NAMES = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
ALL_DAYS = frozenset(range(7))


class ConfigError(Exception):
    """Configuracao ausente ou invalida."""


@dataclass(frozen=True)
class ScheduledMessage:
    """Uma mensagem agendada: o que enviar, para quem, quando e em que dias."""

    key: str  # identificador estavel usado no state.json
    name: str  # nome legivel, usado nos logs e em --send-now
    at: str  # "HH:MM"
    message: str
    chats: tuple[str, ...]
    days: frozenset[int] = ALL_DAYS

    def runs_on(self, weekday: int) -> bool:
        return weekday in self.days

    def describe_days(self) -> str:
        if self.days == ALL_DAYS:
            return "todos os dias"
        if self.days == WEEKDAY_GROUPS["dias uteis"]:
            return "dias uteis"
        if self.days == WEEKDAY_GROUPS["fim de semana"]:
            return "fim de semana"
        return ", ".join(WEEKDAY_NAMES[day] for day in sorted(self.days))

    def describe(self) -> str:
        return (
            f'{self.at} "{self.message}" -> {", ".join(self.chats)} '
            f"({self.describe_days()})"
        )


@dataclass
class Config:
    """Configuracao efetiva do app (valores ja validados e normalizados)."""

    user_name: str
    messages: tuple[ScheduledMessage, ...]
    check_interval_seconds: int
    browser_channel: str

    # Lista padrao de conversas: usada por toda mensagem que nao define a sua.
    target_chats: tuple[str, ...] = ()

    # --- opcionais (nao precisam existir no config.json) ---
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

    @property
    def all_chats(self) -> tuple[str, ...]:
        """Toda conversa citada em qualquer lugar da configuracao."""
        seen: list[str] = list(self.target_chats)
        for message in self.messages:
            for chat in message.chats:
                if chat not in seen:
                    seen.append(chat)
        return tuple(seen)

    def find_message(self, wanted: str) -> ScheduledMessage | None:
        """Busca uma mensagem por chave ou nome (usado por --send-now)."""
        needle = _norm_key(wanted)
        for message in self.messages:
            if needle in (_norm_key(message.key), _norm_key(message.name)):
                return message
        return None


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


def _deaccent(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )


def _norm_key(value: str) -> str:
    """Normaliza para comparar: sem acento, minusculo, espacos colapsados."""
    return re.sub(r"[\s_]+", " ", _deaccent(str(value)).casefold()).strip()


def _clean_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _require_str(raw: dict, key: str) -> str:
    value = _clean_str(raw.get(key))
    if not value:
        raise ConfigError(f'"{key}" precisa ser um texto entre aspas no config.json.')
    return value


def _parse_time(value: object, label: str) -> str:
    text = _clean_str(value)
    if not text:
        raise ConfigError(f'{label} precisa ser um horario entre aspas, como "08:30".')
    if not _TIME_RE.match(text):
        raise ConfigError(
            f'{label} precisa estar no formato "HH:MM" (24 horas). Recebido: "{text}".'
        )
    return text


def _parse_chats(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):  # uma conversa so, sem lista
        value = [value]
    if not isinstance(value, list):
        raise ConfigError(f'{label} precisa ser uma lista de nomes entre aspas.')
    chats: list[str] = []
    for item in value:
        name = _clean_str(item)
        if not name:
            raise ConfigError(f"Todos os nomes em {label} precisam ser textos entre aspas.")
        if name not in chats:
            chats.append(name)
    return tuple(chats)


def _parse_days(value: object, label: str) -> frozenset[int]:
    if value is None:
        return ALL_DAYS
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        raise ConfigError(f'{label} precisa ser uma lista, como ["segunda", "sexta"].')
    days: set[int] = set()
    for item in items:
        key = _norm_key(item)
        if key in WEEKDAY_GROUPS:
            days |= WEEKDAY_GROUPS[key]
        elif key in WEEKDAY_ALIASES:
            days.add(WEEKDAY_ALIASES[key])
        else:
            raise ConfigError(
                f'{label}: nao reconheci o dia "{item}". Use segunda, terca, quarta, '
                'quinta, sexta, sabado, domingo, ou "dias uteis" / "fim de semana" / "todos".'
            )
    if not days:
        raise ConfigError(f"{label} ficou sem nenhum dia. Remova o campo para enviar todo dia.")
    return frozenset(days)


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _deaccent(value).casefold()).strip("-")
    return slug[:40] or fallback


def _legacy_entries(raw: dict) -> list[dict]:
    """Converte o formato antigo (morning_time/evening_time) para a lista nova.

    As chaves ficam "morning"/"evening" para que o state.json de quem ja usava a
    versao anterior continue valendo.
    """
    entries: list[dict] = []
    for prefix, key, name, default in (
        ("morning", "morning", "bom dia", "Bom dia!"),
        ("evening", "evening", "boa noite", "Boa noite!"),
    ):
        when = raw.get(f"{prefix}_time")
        if when is None:
            continue
        entries.append(
            {
                "key": key,
                "name": name,
                "time": when,
                "message": raw.get(f"{prefix}_message", default),
            }
        )
    if entries:
        LOG.info(
            "config.json no formato antigo: convertendo %s horario(s) para scheduled_messages.",
            len(entries),
        )
    return entries


def _parse_messages(raw: dict, default_chats: tuple[str, ...]) -> tuple[ScheduledMessage, ...]:
    entries = raw.get("scheduled_messages")
    if entries is None:
        entries = _legacy_entries(raw)
    if not isinstance(entries, list) or not entries:
        raise ConfigError(
            '"scheduled_messages" precisa ser uma lista com pelo menos uma mensagem. '
            'Exemplo: "scheduled_messages": [{"time": "08:30", "message": "Bom dia!"}]'
        )

    messages: list[ScheduledMessage] = []
    used_keys: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        label = f"mensagem #{index} de scheduled_messages"
        if not isinstance(entry, dict):
            raise ConfigError(
                f'A {label} precisa estar entre chaves, como '
                '{"time": "08:30", "message": "Bom dia!"}.'
            )
        if entry.get("enabled") is False:
            LOG.info("Mensagem #%s desativada (enabled: false).", index)
            continue

        at = _parse_time(entry.get("time"), f'"time" da {label}')
        message_text = _clean_str(entry.get("message"))
        if not message_text:
            raise ConfigError(f'"message" da {label} precisa ser um texto entre aspas.')

        name = _clean_str(entry.get("name")) or message_text
        chats = _parse_chats(entry.get("chats"), f'"chats" da {label}') or default_chats
        if not chats:
            raise ConfigError(
                f'A {label} nao tem conversas: preencha "target_chats" no topo do '
                'arquivo ou um "chats" proprio nessa mensagem.'
            )

        key = _slug(_clean_str(entry.get("key")) or name, f"mensagem-{index}")
        while key in used_keys:  # nomes repetidos nao podem colidir no state.json
            key = f"{key}-{index}"
        used_keys.add(key)

        messages.append(
            ScheduledMessage(
                key=key,
                name=name,
                at=at,
                message=message_text,
                chats=chats,
                days=_parse_days(entry.get("days"), f'"days" da {label}'),
            )
        )

    if not messages:
        raise ConfigError(
            "Todas as mensagens estao desativadas (enabled: false). "
            "Ative pelo menos uma para o programa ter o que fazer."
        )
    return tuple(messages)


def _placeholders_left(user_name: str, chats: tuple[str, ...]) -> list[str]:
    """Valores de exemplo que o usuario ainda nao trocou."""
    known = {value.casefold() for value in PLACEHOLDER_VALUES}
    seen: list[str] = []
    for value in (user_name, *chats):
        if value.casefold() in known and value not in seen:
            seen.append(value)
    return seen


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


def _parse_interval(raw: dict) -> int:
    value = raw.get("check_interval_seconds", 12)
    try:
        interval = int(value)
    except (TypeError, ValueError):
        raise ConfigError('"check_interval_seconds" precisa ser um numero.') from None
    clamped = max(MIN_CHECK_INTERVAL, min(MAX_CHECK_INTERVAL, interval))
    if clamped != interval:
        LOG.warning(
            "check_interval_seconds=%s fora do permitido; usando %s segundos.", interval, clamped
        )
    return clamped


def _parse_channel(raw: dict) -> str:
    channel = str(raw.get("browser_channel", "msedge")).strip().lower()
    if channel not in VALID_CHANNELS:
        LOG.warning(
            'browser_channel "%s" desconhecido; usando "msedge". Opcoes: %s',
            channel,
            ", ".join(VALID_CHANNELS),
        )
        return "msedge"
    return channel


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
            "Abra ele, preencha seu nome, os horarios e as conversas, e rode o programa de novo."
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
    target_chats = _parse_chats(raw.get("target_chats"), '"target_chats"')
    messages = _parse_messages(raw, target_chats)

    every_chat = tuple(dict.fromkeys((*target_chats, *(c for m in messages for c in m.chats))))
    pendentes = _placeholders_left(user_name, every_chat)
    if pendentes:
        raise ConfigError(
            "O arquivo config.json ainda esta com os textos de exemplo: "
            + ", ".join(f'"{value}"' for value in pendentes)
            + ". Abra o arquivo e troque pelo seu nome e pelos nomes exatos das "
            "conversas, como eles aparecem no WhatsApp."
        )

    jitter_min = max(0, int(raw.get("jitter_min_seconds", 10)))
    jitter_max = max(jitter_min, int(raw.get("jitter_max_seconds", 300)))

    config = Config(
        user_name=user_name,
        messages=messages,
        check_interval_seconds=_parse_interval(raw),
        browser_channel=_parse_channel(raw),
        target_chats=target_chats,
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
