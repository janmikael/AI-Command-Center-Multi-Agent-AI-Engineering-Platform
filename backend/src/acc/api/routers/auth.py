from fastapi import APIRouter, Request, Response

from acc.api.deps import (
    SESSION_COOKIE,
    OperatorDep,
    RedisDep,
    SessionFactoryDep,
    SettingsDep,
)
from acc.api.schemas import ChangePasswordIn, LoginIn, OperatorOut
from acc.commands import auth as auth_commands
from acc.config import Settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict"
    )


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    sf: SessionFactoryDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> OperatorOut:
    result = await auth_commands.login(
        sf,
        redis,
        settings,
        email=str(body.email),
        password=body.password,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    response.set_cookie(
        SESSION_COOKIE,
        result.token,
        max_age=settings.session_absolute_days * 86_400,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return OperatorOut.from_domain(result.operator)


@router.post("/logout", status_code=204)
async def logout(
    request: Request, _: OperatorDep, sf: SessionFactoryDep, settings: SettingsDep
) -> Response:
    await auth_commands.logout(sf, session_id=request.state.session_id)
    response = Response(status_code=204)
    _clear_cookie(response, settings)
    return response


@router.get("/me")
async def me(operator: OperatorDep) -> OperatorOut:
    return OperatorOut.from_domain(operator)


@router.post("/change-password", status_code=204)
async def change_password(
    body: ChangePasswordIn, request: Request, operator: OperatorDep, sf: SessionFactoryDep
) -> Response:
    await auth_commands.change_password(
        sf,
        user_id=operator.id,
        current_session_id=request.state.session_id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return Response(status_code=204)
