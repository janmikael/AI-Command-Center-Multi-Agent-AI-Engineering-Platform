"""acc.realtime.publisher.serialize: EventRecord -> JSON, the exact shape sent over the wire."""

import json
import uuid
from datetime import UTC, datetime

from acc.domain.events import EventRecord
from acc.realtime.publisher import as_wire_dict, serialize

EVENT = EventRecord(
    id=uuid.UUID(int=1),
    seq=42,
    ts=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
    type="task.created",
    actor="user:" + str(uuid.UUID(int=2)),
    severity="info",
    project_id=uuid.UUID(int=3),
    task_id=uuid.UUID(int=4),
    agent_id=None,
    run_id=None,
    task_key="ACC-1",
    agent_slug=None,
    payload={"title": "Add health endpoint"},
)


def test_wire_dict_has_exactly_the_expected_fields() -> None:
    assert set(as_wire_dict(EVENT)) == {
        "id", "seq", "ts", "type", "severity", "actor",
        "project_id", "task_id", "agent_id", "run_id", "task_key", "agent_slug", "payload",
    }  # fmt: skip


def test_serializes_to_valid_json_with_string_uuids_and_iso_timestamps() -> None:
    decoded = json.loads(serialize(EVENT))
    assert decoded["id"] == str(EVENT.id)
    assert decoded["task_id"] == str(EVENT.task_id)
    assert decoded["agent_id"] is None
    assert decoded["ts"] == "2026-09-24T12:00:00+00:00"
    assert decoded["seq"] == 42
    assert decoded["payload"] == {"title": "Add health endpoint"}


def test_round_trips_null_optional_fields() -> None:
    decoded = json.loads(serialize(EVENT))
    assert decoded["agent_slug"] is None
