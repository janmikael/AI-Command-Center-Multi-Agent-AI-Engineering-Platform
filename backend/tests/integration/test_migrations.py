import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from acc.db.base import Base
from acc.db.migrations import alembic_head
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
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return list(compare_metadata(context, Base.metadata))

    engine = create_async_engine(test_urls.owner_db)
    async with engine.connect() as conn:
        differences = await conn.run_sync(diff)
    await engine.dispose()
    assert differences == [], f"Models and migrations disagree: {differences}"


def test_single_head() -> None:
    assert alembic_head(str(BACKEND_DIR / "alembic.ini")) is not None
