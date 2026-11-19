"""Service -> base URL resolution per zone.

Most "classic" Ncloud APIs live on the shared gateway
``https://ncloud.apigw.<suffix>/<service>/v2``. Some services use their own
gateway host; those are listed in ``SPECIAL``. Unknown service names fall back
to the shared gateway pattern, so new ``/v2`` services work without code changes.

Requests are only ever signed for hosts under the zone's API gateway domain,
so credentials cannot be sent to an arbitrary host.
"""

from __future__ import annotations

import re

ZONE_SUFFIX = {
    "pub": "ntruss.com",
    "gov": "gov-ntruss.com",
    "fin": "fin-ntruss.com",
}

# Shared gateway host prefix per zone.
SHARED_HOST = {
    "pub": "ncloud",
    "gov": "ncloud",
    "fin": "fin-ncloud",
}

# service -> (host prefix, base path). Host becomes "<prefix>.apigw.<suffix>".
SPECIAL: dict[str, tuple[str, str]] = {
    "billing": ("billingapi", "/billing/v1"),
    "subaccount": ("subaccount", "/api/v1"),
    "vnks": ("nks", "/vnks/v2"),
    "vses": ("vpcsearchengine", "/api/v1"),
    "vses2": ("vpcsearchengine", "/api/v2"),
    "vcdss": ("clouddatastreamingservice", "/api/v1"),
    "sourcecommit": ("sourcecommit", "/api/v1"),
    "sourcebuild": ("sourcebuild", "/api/v1"),
    "sourcepipeline": ("sourcepipeline", "/api/v1"),
    "vsourcedeploy": ("vpcsourcedeploy", "/api/v1"),
    "vsourcepipeline": ("vpcsourcepipeline", "/api/v1"),
}

_SERVICE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")


def resolve(zone: str, service: str) -> tuple[str, str]:
    """Return (host, base_path) for ``service`` in ``zone``."""
    if zone not in ZONE_SUFFIX:
        raise ValueError(f"unknown zone '{zone}'")
    service = service.strip().lower()
    if not _SERVICE_RE.match(service):
        raise ValueError(f"invalid service name '{service}'")
    suffix = ZONE_SUFFIX[zone]
    if service in SPECIAL:
        prefix, base = SPECIAL[service]
        return f"{prefix}.apigw.{suffix}", base
    return f"{SHARED_HOST[zone]}.apigw.{suffix}", f"/{service}/v2"


def host_allowed(zone: str, host: str) -> bool:
    return host.endswith(".apigw." + ZONE_SUFFIX[zone])
