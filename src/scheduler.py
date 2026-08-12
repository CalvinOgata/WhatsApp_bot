"""Agendamento das saudacoes (bom dia / boa noite) com jitter aleatorio.

Design: um unico thread. A biblioteca `schedule` executa os jobs no proprio thread
que chama `run_pending()`, o que e' obrigatorio aqui - a API sincrona do Playwright
nao pode ser usada de outra thread. Por isso `tick()` e' chamado pelo loop principal.

O jitter nao pode bloquear o loop (senao paramos de detectar mencoes), entao ele
funciona em dois passos:
  1. no horario configurado, `schedule` "arma" o job com um horario-alvo aleatorio;
  2. o `tick()` seguinte que passar desse horario e' o que realmente envia.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import random
from dataclasses import dataclass
from typing import Callable

import schedule

from .config import Config

LOG = logging.getLogger(__name__)

# (chave_do_job, mensagem) -> quantas conversas receberam a mensagem
SendCallback = Callable[[str, str], int]

MORNING = "morning"
EVENING = "evening"


@dataclass(frozen=True)
class _ArmedJob:
    key: str
    message: str
    base_at: dt.datetime  # horario configurado de hoje
    due_at: dt.datetime  # horario configurado + jitter


class GreetingScheduler:
    """Dispara as saudacoes no horario configurado + atraso aleatorio."""

    def __init__(self, config: Config, send_callback: SendCallback) -> None:
        self.config = config
        self._send = send_callback
        self._sched = schedule.Scheduler()
        self._armed: dict[str, _ArmedJob] = {}
        self._state = self._load_state()

        self._sched.every().day.at(config.morning_time).do(
            self._arm, MORNING, config.morning_message, config.morning_time
        )
        self._sched.every().day.at(config.evening_time).do(
            self._arm, EVENING, config.evening_message, config.evening_time
        )

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

    def trigger_now(self, key: str) -> int:
        """Envia uma saudacao imediatamente (usado pelos modos de teste)."""
        message = (
            self.config.morning_message if key == MORNING else self.config.evening_message
        )
        LOG.info("Envio manual solicitado (%s).", key)
        return self._send(key, message)

    def describe(self) -> str:
        """Resumo legivel dos proximos disparos, para o log de inicializacao."""
        parts = []
        for job in self._sched.jobs:
            when = job.next_run.strftime("%d/%m %H:%M") if job.next_run else "?"
            parts.append(when)
        jitter = f"+{self.config.jitter_min_seconds}-{self.config.jitter_max_seconds}s"
        return (
            f"bom dia {self.config.morning_time} / boa noite {self.config.evening_time} "
            f"(jitter {jitter}); proximos: {', '.join(parts) or 'nenhum'}"
        )

    # ---------------------------------------------------------------- interno
    def _arm(self, key: str, message: str, base_time: str) -> None:
        now = dt.datetime.now()
        jitter = random.randint(self.config.jitter_min_seconds, self.config.jitter_max_seconds)
        job = _ArmedJob(
            key=key,
            message=message,
            base_at=self._today_at(now, base_time),
            due_at=now + dt.timedelta(seconds=jitter),
        )
        self._armed[key] = job
        LOG.info(
            "Saudacao '%s' armada para %s (atraso aleatorio de %ss).",
            key,
            job.due_at.strftime("%H:%M:%S"),
            jitter,
        )

    def _fire(self, job: _ArmedJob, now: dt.datetime) -> None:
        today = now.date().isoformat()
        if self._state.get(job.key) == today:
            LOG.info("Saudacao '%s' ja foi enviada hoje; ignorando.", job.key)
            return

        # Computador suspenso / programa aberto tarde: nao enviar "Bom dia!" as 15h.
        atraso = now - job.base_at
        limite = dt.timedelta(minutes=self.config.max_catch_up_minutes)
        if atraso > limite:
            LOG.warning(
                "Saudacao '%s' pulada: o horario passou ha %s (limite de %s min).",
                job.key,
                str(atraso).split(".")[0],
                self.config.max_catch_up_minutes,
            )
            self._remember(job.key, today)
            return

        # Marcamos ANTES de enviar: se algo estourar no meio, o pior caso e' nao
        # enviar hoje - nunca enviar duas vezes.
        self._remember(job.key, today)
        enviados = self._send(job.key, job.message)
        LOG.info(
            "Saudacao '%s' concluida: %s de %s conversas.",
            job.key,
            enviados,
            len(self.config.target_chats),
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
