"""Signed HTTP calls with automatic pagination."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from . import endpoints, guard
from .config import Profile
from .signer import signed_headers

TIMEOUT = httpx.Timeout(30.0)
MAX_PAGES = 50
MAX_ERROR_BODY = 2000

# Services outside the shared /v2 gateway that still default to XML.
_JSON_PARAM_SERVICES = frozenset({"billing"})


class CallError(Exception):
    """Error safe to show to the model (never contains credentials)."""


def flatten_params(params: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Ncloud query style: lists -> name.1, name.2; dicts -> name.key."""
    out: list[tuple[str, str]] = []

    def walk(prefix: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}.{k}", v)
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value, start=1):
                walk(f"{prefix}.{i}", v)
        elif isinstance(value, bool):
            out.append((prefix, "true" if value else "false"))
        else:
            out.append((prefix, str(value)))

    for key, val in (params or {}).items():
        walk(key, val)
    return out


def _build_path(base: str, action: str) -> str:
    action = action.strip()
    if not action:
        raise CallError("action is empty")
    if "://" in action or action.startswith("//"):
        raise CallError("action must be a path, not a URL")
    if not action.startswith("/"):
        action = "/" + action
    if ".." in action:
        raise CallError("action must not contain '..'")
    return base.rstrip("/") + action


def _find_page(data: Any) -> tuple[dict, str, int] | None:
    """Locate {totalRows, <x>List} inside a shared-gateway response."""
    if not isinstance(data, dict):
        return None
    candidates = [data] + [v for v in data.values() if isinstance(v, dict)]
    for node in candidates:
        total = node.get("totalRows")
        if not isinstance(total, int):
            continue
        lists = [k for k, v in node.items() if isinstance(v, list)]
        if len(lists) == 1:
            return node, lists[0], total
    return None


async def _send(
    http: httpx.AsyncClient,
    profile: Profile,
    host: str,
    method: str,
    path: str,
    query: list[tuple[str, str]],
    body: Any,
) -> tuple[int, Any]:
    qs = urlencode(query, quote_via=quote, safe="")
    uri = path + ("?" + qs if qs else "")
    headers = signed_headers(method, uri, profile.access_key, profile.secret_key)
    content = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        resp = await http.request(method, f"https://{host}{uri}", headers=headers, content=content)
    except httpx.HTTPError as exc:
        raise CallError(f"network error: {type(exc).__name__}: {exc}") from None
    try:
        payload: Any = resp.json()
    except ValueError:
        payload = resp.text[:MAX_ERROR_BODY]
    return resp.status_code, payload


async def call(
    http: httpx.AsyncClient,
    profile: Profile,
    service: str,
    action: str,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    body: Any = None,
    paginate: bool = True,
) -> dict[str, Any]:
    method = method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise CallError(f"unsupported method {method}")

    try:
        host, base = endpoints.resolve(profile.zone, service)
    except ValueError as exc:
        raise CallError(str(exc)) from None
    if not endpoints.host_allowed(profile.zone, host):
        raise CallError(f"host {host} is outside the {profile.zone} API gateway")

    path = _build_path(base, action)
    read_only = guard.is_read_only(method, path)
    if not read_only and not guard.write_allowed():
        raise CallError(
            f"blocked: {method} {path} is classified as a write operation. "
            "Writes are disabled unless the server runs with NCP_ALLOW_WRITE=true."
        )

    params = dict(params or {})
    shared = service.strip().lower() not in endpoints.SPECIAL
    if (shared or service in _JSON_PARAM_SERVICES) and "responseFormatType" not in params:
        params["responseFormatType"] = "json"

    status, data = await _send(http, profile, host, method, path, flatten_params(params), body)
    result: dict[str, Any] = {
        "profile": profile.name,
        "zone": profile.zone,
        "request": f"{method} https://{host}{path}",
        "status": status,
        "read_only": read_only,
    }
    if status >= 400:
        result["error"] = data
        return result

    pages = 1
    page = _find_page(data) if (paginate and read_only and "pageNo" not in params) else None
    if page:
        node, list_key, total = page
        items = node[list_key]
        page_size = int(params.get("pageSize") or len(items) or 0)
        page_no = 1
        while page_size and len(items) < total and pages < MAX_PAGES:
            page_no += 1
            q = dict(params, pageNo=page_no, pageSize=page_size)
            st, more = await _send(http, profile, host, method, path, flatten_params(q), body)
            if st >= 400:
                result["pagination_error"] = {"page": page_no, "status": st, "error": more}
                break
            nxt = _find_page(more)
            if not nxt or not nxt[0][nxt[1]]:
                break
            items.extend(nxt[0][nxt[1]])
            pages += 1
        if len(items) < total:
            result["truncated"] = {"returned": len(items), "totalRows": total}
    result["pages"] = pages
    result["data"] = data
    return result
