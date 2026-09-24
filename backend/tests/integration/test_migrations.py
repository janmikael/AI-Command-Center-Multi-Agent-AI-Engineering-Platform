import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from acc.db.base import Base
from acc.db.migrations import alembic_head, include_name
from tests.conftest import BACKEND_DIR, DbUrls

pytestmark = pytest.mark.integration


def test_upgrade_downgrade_roundtrip(migrated: Config) -> None:
    """Every migration can be reverted, and re-applied on top of its own downgrade."""
    command.downgrade(migrated, "base")
    command.upgrade(migrated, "head")


async def test_models_match_migrations(migrated: Config, test_urls: DbUrls) -> None:
    """Drift check: the ORM models and the migrated schema must be identical."""
    import acc.db.models  # noqa: F401

    def diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "include_name": include_name}
        )
        return list(compare_metadata(context, Base.metadata))

    engine = create_async_engine(test_urls.owner_db)
    async with engine.connect() as conn:
        differences = await conn.run_sync(diff)
    await engine.dispose()
    assert differences == [], f"Models and migrations disagree: {differences}"


@pytest.mark.parametrize(
    ("name", "type_", "parent", "included"),
    [
        ("events", "table", {}, True),
        ("events_y2026m09", "table", {}, False),
        ("events_default", "table", {}, False),
        ("events_y2026m09_seq_idx", "index", {"table_name": "events_y2026m09"}, False),
        ("ix_events_seq", "index", {"table_name": "events"}, True),
        ("events_archive", "table", {}, True),  # only the exact partition pattern is hidden
    ],
)
def test_autogenerate_never_sees_partitions(
    name: str, type_: str, parent: dict[str, str | None], included: bool
) -> None:
    assert include_name(name, type_, parent) is included


def test_single_head() -> None:
    assert alembic_head(str(BACKEND_DIR / "alembic.ini")) is not None
