"""Read-only classification and write allowlist.

Shared-gateway Ncloud actions are verb-first camelCase names
(getServerInstanceList, terminateServerInstances, ...) and accept GET even for
destructive actions, so the HTTP method alone is not enough.

Read-only rules:
  * non-GET                                   -> write
  * last path segment is verb-first camelCase -> read only if the verb is in READ_VERBS
  * otherwise (REST-style GET, e.g. /sub-accounts) -> read

Writes need BOTH:
  * NCP_ALLOW_WRITE=true
  * the exact "service:action" listed in NCP_WRITE_ALLOWLIST
    (comma separated, e.g. "vserver:stopServerInstances,vserver:startServerInstances";
    for REST services the action is the path, e.g. "subaccount:/sub-accounts")
"""

from __future__ import annotations

import os
import re

READ_VERBS = frozenset({"get", "list", "describe", "check", "search", "view", "find"})

_VERB_RE = re.compile(r"^([a-z]+)[A-Z]")
# Path under a service base: letters, digits, '-', '_', '.', '/' and '{}' placeholders resolved by caller.
_ACTION_RE = re.compile(r"^/?[A-Za-z0-9_\-./]{1,200}$")


def valid_action(action: str) -> bool:
    return bool(
        isinstance(action, str)
        and _ACTION_RE.match(action)
        and ".." not in action
        and "//" not in action
    )


def is_read_only(method: str, path: str) -> bool:
    if method.upper() != "GET":
        return False
    last = path.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    m = _VERB_RE.match(last)
    if m:
        return m.group(1) in READ_VERBS
    return True


def write_allowed() -> bool:
    return os.environ.get("NCP_ALLOW_WRITE", "").strip().lower() in ("1", "true", "yes")


def write_allowlist() -> frozenset[str]:
    raw = os.environ.get("NCP_WRITE_ALLOWLIST", "")
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def _norm_action(action: str) -> str:
    return action.strip()


def write_permitted(service: str, action: str) -> bool:
    allow = write_allowlist()
    s = service.strip().lower()
    a = _norm_action(action)
    candidates = {f"{s}:{a}", f"{s}:{a.lstrip('/')}", f"{s}:/{a.lstrip('/')}"}
    return bool(candidates & allow)
