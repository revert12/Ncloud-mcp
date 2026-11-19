"""MCP server entry point (stdio)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, guard
from .client import MAX_RESULT_CHARS, TIMEOUT, CallError, call
from .config import Profile, load_profiles

MAX_BATCH = 50
BATCH_CONCURRENCY = 5
MIN_ITEM_CHARS = 5_000  # floor per batch item, so a 50-call batch still says something useful

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

# Valid profile names are fixed into the tool schema at startup (restart to pick up changes).
_STARTUP = load_profiles()
PROFILE_NAMES: tuple[str, ...] = tuple(sorted(_STARTUP.profiles))
ProfileName = Literal[PROFILE_NAMES] if PROFILE_NAMES else str  # type: ignore[valid-type]

_profiles_hint = ", ".join(PROFILE_NAMES) if PROFILE_NAMES else "(none configured)"
mcp = MCPServer(
    "ncp-mcp",
    version=__version__,
    instructions=(
        "NAVER Cloud Platform (Ncloud) API access through signed generic calls. "
        f"Credential profiles (name ends with its zone pub/gov/fin): {_profiles_hint}. "
        "When more than one profile exists, every call must name its profile; pick the one that "
        "matches the account the user is asking about and ask the user if unsure. "
        "ncp_profiles(verify=true) shows which NCP account/user each key really belongs to. "
        "Shared-gateway services use https://ncloud.apigw.<zone>/<service>/v2/<action> "
        "(e.g. service='vserver', action='getServerInstanceList'); responseFormatType=json "
        "is added automatically. List parameters are passed as JSON arrays and are "
        "expanded to name.1, name.2. ncp_call and ncp_batch are read-only. "
        "ncp_write exists only when the server is started with NCP_ALLOW_WRITE=true. "
        "Use 'fields' to keep only the columns you need from large list responses."
    ),
)


def _pick(profile: str | None) -> Profile:
    res = load_profiles()
    usable = res.profiles
    if profile:
        if profile in usable:
            return usable[profile]
        bad = next((p for p in res.problems if p["name"] == profile), None)
        if bad:
            raise CallError(f"profile '{profile}' is disabled: {bad['error']}")
        raise CallError(f"unknown profile '{profile}'. available: {sorted(usable)}")
    if len(usable) == 1:
        return next(iter(usable.values()))
    if not usable:
        detail = f" problems: {res.problems}" if res.problems else ""
        raise CallError(
            "no usable credential profile (see ncp_profiles). Configure ~/.ncloud/configure "
            "or NCLOUD_ACCESS_KEY/NCLOUD_SECRET_KEY/NCLOUD_ZONE." + detail
        )
    raise CallError(
        f"profile is required when several profiles exist. available: {sorted(usable)}"
    )


def _http() -> httpx.AsyncClient:  # patched in tests
    return httpx.AsyncClient(timeout=TIMEOUT)


def _unexpected(exc: Exception) -> dict[str, Any]:
    # Never echo request details: only the exception type.
    return {"error": f"internal error ({type(exc).__name__}); the request was not completed"}


async def _run(profile: str | None, **kwargs: Any) -> dict[str, Any]:
    try:
        if profile is not None and not isinstance(profile, str):
            raise CallError("profile must be a string")
        p = _pick(profile)
        async with _http() as http:
            return await call(http, p, **kwargs)
    except CallError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - keep the MCP server alive
        return _unexpected(exc)


@mcp.tool(annotations=READ_ONLY)
async def ncp_profiles(verify: bool = False) -> dict[str, Any]:
    """List usable credential profiles, disabled profiles with the reason, and whether writes are enabled.

    Secrets are never returned. With verify=true, each usable profile is checked against
    NCP STS (GET /api/v1/caller-identity) and the key owner's account id, login alias and
    user type (Customer = main account, Sub = sub account, Role) are shown.
    """
    res = load_profiles()
    out: dict[str, Any] = {
        "version": __version__,
        "write_allowed": guard.write_allowed(),
        "profiles": [p.public_view() for p in res.profiles.values()],
        "disabled": res.problems,
    }
    if set(res.profiles) != set(PROFILE_NAMES):
        out["note"] = "profiles changed since startup; restart the MCP server to refresh the tool schema"
    if verify and res.profiles:
        async with _http() as http:

            async def who(p: Profile) -> dict[str, Any]:
                try:
                    r = await call(http, p, "sts", "/caller-identity", paginate=False)
                except CallError as exc:
                    return {"error": str(exc)}
                except Exception as exc:  # noqa: BLE001
                    return _unexpected(exc)
                if r["status"] >= 400:
                    return {"error": {"status": r["status"], "body": r.get("error")}}
                d = r["data"] if isinstance(r["data"], dict) else {}
                return {k: d.get(k) for k in ("id", "loginAlias", "userType")}

            ids = await asyncio.gather(*(who(p) for p in res.profiles.values()))
        for view, ident in zip(out["profiles"], ids):
            view["identity"] = ident
    return out


@mcp.tool(annotations=READ_ONLY)
async def ncp_call(
    service: str,
    action: str,
    params: dict[str, Any] | None = None,
    fields: list[str] | None = None,
    profile: ProfileName | None = None,
    paginate: bool = True,
) -> dict[str, Any]:
    """Call one read-only Ncloud API (get*/list*/describe*/... or REST GET) with request signing.

    Args:
        service: Allowlisted Ncloud service key, e.g. 'vserver', 'vpc', 'vloadbalancer',
            'vpostgresql', 'billing', 'subaccount'. The allowlist depends on the profile's zone;
            a rejected call lists the allowed services.
        action: Path under the service base, e.g. 'getServerInstanceList' or '/sub-accounts'.
        params: Query parameters. Lists become name.1, name.2; dicts become name.key.
            Do not pass pageNo/page unless you want one specific page.
        fields: Keep only these keys in each list item (dot paths allowed,
            e.g. ['serverName', 'serverInstanceStatus.code']).
        profile: Credential profile. Required when more than one profile exists.
        paginate: Fetch every page when the first response is incomplete.
            The result's 'paging.complete' says whether the list is complete.
    """
    return await _run(
        profile, service=service, action=action, method="GET", params=params,
        paginate=paginate, fields=fields, mode="read",
    )


@mcp.tool(annotations=READ_ONLY)
async def ncp_batch(
    calls: list[dict[str, Any]], profile: ProfileName | None = None
) -> dict[str, Any]:
    """Run several read-only ncp_call requests concurrently; results keep the input order.

    Each item takes the ncp_call keys: service, action, and optionally params, fields,
    profile, paginate. A per-item 'profile' overrides the batch profile.
    Failures are reported per item and do not stop the others.
    """
    if not isinstance(calls, list) or not calls:
        return {"error": "calls must be a non-empty list"}
    if len(calls) > MAX_BATCH:
        return {"error": f"at most {MAX_BATCH} calls per batch"}

    sem = asyncio.Semaphore(BATCH_CONCURRENCY)
    # The whole batch shares one result budget, split evenly across the calls.
    item_chars = max(MIN_ITEM_CHARS, MAX_RESULT_CHARS // len(calls))
    allowed_keys = {"service", "action", "params", "fields", "profile", "paginate"}

    async def one(http: httpx.AsyncClient, idx: int, item: Any) -> dict[str, Any]:
        try:
            if not isinstance(item, dict) or "service" not in item or "action" not in item:
                raise CallError("each call needs 'service' and 'action'")
            unknown = set(item) - allowed_keys
            if unknown:
                raise CallError(f"unknown keys {sorted(unknown)} (read-only batch; no method/body)")
            item_profile = item.get("profile") or profile
            if item_profile is not None and not isinstance(item_profile, str):
                raise CallError("profile must be a string")
            paginate = item.get("paginate", True)
            if not isinstance(paginate, bool):
                raise CallError("paginate must be true or false (JSON boolean, not a string)")
            p = _pick(item_profile)
            async with sem:
                r = await call(
                    http, p, item["service"], item["action"], "GET",
                    item.get("params"), None, paginate, item.get("fields"), "read",
                    max_chars=item_chars,
                )
        except CallError as exc:
            r = {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - one bad item must not fail the batch
            r = _unexpected(exc)
        return {"index": idx, **r}

    async with _http() as http:
        results = await asyncio.gather(*(one(http, i, c) for i, c in enumerate(calls)))
    failed = sum(1 for r in results if "error" in r)
    return {"count": len(results), "failed": failed, "results": list(results)}


async def ncp_write(
    service: str,
    action: str,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    body: Any = None,
    profile: ProfileName | None = None,
) -> dict[str, Any]:
    """Call one Ncloud API that creates, changes or deletes resources.

    Only registered when the server runs with NCP_ALLOW_WRITE=true, and each call must
    match an NCP_WRITE_ALLOWLIST entry: 'service:action' for GET, or
    'METHOD service:action' for other methods. Read-only actions are
    rejected here; use ncp_call for those. Writes are never retried: on a timeout the
    outcome is unknown, so check the resource state before trying again.

    Args:
        service: Ncloud service key (see ncp_call).
        action: e.g. 'stopServerInstances', 'deletePublicIpInstance', or a REST path.
        method: HTTP method. Shared-gateway actions accept GET; REST services use POST/PUT/DELETE.
        params: Query parameters (lists become name.1, name.2).
        body: JSON body for REST-style services.
        profile: Credential profile. Required when more than one profile exists.
    """
    return await _run(
        profile, service=service, action=action, method=method, params=params,
        body=body, paginate=False, mode="write",
    )


if guard.write_allowed():
    mcp.tool(annotations=WRITE)(ncp_write)


def main() -> None:
    # httpx logs every request URL at INFO; keep stderr quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run()


if __name__ == "__main__":
    main()
