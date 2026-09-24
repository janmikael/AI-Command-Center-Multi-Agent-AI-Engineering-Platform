import asyncio

from fastapi import APIRouter, Request, Response

from acc import __version__
from acc.api.deps import RedisDep, SessionFactoryDep
from acc.api.schemas import CheckOut, HealthOut, ReadinessOut
from acc.queries import health as health_queries

router = APIRouter(tags=["health"])

_CHECK_TIMEOUT_S = 2.0


@router.get("/healthz")
async def healthz() -> HealthOut:
    """Liveness: the process is up. Deliberately checks no dependencies."""
    return HealthOut(status="ok")


@router.get("/readyz", responses={503: {"model": ReadinessOut}})
async def readyz(
    request: Request, response: Response, sf: SessionFactoryDep, redis: RedisDep
) -> ReadinessOut:
    """Readiness: database reachable and fully migrated, Redis reachable."""
    checks: dict[str, CheckOut] = {}
    expected_head: str | None = request.app.state.alembic_head

    try:
        revision = await asyncio.wait_for(health_queries.database_revision(sf), _CHECK_TIMEOUT_S)
        checks["database"] = CheckOut(ok=True)
        checks["migrations"] = CheckOut(
            ok=revision == expected_head, detail=f"database={revision} expected={expected_head}"
        )
    except Exception as exc:
        checks["database"] = CheckOut(ok=False, detail=type(exc).__name__)
        checks["migrations"] = CheckOut(ok=False, detail="database unavailable")

    try:
        await asyncio.wait_for(redis.ping(), _CHECK_TIMEOUT_S)
        checks["redis"] = CheckOut(ok=True)
    except Exception as exc:
        checks["redis"] = CheckOut(ok=False, detail=type(exc).__name__)

    ready = all(check.ok for check in checks.values())
    response.status_code = 200 if ready else 503
    return ReadinessOut(
        status="ready" if ready else "not_ready", version=__version__, checks=checks
    )
