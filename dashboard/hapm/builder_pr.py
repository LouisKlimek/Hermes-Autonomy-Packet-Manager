"""PR-based activation path for the v1.2 In-UI Addon Builder (Requirement 2, §5).

Implements the "PR Required to Activate" half of the spec's hybrid write target.
A draft becomes a *candidate* community addon only by opening a Pull Request that
adds it to the shared ``addons/`` registry. This module:

1. Runs the **final, non-overridable** :func:`hapm.builder_sanitize.check_addon`
   pass (§4.2: "a final check before PR creation"). If it fails, nothing is
   written and no branch/PR is created.
2. Materializes the draft into exactly the files enumerated by
   :func:`hapm.builder_sanitize.enumerate_targets` — ``addons/<id>/manifest.json``,
   optional ``addons/<id>/soul_block.md``, optional
   ``addons/<id>/skills/hapm-addon-<id>/SKILL.md`` — and nothing else. Every
   intended write path is re-checked with :func:`assert_target_allowed` so a
   forced/free path is rejected server-side regardless of client (acceptance
   criterion #1).
3. Emits the *same* manifest schema core addons use (FR-7): ``author`` and
   ``origin`` are written as audit-only metadata under ``_provenance`` and do
   **not** change any activation code path.
4. Opens the PR through a service account that may only **create a branch and
   open a PR** — it never pushes to ``main`` and never auto-merges (§5). Merge
   stays human / pr-reviewer driven.

The materialization itself is filesystem-pure and unit-testable
(:func:`materialize_addon`); the git/GitHub side (:func:`open_addon_pr`) is a
thin, side-effecting wrapper that shells out to ``git`` and the GitHub REST API
and is exercised by the endpoint / integration path, not the pure unit tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .builder_drafts import Draft
from .builder_sanitize import (
    SanitizeError,
    SanitizeResult,
    assert_target_allowed,
    check_addon,
    enumerate_targets,
    skill_dir_name,
    validate_curated_skill,
    validate_inline_skill,
)

# §5: the branch the service account creates for each community addon PR. The
# id is unique per addon so re-submitting updates the same PR branch.
BRANCH_PREFIX = "hapm/community-addon/"

# Provenance tag written into every builder-authored manifest. Audit-only.
ORIGIN_TAG = "community-builder"

# Protected branches the service account may NEVER push to (§5). Enforced here
# as defense-in-depth in addition to the account's server-side branch
# protection: a push target intersecting this set is refused before any git op.
PROTECTED_BRANCHES = frozenset({"main", "master", "production", "release"})

GITHUB_API = "https://api.github.com"


class BuilderPRError(Exception):
    """Raised when materialization or PR creation cannot proceed safely."""


# ---------------------------------------------------------------------------
# Pure materialization (filesystem-only, unit-testable)
# ---------------------------------------------------------------------------


def build_manifest(draft: Draft) -> dict:
    """Compose the community addon ``manifest.json`` (same schema as core).

    ``contributes`` is derived from which parts the draft enables. FR-7: one
    manifest schema for core + community; the only difference is the audit-only
    ``_provenance`` block, which no code path may branch on.
    """
    soul_on = bool((draft.soul or {}).get("enabled"))
    skill_on = bool((draft.skill or {}).get("enabled"))
    if not (soul_on or skill_on):
        raise BuilderPRError(
            f"draft {draft.addon_id!r} contributes neither soul_block nor skills"
        )
    return {
        "id": draft.addon_id,
        "name": draft.name,
        "description": draft.description,
        "version": "1.0.0",
        "contributes": {"soul_block": soul_on, "skills": skill_on},
        # Community builder addons are, by default, only compatible with the
        # authoring surface. A repo maintainer can widen this in review; the
        # builder never lets a contributor self-grant ``"*"``.
        "compatible_profiles_or_presets": [],
        # FR-7 audit metadata ONLY — never read by activation/toggle code.
        "_provenance": {
            "author": draft.author,
            "origin": draft.origin or ORIGIN_TAG,
            "built_with": "hapm-v1.2-addon-builder",
        },
    }


def planned_files(draft: Draft) -> dict[str, str]:
    """Return ``{repo_relative_path: file_content}`` for a draft.

    The set of paths is exactly :func:`enumerate_targets`; every one is
    re-checked with :func:`assert_target_allowed` so nothing outside the fixed
    enumeration can ever be produced, even if a caller mutated the draft.
    """
    soul_on = bool((draft.soul or {}).get("enabled"))
    skill_on = bool((draft.skill or {}).get("enabled"))
    allowed = set(enumerate_targets(draft.addon_id, soul_on, skill_on))

    files: dict[str, str] = {}

    manifest_path = f"addons/{draft.addon_id}/manifest.json"
    assert_target_allowed(draft.addon_id, manifest_path, soul_on, skill_on)
    files[manifest_path] = (
        json.dumps(build_manifest(draft), indent=2, ensure_ascii=False) + "\n"
    )

    if soul_on:
        soul_path = f"addons/{draft.addon_id}/soul_block.md"
        assert_target_allowed(draft.addon_id, soul_path, soul_on, skill_on)
        # Store the raw body only — the FR-7 engine adds the markers at apply
        # time (same as core soul_block.md files).
        files[soul_path] = str((draft.soul or {}).get("body", "")).rstrip("\n") + "\n"

    if skill_on:
        skill = draft.skill or {}
        source = str(skill.get("source", "inline"))
        skill_path = (
            f"addons/{draft.addon_id}/skills/"
            f"{skill_dir_name(draft.addon_id)}/SKILL.md"
        )
        assert_target_allowed(draft.addon_id, skill_path, soul_on, skill_on)
        if source == "curated-list-ref":
            # Curated selections are validated here too; the actual body is a
            # short reference stub pointing at the curated source (the real
            # content lands via the maintained curated skill, not user text).
            validate_curated_skill(str(skill.get("source_ref", "")))
            files[skill_path] = (
                f"<!-- curated:{skill.get('source_ref')} -->\n"
                f"{str(skill.get('body', '')).rstrip(chr(10))}\n"
            )
        else:
            # Structural re-check (raises on smuggled extra-file keys).
            validate_inline_skill(skill)
            files[skill_path] = str(skill.get("body", "")).rstrip("\n") + "\n"

    # Final invariant: never emit a path outside the fixed enumeration.
    extra = set(files) - allowed
    if extra:
        raise BuilderPRError(f"internal error: produced disallowed paths {sorted(extra)}")
    return files


def materialize_addon(draft: Draft, repo_root: str | Path) -> list[str]:
    """Write a draft's files into ``repo_root`` under ``addons/<id>/``.

    Runs the final sanitize gate first; on any violation nothing is written and
    a :class:`SanitizeError` is raised carrying the blocking messages. Returns
    the list of repo-relative paths written (sorted, deterministic).
    """
    gate: SanitizeResult = check_addon(draft.to_dict())
    if not gate.ok:
        raise SanitizeError(
            "draft failed the final sanitizing check; not writing any files: "
            + "; ".join(v.message() for v in gate.violations)
        )

    root = Path(repo_root)
    files = planned_files(draft)
    written: list[str] = []
    for rel, content in sorted(files.items()):
        # Re-assert allowed target against the *resolved* path — belt and
        # suspenders against traversal even after enumeration.
        soul_on = bool((draft.soul or {}).get("enabled"))
        skill_on = bool((draft.skill or {}).get("enabled"))
        assert_target_allowed(draft.addon_id, rel, soul_on, skill_on)
        dest = (root / rel).resolve()
        if not str(dest).startswith(str(root.resolve()) + os.sep):
            raise BuilderPRError(f"resolved path escapes repo root: {rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(rel)
    return written


# ---------------------------------------------------------------------------
# git + GitHub PR side (side-effecting; service-account constrained)
# ---------------------------------------------------------------------------


@dataclass
class PRResult:
    branch: str
    pr_url: str
    pr_number: int
    head_sha: str
    files: list[str]


def branch_for(addon_id: str) -> str:
    """Deterministic PR branch name for an addon id (re-submits update it)."""
    return f"{BRANCH_PREFIX}{addon_id}"


def _assert_not_protected(branch: str) -> None:
    """Refuse any operation that would target a protected branch (§5)."""
    if branch in PROTECTED_BRANCHES:
        raise BuilderPRError(
            f"service account may not push to protected branch {branch!r}; "
            "community addons land via PR only"
        )


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise BuilderPRError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def _github_token(repo_root: Path) -> str:
    """Resolve a GitHub token for the PR REST call.

    Order: ``$HAPM_GH_TOKEN`` / ``$GITHUB_TOKEN`` / ``$GH_TOKEN``, else the
    password stored by the git credential helper for github.com. Never logged.
    """
    for var in ("HAPM_GH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    raise BuilderPRError("no GitHub token available for opening the addon PR")


def _repo_slug(repo_root: Path) -> str:
    url = _git(repo_root, "remote", "get-url", "origin")
    # Normalize https/ssh/@token forms to owner/repo.
    slug = url.rstrip("/")
    if slug.endswith(".git"):
        slug = slug[: -len(".git")]
    if slug.startswith("git@"):
        slug = slug.split(":", 1)[1]
    else:
        slug = slug.split("github.com/", 1)[-1]
        slug = slug.split("@github.com/", 1)[-1]
    return slug


def _open_pr_via_api(
    repo_root: Path,
    slug: str,
    branch: str,
    base: str,
    title: str,
    body: str,
) -> tuple[str, int]:
    token = _github_token(repo_root)
    payload = json.dumps(
        {"title": title, "head": branch, "base": base, "body": body}
    ).encode()
    req = urllib.request.Request(
        f"{GITHUB_API}/repos/{slug}/pulls",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "hapm-addon-builder",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data["html_url"], int(data["number"])
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        # A 422 "A pull request already exists" is benign on re-submit; surface
        # the existing PR by querying it.
        if exc.code == 422 and "already exists" in detail:
            existing = _find_existing_pr(repo_root, slug, branch, base, token)
            if existing:
                return existing
        raise BuilderPRError(
            f"GitHub PR creation failed ({exc.code}): {detail[:300]}"
        ) from exc


def _find_existing_pr(
    repo_root: Path, slug: str, branch: str, base: str, token: str
) -> tuple[str, int] | None:
    owner = slug.split("/", 1)[0]
    req = urllib.request.Request(
        f"{GITHUB_API}/repos/{slug}/pulls?head={owner}:{branch}&base={base}&state=open",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "hapm-addon-builder",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        items = json.loads(resp.read().decode())
    if items:
        return items[0]["html_url"], int(items[0]["number"])
    return None


def open_addon_pr(
    draft: Draft,
    repo_root: str | Path,
    *,
    base: str = "main",
    push: bool = True,
) -> PRResult:
    """Materialize the draft and open (or update) its community-addon PR.

    Side-effecting. The service account only ever: creates/updates the addon's
    dedicated branch and opens a PR against ``base``. It never pushes to a
    protected branch and never merges. On any sanitize failure nothing is
    written or pushed.
    """
    root = Path(repo_root)
    branch = branch_for(draft.addon_id)
    _assert_not_protected(branch)
    # NOTE: ``base`` (the PR target, typically ``main``) is deliberately NOT
    # passed through _assert_not_protected — we open a PR *against* it, we never
    # push to it. The push target below is always the addon branch.

    # Create/reset the addon branch from the base without touching base itself.
    _git(root, "fetch", "origin", base)
    _git(root, "checkout", "-B", branch, f"origin/{base}")

    files = materialize_addon(draft, root)
    for rel in files:
        _git(root, "add", rel)

    title = f"HAPM community addon: {draft.name} ({draft.addon_id})"
    _git(
        root, "-c", f"user.name={draft.author or 'hapm-builder'}",
        "-c", "user.email=hapm-bot@users.noreply.github.com",
        "commit", "-m", title,
    )
    head_sha = _git(root, "rev-parse", "HEAD")

    if push:
        # force-with-lease so a re-submit updates the same branch safely; the
        # target is the addon branch, never a protected branch.
        _git(root, "push", "--force-with-lease", "origin", f"{branch}:{branch}")

    slug = _repo_slug(root)
    body = _pr_body(draft, head_sha)
    pr_url, pr_number = _open_pr_via_api(root, slug, branch, base, title, body)
    return PRResult(
        branch=branch,
        pr_url=pr_url,
        pr_number=pr_number,
        head_sha=head_sha,
        files=files,
    )


def _pr_body(draft: Draft, head_sha: str) -> str:
    return (
        f"Community addon `{draft.addon_id}` authored via the HAPM v1.2 "
        f"In-UI Addon Builder.\n\n"
        f"- Author (audit-only): `{draft.author}`\n"
        f"- Origin (audit-only): `{draft.origin or ORIGIN_TAG}`\n"
        f"- Head: `{head_sha}`\n\n"
        "This addon uses the identical manifest schema, marker convention and "
        "toggle code path as core addons (FR-7). It is inert until this PR is "
        "merged by a human / pr-reviewer — the builder cannot self-activate it, "
        "and the service account has no auto-merge rights on the base branch.\n\n"
        f"Built at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}.\n"
    )
