"""Private, user-owned custom addon storage and safe ZIP export."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .registry import Addon, RegistryError, load_addon, load_registry

CUSTOM_ADDONS_DIRNAME = "hapm-custom-addons"
_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+")


class CustomAddonError(Exception):
    """Raised when a custom addon cannot be safely persisted or exported."""


def custom_addons_root(hermes_home: Path | None = None) -> Path:
    value = os.environ.get("HAPM_CUSTOM_ADDONS_ROOT", "").strip()
    if value:
        return Path(value)
    home = hermes_home
    if home is None:
        value = os.environ.get("HERMES_HOME", "").strip()
        home = Path(value) if value else Path.home() / ".hermes"
    return home / CUSTOM_ADDONS_DIRNAME


def default_shipped_addons_root() -> Path:
    """Return the immutable shipped-addon registry, honoring test overrides."""
    value = os.environ.get("HAPM_ADDONS_ROOT", "").strip()
    if value:
        return Path(value)
    return Path(__file__).resolve().parents[2] / "addons"


@dataclass(frozen=True)
class CustomAddon:
    addon: Addon

    @property
    def id(self) -> str:
        return self.addon.id

    @property
    def name(self) -> str:
        return self.addon.name

    @property
    def is_custom(self) -> bool:
        return True


def _required_string(payload: dict, key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise CustomAddonError(f"'{key}' is required.")
    return value


def _normalize(payload: dict, *, fixed_id: str | None = None) -> tuple[dict, str]:
    addon_id = fixed_id or _required_string(payload, "id")
    if not _SLUG_RE.fullmatch(addon_id):
        raise CustomAddonError("'id' must use only letters, numbers, '.', '_' or '-'.")
    name = _required_string(payload, "name")
    description = _required_string(payload, "description")
    soul_block = str(payload.get("soul_block", "")).strip()
    if not soul_block:
        raise CustomAddonError("'soul_block' is required.")
    compatible = payload.get("compatible_profiles_or_presets", ["*"])
    if not isinstance(compatible, list) or not compatible or not all(
        isinstance(item, str) and item.strip() for item in compatible
    ):
        raise CustomAddonError("'compatible_profiles_or_presets' must be a non-empty list of names.")
    manifest = {
        "id": addon_id,
        "name": name,
        "description": description,
        "version": "0.1.0",
        "contributes": {"soul_block": True, "skills": False},
        "compatible_profiles_or_presets": [item.strip() for item in compatible],
    }
    return manifest, soul_block + "\n"


class CustomAddonStore:
    """Stores only full addon packages under the HAPM user-owned boundary."""

    def __init__(
        self,
        root: str | Path | None = None,
        hermes_home: Path | None = None,
        shipped_addons_root: str | Path | None = None,
    ):
        self.root = Path(root) if root is not None else custom_addons_root(hermes_home)
        self.shipped_addons_root = (
            Path(shipped_addons_root)
            if shipped_addons_root is not None
            else default_shipped_addons_root()
        )

    def _directory(self, addon_id: str) -> Path:
        if not _SLUG_RE.fullmatch(addon_id):
            raise CustomAddonError("Invalid custom addon id.")
        return self.root / addon_id

    def _load_dir(self, directory: Path) -> CustomAddon:
        try:
            return CustomAddon(load_addon(directory))
        except RegistryError as exc:
            raise CustomAddonError(str(exc)) from exc

    def list(self) -> list[CustomAddon]:
        if not self.root.is_dir():
            return []
        addons = []
        for entry in sorted(self.root.iterdir()):
            if entry.is_dir() and (entry / "manifest.json").is_file():
                try:
                    addons.append(self._load_dir(entry))
                except CustomAddonError:
                    continue
        return addons

    def load(self, addon_id: str) -> CustomAddon:
        directory = self._directory(addon_id)
        if not directory.is_dir():
            raise CustomAddonError(f"No custom addon with id {addon_id!r}.")
        return self._load_dir(directory)

    def _name_exists(self, name: str, *, except_id: str | None = None) -> bool:
        return any(addon.name.casefold() == name.casefold() and addon.id != except_id for addon in self.list())

    def _is_shipped_id(self, addon_id: str) -> bool:
        try:
            return any(addon.id == addon_id for addon in load_registry(self.shipped_addons_root))
        except RegistryError:
            return False

    def _write(self, directory: Path, manifest: dict, soul_block: str) -> CustomAddon:
        self.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".hapm-custom-", dir=self.root))
        backup = directory.with_name(directory.name + ".bak")
        try:
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            (staging / "soul_block.md").write_text(soul_block, encoding="utf-8")
            if backup.exists():
                shutil.rmtree(backup)
            if directory.exists():
                os.replace(directory, backup)
            os.replace(staging, directory)
            if backup.exists():
                shutil.rmtree(backup)
        except OSError as exc:
            if not directory.exists() and backup.exists():
                os.replace(backup, directory)
            raise CustomAddonError(f"Could not save custom addon: {exc}") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        return self._load_dir(directory)

    def create(self, payload: dict) -> CustomAddon:
        manifest, soul_block = _normalize(payload)
        directory = self._directory(manifest["id"])
        if self._is_shipped_id(manifest["id"]):
            raise CustomAddonError(
                f"Custom addon id {manifest['id']!r} is reserved by a shipped addon."
            )
        if directory.exists():
            raise CustomAddonError(f"A custom addon with id {manifest['id']!r} already exists.")
        if self._name_exists(manifest["name"]):
            raise CustomAddonError(f"A custom addon named {manifest['name']!r} already exists.")
        return self._write(directory, manifest, soul_block)

    def update(self, addon_id: str, payload: dict) -> CustomAddon:
        self.load(addon_id)  # rejects missing ids; built-ins are not in this store
        manifest, soul_block = _normalize(payload, fixed_id=addon_id)
        if self._name_exists(manifest["name"], except_id=addon_id):
            raise CustomAddonError(f"A custom addon named {manifest['name']!r} already exists.")
        return self._write(self._directory(addon_id), manifest, soul_block)


def addon_zip_bytes(addon: CustomAddon) -> bytes:
    """Return a deterministic, in-memory ZIP containing only package files."""
    directory = addon.addon.directory
    files = ("manifest.json", "soul_block.md")
    if any(not (directory / filename).is_file() for filename in files):
        raise CustomAddonError("Custom addon package is incomplete and cannot be downloaded.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in files:
            info = zipfile.ZipInfo(f"{addon.id}/{filename}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (directory / filename).read_bytes())
    return output.getvalue()
