"""Tests for the shared Repository Scope editor backend."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from starlette.requests import Request

_DASHBOARD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DASHBOARD))

import plugin_api  # noqa: E402
from hapm.registry import load_addon  # noqa: E402
from hapm.repository_scope import render_soul_block  # noqa: E402
from hapm.toggle import enable_addon  # noqa: E402


def _body(result):
    if hasattr(result, "body"):
        return result.status_code, json.loads(result.body)
    return 200, result


def _request(actor: str | None = None) -> Request:
    request = Request({"type": "http", "headers": [], "method": "PUT", "path": "/"})
    if actor is not None:
        request.state.authenticated_user = actor
    return request


def _enable_scope(profile: Path) -> None:
    addon = load_addon(_DASHBOARD.parent / "addons" / "repository-scope")
    enable_addon(
        profile,
        addon,
        target="fullstack-developer",
        soul_block_content=render_soul_block(["Acme/Initial"]),
    )


def test_update_repository_scope_updates_every_active_profile(tmp_path: Path):
    home = tmp_path / "hermes"
    profiles = home / "profiles"
    first = profiles / "fullstack-developer"
    second = profiles / "other-profile"
    inactive = profiles / "inactive"
    for profile in (first, second, inactive):
        profile.mkdir(parents=True)
        (profile / "SOUL.md").write_text(f"base {profile.name}\n", encoding="utf-8")
    _enable_scope(first)
    _enable_scope(second)

    old_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home)
    try:
        status, body = _body(
            plugin_api.update_repository_scope(
                _request("ceo-orchestrator"),
                {"repositories": ["Acme/One", "Acme/Two"]}
            )
        )
        assert status == 200
        assert body == {
            "repositories": ["Acme/One", "Acme/Two"],
            "updated_profiles": ["fullstack-developer", "other-profile"],
        }
        for profile in (first, second):
            soul = (profile / "SOUL.md").read_text(encoding="utf-8")
            assert "`Acme/One`" in soul and "`Acme/Two`" in soul
            assert "`Acme/Initial`" not in soul
        assert (inactive / "SOUL.md").read_text(encoding="utf-8") == "base inactive\n"
        assert json.loads((home / "hapm_repository_scope.json").read_text()) == {
            "repositories": ["Acme/One", "Acme/Two"]
        }
    finally:
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home


def test_update_repository_scope_rejects_invalid_repository(tmp_path: Path):
    old_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(tmp_path)
    (tmp_path / "profiles").mkdir()
    try:
        status, body = _body(
            plugin_api.update_repository_scope(
                _request("ceo-orchestrator"), {"repositories": ["not a repo"]}
            )
        )
        assert status == 400
        assert body["error"] == "repository_scope_invalid"
    finally:
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home


def test_update_repository_scope_denies_unauthorized_without_mutating(tmp_path: Path):
    home = tmp_path / "hermes"
    profile = home / "profiles" / "fullstack-developer"
    profile.mkdir(parents=True)
    soul_path = profile / "SOUL.md"
    soul_path.write_text("base\n", encoding="utf-8")
    _enable_scope(profile)
    original_soul = soul_path.read_text(encoding="utf-8")

    old_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home)
    try:
        for actor in (None, "fullstack-developer"):
            status, body = _body(
                plugin_api.update_repository_scope(
                    _request(actor), {"repositories": ["Acme/Denied"]}
                )
            )
            assert status == 403
            assert body["error"] == "policy_admin_required"
            assert not (home / "hapm_repository_scope.json").exists()
            assert soul_path.read_text(encoding="utf-8") == original_soul
    finally:
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home


def test_new_repository_scope_activation_uses_shared_setting(tmp_path: Path):
    home = tmp_path / "hermes"
    profile = home / "profiles" / "fullstack-developer"
    profile.mkdir(parents=True)
    (profile / "SOUL.md").write_text("base\n", encoding="utf-8")
    (home / "hapm_repository_scope.json").write_text(
        json.dumps({"repositories": ["Acme/Shared"]}), encoding="utf-8"
    )

    old_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home)
    try:
        result = plugin_api.enable_addon_route(
            {"profile": "fullstack-developer", "addon": "repository-scope"}
        )
        status, body = _body(result)
        assert status == 200 and body["enabled"] is True
        assert "`Acme/Shared`" in (profile / "SOUL.md").read_text(encoding="utf-8")
    finally:
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home
