"""HTTP client for yo-server.

Adds:
  • Authorization header from saved config
  • Auto-refresh on 401 (consumes refresh_token, retries once)
  • Sane timeouts and error mapping
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx

from .config import load_config, save_config


class ApiError(Exception):
    def __init__(self, status: int, message: str, body: Any = None):
        self.status = status
        self.message = message
        self.body = body
        super().__init__(f"[{status}] {message}")


def _server_url() -> str:
    return load_config().server_url.rstrip("/")


def _auth_headers() -> dict[str, str]:
    cfg = load_config()
    if cfg.access_token:
        return {"Authorization": f"Bearer {cfg.access_token}"}
    return {}


async def _try_refresh() -> bool:
    """If we have a refresh_token, hit /api/session/refresh and rotate tokens.
    Returns True if refresh succeeded, False otherwise."""
    cfg = load_config()
    if not cfg.refresh_token:
        return False
    try:
        async with httpx.AsyncClient(base_url=_server_url(), timeout=10.0) as c:
            r = await c.post("/api/session/refresh", json={"refreshToken": cfg.refresh_token})
            if not r.is_success:
                return False
            data = r.json()
            access = data.get("accessToken") or data.get("access_token")
            refresh = data.get("refreshToken") or data.get("refresh_token")
            if not access:
                return False
            updates: dict[str, Any] = {"access_token": access}
            if refresh:
                updates["refresh_token"] = refresh
            save_config(updates)
            return True
    except Exception:
        return False


@asynccontextmanager
async def client(timeout: float = 30.0) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=_server_url(),
        headers=_auth_headers(),
        timeout=timeout,
    ) as c:
        yield c


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    url = _server_url() + path
    headers = {**_auth_headers(), **(kwargs.pop("headers", {}) or {})}
    timeout = kwargs.pop("timeout", 30.0)

    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.request(method, url, headers=headers, **kwargs)

        # Auto-refresh once on 401
        if r.status_code == 401 and await _try_refresh():
            headers = {**_auth_headers(), **(kwargs.pop("headers", {}) or {})}
            r = await c.request(method, url, headers=headers, **kwargs)

        return _handle(r)


async def get(path: str, **kwargs: Any) -> Any:
    return await _request("GET", path, **kwargs)


async def post(path: str, json: Any = None, **kwargs: Any) -> Any:
    if json is not None:
        kwargs["json"] = json
    return await _request("POST", path, **kwargs)


async def put(path: str, json: Any = None, **kwargs: Any) -> Any:
    if json is not None:
        kwargs["json"] = json
    return await _request("PUT", path, **kwargs)


async def delete(path: str, **kwargs: Any) -> Any:
    return await _request("DELETE", path, **kwargs)


def _handle(r: httpx.Response) -> Any:
    if r.is_success:
        if not r.content:
            return None
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            return r.json()
        return r.text
    body: Any
    try:
        body = r.json()
    except Exception:
        body = r.text
    msg = body.get("error", body) if isinstance(body, dict) else str(body)
    raise ApiError(r.status_code, str(msg), body)
