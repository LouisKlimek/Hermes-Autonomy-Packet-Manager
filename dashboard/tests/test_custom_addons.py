"""Focused custom-addon storage and export tests."""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

_DASHBOARD = Path(__file__).resolve().parent.parent
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

from hapm.custom_addons import (  # noqa: E402
    CustomAddonError,
    CustomAddonStore,
    addon_zip_bytes,
)


def _payload(name="My custom addon", addon_id="my-custom-addon"):
    return {
        "id": addon_id,
        "name": name,
        "description": "A safe custom contribution.",
        "soul_block": "Use concise replies.\n",
        "compatible_profiles_or_presets": ["*"],
    }


def test_create_persists_only_custom_boundary_and_exports_valid_zip():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "custom"
        store = CustomAddonStore(root)
        addon = store.create(_payload())
        assert (root / "my-custom-addon" / "manifest.json").is_file()
        assert addon.is_custom is True
        archive = addon_zip_bytes(addon)
        with zipfile.ZipFile(__import__("io").BytesIO(archive)) as zf:
            assert zf.namelist() == [
                "my-custom-addon/manifest.json",
                "my-custom-addon/soul_block.md",
            ]
            assert json.loads(zf.read("my-custom-addon/manifest.json"))["id"] == "my-custom-addon"
            assert all(not name.startswith("/") and ".." not in name for name in zf.namelist())


def test_create_rejects_duplicate_id_and_name():
    with tempfile.TemporaryDirectory() as td:
        store = CustomAddonStore(Path(td) / "custom")
        store.create(_payload())
        for payload in (_payload(), _payload(name="My custom addon", addon_id="other")):
            try:
                store.create(payload)
                raise AssertionError("expected duplicate custom addon rejection")
            except CustomAddonError:
                pass


def test_create_rejects_id_reserved_by_shipped_addon():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        registry = root / "addons"
        shipped = registry / "shipped-addon"
        shipped.mkdir(parents=True)
        (shipped / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "shipped-addon",
                    "name": "Shipped addon",
                    "description": "Built in.",
                    "version": "1.0.0",
                    "contributes": {"soul_block": True, "skills": False},
                    "compatible_profiles_or_presets": ["*"],
                }
            )
        )
        store = CustomAddonStore(root / "custom", shipped_addons_root=registry)

        try:
            store.create(_payload(addon_id="shipped-addon"))
            raise AssertionError("expected shipped addon id rejection")
        except CustomAddonError as exc:
            assert "reserved by a shipped addon" in str(exc)


def test_update_preserves_identity_and_cancel_never_mutates():
    with tempfile.TemporaryDirectory() as td:
        store = CustomAddonStore(Path(td) / "custom")
        store.create(_payload())
        before = (store.root / "my-custom-addon" / "manifest.json").read_bytes()
        # Merely loading for an edit/cancel changes nothing.
        assert store.load("my-custom-addon").id == "my-custom-addon"
        assert (store.root / "my-custom-addon" / "manifest.json").read_bytes() == before
        updated = store.update("my-custom-addon", {**_payload(name="Updated custom addon"), "id": "changed"})
        assert updated.id == "my-custom-addon"
        assert updated.name == "Updated custom addon"


def test_update_rejects_missing_or_invalid_content():
    with tempfile.TemporaryDirectory() as td:
        store = CustomAddonStore(Path(td) / "custom")
        store.create(_payload())
        for payload in ({"name": ""}, {**_payload(), "soul_block": ""}):
            try:
                store.update("my-custom-addon", payload)
                raise AssertionError("expected invalid custom addon rejection")
            except CustomAddonError:
                pass
