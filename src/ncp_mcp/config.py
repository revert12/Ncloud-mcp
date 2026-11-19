"""Credential profiles.

Profiles are read from the Ncloud CLI file ``~/.ncloud/configure`` (INI format,
one section per profile). Environment variables ``NCLOUD_ACCESS_KEY`` /
``NCLOUD_SECRET_KEY`` / ``NCLOUD_ZONE`` add a profile named ``env-<zone>``.

Rules (a profile that breaks one is disabled and reported, never used):
  * ``ncloud_zone`` must be set explicitly: pub | gov | fin
  * the profile name must end with ``-<zone>`` (e.g. ``mycompany-pub``, ``customer-gov``),
    so the zone is visible wherever the name appears
  * name characters: lowercase letters, digits, hyphen
  * ``ncloud_access_key_id`` and ``ncloud_secret_access_key`` are required

Recognized keys per section:
    ncloud_access_key_id      (required)
    ncloud_secret_access_key  (required)
    ncloud_zone               (required)
    ncloud_api_url            (ignored; used by the Ncloud CLI)

Secrets never leave this module except to sign requests.
"""

from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ZONES = ("pub", "gov", "fin")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*-(pub|gov|fin)$")


@dataclass(frozen=True)
class Profile:
    name: str
    zone: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    def public_view(self) -> dict:
        masked = self.access_key[:4] + "…" if len(self.access_key) > 4 else "…"
        return {"name": self.name, "zone": self.zone, "access_key": masked}


@dataclass
class LoadResult:
    profiles: dict[str, Profile]
    problems: list[dict[str, str]]
    source: str


def _config_path() -> Path:
    override = os.environ.get("NCP_MCP_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ncloud" / "configure"


def validate(name: str, zone: str, access_key: str, secret_key: str) -> str | None:
    """Return an error message, or None if the profile is usable."""
    if not access_key or not secret_key:
        return "ncloud_access_key_id and ncloud_secret_access_key are required"
    if not zone:
        return "ncloud_zone is required (pub | gov | fin)"
    if zone not in ZONES:
        return f"ncloud_zone '{zone}' is invalid (pub | gov | fin)"
    m = _NAME_RE.match(name)
    if not m:
        return (
            f"profile name must be lowercase letters/digits/hyphens ending with '-{zone}' "
            f"(e.g. 'mycompany-{zone}')"
        )
    if m.group(1) != zone:
        return f"profile name ends with '-{m.group(1)}' but ncloud_zone is '{zone}'"
    return None


def load_profiles() -> LoadResult:
    profiles: dict[str, Profile] = {}
    problems: list[dict[str, str]] = []
    path = _config_path()

    if path.is_file():
        parser = configparser.ConfigParser(default_section="__none__", interpolation=None)
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error as exc:
            problems.append({"name": "*", "error": f"cannot parse {path.name}: {type(exc).__name__}"})
            parser = configparser.ConfigParser(default_section="__none__")
        for name in parser.sections():
            sec = parser[name]
            ak = sec.get("ncloud_access_key_id", "").strip()
            sk = sec.get("ncloud_secret_access_key", "").strip()
            zone = sec.get("ncloud_zone", "").strip().lower()
            err = validate(name, zone, ak, sk)
            if err:
                problems.append({"name": name, "error": err})
            else:
                profiles[name] = Profile(name, zone, ak, sk)

    env_ak = os.environ.get("NCLOUD_ACCESS_KEY", "").strip()
    env_sk = os.environ.get("NCLOUD_SECRET_KEY", "").strip()
    if env_ak or env_sk:
        zone = os.environ.get("NCLOUD_ZONE", "").strip().lower()
        name = f"env-{zone}" if zone else "env"
        err = validate(name, zone, env_ak, env_sk)
        if err:
            problems.append({"name": name, "error": err.replace("ncloud_zone", "NCLOUD_ZONE")})
        else:
            profiles[name] = Profile(name, zone, env_ak, env_sk)

    return LoadResult(profiles, problems, str(path))
