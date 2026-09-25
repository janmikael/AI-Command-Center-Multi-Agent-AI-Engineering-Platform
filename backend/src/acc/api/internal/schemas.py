from typing import Annotated

from pydantic import StringConstraints

from acc.api.schemas import ApiModel

Step = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Summary = Annotated[str, StringConstraints(max_length=4000)]


class HeartbeatIn(ApiModel):
    current_step: Step | None = None


class HeartbeatOut(ApiModel):
    cancel_requested: bool


class CompleteIn(ApiModel):
    result_summary: Summary


class FailIn(ApiModel):
    reason: Summary
