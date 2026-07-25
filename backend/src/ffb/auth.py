"""Lightweight identity: a Sleeper username lookup plus a signed session cookie."""

import os
import re

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from itsdangerous import BadData, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import select

from ffb.db import Session
from ffb.models import User
from ffb.sleeper_client import SleeperClient

COOKIE_NAME = "ffb_session"
MAX_AGE_SECONDS = 60 * 60 * 24 * 30

SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-insecure-secret")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax").lower()

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")

serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="ffb-session")
router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str


def _user_payload(user: User) -> dict:
    return {
        "sleeper_user_id": user.sleeper_user_id,
        "sleeper_username": user.sleeper_username,
        "display_name": user.display_name,
        "avatar": user.avatar,
    }


def _set_cookie(response: Response, sleeper_user_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        serializer.dumps(sleeper_user_id),
        max_age=MAX_AGE_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def current_user_id(request: Request) -> str | None:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        return serializer.loads(raw, max_age=MAX_AGE_SECONDS)
    except BadData:
        return None


@router.post("/login")
def login(body: LoginRequest, response: Response) -> dict:
    username = body.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "That doesn't look like a Sleeper username")

    try:
        with SleeperClient() as client:
            info = client.get_user(username)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(404, "No Sleeper user with that username")
        raise HTTPException(502, "Couldn't reach Sleeper right now")
    except httpx.HTTPError:
        raise HTTPException(502, "Couldn't reach Sleeper right now")

    if not info or not info.get("user_id"):
        raise HTTPException(404, "No Sleeper user with that username")

    with Session() as session:
        user = session.scalar(
            select(User).where(User.sleeper_user_id == info["user_id"])
        )
        if user is None:
            user = User(sleeper_user_id=info["user_id"], sleeper_username=username)
            session.add(user)
        user.sleeper_username = info.get("username") or username
        user.display_name = info.get("display_name")
        user.avatar = info.get("avatar")
        session.commit()
        payload = _user_payload(user)

    _set_cookie(response, payload["sleeper_user_id"])
    return payload


@router.get("/me")
def me(request: Request) -> dict:
    user_id = current_user_id(request)
    if user_id is None:
        raise HTTPException(401, "Not signed in")

    with Session() as session:
        user = session.scalar(select(User).where(User.sleeper_user_id == user_id))
        if user is None:
            raise HTTPException(401, "Not signed in")
        return _user_payload(user)


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )
    return {"ok": True}
