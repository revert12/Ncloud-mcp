"""Signed HTTP calls: allowlist, read-only guard, retries, concurrency limit, paging.

Paging rules (verified against getServerInstanceList docs and live calls):
  * pageNo 0 and 1 both return the first page; pageSize is required with pageNo.
  * Without pageNo the API returns the whole list and totalRows is the full count.
  * With pageNo, totalRows is the number of items on THAT page, not the total.
So: call once without pageNo; if fewer items than totalRows came back, fetch again
page by page (pageNo=1..N) until a short/empty page, and report whether the result
is complete. REST services that answer {items, hasNext, page} (Sub Account v1, page
is 0-based) are followed with page=1..N while hasNext is true.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from . import endpoints, guard
from .config import Profile
from .signer import signed_headers


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except ValueError:
        return default


TIMEOUT = httpx.Timeout(float(_env_int("NCP_MCP_TIMEOUT", 30, 5, 120)))
RETRIES = _env_int("NCP_MCP_RETRIES", 2, 0, 5)           # extra attempts for read-only calls
MAX_CONCURRENCY = _env_int("NCP_MCP_CONCURRENCY", 5, 1, 20)  # in-flight requests per process
MAX_PAGES = 50
MAX_PAGE_SIZE = 1000
MAX_ERROR_BODY = 2000
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_BACKOFF = 10.0

_sem: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(MAX_CONCURRENCY)
    return _sem


async def _sleep(seconds: float) -> None:  # patched in tests
    await asyncio.sleep(seconds)


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
        elif isinstance(value, (str, int, float)):
            out.append((prefix, str(value)))
        else:
            raise CallError(f"parameter '{prefix}' has unsupported type {type(value).__name__}")

    for key, val in (params or {}).items():
        if not isinstance(key, str) or not key:
            raise CallError("parameter names must be non-empty strings")
        walk(key, val)
    return out


def _build_path(base: str, action: str) -> str:
    if not isinstance(action, str) or not action.strip():
        raise CallError("action is empty")
    action = action.strip()
    if "://" in action or action.startswith("//"):
        raise CallError("action must be a path, not a URL")
    if ".." in action:
        raise CallError("action must not contain '..'")
    if not guard.valid_action(action):
        raise CallError("action contains invalid characters")
    if not action.startswith("/"):
        action = "/" + action
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


def _find_rest_page(data: Any) -> bool:
    """Sub Account v1 style: {items: [...], hasNext: bool, page: int}."""
    return (
        isinstance(data, dict)
        and isinstance(data.get("items"), list)
        and isinstance(data.get("hasNext"), bool)
    )


def _pick_field(item: Any, path: str) -> Any:
    cur = item
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def project(items: list, fields: list[str]) -> list:
    """Keep only ``fields`` (dot paths allowed, e.g. 'serverInstanceStatus.code')."""
    return [
        {f: _pick_field(it, f) for f in fields} if isinstance(it, dict) else it for it in items
    ]


def _retry_after(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        ra = resp.headers.get("Retry-After", "")
        if ra.isdigit():
            return min(float(ra), MAX_BACKOFF)
    return min(0.5 * (2 ** attempt) + random.uniform(0, 0.3), MAX_BACKOFF)


async def _send(
    http: httpx.AsyncClient,
    profile: Profile,
    host: str,
    method: str,
    path: str,
    query: list[tuple[str, str]],
    body: Any,
    retry: bool = False,
) -> tuple[int, Any, int]:
    """Send one signed request. Returns (status, payload, attempts)."""
    qs = urlencode(query, quote_via=quote, safe="")
    uri = path + ("?" + qs if qs else "")
    content = None
    if body is not None:
        content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    attempts = 0
    max_attempts = 1 + (RETRIES if retry else 0)
    while True:
        attempts += 1
        # Re-sign every attempt: the signature embeds a timestamp valid for 5 minutes.
        headers = signed_headers(method, uri, profile.access_key, profile.secret_key)
        if content is not None:
            headers["Content-Type"] = "application/json"
        resp: httpx.Response | None = None
        try:
            async with _semaphore():
                resp = await http.request(
                    method, f"https://{host}{uri}", headers=headers, content=content
                )
        except httpx.TimeoutException:
            if attempts < max_attempts:
                await _sleep(_retry_after(None, attempts))
                continue
            if retry:
                raise CallError(f"timeout after {attempts} attempt(s)") from None
            raise CallError(
                "timeout: the request may or may not have been applied by NCP. "
                "Check the resource state before sending it again."
            ) from None
        except httpx.HTTPError as exc:
            if retry and attempts < max_attempts and isinstance(exc, httpx.TransportError):
                await _sleep(_retry_after(None, attempts))
                continue
            raise CallError(f"network error: {type(exc).__name__}") from None

        if resp.status_code in RETRY_STATUS and attempts < max_attempts:
            await _sleep(_retry_after(resp, attempts))
            continue
        try:
            payload: Any = resp.json()
        except ValueError:
            payload = resp.text[:MAX_ERROR_BODY]
        if resp.status_code >= 400 and isinstance(payload, str):
            payload = payload[:MAX_ERROR_BODY]
        return resp.status_code, payload, attempts


def _check_inputs(params: Any, fields: Any, body: Any) -> None:
    if params is not None and not isinstance(params, dict):
        raise CallError("params must be an object (JSON dict)")
    if fields is not None and (
        not isinstance(fields, list) or not all(isinstance(f, str) and f for f in fields)
    ):
        raise CallError("fields must be a list of non-empty strings")
    if body is not None and not isinstance(body, (dict, list)):
        raise CallError("body must be a JSON object or array")


async def call(
    http: httpx.AsyncClient,
    profile: Profile,
    service: str,
    action: str,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    body: Any = None,
    paginate: bool = True,
    fields: list[str] | None = None,
    mode: str = "read",
) -> dict[str, Any]:
    if not isinstance(service, str) or not isinstance(method, str):
        raise CallError("service and method must be strings")
    _check_inputs(params, fields, body)
    method = method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise CallError(f"unsupported method {method}")

    try:
        host, base = endpoints.resolve(profile.zone, service)
    except endpoints.NotAllowed as exc:
        raise CallError(str(exc)) from None
    if not endpoints.host_allowed(profile.zone, host):
        raise CallError(f"host {host} is outside the {profile.zone} API gateway")
    service = service.strip().lower()

    path = _build_path(base, action)
    read_only = guard.is_read_only(method, path)
    if mode == "read" and not read_only:
        raise CallError(
            f"blocked: {method} {path} is classified as a write operation. "
            "ncp_call/ncp_batch are read-only; use ncp_write (available only when the "
            "server runs with NCP_ALLOW_WRITE=true)."
        )
    if mode == "write":
        if read_only:
            raise CallError(f"{method} {path} is read-only; use ncp_call instead.")
        if not guard.write_allowed():
            raise CallError("writes are disabled (start the server with NCP_ALLOW_WRITE=true).")
        if not guard.write_permitted(service, action):
            raise CallError(
                f"blocked: '{service}:{action.strip()}' is not in NCP_WRITE_ALLOWLIST. "
                "Each write action must be allowed explicitly."
            )

    params = dict(params or {})
    if (endpoints.is_shared(profile.zone, service) or service in endpoints.JSON_PARAM_SERVICES) \
            and "responseFormatType" not in params:
        params["responseFormatType"] = "json"

    status, data, attempts = await _send(
        http, profile, host, method, path, flatten_params(params), body, retry=read_only
    )
    result: dict[str, Any] = {
        "profile": profile.name,
        "zone": profile.zone,
        "request": f"{method} https://{host}{path}",
        "status": status,
        "read_only": read_only,
    }
    if attempts > 1:
        result["attempts"] = attempts
    if status >= 400:
        result["error"] = data
        return result

    paging: dict[str, Any] | None = None
    if read_only:
        paging = await _paginate(http, profile, host, method, path, params, data, paginate)
    if paging is not None:
        data = paging.pop("_data")
        result["paging"] = paging
        if not paging["complete"]:
            result["truncated"] = {k: paging.get(k) for k in ("returned", "total", "reason")}
    result["pages"] = paging["pages"] if paging else 1

    if fields:
        target = _find_page(data)
        if target:
            node, list_key, _ = target
            node[list_key] = project(node[list_key], fields)
        elif _find_rest_page(data):
            data["items"] = project(data["items"], fields)
        else:
            result["fields_note"] = "no list found in response; fields ignored"
    result["data"] = data
    return result


async def _paginate(
    http: httpx.AsyncClient,
    profile: Profile,
    host: str,
    method: str,
    path: str,
    params: dict[str, Any],
    data: Any,
    paginate: bool,
) -> dict[str, Any] | None:
    """Return paging info (+ '_data' with merged items) or None when not a list response."""
    page = _find_page(data)
    if page:
        node, list_key, total = page
        got = len(node[list_key])
        if "pageNo" in params:
            return {"_data": data, "auto": False, "complete": False, "pages": 1,
                    "returned": got, "total": None,
                    "reason": "pageNo was given: this is one page only and totalRows counts "
                              "only the items on this page"}
        if got >= total:
            return {"_data": data, "auto": False, "complete": True, "pages": 1,
                    "returned": got, "total": total}
        if not paginate:
            return {"_data": data, "auto": False, "complete": False, "pages": 1,
                    "returned": got, "total": total, "reason": "paginate=false"}
        # Server returned fewer than totalRows: refetch consistently page by page.
        size = max(1, min(int(params.get("pageSize") or got or 100), MAX_PAGE_SIZE))
        items: list = []
        pages = 0
        reason = None
        for page_no in range(1, MAX_PAGES + 1):
            q = dict(params, pageNo=page_no, pageSize=size)
            st, more, _ = await _send(http, profile, host, method, path, flatten_params(q), None, retry=True)
            pages += 1
            if st >= 400:
                reason = f"page {page_no} failed with HTTP {st}"
                break
            nxt = _find_page(more)
            chunk = nxt[0][nxt[1]] if nxt else []
            items.extend(chunk)
            if len(chunk) < size or len(items) >= total:
                break
        else:
            reason = f"stopped at the {MAX_PAGES}-page limit"
        if reason is None and len(items) < total:
            reason = "fewer items than totalRows were returned"
        node[list_key] = items
        node["totalRows"] = total
        return {"_data": data, "auto": True, "complete": reason is None, "pages": pages,
                "returned": len(items), "total": total, **({"reason": reason} if reason else {})}

    if _find_rest_page(data):
        items = list(data["items"])
        total = data.get("totalItems") if isinstance(data.get("totalItems"), int) else None
        if "page" in params:
            return {"_data": data, "auto": False, "complete": not data["hasNext"], "pages": 1,
                    "returned": len(items), "total": total,
                    **({"reason": "page was given: this is one page only"} if data["hasNext"] else {})}
        if not data["hasNext"]:
            return {"_data": data, "auto": False, "complete": True, "pages": 1,
                    "returned": len(items), "total": total}
        if not paginate:
            return {"_data": data, "auto": False, "complete": False, "pages": 1,
                    "returned": len(items), "total": total, "reason": "paginate=false"}
        size = int(params.get("size") or len(items) or 10)
        pages, reason, has_next = 1, None, True
        for page_no in range(1, MAX_PAGES):
            q = dict(params, page=page_no, size=size)
            st, more, _ = await _send(http, profile, host, method, path, flatten_params(q), None, retry=True)
            pages += 1
            if st >= 400 or not _find_rest_page(more):
                reason = f"page {page_no} failed with HTTP {st}"
                break
            items.extend(more["items"])
            has_next = more["hasNext"]
            if not has_next or not more["items"]:
                break
        else:
            reason = f"stopped at the {MAX_PAGES}-page limit"
        if reason is None and has_next:
            reason = "server still reports more pages"
        data = dict(data, items=items, hasNext=False if reason is None else data["hasNext"])
        return {"_data": data, "auto": True, "complete": reason is None, "pages": pages,
                "returned": len(items), "total": total, **({"reason": reason} if reason else {})}

    # Other list shapes (e.g. government Sub Account v2: totalElements) are not followed
    # automatically, but an incomplete first page is reported instead of passing silently.
    if isinstance(data, dict):
        lists = [k for k, v in data.items() if isinstance(v, list)]
        total_key = next((k for k in ("totalElements", "totalCount", "totalItems")
                          if isinstance(data.get(k), int)), None)
        if total_key and len(lists) == 1:
            got = len(data[lists[0]])
            total = data[total_key]
            info = {"_data": data, "auto": False, "complete": got >= total, "pages": 1,
                    "returned": got, "total": total}
            if got < total:
                info["reason"] = (f"only the first page was returned ({total_key}={total}); "
                                  "request further pages explicitly")
            return info
    return None
