"""Per-run token authentication for the internal API (PLAN-PHASE-2-M2.md §2.4-2.5)."""

import time
import uuid
from typing import Annotated

from fastapi import Depends, Header, Path

from acc.api.deps import SettingsDep
from acc.domain.errors import AuthenticationFailed, PermissionDenied
from acc.security import RunTokenClaims, verify_run_token


async def current_run(
    run_id: Annotated[uuid.UUID, Path()],
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> RunTokenClaims:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthenticationFailed("Missing or malformed Authorization header")
    token = authorization.removeprefix("Bearer ")
    claims = verify_run_token(
        token, settings.runner_token_secret.get_secret_value(), now=int(time.time())
    )
    if claims is None:
        raise AuthenticationFailed("Invalid or expired run token")
    if claims.run_id != run_id:
        # A well-formed, currently-valid token — just not for this run. 403, not 401:
        # the credential itself is genuine, it just doesn't authorize this resource.
        raise PermissionDenied("Run token does not authorize this run")
    return claims


RunClaimsDep = Annotated[RunTokenClaims, Depends(current_run)]
