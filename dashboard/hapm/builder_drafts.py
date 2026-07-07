"""Local draft store for the v1.2 In-UI Addon Builder (Requirement 2, §5).

Implements the "Local Draft (not activatable)" half of the spec's hybrid write
target (§5): a builder draft is persisted **only** to a private HAPM-owned
draft directory and has **zero effect on any real profile** — no SOUL.md, no
skills, no ``hapm.lock`` is touched by saving a draft. Activation is impossible
from a draft; the only path to activation is opening a PR (:mod:`hapm.builder_pr`)
that lands the addon in the shared ``addons/`` registry, after which it enables
through the *identical* FR-6 toggle code path as any core addon (FR-7 — no
special-case activation).

Where drafts live
-----------------
``$HAPM_DRAFTS_ROOT`` when set, else ``$HERMES_HOME/hapm-drafts/``. This is
deliberately **outside** any profile directory and outside the repo working
tree, so a draft can never be picked up by the registry scan
(:func:`hapm.registry.load_registry`) nor by a profile's skill loader. A draft
is a single ``<addon_id>.json`` file holding the raw builder inputs plus
audit metadata (author/origin — FR-7 audit-only, never branches code).

The draft body is always re-validated by :mod:`hapm.builder_sanitize` before it
is written (the endpoint calls ``check_addon`` first); this module does not
re-implement sanitizing — it only stores already-checked content and never
grants activation.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .builder_sanitize import make_addon_id, validate_addon_id

DRAFTS_DIRNAME = "hapm-drafts"

_ID_FILE_RE = re.compile(r"^community-[A-Za-z0-9._-]+\.json$")


class DraftError(Exception):
    """Raised on a malformed draft or an illegal draft-store operation."""


def drafts_root(hermes_home: Path | None = None) -> Path:
    """Resolve the draft-store directory.

    ``$HAPM_DRAFTS_ROOT`` overrides for tests/custom installs; otherwise the
    store is ``<hermes_home>/hapm-drafts/`` (``$HERMES_HOME`` default when
    ``hermes_home`` is not given). Never inside a profile or the repo tree.
    """
    val = os.environ.get("HAPM_DRAFTS_ROOT", "").strip()
    if val:
        return Path(val)
    home = hermes_home
    if home is None:
        env_home = os.environ.get("HERMES_HOME", "").strip()
        home = Path(env_home) if env_home else (Path.home() / ".hermes")
    return home / DRAFTS_DIRNAME


@dataclass
class Draft:
    """A stored builder draft — inputs only, never an activatable artifact.

    Attributes:
        addon_id: server-generated ``community-<user>-<slug>`` id.
        name / description: display metadata (already length-checked).
        soul: ``{"enabled": bool, "body": str}`` SOUL.md block inputs.
        skill: ``{"enabled": bool, "source": ..., "body": ..., "source_ref": ...}``.
        author: git username of the contributor (FR-7 audit metadata only).
        origin: constant ``"community-builder"`` provenance tag (audit only).
        created_at / updated_at: unix timestamps.
    """

    addon_id: str
    name: str
    description: str
    soul: dict = field(default_factory=dict)
    skill: dict = field(default_factory=dict)
    author: str = ""
    origin: str = "community-builder"
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "addon_id": self.addon_id,
            "name": self.name,
            "description": self.description,
            "soul": self.soul,
            "skill": self.skill,
            # ``author``/``origin`` are audit metadata only (FR-7): they are
            # stored and surfaced but MUST NOT branch any activation code path.
            "author": self.author,
            "origin": self.origin,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Draft":
        if not isinstance(data, dict):
            raise DraftError("draft record is not a JSON object")
        try:
            addon_id = str(data["addon_id"])
        except KeyError as exc:
            raise DraftError(f"draft missing required field: {exc}") from exc
        validate_addon_id(addon_id)
        return cls(
            addon_id=addon_id,
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            soul=dict(data.get("soul") or {}),
            skill=dict(data.get("skill") or {}),
            author=str(data.get("author", "")),
            origin=str(data.get("origin", "community-builder")),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
        )


class DraftStore:
    """Filesystem-backed draft store — one JSON file per draft.

    The store is intentionally dumb: it persists and reads back builder inputs
    and nothing else. It has no reference to any profile and exposes no method
    that could activate a draft. Activation is exclusively the PR path.
    """

    def __init__(self, root: str | Path | None = None, hermes_home: Path | None = None):
        self.root = Path(root) if root is not None else drafts_root(hermes_home)

    # -- path helpers ---------------------------------------------------

    def _path_for(self, addon_id: str) -> Path:
        validate_addon_id(addon_id)
        return self.root / f"{addon_id}.json"

    # -- write ----------------------------------------------------------

    def save(self, draft: Draft) -> Path:
        """Persist a draft (create or overwrite). Returns the file path.

        Only writes under :attr:`root`; can never write into a profile. The
        caller must have already run :func:`hapm.builder_sanitize.check_addon`
        — this method stores content verbatim and does not grant activation.
        """
        validate_addon_id(draft.addon_id)
        now = time.time()
        if not draft.created_at:
            draft.created_at = now
        draft.updated_at = now
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(draft.addon_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(draft.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return path

    def create(
        self,
        *,
        name: str,
        description: str,
        author: str,
        soul: dict | None = None,
        skill: dict | None = None,
    ) -> Draft:
        """Build a :class:`Draft` with a server-generated id and save it."""
        addon_id = make_addon_id(author, name)
        draft = Draft(
            addon_id=addon_id,
            name=name,
            description=description,
            soul=dict(soul or {}),
            skill=dict(skill or {}),
            author=author,
        )
        self.save(draft)
        return draft

    # -- read -----------------------------------------------------------

    def load(self, addon_id: str) -> Draft:
        path = self._path_for(addon_id)
        if not path.is_file():
            raise DraftError(f"no draft with id {addon_id!r}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DraftError(f"corrupt draft {path}: {exc}") from exc
        return Draft.from_dict(data)

    def exists(self, addon_id: str) -> bool:
        try:
            return self._path_for(addon_id).is_file()
        except Exception:
            return False

    def list(self) -> list[Draft]:
        """Return all stored drafts (skipping unparseable files, never 500s)."""
        if not self.root.is_dir():
            return []
        out: list[Draft] = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_file() or not _ID_FILE_RE.match(entry.name):
                continue
            try:
                out.append(Draft.from_dict(json.loads(entry.read_text(encoding="utf-8"))))
            except Exception:  # noqa: BLE001 - one bad file must not break listing
                continue
        return out

    def delete(self, addon_id: str) -> bool:
        """Delete a draft file. Returns True if it existed."""
        path = self._path_for(addon_id)
        if path.is_file():
            path.unlink()
            return True
        return False
