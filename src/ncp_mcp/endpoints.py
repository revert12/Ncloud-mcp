"""Service allowlist: service -> (host, base path) per zone.

Only services whose API URL is documented are listed. Sources:
  * public shared-gateway /v2 services and the special hosts (nks, search engine,
    data streaming, source*) - official ncloud-sdk-go-v2 READMEs (generated from NCP specs)
  * billing, subaccount, sts - api.ncloud-docs.com service overviews
  * government / financial zone services - api-gov / api-fin.ncloud-docs.com overviews
Calls to any other service are rejected. Extra shared-gateway services can be enabled
explicitly with NCP_MCP_EXTRA_SERVICES="gov:cdn,fin:vnas" (zone:service, mapped to
https://<zone gateway>/<service>/v2).
"""

from __future__ import annotations

import os
import re

ZONE_ROOT = {
    "pub": "ntruss.com",
    "gov": "gov-ntruss.com",
    "fin": "fin-ntruss.com",
}

GATEWAY = {
    "pub": "ncloud.apigw.ntruss.com",
    "gov": "ncloud.apigw.gov-ntruss.com",
    "fin": "fin-ncloud.apigw.fin-ntruss.com",
}

_SHARED_V2 = {
    "pub": (
        "autoscaling", "cdn", "clouddb", "loadbalancer", "server", "vautoscaling", "vhadoop",
        "vloadbalancer", "vmongodb", "vmssql", "vmysql", "vnas", "vpc", "vpostgresql",
        "vredis", "vserver",
    ),
    "gov": (
        "vserver", "vpc", "vloadbalancer", "vautoscaling", "vnas", "vmysql", "vpostgresql",
        "vredis", "vcache", "vmssql", "vmongodb",
    ),
    "fin": (
        "vserver", "vpc", "vloadbalancer", "vautoscaling", "vmysql", "vpostgresql",
        "vredis", "vcache", "vmssql", "vmongodb",
    ),
}

# Services that are not on the shared gateway: full host + base path.
_SPECIAL: dict[str, dict[str, tuple[str, str]]] = {
    "pub": {
        "billing": ("billingapi.apigw.ntruss.com", "/billing/v1"),
        "subaccount": ("subaccount.apigw.ntruss.com", "/api/v1"),
        "sts": ("sts.apigw.ntruss.com", "/api/v1"),
        "vnks": ("nks.apigw.ntruss.com", "/vnks/v2"),
        "vses": ("vpcsearchengine.apigw.ntruss.com", "/api/v1"),
        "vses2": ("vpcsearchengine.apigw.ntruss.com", "/api/v2"),
        "vcdss": ("clouddatastreamingservice.apigw.ntruss.com", "/api/v1"),
        "sourcecommit": ("sourcecommit.apigw.ntruss.com", "/api/v1"),
        "sourcebuild": ("sourcebuild.apigw.ntruss.com", "/api/v1"),
        "sourcepipeline": ("sourcepipeline.apigw.ntruss.com", "/api/v1"),
        "vsourcedeploy": ("vpcsourcedeploy.apigw.ntruss.com", "/api/v1"),
        "vsourcepipeline": ("vpcsourcepipeline.apigw.ntruss.com", "/api/v1"),
    },
    "gov": {
        "billing": ("billingapi.apigw.gov-ntruss.com", "/billing/v1"),
        # The government zone runs Sub Account API v2 (different paths and paging).
        "subaccount": ("subaccount.apigw.gov-ntruss.com", "/api/v2"),
    },
    "fin": {
        "billing": ("billingapi.apigw-pub.fin-ntruss.com", "/billing/v1"),
    },
}

# Services whose responses default to XML unless responseFormatType=json is sent.
JSON_PARAM_SERVICES = frozenset({"billing"})

_SERVICE_RE = re.compile(r"^[a-z][a-z0-9]{1,40}$")


class NotAllowed(ValueError):
    pass


def _extra(zone: str) -> set[str]:
    out = set()
    for item in os.environ.get("NCP_MCP_EXTRA_SERVICES", "").split(","):
        z, _, s = item.strip().partition(":")
        if z == zone and _SERVICE_RE.match(s):
            out.add(s)
    return out


def allowed_services(zone: str) -> list[str]:
    return sorted(set(_SHARED_V2[zone]) | set(_SPECIAL[zone]) | _extra(zone))


def is_shared(zone: str, service: str) -> bool:
    return service not in _SPECIAL[zone]


def resolve(zone: str, service: str) -> tuple[str, str]:
    """Return (host, base_path) for an allowlisted service, else raise NotAllowed."""
    if zone not in ZONE_ROOT:
        raise NotAllowed(f"unknown zone '{zone}'")
    service = (service or "").strip().lower()
    if not _SERVICE_RE.match(service):
        raise NotAllowed(f"invalid service name '{service}'")
    if service in _SPECIAL[zone]:
        return _SPECIAL[zone][service]
    if service in _SHARED_V2[zone] or service in _extra(zone):
        return GATEWAY[zone], f"/{service}/v2"
    raise NotAllowed(
        f"service '{service}' is not in the {zone} allowlist. allowed: {allowed_services(zone)}"
    )


def host_allowed(zone: str, host: str) -> bool:
    return host.endswith("." + ZONE_ROOT[zone])
