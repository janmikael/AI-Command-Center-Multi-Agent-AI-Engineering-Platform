from sqlalchemy import text

from acc.db.session import SessionFactory, transaction


async def ensure_event_partitions(sf: SessionFactory, months_ahead: int = 3) -> int:
    """Create missing monthly partitions of `events` (via the SECURITY DEFINER function).

    Returns the number of partitions created.
    """
    async with transaction(sf) as session:
        created = await session.scalar(
            text("SELECT acc_ensure_event_partitions(:n)"), {"n": months_ahead}
        )
    return int(created or 0)
