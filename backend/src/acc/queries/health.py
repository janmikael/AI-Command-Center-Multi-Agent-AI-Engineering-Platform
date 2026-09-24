from sqlalchemy import text

from acc.db.session import SessionFactory


async def database_revision(sf: SessionFactory) -> str | None:
    """Current Alembic revision of the connected database (None if never migrated)."""
    async with sf() as session:
        exists = await session.scalar(text("SELECT to_regclass('public.alembic_version')"))
        if exists is None:
            return None
        revision: str | None = await session.scalar(text("SELECT version_num FROM alembic_version"))
        return revision
