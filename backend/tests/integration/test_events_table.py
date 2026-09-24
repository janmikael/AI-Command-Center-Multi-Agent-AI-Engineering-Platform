"""The events table is append-only, enforced by Postgres itself, for every role."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from acc.commands._base import unit_of_work
from acc.db.models import Event
from acc.db.session import SessionFactory
from acc.domain.events import SYSTEM, AgentMessage, EventRecord, EventType, TaskCreated
from acc.domain.task_status import TaskStatus

pytestmark = pytest.mark.integration


class RecordingSink:
    def __init__(self, fail: bool = False) -> None:
        self.published: list[EventRecord] = []
        self.fail = fail

    async def publish(self, events: Sequence[EventRecord]) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.published.extend(events)


async def emit_messages(sf: SessionFactory, n: int = 1) -> list[EventRecord]:
    async with unit_of_work(sf, None) as uow:
        for i in range(n):
            await uow.emit(EventType.AGENT_MESSAGE, AgentMessage(text=f"msg {i}"), actor=SYSTEM)
    return uow.events


def current_partition() -> str:
    now = datetime.now(UTC)
    return f"events_y{now:%Y}m{now:%m}"


class TestWritePath:
    async def test_emit_assigns_increasing_seq(self, sf: SessionFactory) -> None:
        events = await emit_messages(sf, 3)
        seqs = [e.seq for e in events]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 3

    async def test_rows_land_in_the_monthly_partition(
        self, sf: SessionFactory, owner_conn: AsyncConnection
    ) -> None:
        [event] = await emit_messages(sf)
        partition = await owner_conn.scalar(
            text("SELECT tableoid::regclass::text FROM events WHERE id = :id"), {"id": event.id}
        )
        assert partition == current_partition()

    async def test_commit_then_publish(self, sf: SessionFactory) -> None:
        sink = RecordingSink()
        async with unit_of_work(sf, sink) as uow:
            await uow.emit(EventType.AGENT_MESSAGE, AgentMessage(text="hi"), actor=SYSTEM)
            assert sink.published == []  # nothing published before commit
        assert [e.payload for e in sink.published] == [{"text": "hi"}]

    async def test_rollback_discards_state_and_events_and_publishes_nothing(
        self, sf: SessionFactory
    ) -> None:
        sink = RecordingSink()

        async def failing_command() -> None:
            async with unit_of_work(sf, sink) as uow:
                await uow.emit(EventType.AGENT_MESSAGE, AgentMessage(text="lost"), actor=SYSTEM)
                raise RuntimeError("command failed after emitting")

        with pytest.raises(RuntimeError):
            await failing_command()
        async with sf() as session:
            assert (await session.scalars(select(Event))).all() == []
        assert sink.published == []

    async def test_publish_failure_does_not_undo_the_commit(self, sf: SessionFactory) -> None:
        async with unit_of_work(sf, RecordingSink(fail=True)) as uow:
            await uow.emit(EventType.AGENT_MESSAGE, AgentMessage(text="durable"), actor=SYSTEM)
        async with sf() as session:
            assert len((await session.scalars(select(Event))).all()) == 1

    async def test_payload_type_checked_before_writing(self, sf: SessionFactory) -> None:
        wrong = TaskCreated(key="X-1", title="t", priority=3, status=TaskStatus.BACKLOG)

        async def command() -> None:
            async with unit_of_work(sf, None) as uow:
                await uow.emit(EventType.AGENT_MESSAGE, wrong, actor=SYSTEM)

        with pytest.raises(TypeError):
            await command()


class TestAppendOnly:
    @pytest.mark.parametrize(
        "sql",
        ["UPDATE events SET actor = 'tampered'", "DELETE FROM events"],
        ids=["update", "delete"],
    )
    async def test_trigger_blocks_even_the_owner(
        self, sf: SessionFactory, owner_conn: AsyncConnection, sql: str
    ) -> None:
        await emit_messages(sf)
        with pytest.raises(DBAPIError, match="append-only"):
            await owner_conn.execute(text(sql))

    async def test_trigger_blocks_writes_to_a_partition_directly(
        self, sf: SessionFactory, owner_conn: AsyncConnection
    ) -> None:
        await emit_messages(sf)
        # Partition name is derived from the clock, not user input.
        sql = f"UPDATE {current_partition()} SET actor = 'tampered'"  # noqa: S608
        with pytest.raises(DBAPIError, match="append-only"):
            await owner_conn.execute(text(sql))

    @pytest.mark.parametrize(
        "sql",
        [
            "UPDATE events SET actor = 'tampered'",
            "DELETE FROM events",
            "TRUNCATE events",
            "UPDATE events_default SET actor = 'tampered'",
            "UPDATE {partition} SET actor = 'tampered'",
            "DELETE FROM {partition}",
        ],
    )
    async def test_runtime_role_has_no_privilege(
        self, sf: SessionFactory, app_conn: AsyncConnection, sql: str
    ) -> None:
        await emit_messages(sf)
        with pytest.raises(DBAPIError, match="permission denied"):
            await app_conn.execute(text(sql.format(partition=current_partition())))

    async def test_runtime_role_can_read_and_insert(self, app_conn: AsyncConnection) -> None:
        for privilege, expected in [("SELECT", True), ("INSERT", True), ("UPDATE", False)]:
            granted = await app_conn.scalar(
                text("SELECT has_table_privilege('acc_app', 'events', :p)"), {"p": privilege}
            )
            assert granted is expected, privilege


class TestPartitions:
    async def test_ensure_is_idempotent_and_new_partitions_are_locked_down(
        self, app_conn: AsyncConnection
    ) -> None:
        await app_conn.scalar(text("SELECT acc_ensure_event_partitions(6)"))
        assert await app_conn.scalar(text("SELECT acc_ensure_event_partitions(6)")) == 0

        partitions = (
            await app_conn.scalars(
                text(
                    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                    "WHERE i.inhparent = 'events'::regclass ORDER BY c.relname"
                )
            )
        ).all()
        monthly = [p for p in partitions if p != "events_default"]
        assert len(monthly) >= 7
        assert "events_default" in partitions

        newest = monthly[-1]
        for privilege, expected in [("INSERT", True), ("UPDATE", False), ("DELETE", False)]:
            granted = await app_conn.scalar(
                text("SELECT has_table_privilege('acc_app', :t, :p)"),
                {"t": newest, "p": privilege},
            )
            assert granted is expected, f"{newest} {privilege}"

    async def test_runtime_role_cannot_run_ddl_itself(self, app_conn: AsyncConnection) -> None:
        with pytest.raises(DBAPIError, match="permission denied"):
            await app_conn.execute(
                text(
                    "CREATE TABLE events_rogue PARTITION OF events "
                    "FOR VALUES FROM ('2099-01-01') TO ('2099-02-01')"
                )
            )

    async def test_argument_is_bounded(self, app_conn: AsyncConnection) -> None:
        with pytest.raises(DBAPIError, match="between 0 and 24"):
            await app_conn.execute(text("SELECT acc_ensure_event_partitions(100)"))
