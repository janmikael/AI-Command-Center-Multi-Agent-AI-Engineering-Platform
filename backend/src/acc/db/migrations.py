import re
from collections.abc import MutableMapping
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory

# Monthly partitions of `events` (and the default partition) are created at runtime by
# acc_ensure_event_partitions(), not by the ORM. Autogenerate must never see them,
# otherwise it would propose dropping them.
_EVENT_PARTITION = re.compile(r"^events_(default|y\d{4}m\d{2})$")


def alembic_head(alembic_ini: str) -> str | None:
    """The newest migration revision shipped with this build."""
    return ScriptDirectory.from_config(Config(alembic_ini)).get_current_head()


def include_name(
    name: str | None, type_: str, parent_names: MutableMapping[Any, str | None]
) -> bool:
    """Alembic `include_name` hook: hide runtime-managed partitions and their indexes."""
    if type_ == "table" and name and _EVENT_PARTITION.match(name):
        return False
    table = parent_names.get("table_name")
    return not (type_ in {"index", "unique_constraint"} and table and _EVENT_PARTITION.match(table))
