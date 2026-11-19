"""Read-only classification.

Shared-gateway Ncloud actions are verb-first camelCase names
(getServerInstanceList, terminateServerInstances, ...) and accept GET even for
destructive actions, so the HTTP method alone is not enough.

Rules:
  * non-GET                                  -> write
  * last path segment is verb-first camelCase -> read only if the verb is in READ_VERBS
  * otherwise (REST-style GET, e.g. /sub-accounts) -> read
"""

from __future__ import annotations

import os
import re

READ_VERBS = frozenset({"get", "list", "describe", "check", "search", "view", "find"})

_VERB_RE = re.compile(r"^([a-z]+)[A-Z]")


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
