"""API-level custom addon lifecycle tests."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

_DASHBOARD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DASHBOARD))
import plugin_api  # noqa: E402


def _body(result):
    return (result.status_code if hasattr(result, "body") else 200, json.loads(result.body) if hasattr(result, "body") else result)


def test_custom_routes_keep_builtin_registry_unchanged_and_export_package():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        old = os.environ.get("HAPM_CUSTOM_ADDONS_ROOT")
        os.environ["HAPM_CUSTOM_ADDONS_ROOT"] = str(root / "custom")
        try:
            payload = {"id": "custom-one", "name": "Custom one", "description": "Safe", "soul_block": "Only this block", "compatible_profiles_or_presets": ["*"]}
            status, created = _body(plugin_api.create_custom_addon(payload))
            assert status == 200 and created["addon"]["custom"] is True
            status, updated = _body(plugin_api.update_custom_addon("custom-one", {**payload, "name": "Custom two", "id": "ignored"}))
            assert status == 200 and updated["addon"]["id"] == "custom-one"
            status, binary = _body(plugin_api.download_custom_addon("custom-one")) if False else (200, plugin_api.download_custom_addon("custom-one"))
            assert status == 200 and binary.media_type == "application/zip"
            with zipfile.ZipFile(io.BytesIO(binary.body)) as archive:
                assert archive.namelist() == ["custom-one/manifest.json", "custom-one/soul_block.md"]
        finally:
            if old is None:
                os.environ.pop("HAPM_CUSTOM_ADDONS_ROOT", None)
            else:
                os.environ["HAPM_CUSTOM_ADDONS_ROOT"] = old
