"""Exchange a GitHub OAuth code and read the authenticated public profile."""

import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_PROFILE_URL = "https://api.github.com/user"


class GitHubLoginError(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class GitHubIdentity:
    user_id: int
    login: str
    name: str
    avatar_url: str | None


def _read_json(request: Request) -> dict:
    try:
        with build_opener(_NoRedirect()).open(request, timeout=10) as response:
            data = json.loads(response.read(64 * 1024 + 1))
    except HTTPError as error:
        error.close()
        raise GitHubLoginError from None
    except (URLError, OSError, ValueError):
        raise GitHubLoginError from None
    if not isinstance(data, dict):
        raise GitHubLoginError
    return data


def fetch_identity(client_id: str, client_secret: str, callback: str, code: str, verifier: str) -> GitHubIdentity:
    data = _read_json(Request(_TOKEN_URL, data=urlencode({
        "client_id": client_id, "client_secret": client_secret, "redirect_uri": callback,
        "code": code, "code_verifier": verifier,
    }).encode(), headers={"Accept": "application/json"}))
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", access_token):
        raise GitHubLoginError
    data = _read_json(Request(_PROFILE_URL, headers={
        "Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json",
        "User-Agent": "CyberFlow",
    }))
    user_id, login = data.get("id"), data.get("login")
    if type(user_id) is not int or user_id <= 0 or not isinstance(login, str) or not 1 <= len(login) <= 100:
        raise GitHubLoginError
    name = data.get("name")
    name = name.strip()[:120] if isinstance(name, str) else ""
    avatar = data.get("avatar_url")
    try:
        avatar_url = urlsplit(avatar) if isinstance(avatar, str) and len(avatar) <= 2048 else None
    except ValueError:
        avatar_url = None
    if avatar_url is None or avatar_url.scheme != "https" or avatar_url.netloc != "avatars.githubusercontent.com":
        avatar = None
    return GitHubIdentity(user_id, login, name or login, avatar)
