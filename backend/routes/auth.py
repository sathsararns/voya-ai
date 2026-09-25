"""Authentication routes: signup, login, logout, forgot/reset password (via
a secure single-use reset link, not a code), and /me for session hydration.

Sits entirely alongside the existing chat routes — no change to how
routes/chat.py works, and no auth requirement added to it. See the auth
writeup's "limitations" section for why linking authenticated users to
their chat conversations is a deliberate follow-up, not part of this change.

Signup is a single step: the account is created and the caller is signed in
immediately — no email-verification code involved. Password reset uses a
single-use, time-limited link (see services/reset_token_service.py) emailed
to the account's address, instead of a code the user has to type back in.
"""

import os
from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from db.crud import create_user, get_user_by_email, get_user_by_id, update_user_password
from db.models import User
from db.session import SessionLocal
from redis_client import redis_client
from schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SignupRequest,
    UserResponse,
)
from services.email_service import send_password_reset_email
from services.password_service import hash_password, verify_password
from services.rate_limit_service import check_and_increment
from services.reset_token_service import consume_reset_token, issue_reset_token
from services.token_service import (
    ACCESS_TOKEN_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    TokenError,
    access_token_cookie_kwargs,
    blocklist_token,
    create_access_token,
    csrf_token_cookie_kwargs,
    decode_access_token,
    generate_csrf_token,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Secure cookies require HTTPS. Off by default so cookie auth actually works
# over plain http://localhost in local dev — set APP_ENV=production (see
# .env) to turn it on for a real deployment.
COOKIE_SECURE = os.getenv("APP_ENV", "development") == "production"

# Vite auto-increments its port (5173 -> 5174 -> ...) whenever the one it
# wants is already taken, so FRONTEND_ORIGIN (see main.py's CORS setup,
# which reads the same variable) accepts a comma-separated list. The reset
# link only needs one concrete origin to point at — the first entry is
# always the "real" configured frontend, the rest are just local-dev
# fallbacks for whichever port Vite ended up on.
DEFAULT_FRONTEND_ORIGIN = "http://localhost:5173"
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", DEFAULT_FRONTEND_ORIGIN).split(",")[0].strip()

RESET_REQUEST_MAX = 3
RESET_REQUEST_WINDOW_SECONDS = 60 * 60

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency for protected endpoints — reads the JWT from the
    httpOnly cookie (never from a header/body, so the frontend never
    handles the raw token) and resolves it to a live, still-valid User.
    """
    token = request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_access_token(token, redis_client)
    except TokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = get_user_by_id(db, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    # Password changed since this token was issued (e.g. via forgot-password
    # on another device) -> every earlier session dies at once, even though
    # the token itself hasn't technically expired yet.
    issued_pwd_changed_at = payload.get("pwd_changed_at", 0)
    current_pwd_changed_at = int(user.password_changed_at.replace(tzinfo=timezone.utc).timestamp())
    if issued_pwd_changed_at != current_pwd_changed_at:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired, please log in again"
        )

    return user


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Same resolution as get_current_user, but returns None instead of
    raising when there's no valid session, rather than rejecting the
    request outright. Used by routes/chat.py, which serves both logged-in
    callers (whose conversations get scoped to their account) and anonymous
    callers (e.g. a direct API call with no session cookie, or this
    project's own integration tests) — the chat endpoints must keep working
    for both, so they can't use get_current_user's hard 401.
    """
    token = request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        return None

    try:
        payload = decode_access_token(token, redis_client)
    except TokenError:
        return None

    user = get_user_by_id(db, payload["sub"])
    if not user or not user.is_active:
        return None

    issued_pwd_changed_at = payload.get("pwd_changed_at", 0)
    current_pwd_changed_at = int(user.password_changed_at.replace(tzinfo=timezone.utc).timestamp())
    if issued_pwd_changed_at != current_pwd_changed_at:
        return None

    return user


def verify_csrf(request: Request) -> None:
    """Double-submit cookie check for state-changing requests made with an
    existing session (e.g. logout). Signup/login/forgot/reset don't need
    this — none of them rely on an existing authenticated cookie, so there's
    no session for a cross-site request to ride on in the first place; CSRF
    only matters once a session exists.
    """
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get("x-csrf-token")
    if not cookie_token or not header_token or cookie_token != header_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")


def _set_session_cookies(response: Response, user: User) -> None:
    access_token = create_access_token(user.id, user.email, user.password_changed_at)
    csrf_token = generate_csrf_token()
    response.set_cookie(ACCESS_TOKEN_COOKIE_NAME, access_token, **access_token_cookie_kwargs(COOKIE_SECURE))
    response.set_cookie(CSRF_COOKIE_NAME, csrf_token, **csrf_token_cookie_kwargs(COOKIE_SECURE))


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(request: SignupRequest, response: Response, db: Session = Depends(get_db)):
    if get_user_by_email(db, request.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not create account with that email",
        )

    user = create_user(db, email=request.email, name=request.name, password_hash=hash_password(request.password))
    print(f"[auth] signup: created account {user.id} ({request.email})")

    # No email-verification step anymore — the account is usable the moment
    # it's created, so sign the caller in immediately rather than making
    # them submit the login form again right after.
    _set_session_cookies(response, user)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=UserResponse)
def login(request: LoginRequest, response: Response, db: Session = Depends(get_db)):
    rate_limit_key = f"login_attempts:{request.email.lower()}"
    if not check_and_increment(redis_client, rate_limit_key, LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts — try again later"
        )

    user = get_user_by_email(db, request.email)
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    _set_session_cookies(response, user)
    return UserResponse.model_validate(user)


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, response: Response, _: None = Depends(verify_csrf)):
    token = request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if token:
        try:
            payload = decode_access_token(token, redis_client)
            blocklist_token(redis_client, payload)
        except TokenError:
            pass  # already invalid/expired — nothing left to revoke

    _clear_session_cookies(response)
    return MessageResponse(message="Logged out")


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(request: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = get_user_by_email(db, request.email)
    # Same response whether or not the account exists, so this endpoint
    # can't be used to enumerate registered emails.
    generic_response = MessageResponse(message="If that account exists, a reset link has been sent.")

    if not user:
        return generic_response

    rate_limit_key = f"pwd_reset_request:{request.email.lower()}"
    if not check_and_increment(redis_client, rate_limit_key, RESET_REQUEST_MAX, RESET_REQUEST_WINDOW_SECONDS):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many reset requests — please try again later",
        )

    token = issue_reset_token(redis_client, user.id)
    reset_url = f"{FRONTEND_ORIGIN}/reset-password?token={token}"
    send_password_reset_email(user.email, user.name, reset_url)

    return generic_response


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    user_id = consume_reset_token(redis_client, request.token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset link")

    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset link")

    # Also bumps password_changed_at, which fences out every token issued
    # before this moment — see get_current_user's pwd_changed_at check.
    update_user_password(db, user.id, hash_password(request.new_password))

    return MessageResponse(message="Password updated. You can now log in with your new password.")


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)
