"""Login, logout, first-time setup and password change."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...config import settings as env
from ...core import auth, events
from ...db import get_session

router = APIRouter()


def client_id(request: Request) -> str:
    # nginx sets X-Real-IP; direct calls (local debugging) use the socket peer.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def _set_cookie(response: Response, session: Session) -> None:
    response.set_cookie(auth.COOKIE, auth.make_token(session), max_age=env.session_hours * 3600,
                        httponly=True, samesite="lax", path="/")


@router.get("/auth/status")
def status(request: Request, session: Session = Depends(get_session)):
    configured = auth.configured(session)
    return {"configured": configured,
            "authenticated": configured and auth.valid_token(session, request.cookies.get(auth.COOKIE)),
            "managed_by_env": auth.managed_by_env()}


class SetupBody(BaseModel):
    setup_code: str
    password: str


@router.post("/auth/setup")
def setup(body: SetupBody, request: Request, response: Response, session: Session = Depends(get_session)):
    client = client_id(request)
    if auth.configured(session):
        raise HTTPException(400, "A password is already set")
    if wait := auth.locked_out(client):
        raise HTTPException(429, f"Too many attempts. Try again in {wait} seconds.")
    if not auth.check_setup_code(body.setup_code):
        auth.record_failure(client)
        raise HTTPException(403, "That setup code is not right. It is in the app's logs "
                                 "(docker compose logs app) and in data/secrets/setup-code.")
    try:
        auth.set_password(session, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.record_success(client)
    events.log(session, "password_set", actor="user", client=client)
    session.commit()
    _set_cookie(response, session)
    return {"ok": True}


class LoginBody(BaseModel):
    password: str


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response, session: Session = Depends(get_session)):
    client = client_id(request)
    if wait := auth.locked_out(client):
        raise HTTPException(429, f"Too many attempts. Try again in {wait} seconds.")
    if not auth.check_password(session, body.password):
        auth.record_failure(client)
        events.log(session, "login_failed", actor="user", client=client)
        session.commit()
        raise HTTPException(401, "Wrong password")
    auth.record_success(client)
    events.log(session, "login", actor="user", client=client)
    session.commit()
    _set_cookie(response, session)
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


class PasswordBody(BaseModel):
    current: str
    new: str


@router.post("/auth/password")
def change_password(body: PasswordBody, request: Request, response: Response,
                    session: Session = Depends(get_session)):
    if auth.managed_by_env():
        raise HTTPException(400, "The password is set by APP_PASSWORD in the server's .env; change it there")
    if not auth.check_password(session, body.current):
        auth.record_failure(client_id(request))
        raise HTTPException(403, "Current password is wrong")
    try:
        auth.set_password(session, body.new)   # signs out every other session
    except ValueError as e:
        raise HTTPException(400, str(e))
    events.log(session, "password_changed", actor="user", client=client_id(request))
    session.commit()
    _set_cookie(response, session)
    return {"ok": True}
