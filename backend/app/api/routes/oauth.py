import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app import crud
from app.api.deps import SessionDep
from app.core import security
from app.core.config import settings

router = APIRouter()

_STATE_COOKIE = "__google_oauth_state"


@router.get("/login/google")
def login_google() -> RedirectResponse:
    """Redirect the browser to Google's OAuth consent page."""
    state = secrets.token_urlsafe(16)
    params = urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
        }
    )
    response = RedirectResponse(url=f"{settings.GOOGLE_AUTH_URI}?{params}")
    response.set_cookie(
        key=_STATE_COOKIE,
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/login/google/callback")
def google_callback(
    session: SessionDep,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Google's redirect after the user grants (or denies) access."""
    if error:
        return RedirectResponse(
            url=f"{settings.FRONTEND_HOST}/login?error=oauth_cancelled"
        )

    stored_state = request.cookies.get(_STATE_COOKIE)
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    # Exchange the authorization code for tokens
    token_resp = httpx.post(
        settings.GOOGLE_TOKEN_URI,
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to exchange code with Google")

    access_token: str = token_resp.json()["access_token"]

    # Fetch the user's Google profile
    userinfo_resp = httpx.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if userinfo_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch user info from Google")

    info = userinfo_resp.json()
    email: str | None = info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email address")

    user = crud.get_or_create_oauth_user(
        session=session,
        email=email,
        full_name=info.get("name"),
    )

    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    jwt = security.create_access_token(
        subject=str(user.id),
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    response = RedirectResponse(
        url=f"{settings.FRONTEND_HOST}/auth/callback#token={jwt}"
    )
    response.delete_cookie(_STATE_COOKIE)
    return response
