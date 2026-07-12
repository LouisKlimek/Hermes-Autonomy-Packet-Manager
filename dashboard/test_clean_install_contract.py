"""Clean-install regression coverage for HAPM dashboard registration.

Hermes discovers a user dashboard plugin at
``$HERMES_HOME/plugins/<name>/dashboard/manifest.json`` and mounts the manifest
``api`` router at ``/api/plugins/<name>``.  This test stages the repository as
that clean install, rather than importing it from the checkout, so a missing
root manifest, dashboard manifest, API file, or router would fail before the
profiles UI could regress to ``404 Plugin not found`` again.

Run with: ``python dashboard/test_clean_install_contract.py``
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_staged_api(plugin_root: Path):
    """Load the staged dashboard API exactly as the dashboard loader does."""
    manifest = plugin_root / "dashboard" / "manifest.json"
    assert manifest.is_file()
    api_file = plugin_root / "dashboard" / "plugin_api.py"
    assert api_file.is_file()

    dashboard_dir = str(api_file.parent)
    sys.path.insert(0, dashboard_dir)
    try:
        spec = importlib.util.spec_from_file_location("staged_hapm_plugin_api", api_file)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(dashboard_dir)


def test_clean_install_registers_profiles_route(tmp_path: Path):
    """A staged user install mounts GET /api/plugins/hapm/profiles successfully."""
    hermes_home = tmp_path / "hermes-home"
    plugin_root = hermes_home / "plugins" / "hapm"
    shutil.copytree(REPO_ROOT, plugin_root, ignore=shutil.ignore_patterns(".git"))

    # Full agent-plugin contract: the root artifacts are required by Hermes'
    # plugin loader; the nested dashboard manifest declares the backend API.
    assert (plugin_root / "plugin.yaml").is_file()
    assert (plugin_root / "__init__.py").is_file()
    assert (plugin_root / "dashboard" / "manifest.json").is_file()

    profiles = hermes_home / "profiles"
    (profiles / "clean-profile").mkdir(parents=True)
    saved_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(hermes_home)
    try:
        module = _load_staged_api(plugin_root)
        app = FastAPI()
        app.include_router(module.router, prefix="/api/plugins/hapm")

        # Router internals vary across supported FastAPI/Starlette versions
        # (some represent included routers as a mounted route).  Exercising the
        # public URL is the stable registration contract: a missing mount would
        # return the dashboard's 404 instead of this successful response.
        response = TestClient(app).get("/api/plugins/hapm/profiles")
        assert response.status_code == 200
        # The exact HTTP request returns only profile names and paths — never
        # profile configuration or SOUL contents.
        assert response.json() == {
            "profiles_dir": str(profiles),
            "profiles": [
                {"name": "clean-profile", "path": str(profiles / "clean-profile")}
            ],
        }
    finally:
        sys.modules.pop("staged_hapm_plugin_api", None)
        sys.modules.pop("hapm", None)
        if saved_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved_home


def _run_standalone() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        test_clean_install_registers_profiles_route(Path(directory))
    print("PASS test_clean_install_registers_profiles_route")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
