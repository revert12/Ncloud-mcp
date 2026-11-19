"""MCP server entry point (stdio)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, guard
from .client import TIMEOUT, CallError, call
from .config import Profile, default_profile_name, load_profiles

MAX_BATCH = 50
BATCH_CONCURRENCY = 5

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

mcp = MCPServer(
    "ncp-mcp",
    version=__version__,
    instructions=(
        "NAVER Cloud Platform (Ncloud) API access through signed generic calls. "
        "Call ncp_profiles first to see available credential profiles and zones. "
        "Shared-gateway services use https://ncloud.apigw.<zone>/<service>/v2/<action> "
        "(e.g. service='vserver', action='getServerInstanceList'); responseFormatType=json "
        "is added automatically. List parameters are passed as JSON arrays and are "
        "expanded to name.1, name.2. ncp_call and ncp_batch are read-only. "
        "ncp_write exists only when the server is started with NCP_ALLOW_WRITE=true. "
        "Use 'fields' to keep only the columns you need from large list responses."
    ),
)


def _pick(profile: str | None) -> Profile:
    profiles = load_profiles()
    if not profiles:
        raise CallError(
            "no credentials: create ~/.ncloud/configure or set NCLOUD_ACCESS_KEY/NCLOUD_SECRET_KEY"
        )
    name = profile or default_profile_name(profiles)
    if name not in profiles:
        raise CallError(f"unknown profile '{name}'. available: {sorted(profiles)}")
    return profiles[name]


async def _run(profile: str | None, **kwargs: Any) -> dict[str, Any]:
    try:
        p = _pick(profile)
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            return await call(http, p, **kwargs)
    except CallError as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=READ_ONLY)
def ncp_profiles() -> dict[str, Any]:
    """List credential profiles (secrets are never returned), the default profile and whether writes are enabled."""
    profiles = load_profiles()
    return {
        "version": __version__,
        "default": default_profile_name(profiles),
        "write_allowed": guard.write_allowed(),
        "profiles": [p.public_view() for p in profiles.values()],
    }


@mcp.tool(annotations=READ_ONLY)
async def ncp_call(
    service: str,
    action: str,
    params: dict[str, Any] | None = None,
    fields: list[str] | None = None,
    profile: str | None = None,
    paginate: bool = True,
) -> dict[str, Any]:
    """Call one read-only Ncloud API (get*/list*/describe*/... or REST GET) with request signing.

    Args:
        service: Ncloud service key, e.g. 'vserver', 'vpc', 'vloadbalancer', 'vpostgresql',
            'billing', 'subaccount'. Unknown names map to the shared gateway /<service>/v2.
        action: Path under the service base, e.g. 'getServerInstanceList' or '/sub-accounts'.
        params: Query parameters. Lists become name.1, name.2; dicts become name.key.
        fields: Keep only these keys in each list item (dot paths allowed,
            e.g. ['serverName', 'serverInstanceStatus.code']).
        profile: Credential profile name from ncp_profiles (default profile if omitted).
        paginate: Follow pageNo until totalRows is reached for list actions.
    """
    return await _run(
        profile, service=service, action=action, method="GET", params=params,
        paginate=paginate, fields=fields, mode="read",
    )


@mcp.tool(annotations=READ_ONLY)
async def ncp_batch(calls: list[dict[str, Any]], profile: str | None = None) -> dict[str, Any]:
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

    async def one(http: httpx.AsyncClient, item: dict[str, Any]) -> dict[str, Any]:
        try:
            if not isinstance(item, dict) or "service" not in item or "action" not in item:
                raise CallError("each call needs 'service' and 'action'")
            p = _pick(item.get("profile") or profile)
            async with sem:
                return await call(
                    http, p, item["service"], item["action"], "GET",
                    item.get("params"), None, item.get("paginate", True),
                    item.get("fields"), "read",
                )
        except CallError as exc:
            return {"error": str(exc)}

    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        results = await asyncio.gather(*(one(http, c) for c in calls))
    return {"count": len(results), "results": list(results)}


async def ncp_write(
    service: str,
    action: str,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    body: Any = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Call one Ncloud API that creates, changes or deletes resources.

    Only registered when the server runs with NCP_ALLOW_WRITE=true. Read-only actions
    are rejected here; use ncp_call for those.

    Args:
        service: Ncloud service key (see ncp_call).
        action: e.g. 'stopServerInstances', 'deletePublicIpInstance', or a REST path.
        method: HTTP method. Shared-gateway actions accept GET; REST services use POST/PUT/DELETE.
        params: Query parameters (lists become name.1, name.2).
        body: JSON body for REST-style services.
        profile: Credential profile name from ncp_profiles.
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
