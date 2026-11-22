"""GitHub sign-in and server-side sessions for browser requests."""

from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated
from urllib.parse import unquote, urlencode, urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Cookie, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import StringConstraints
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ..db.encryption import check_storage_key, require_cipher
from ..db.models import OAuthAttempt, User, UserSession
from ..db.session import Database
from ..db.workspaces import provision_personal_workspace
from ..integrations.github_oauth import AUTHORIZE_URL, GitHubLoginError, fetch_identity
from ..schemas.contracts import Input, Name, ReadModel
from .common import ApiError

router = APIRouter(tags=["account"])
_SESSION = "cyberflow_session"
_BROWSER = "cyberflow_oauth"
_ATTEMPT_SECONDS = 600
_SESSION_SECONDS = 7 * 24 * 60 * 60


class ProfileRead(ReadModel):
    id: UUID
    github_login: str
    display_name: str
    avatar_url: str | None
    timezone: str


class ProfileUpdate(Input):
    display_name: Name
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=128)]


def token_hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def app_origin(request: Request) -> str:
    try:
        url = urlsplit(request.app.state.settings.app_base_url)
        secure = url.scheme == "https" or url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
        if secure and url.hostname and url.username is None and url.path in {"", "/"} and not url.query and not url.fragment:
            # Browsers omit default ports from Origin.
            port = url.port
            host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
            suffix = f":{port}" if port and (url.scheme, port) not in {("https", 443), ("http", 80)} else ""
            return f"{url.scheme}://{host}{suffix}"
    except ValueError:
        pass
    raise ApiError(503, "INVALID_APP_ORIGIN", "Укажите публичный адрес приложения в APP_BASE_URL.")


def _require_origin(request: Request) -> None:
    if request.headers.get("origin") != app_origin(request):
        raise ApiError(403, "INVALID_REQUEST_ORIGIN", "Запрос должен быть отправлен со страницы CyberFlow.")


def _session_hash(session: Annotated[str | None, Cookie(alias=_SESSION)] = None) -> str:
    if session is None or len(session) != 43:
        raise ApiError(401, "AUTH_REQUIRED", "Войдите через GitHub.")
    return token_hash(session)


SessionHash = Annotated[str, Depends(_session_hash)]


def session_user(db: Database, session_hash: str) -> User | None:
    return db.scalar(
        select(User).join(UserSession, User.id == UserSession.user_id)
        .where(UserSession.token_hash == session_hash, UserSession.expires_at > datetime.now(UTC))
    )


