from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from acc.api.schemas import ApiModel

Step = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Summary = Annotated[str, StringConstraints(max_length=4000)]
FilePath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class HeartbeatIn(ApiModel):
    current_step: Step | None = None


class HeartbeatOut(ApiModel):
    cancel_requested: bool


class CompleteIn(ApiModel):
    result_summary: Summary


class FailIn(ApiModel):
    reason: Summary


class FileChangeIn(ApiModel):
    path: FilePath
    change_type: Literal["added", "modified", "deleted", "renamed"]
    additions: int | None = Field(default=None, ge=0)
    deletions: int | None = Field(default=None, ge=0)


class FileChangesIn(ApiModel):
    changes: list[FileChangeIn] = Field(min_length=1, max_length=500)
