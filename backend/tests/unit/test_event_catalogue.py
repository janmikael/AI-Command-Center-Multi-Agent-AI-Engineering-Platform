import uuid

import pytest
from pydantic import ValidationError

from acc.domain.events import (
    PAYLOAD_SCHEMAS,
    AgentDecision,
    AgentMessage,
    EventType,
    FieldChange,
    TaskCreated,
    TaskStatusChanged,
    TaskUpdated,
    check_payload,
    user_actor,
)
from acc.domain.task_status import TaskStatus


def test_every_event_type_has_a_schema() -> None:
    assert set(PAYLOAD_SCHEMAS) == set(EventType)


def test_payload_must_match_its_type() -> None:
    payload = TaskStatusChanged(from_status=TaskStatus.BACKLOG, to_status=TaskStatus.PLANNING)
    check_payload(EventType.TASK_STATUS_CHANGED, payload)
    with pytest.raises(TypeError, match="TaskCreated"):
        check_payload(EventType.TASK_CREATED, payload)


def test_payloads_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TaskCreated(key="ACC-1", title="t", priority=3, status=TaskStatus.BACKLOG, extra=1)  # type: ignore[call-arg]


def test_payloads_are_immutable() -> None:
    payload = AgentMessage(text="hello")
    with pytest.raises(ValidationError):
        payload.text = "changed"  # type: ignore[misc]


def test_empty_update_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskUpdated(changes={})
    TaskUpdated(changes={"title": FieldChange(before="a", after="b")})


def test_serializes_to_json_friendly_dict() -> None:
    dumped = TaskStatusChanged(
        from_status=TaskStatus.WORKING, to_status=TaskStatus.REVIEW, reason="ready"
    ).model_dump(mode="json")
    assert dumped == {"from_status": "working", "to_status": "review", "reason": "ready"}


def test_bounds() -> None:
    with pytest.raises(ValidationError):
        AgentMessage(text="")
    with pytest.raises(ValidationError):
        AgentDecision(decision="x", rationale="y", alternatives=["a"] * 11)


def test_actor_format() -> None:
    uid = uuid.UUID(int=1)
    assert user_actor(uid) == f"user:{uid}"
