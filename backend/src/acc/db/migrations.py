from alembic.config import Config
from alembic.script import ScriptDirectory


def alembic_head(alembic_ini: str) -> str | None:
    """The newest migration revision shipped with this build."""
    return ScriptDirectory.from_config(Config(alembic_ini)).get_current_head()
