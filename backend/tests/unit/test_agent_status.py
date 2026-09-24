from datetime import UTC, datetime, timedelta

import pytest

from acc.domain.agent_status import ACTIVITY_WINDOW, AgentStatus, derive_agent_status

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("enabled", "active_tasks", "last_activity", "expected"),
    [
        (False, 3, NOW, AgentStatus.DISABLED),  # disabled wins over everything
        (True, 0, None, AgentStatus.OFFLINE),  # never active
        (True, 2, None, AgentStatus.OFFLINE),  # assigned but silent
        (True, 1, NOW - ACTIVITY_WINDOW - timedelta(seconds=1), AgentStatus.OFFLINE),
        (True, 1, NOW - ACTIVITY_WINDOW, AgentStatus.WORKING),  # boundary is inclusive
        (True, 1, NOW, AgentStatus.WORKING),
        (True, 0, NOW - timedelta(minutes=1), AgentStatus.IDLE),
    ],
)
def test_derive_agent_status(
    enabled: bool, active_tasks: int, last_activity: datetime | None, expected: AgentStatus
) -> None:
    assert (
        derive_agent_status(
            enabled=enabled,
            active_task_count=active_tasks,
            last_activity_at=last_activity,
            now=NOW,
        )
        == expected
    )