def _current_user(request: Request, db: Database, session_hash: SessionHash) -> User:
    user = session_user(db, session_hash)
    if user is None:
        raise ApiError(401, "AUTH_REQUIRED", "Войдите через GitHub.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        _require_origin(request)
    return user


CurrentUser = Annotated[User, Depends(_current_user)]


def _return_path(value: str) -> str:
    decoded = unquote(value)
    if (
        value.startswith("/") and not decoded.startswith("//") and "\\" not in decoded
        and not any(ord(char) < 32 or ord(char) == 127 for char in decoded)
    ):
        return value
    return "/"


def _cookie(response: Response, name: str, value: str, *, origin: str, lifetime: int) -> None:
    response.set_cookie(name, value, max_age=lifetime, path="/", httponly=True, secure=origin.startswith("https:"), samesite="lax")


def _clear_cookie(response: Response, name: str, origin: str) -> None:
    response.delete_cookie(name, path="/", httponly=True, secure=origin.startswith("https:"), samesite="lax")


def _github_config(request: Request, db: Database) -> tuple[str, str, str]:
    settings = request.app.state.settings
    secret = settings.github_client_secret.get_secret_value() if settings.github_client_secret else ""
    if not settings.github_client_id or not secret or not settings.data_encryption_key:
        raise ApiError(503, "GITHUB_LOGIN_NOT_CONFIGURED", "Настройте вход через GitHub и ключ шифрования на сервере.")
    try:
        check_storage_key(db.connection())
    except RuntimeError:
        raise ApiError(503, "STORAGE_KEY_MISMATCH", "Проверьте ключ шифрования базы CyberFlow.") from None
    return settings.github_client_id, secret, app_origin(request)


@router.get("/auth/github/start", response_class=RedirectResponse)
def github_start(request: Request, db: Database, next: Annotated[str, Query(max_length=2048)] = "/") -> RedirectResponse:
    client_id, _, origin = _github_config(request, db)
    state, browser, verifier = token_urlsafe(32), token_urlsafe(32), token_urlsafe(64)
    now = datetime.now(UTC)
    db.execute(delete(OAuthAttempt).where(OAuthAttempt.expires_at <= now))
    db.add(OAuthAttempt(
        state_hash=token_hash(state), browser_hash=token_hash(browser), verifier=verifier,
        return_path=_return_path(next), expires_at=now + timedelta(seconds=_ATTEMPT_SECONDS),
    ))
    db.commit()
    challenge = urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode("ascii")
    response = RedirectResponse(AUTHORIZE_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": origin + "/api/auth/github/callback",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    }), status_code=303)
    _cookie(response, _BROWSER, browser, origin=origin, lifetime=_ATTEMPT_SECONDS)
    return response


@router.get("/auth/github/callback", response_class=RedirectResponse)
def github_callback(
    request: Request, db: Database,
    state: Annotated[str | None, Query(max_length=128)] = None,
    code: Annotated[str | None, Query(max_length=512)] = None,
    error: Annotated[str | None, Query(max_length=128)] = None,
    browser: Annotated[str | None, Cookie(alias=_BROWSER)] = None,
    old_session: Annotated[str | None, Cookie(alias=_SESSION)] = None,
) -> RedirectResponse:
    client_id, secret, origin = _github_config(request, db)
    if state is None or browser is None or len(state) != 43 or len(browser) != 43:
        return _failed_login(origin, "expired")
    now = datetime.now(UTC)
    attempt = db.execute(
        delete(OAuthAttempt).where(
            OAuthAttempt.state_hash == token_hash(state), OAuthAttempt.browser_hash == token_hash(browser),
            OAuthAttempt.expires_at > now,
        ).returning(OAuthAttempt.verifier, OAuthAttempt.return_path)
    ).one_or_none()
    db.commit()
    if attempt is None:
        return _failed_login(origin, "expired")
    if error or not code:
        return _failed_login(origin, "cancelled")
    try:
        identity = fetch_identity(client_id, secret, origin + "/api/auth/github/callback", code, attempt.verifier)
    except GitHubLoginError:
        return _failed_login(origin, "github")

    cipher = require_cipher(db.get_bind().dialect)
    statement = insert(User).values(
        github_key=cipher.digest("github-user", str(identity.user_id)),
        github_login=identity.login, display_name=identity.name, avatar_url=identity.avatar_url, timezone="UTC",
    )
    user_id = db.scalar(statement.on_conflict_do_update(
        index_elements=[User.github_key],
        set_={"github_login": statement.excluded.github_login, "avatar_url": statement.excluded.avatar_url},
    ).returning(User.id))
    provision_personal_workspace(db, user_id)
    db.execute(delete(UserSession).where(UserSession.expires_at <= now))
    if old_session and len(old_session) == 43:
        db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(old_session)))
    session = token_urlsafe(32)
    db.add(UserSession(token_hash=token_hash(session), user_id=user_id, expires_at=now + timedelta(seconds=_SESSION_SECONDS)))
    db.commit()
    response = RedirectResponse(origin + attempt.return_path, status_code=303)
    _clear_cookie(response, _BROWSER, origin)
    _cookie(response, _SESSION, session, origin=origin, lifetime=_SESSION_SECONDS)
    return response


def _failed_login(origin: str, reason: str) -> RedirectResponse:
    response = RedirectResponse(f"{origin}/?auth_error={reason}", status_code=303)
    _clear_cookie(response, _BROWSER, origin)
    return response


@router.get("/me", response_model=ProfileRead)
def profile(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=ProfileRead)
def update_profile(data: ProfileUpdate, user: CurrentUser, db: Database) -> User:
    try:
        ZoneInfo(data.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(422, "INVALID_TIMEZONE", "Укажите часовой пояс IANA.", field="timezone") from None
    user.display_name = data.display_name
    user.timezone = data.timezone
    db.commit()
    return user


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request, db: Database, user: CurrentUser, session_hash: SessionHash,
) -> Response:
    db.execute(delete(UserSession).where(UserSession.token_hash == session_hash, UserSession.user_id == user.id))
    db.commit()
    response = Response(status_code=204)
    _clear_cookie(response, _SESSION, app_origin(request))
    return response
