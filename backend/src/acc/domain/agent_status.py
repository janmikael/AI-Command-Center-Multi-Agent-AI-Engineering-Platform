"""Agent status is derived, never stored.

Phase 1 inputs: enabled flag, number of active tasks assigned to the agent, and the time
of the agent's latest event. Phase 2 swaps the inputs for runs + heartbeats; the function
signature and the UI stay the same.
"""

from datetime import datetime, timedelta
from enum import StrEnum

ACTIVITY_WINDOW = timedelta(minutes=10)


class AgentStatus(StrEnum):
    WORKING = "working"
    IDLE = "idle"
    OFFLINE = "offline"
    DISABLED = "disabled"


def derive_agent_status(
    *,
    enabled: bool,
    active_task_count: int,
    last_activity_at: datetime | None,
    now: datetime,
    window: timedelta = ACTIVITY_WINDOW,
) -> AgentStatus:
    if not enabled:
        return AgentStatus.DISABLED
    recently_active = last_activity_at is not None and now - last_activity_at <= window
    if not recently_active:
        return AgentStatus.OFFLINE
    return AgentStatus.WORKING if active_task_count > 0 else AgentStatus.IDLE
