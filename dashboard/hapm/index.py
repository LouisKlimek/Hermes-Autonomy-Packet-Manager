"""Optional central HAPM index under ``$HERMES_HOME`` (OQ-3).

Per OQ-3 the recommended design is a per-profile lock plus an *optional* central
index used only for cross-profile status queries — so the dashboard can answer
"which profiles have HAPM state active?" without scanning every profile folder
and parsing each ``hapm.lock``.

The index is a tiny JSON document at ``$HERMES_HOME/hapm_index.json`` mapping
profile name -> a compact status summary. It is a *cache/convenience*: the
per-profile ``hapm.lock`` remains the source of truth. If the index is missing
or stale it can always be rebuilt from the locks.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import LockState

INDEX_FILENAME = "hapm_index.json"
INDEX_SCHEMA_VERSION = 1


def default_index_path(hermes_home: str | os.PathLike[str] | None = None) -> Path:
    """Return the central index path under ``$HERMES_HOME``.

    Args:
        hermes_home: Override for the Hermes home dir. If ``None``, uses the
            ``HERMES_HOME`` env var, falling back to ``~/.hermes``.
    """
    if hermes_home is None:
        hermes_home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(hermes_home) / INDEX_FILENAME


class CentralIndex:
    """Read/update the optional central HAPM status index.

    The index maps ``profile -> {active, preset, addons, updated_at}``. Only
    profiles with active HAPM state are kept; reverting a profile removes its
    entry so a fully-clean system has an empty (or absent) index.
    """

    def __init__(self, index_path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(index_path) if index_path else default_index_path()

    # -- load/save -----------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": INDEX_SCHEMA_VERSION, "profiles": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Treat a corrupt index as empty; it is only a cache.
            return {"schema_version": INDEX_SCHEMA_VERSION, "profiles": {}}
        data.setdefault("profiles", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)

    # -- queries -------------------------------------------------------

    def active_profiles(self) -> list[str]:
        """Return the names of profiles with active HAPM state."""
        return sorted(self._load()["profiles"].keys())

    def get(self, profile: str) -> dict[str, Any] | None:
        return self._load()["profiles"].get(profile)

    def all(self) -> dict[str, Any]:
        return dict(self._load()["profiles"])

    # -- mutations -----------------------------------------------------

    def update_from_lock(self, state: LockState) -> None:
        """Reflect a profile's lock state into the index.

        If the profile is no longer active its entry is removed, keeping the
        index limited to profiles HAPM currently manages.
        """
        data = self._load()
        profiles = data["profiles"]
        if not state.is_active:
            profiles.pop(state.profile, None)
        else:
            profiles[state.profile] = {
                "active": True,
                "preset": state.active_preset,
                "addons": [
                    {"addon_id": a.addon_id, "mode": a.mode} for a in state.addons
                ],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        self._save(data)

    def remove(self, profile: str) -> None:
        """Drop a profile from the index (e.g. after a full revert)."""
        data = self._load()
        if data["profiles"].pop(profile, None) is not None:
            self._save(data)
