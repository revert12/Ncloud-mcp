"""Read-only classification and write allowlist.

Shared-gateway Ncloud actions are verb-first camelCase names
(getServerInstanceList, terminateServerInstances, ...) and accept GET even for
destructive actions, so the HTTP method alone is not enough.

Read-only rules (anything not proven read-only is treated as a write):
  * non-GET -> write
  * shared-gateway service (/<service>/v2/<action>): read only if the action is exactly
    one segment, verb-first camelCase, with the verb in READ_VERBS
    (getServerInstanceList). Lowercase, PascalCase or nested paths are writes, so a
    gateway that is lenient about case or trailing segments cannot be used to sneak a
    destructive action through the read-only tools.
  * other services (REST style, e.g. /sub-accounts/{id}, /cost/getDemandCostList):
    write if ANY segment is verb-first camelCase with a non-read verb, else read

Sensitive actions (SENSITIVE) return credentials such as server root passwords or
kubeconfigs. They are refused by the read-only tools and, like writes, can only be
called through ncp_write with an explicit NCP_WRITE_ALLOWLIST entry.

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

# Actions that are reads but return secrets (compared lowercase, per path segment).
SENSITIVE = frozenset({
    "getrootpassword",                    # server / vserver: root/admin password
    "getrootpasswordserverinstancelist",  # vserver: root passwords of many servers
    "kubeconfig",                         # vnks: GET /clusters/{uuid}/kubeconfig
})

_VERB_RE = re.compile(r"^([a-z]+)[A-Z]")
_SHARED_ACTION_RE = re.compile(r"^([a-z]+)[A-Z][A-Za-z0-9]*$")
# Path under a service base: letters, digits, '-', '_', '.', '/' and '{}' placeholders resolved by caller.
_ACTION_RE = re.compile(r"^/?[A-Za-z0-9_\-./]{1,200}$")


def valid_action(action: str) -> bool:
    return bool(
        isinstance(action, str)
        and _ACTION_RE.match(action)
        and ".." not in action
        and "//" not in action
    )


def _segments(action: str) -> list[str]:
    return [s for s in action.split("?", 1)[0].split("/") if s]


def is_read_only(method: str, action: str, shared: bool = False) -> bool:
    """``action`` is the path under the service base, e.g. 'getServerInstanceList'."""
    if method.upper() != "GET":
        return False
    segs = _segments(action)
    if shared:
        if len(segs) != 1:
            return False
        m = _SHARED_ACTION_RE.match(segs[0])
        return bool(m) and m.group(1) in READ_VERBS
    for seg in segs:
        m = _VERB_RE.match(seg)
        if m and m.group(1) not in READ_VERBS:
            return False
    return True


def is_sensitive(action: str) -> bool:
    """True if any path segment names an action that returns credentials."""
    return any(seg.lower() in SENSITIVE for seg in _segments(action))


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
