"""Focused tests for the canonical GitHub repository policy."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_DASHBOARD = Path(__file__).resolve().parents[1]
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

from hapm.repo_policy import (  # noqa: E402
    GITHUB_ADDON_ID,
    RepositoryPolicyError,
    add_repository,
    is_repository_allowed,
    list_repositories,
    migrate_legacy_allowlists,
    remove_repository,
    replace_repositories,
)
from hapm.registry import compatible_addons  # noqa: E402
from hapm.toggle import enable_addon  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _github_addon(root: Path) -> Path:
    addon = root / GITHUB_ADDON_ID
    _write(addon / "manifest.json", json.dumps({
        "id": GITHUB_ADDON_ID, "name": "GitHub Agent", "description": "test",
        "version": "1.0.0", "contributes": {"soul_block": True, "skills": False},
        "compatible_profiles_or_presets": ["enabled"],
    }))
    _write(addon / "soul_block.md", "central policy only\n")
    return addon


def test_crud_validation_and_default_deny() -> None:
    with tempfile.TemporaryDirectory() as td:
        policy = Path(td) / "repo_allowlist.json"
        assert list_repositories(policy) == []
        assert add_repository(policy, "LouisKlimek/Hermes-Autonomy-Packet-Manager") == ["LouisKlimek/Hermes-Autonomy-Packet-Manager"]
        assert remove_repository(policy, "LouisKlimek/Hermes-Autonomy-Packet-Manager") == []
        try:
            add_repository(policy, "not a repository")
            raise AssertionError("invalid repository was accepted")
        except RepositoryPolicyError:
            pass


def test_policy_requires_the_unified_addon() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        policy = root / "policy.json"
        replace_repositories(policy, ["LouisKlimek/Hermes-Autonomy-Packet-Manager"])
        disabled = root / "profiles" / "disabled"
        enabled = root / "profiles" / "enabled"
        _write(disabled / "SOUL.md", "base\n")
        _write(enabled / "SOUL.md", "base\n")
        addon = _github_addon(root / "addons")
        assert not is_repository_allowed(disabled, policy, "LouisKlimek/Hermes-Autonomy-Packet-Manager")
        enable_addon(enabled, addon, target="enabled")
        assert is_repository_allowed(enabled, policy, "LouisKlimek/Hermes-Autonomy-Packet-Manager")
        assert not is_repository_allowed(enabled, policy, "LouisKlimek/Other")


def test_github_agent_is_compatible_with_all_approved_profiles() -> None:
    registry = Path(__file__).resolve().parents[2] / "addons"
    for profile in ("ceo-orchestrator", "fullstack-developer", "pr-reviewer", "github-manager"):
        ids = {addon.id for addon in compatible_addons(registry, profile)}
        assert GITHUB_ADDON_ID in ids, profile


def test_migration_is_idempotent_and_keeps_rollback_backup() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        policy = root / "repo_allowlist.json"
        replace_repositories(policy, ["Example/Existing"])
        before = policy.read_bytes()
        legacy = root / "legacy.md"
        _write(legacy, "Allowed: `LouisKlimek/Hermes-Autonomy-Packet-Manager`\n")
        first = migrate_legacy_allowlists(policy, [legacy])
        assert first["changed"]
        assert policy.with_suffix(".json.bak").read_bytes() == before
        second = migrate_legacy_allowlists(policy, [legacy])
        assert not second["changed"]
        assert first["repositories"] == second["repositories"]


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
