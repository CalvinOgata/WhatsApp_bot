"""Agendamento das mensagens configuradas, com jitter aleatorio.

Design: um unico thread. A biblioteca `schedule` executa os jobs no proprio thread
que chama `run_pending()`, o que e' obrigatorio aqui - a API sincrona do Playwright
nao pode ser usada de outra thread. Por isso `tick()` e' chamado pelo loop principal.

O jitter nao pode bloquear o loop (senao paramos de detectar mencoes), entao ele
funciona em dois passos:
  1. no horario configurado, `schedule` "arma" a mensagem com um alvo aleatorio;
  2. o `tick()` seguinte que passar desse alvo e' o que realmente envia.

O agendador nao sabe nada sobre "manha" ou "noite": ele so percorre a lista de
`ScheduledMessage` que veio da configuracao.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import random
from dataclasses import dataclass
from typing import Callable

import schedule

from .config import WEEKDAY_NAMES, Config, ScheduledMessage

LOG = logging.getLogger(__name__)

# Recebe a mensagem agendada, devolve quantas conversas receberam o texto.
SendCallback = Callable[[ScheduledMessage], int]


@dataclass(frozen=True)
class _ArmedJob:
    message: ScheduledMessage
    base_at: dt.datetime  # horario configurado de hoje
    due_at: dt.datetime  # horario configurado + jitter


class MessageScheduler:
    """Dispara cada mensagem configurada no seu horario + atraso aleatorio."""

    def __init__(self, config: Config, send_callback: SendCallback) -> None:
        self.config = config
        self._send = send_callback
        self._sched = schedule.Scheduler()
        self._armed: dict[str, _ArmedJob] = {}
        self._state = self._load_state()

        # Um job de `schedule` por mensagem, guardado pela chave para que
        # describe() saiba o proximo disparo sem adivinhar pelo horario.
        self._jobs = {
            message.key: self._sched.every().day.at(message.at).do(self._arm, message)
            for message in config.messages
        }

    # ------------------------------------------------------------------- API
    def tick(self) -> None:
        """Chamado pelo loop principal a cada ciclo. Nunca bloqueia por muito tempo."""
        self._sched.run_pending()
        now = dt.datetime.now()
        for key, job in list(self._armed.items()):
            if now < job.due_at:
                continue
            self._armed.pop(key, None)
            self._fire(job, now)

    def trigger_now(self, wanted: str) -> int:
        """Envia uma mensagem imediatamente, por nome ou chave (modos de teste)."""
        message = self.config.find_message(wanted)
        if message is None:
            disponiveis = ", ".join(f'"{m.name}"' for m in self.config.messages)
            raise KeyError(f'Nao existe mensagem "{wanted}". Disponiveis: {disponiveis}')
        LOG.info("Envio manual solicitado: %s", message.name)
        return self._send(message)

    def next_run(self, key: str) -> dt.datetime | None:
        """Quando `schedule` vai armar essa mensagem outra vez."""
        job = self._jobs.get(key)
        return job.next_run if job else None

    def describe(self) -> list[str]:
        """Resumo legivel de cada mensagem e do proximo disparo."""
        linhas = []
        for message in self.config.messages:
            quando = self.next_run(message.key)
            proximo = quando.strftime("%d/%m %H:%M") if quando else "?"
            linhas.append(f"{message.name}: {message.describe()} | proximo: {proximo}")
        return linhas

    @property
    def armed_keys(self) -> tuple[str, ...]:
        """Mensagens esperando o atraso aleatorio terminar (usado no diagnostico)."""
        return tuple(self._armed)

    def sent_today(self, key: str, today: dt.date | None = None) -> bool:
        day = (today or dt.date.today()).isoformat()
        return self._state.get(key) == day

    # ---------------------------------------------------------------- interno
    def _arm(self, message: ScheduledMessage) -> None:
        now = dt.datetime.now()
        jitter = random.randint(self.config.jitter_min_seconds, self.config.jitter_max_seconds)
        job = _ArmedJob(
            message=message,
            base_at=self._today_at(now, message.at),
            due_at=now + dt.timedelta(seconds=jitter),
        )
        self._armed[message.key] = job
        LOG.info(
            "Mensagem '%s' armada para %s (atraso aleatorio de %ss).",
            message.name,
            job.due_at.strftime("%H:%M:%S"),
            jitter,
        )

    def _fire(self, job: _ArmedJob, now: dt.datetime) -> None:
        message = job.message
        today = now.date().isoformat()

        # `schedule` dispara todo dia; o filtro de dia da semana e' nosso.
        if not message.runs_on(now.weekday()):
            LOG.info(
                "Mensagem '%s' nao envia hoje (%s); configurada para %s.",
                message.name,
                _weekday_name(now.weekday()),
                message.describe_days(),
            )
            return

        if self._state.get(message.key) == today:
            LOG.info("Mensagem '%s' ja foi enviada hoje; ignorando.", message.name)
            return

        # Computador suspenso / programa aberto tarde: nao enviar "Bom dia!" as 15h.
        atraso = now - job.base_at
        limite = dt.timedelta(minutes=self.config.max_catch_up_minutes)
        if atraso > limite:
            LOG.warning(
                "Mensagem '%s' pulada: o horario passou ha %s (limite de %s min).",
                message.name,
                str(atraso).split(".")[0],
                self.config.max_catch_up_minutes,
            )
            self._remember(message.key, today)
            return

        # Marcamos ANTES de enviar: se algo estourar no meio, o pior caso e' nao
        # enviar hoje - nunca enviar duas vezes.
        self._remember(message.key, today)
        enviados = self._send(message)
        LOG.info(
            "Mensagem '%s' concluida: %s de %s conversas.",
            message.name,
            enviados,
            len(message.chats),
        )

    def _today_at(self, now: dt.datetime, base_time: str) -> dt.datetime:
        hour, minute = (int(part) for part in base_time.split(":"))
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # ------------------------------------------------------------------ estado
    def _load_state(self) -> dict[str, str]:
        path = self.config.state_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOG.warning("state.json ilegivel; comecando com estado vazio.", exc_info=True)
            return {}
        sent = data.get("last_sent") if isinstance(data, dict) else None
        if not isinstance(sent, dict):
            return {}
        return {str(k): str(v) for k, v in sent.items()}

    def _remember(self, key: str, day: str) -> None:
        self._state[key] = day
        path = self.config.state_path
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(
                json.dumps({"last_sent": self._state}, indent=2), encoding="utf-8"
            )
            temp.replace(path)  # troca atomica: nunca deixa um state.json truncado
        except OSError:
            LOG.warning("Nao foi possivel salvar o state.json.", exc_info=True)


def _weekday_name(weekday: int) -> str:
    return WEEKDAY_NAMES[weekday]
