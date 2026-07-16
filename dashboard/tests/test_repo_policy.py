"""Focused security, authorization, operation, and migration tests for GitHub policy."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_DASHBOARD = Path(__file__).resolve().parents[1]
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

from hapm.repo_policy import (  # noqa: E402
    GITHUB_ADDON_ID,
    RepositoryNotAllowedError,
    RepositoryPolicyError,
    add_repository,
    is_repository_allowed,
    list_repositories,
    reconcile_legacy_github_addons,
    replace_repositories,
    require_repository_allowed,
)
from hapm.toggle import enable_addon, list_active_addons  # noqa: E402
from hapm.builder_drafts import Draft  # noqa: E402
from hapm.builder_pr import BuilderPRError, open_addon_pr  # noqa: E402


KEY = base64.urlsafe_b64encode(b"0" * 32)
LEGACY_ID = "repository-scope"
REPOSITORY = "LouisKlimek/Hermes-Autonomy-Packet-Manager"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _addon(root: Path, addon_id: str, compatible: list[str], soul: str) -> Path:
    addon = root / addon_id
    _write(addon / "manifest.json", json.dumps({
        "id": addon_id, "name": addon_id, "description": "test", "version": "1.0.0",
        "contributes": {"soul_block": True, "skills": False},
        "compatible_profiles_or_presets": compatible,
    }))
    _write(addon / "soul_block.md", soul)
    return addon


def _state(root: Path) -> tuple[Path, Path, Path]:
    addons = root / "addons"
    profile = root / "profiles" / "fullstack-developer"
    _write(profile / "SOUL.md", "base\n")
    legacy = _addon(addons, LEGACY_ID, ["fullstack-developer"], f"Allowed: {REPOSITORY}\n")
    _addon(addons, GITHUB_ADDON_ID, ["fullstack-developer"], "central policy only\n")
    enable_addon(profile, legacy, target="fullstack-developer")
    return addons, profile, root / "policy.json"


def test_crud_validation_and_default_deny() -> None:
    with tempfile.TemporaryDirectory() as td:
        policy = Path(td) / "repo_allowlist.json"
        assert list_repositories(policy) == []
        assert add_repository(policy, REPOSITORY) == [REPOSITORY]
        try:
            add_repository(policy, "not a repository")
            raise AssertionError("invalid repository was accepted")
        except RepositoryPolicyError:
            pass


def test_policy_denies_disabled_profile_and_runtime_operation() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        addons, profile, policy = _state(root)
        replace_repositories(policy, [REPOSITORY])
        assert not is_repository_allowed(profile, policy, REPOSITORY)
        try:
            require_repository_allowed(profile, policy, REPOSITORY)
            raise AssertionError("disabled profile reached GitHub operation")
        except RepositoryNotAllowedError:
            pass
        enable_addon(profile, addons / GITHUB_ADDON_ID, target="fullstack-developer")
        assert require_repository_allowed(profile, policy, REPOSITORY) == REPOSITORY
        try:
            require_repository_allowed(profile, policy, "LouisKlimek/Denied")
            raise AssertionError("disallowed repository reached GitHub operation")
        except RepositoryNotAllowedError:
            pass


def test_builder_github_operation_is_denied_before_git_fetch() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        addons, profile, policy = _state(root)
        enable_addon(profile, addons / GITHUB_ADDON_ID, target="fullstack-developer")
        replace_repositories(policy, [REPOSITORY])
        repo = root / "denied-repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/LouisKlimek/Denied.git"], check=True, capture_output=True)
        draft = Draft(addon_id="test-addon", name="test", description="test", soul={"enabled": True, "body": "x"})
        try:
            open_addon_pr(draft, repo, profile_dir=profile, policy_path=policy, push=False)
            raise AssertionError("denied repository reached GitHub operation")
        except BuilderPRError as exc:
            assert "not authorized" in str(exc)


def test_reconciliation_migrates_explicit_inventory_with_encrypted_backup() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        addons, profile, policy = _state(root)
        result = reconcile_legacy_github_addons(policy, [profile], addons, [REPOSITORY], KEY)
        assert result["changed"]
        assert result["repositories"] == [REPOSITORY]
        assert result["inventory"] == [REPOSITORY]
        assert {item.addon_id for item in list_active_addons(profile)} == {GITHUB_ADDON_ID}
        backup = Path(result["backup_path"])
        assert backup.suffix == ".fernet"
        assert backup.stat().st_mode & 0o777 == 0o600
        assert REPOSITORY.encode() not in backup.read_bytes()
        assert not policy.with_suffix(".json.bak").exists()
        second = reconcile_legacy_github_addons(policy, [profile], addons, [REPOSITORY], KEY)
        assert not second["changed"]


def test_reconciliation_restores_policy_and_profile_on_failure() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        addons, profile, policy = _state(root)
        # Deliberately make unified activation fail after legacy state is removed.
        _addon(addons, GITHUB_ADDON_ID, ["other-profile"], "central policy only\n")
        before_soul = (profile / "SOUL.md").read_bytes()
        try:
            reconcile_legacy_github_addons(policy, [profile], addons, [REPOSITORY], KEY)
            raise AssertionError("migration unexpectedly succeeded")
        except RepositoryPolicyError:
            pass
        assert not policy.exists()
        assert (profile / "SOUL.md").read_bytes() == before_soul
        assert {item.addon_id for item in list_active_addons(profile)} == {LEGACY_ID}


def _run_all() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
