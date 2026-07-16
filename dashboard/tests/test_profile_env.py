"""Focused security and onboarding coverage for profile-local environment editing."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from starlette.requests import Request

_DASHBOARD = Path(__file__).resolve().parents[1]
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

import plugin_api  # noqa: E402
from hapm import profile_env  # noqa: E402


class _Cipher:
    """Test-only reversible cipher; production fails closed without Fernet."""
    def encrypt(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)

    def decrypt(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)


def _request(actor: str | None = None) -> Request:
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    if actor:
        request.state.authenticated_user = actor
    return request


def _body(result):
    return json.loads(result.body) if hasattr(result, "body") else result


def _home() -> tuple[tempfile.TemporaryDirectory, Path, Path]:
    td = tempfile.TemporaryDirectory()
    home = Path(td.name)
    profile = home / "profiles" / "fullstack-developer"
    profile.mkdir(parents=True)
    return td, home, profile


def test_status_masks_token_and_is_live() -> None:
    td, home, profile = _home()
    old = os.environ.get("HERMES_HOME")
    try:
        os.environ["HERMES_HOME"] = str(home)
        (profile / ".env").write_text("GH_TOKEN=synthetic-token\nPUBLIC=value\n")
        response = _body(plugin_api.profile_environment("fullstack-developer", _request("ceo-orchestrator")))
        encoded = json.dumps(response)
        assert "synthetic-token" not in encoded and "value" not in encoded
        assert response["github_token_present"] is True
        assert {field["key"] for field in response["fields"]} == {"GH_TOKEN", "PUBLIC"}
        (profile / ".env").write_text("PUBLIC=changed\n")
        assert _body(plugin_api.profile_environment("fullstack-developer", _request("ceo-orchestrator")))["github_token_present"] is False
    finally:
        if old is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = old
        td.cleanup()


def test_authorization_and_atomic_rollback(monkeypatch) -> None:
    monkeypatch.setattr(profile_env, "_fernet", lambda: _Cipher())
    td, home, profile = _home()
    old = os.environ.get("HERMES_HOME")
    try:
        os.environ["HERMES_HOME"] = str(home)
        (profile / ".env").write_text("UNCHANGED=original\nGH_TOKEN=synthetic-token\n")
        denied = plugin_api.update_profile_environment("fullstack-developer", _request("fullstack-developer"), {"values": {"PUBLIC": "yes"}})
        assert denied.status_code == 403
        response = _body(plugin_api.update_profile_environment("fullstack-developer", _request("ceo-orchestrator"), {"values": {"GH_TOKEN": "", "PUBLIC": "yes"}}))
        assert "synthetic-token" not in json.dumps(response)
        assert (profile / ".env").read_text() == "UNCHANGED=original\nGH_TOKEN=\nPUBLIC=yes\n"
        assert (profile / ".env").stat().st_mode & 0o777 == 0o600
        backup = home / "hapm_env" / "backups" / f"{response['backup_id']}.fernet"
        assert b"synthetic-token" not in backup.read_bytes()
        restored = _body(plugin_api.rollback_profile_environment("fullstack-developer", _request("ceo-orchestrator"), {"backup_id": response["backup_id"]}))
        assert restored["github_token_present"] is True
        assert (profile / ".env").read_text() == "UNCHANGED=original\nGH_TOKEN=synthetic-token\n"
        audit = next((home / "hapm_env" / "audit").glob("*.json")).read_text()
        assert "synthetic-token" not in audit and "GH_TOKEN" in audit
    finally:
        if old is None: os.environ.pop("HERMES_HOME", None)
        else: os.environ["HERMES_HOME"] = old
        td.cleanup()


def test_github_onboarding_does_not_overwrite_existing_identity(monkeypatch) -> None:
    monkeypatch.setattr(profile_env, "_fernet", lambda: _Cipher())
    td, home, profile = _home()
    try:
        (profile / ".env").write_text("GIT_AUTHOR_NAME=Existing Name\nGH_TOKEN=synthetic-token\n")
        result = profile_env.initialize_github(profile, home, "fullstack-developer", "ceo-orchestrator")
        text = (profile / ".env").read_text()
        assert "GIT_AUTHOR_NAME=Existing Name\n" in text
        assert set(result["initialized"]) == {"GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}
        assert result["github_token_present"] is True
        assert "synthetic-token" not in json.dumps(result)
    finally:
        td.cleanup()


def _run_all() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    class MonkeyPatch:
        def setattr(self, target, name, value): setattr(target, name, value)
    for test in tests:
        if "monkeypatch" in test.__code__.co_varnames: test(MonkeyPatch())
        else: test()
        print(f"PASS {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
