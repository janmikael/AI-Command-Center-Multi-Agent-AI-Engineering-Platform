"""Unit of work: one transaction for a state change *and* the events describing it.

    async with unit_of_work(sf, sink) as uow:
        task.status = ...                               # mutate
        await uow.emit(EventType.TASK_STATUS_CHANGED, TaskStatusChanged(...), actor=...)
    # committed here; only now are the events handed to the sink (realtime fan-out)

If anything inside the block raises, neither the state change nor its events persist.
Publishing happens strictly after commit, so subscribers never see an event that was
rolled back. A publish failure is logged and swallowed: the event is already durable in
Postgres, and clients catch up from there on reconnect.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from acc.db.models import Event
from acc.db.session import SessionFactory
from acc.domain.events import EventRecord, EventType, Payload, Severity, check_payload
from acc.ids import new_id

log = structlog.get_logger("acc.commands")


class EventSink(Protocol):
    """Receives committed events (implemented by acc.realtime.publisher)."""

    async def publish(self, events: Sequence[EventRecord]) -> None: ...


class UnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.events: list[EventRecord] = []

    async def emit(
        self,
        event_type: EventType,
        payload: Payload,
        *,
        actor: str,
        severity: Severity = Severity.INFO,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        task_key: str | None = None,
        agent_slug: str | None = None,
    ) -> EventRecord:
        check_payload(event_type, payload)
        row = Event(
            id=new_id(),
            actor=actor,
            type=event_type.value,
            severity=severity.value,
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            run_id=run_id,
            payload=payload.model_dump(mode="json"),
        )
        self.session.add(row)
        await self.session.flush()  # assigns ts and seq (RETURNING)
        record = EventRecord(
            id=row.id,
            seq=row.seq,
            ts=row.ts,
            type=row.type,
            actor=row.actor,
            severity=row.severity,
            project_id=row.project_id,
            task_id=row.task_id,
            agent_id=row.agent_id,
            run_id=row.run_id,
            task_key=task_key,
            agent_slug=agent_slug,
            payload=row.payload,
        )
        self.events.append(record)
        return record


@asynccontextmanager
async def unit_of_work(sf: SessionFactory, sink: EventSink | None) -> AsyncIterator[UnitOfWork]:
    async with sf() as session, session.begin():
        uow = UnitOfWork(session)
        yield uow
    if sink is not None and uow.events:
        try:
            await sink.publish(uow.events)
        except Exception:
            log.exception("event_publish_failed", count=len(uow.events))
