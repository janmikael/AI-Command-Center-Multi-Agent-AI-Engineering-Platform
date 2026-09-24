import asyncio

from fastapi import APIRouter, Request

from acc.api.deps import OperatorDep, RedisDep, SessionFactoryDep
from acc.api.schemas import OverviewOut, SystemOut
from acc.ids import utcnow
from acc.queries import overview as overview_queries

router = APIRouter(prefix="/overview", tags=["overview"])


@router.get("")
async def overview(
    request: Request, _: OperatorDep, sf: SessionFactoryDep, redis: RedisDep
) -> OverviewOut:
    """Everything the Overview page needs, in one round trip."""
    data = await overview_queries.get_overview(sf, now=utcnow())
    try:
        redis_ok = bool(await asyncio.wait_for(redis.ping(), 1.0))
    except Exception:
        redis_ok = False
    hub = getattr(request.app.state, "hub", None)
    system = SystemOut(
        database=True,  # the queries above just succeeded
        redis=redis_ok,
        realtime_clients=hub.client_count if hub is not None else 0,
    )
    return OverviewOut.model_validate({**data.__dict__, "system": system})
