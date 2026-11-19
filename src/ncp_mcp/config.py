"""Credential profiles.

Profiles are read from the Ncloud CLI file ``~/.ncloud/configure`` (INI format,
one section per profile). Environment variables ``NCLOUD_ACCESS_KEY`` /
``NCLOUD_SECRET_KEY`` (optionally ``NCLOUD_ZONE``) add a profile named ``env``.

Recognized keys per section:
    ncloud_access_key_id      (required)
    ncloud_secret_access_key  (required)
    ncloud_api_url            (optional, informational)
    ncloud_zone               (optional: pub | gov | fin, default pub;
                               inferred as gov/fin from ncloud_api_url if absent)

Secrets never leave this module except to sign requests.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

ZONES = ("pub", "gov", "fin")


@dataclass(frozen=True)
class Profile:
    name: str
    zone: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    def public_view(self) -> dict:
        masked = self.access_key[:4] + "…" if len(self.access_key) > 4 else "…"
        return {"name": self.name, "zone": self.zone, "access_key": masked}


def _config_path() -> Path:
    override = os.environ.get("NCP_MCP_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ncloud" / "configure"


def _infer_zone(section: configparser.SectionProxy) -> str:
    zone = section.get("ncloud_zone", "").strip().lower()
    if zone:
        if zone not in ZONES:
            raise ValueError(f"unknown ncloud_zone '{zone}' (expected one of {ZONES})")
        return zone
    url = section.get("ncloud_api_url", "")
    if "gov-ntruss.com" in url:
        return "gov"
    if "fin-ntruss.com" in url:
        return "fin"
    return "pub"


def load_profiles() -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    path = _config_path()
    if path.is_file():
        parser = configparser.ConfigParser(default_section="__none__")
        parser.read(path, encoding="utf-8")
        for name in parser.sections():
            sec = parser[name]
            ak = sec.get("ncloud_access_key_id", "").strip()
            sk = sec.get("ncloud_secret_access_key", "").strip()
            if ak and sk:
                profiles[name] = Profile(name, _infer_zone(sec), ak, sk)

    env_ak = os.environ.get("NCLOUD_ACCESS_KEY", "").strip()
    env_sk = os.environ.get("NCLOUD_SECRET_KEY", "").strip()
    if env_ak and env_sk:
        zone = os.environ.get("NCLOUD_ZONE", "pub").strip().lower()
        if zone not in ZONES:
            raise ValueError(f"unknown NCLOUD_ZONE '{zone}'")
        profiles["env"] = Profile("env", zone, env_ak, env_sk)
    return profiles


def default_profile_name(profiles: dict[str, Profile]) -> str | None:
    preferred = os.environ.get("NCP_MCP_DEFAULT_PROFILE")
    if preferred and preferred in profiles:
        return preferred
    if "DEFAULT" in profiles:
        return "DEFAULT"
    return next(iter(profiles), None)
