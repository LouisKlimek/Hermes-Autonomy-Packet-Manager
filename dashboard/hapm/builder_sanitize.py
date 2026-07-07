"""Server-side sanitizing + structural whitelist for the v1.2 In-UI Addon Builder.

This module implements Requirement 1 of ``HAPM_V1_2_ADDON_BUILDER_SPEC.md``
(§4). It is the **security boundary** for user-authored addon content: the
client's live check is only UX convenience — enforcement is exclusively
server-side and lives here. Both the draft-save path and the PR-creation path
(§4.2: "runs synchronously on every draft save AND as a final, non-overridable
check before PR creation") call :func:`check_content` / :func:`check_addon`.

What it enforces
----------------
* §4.1 Structural whitelist: the builder never chooses a free file path. The
  only two write targets are derived from the generated ``addon_id``:
    1. exactly ONE SOUL.md marked block ``<!-- HAPM:addon:{id} START/END -->``
       under a fixed ``## Addon: {Name}`` heading, and
    2. exactly ONE skill dir ``skills/hapm-addon-{id}/SKILL.md``.
  :func:`enumerate_targets` returns exactly those two paths; anything else is
  rejected by :func:`assert_target_allowed` regardless of the client.
* §4.1 Forbidden SOUL.md sections: the builder may never create/reference the
  reserved core/preset headings, nor wrap another addon's / a preset's marker.
* §4.2 Seven blocking deny-pattern rules (secrets, forbidden config keys,
  exfiltration/bypass phrasing, path/env refs, executable code/shell, size
  limit, HTML/script tags). Every hit is a blocking, **non-overridable**
  error carrying the rule name + 1-based line number, matching the copy-string
  ``builder.error.sanitizing`` ("... {RULE}, line {N} ...").
* §4.3 Skill source whitelist: inline markdown only (no scripts/references/
  assets subfolders, no file smuggling); the same §4.2 linter applies 1:1 to
  the skill body; curated-list selections are restricted to a fixed set.

Nothing here touches the network or a real profile; it is a pure text/dict
validator so it is unit-testable in isolation and cannot be bypassed by any
client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# ---------------------------------------------------------------------------
# Limits & fixed vocabulary (spec §4)
# ---------------------------------------------------------------------------

# §4.2 rule 6 / §Screen step 3: the editable SOUL.md block body is capped.
SOUL_BLOCK_MAX_CHARS = 4000
# §Screen step 1: metadata field caps.
NAME_MAX_CHARS = 60
DESCRIPTION_MAX_CHARS = 200

# §4.3: every builder-authored skill file is namespaced with this prefix so it
# can never collide with a pre-existing same-name skill (a prerequisite for the
# FR-7 backup/restore guarantee).
SKILL_NAME_PREFIX = "hapm-addon-"

ADDON_ID_PREFIX = "community-"

# §4.1 Forbidden SOUL.md sections — the builder must never create, reference,
# or wrap these headings/markers (reserved for presets/core). Matched
# case-insensitively at the start of a markdown heading line.
FORBIDDEN_SOUL_HEADINGS = (
    "# Identity",
    "## Role Boundaries",
    "## Non-Goals",
    "## Operating Principles",
    "## Escalation",
    "## Current Mode",
)

# §4.3 curated skill sources whitelist. Deliberately a fixed, repo-maintained
# list (no free-text GitHub URL). Kept small and explicit for v1.2 — extend
# only on a concrete, vetted need. Entries reference skills that already ship in
# this repo / are explicitly approved.
CURATED_SKILL_SOURCES = (
    "yagni",
)


class SanitizeError(Exception):
    """Raised on a structural whitelist violation (never client-overridable)."""


@dataclass
class Violation:
    """One blocking sanitizing hit.

    Attributes:
        rule: Human-readable rule name (goes into ``builder.error.sanitizing``).
        line: 1-based line number of the offending line, or 0 for whole-content
            rules (e.g. the size limit) that have no single line.
        detail: Short machine/debug detail (the matched token/phrase).
        field: Which input the hit came from ("soul" | "skill" | ...), so the
            UI can point at the right editor.
    """

    rule: str
    line: int
    detail: str = ""
    field: str = ""

    def message(self) -> str:
        """Render the exact ``builder.error.sanitizing`` copy string."""
        return (
            f"This content cannot be saved: {self.rule}, line {self.line}. "
            "Remove the flagged section to continue."
        )

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "line": self.line,
            "detail": self.detail,
            "field": self.field,
            "message": self.message(),
        }


@dataclass
class SanitizeResult:
    """Aggregate result of a sanitizing pass.

    ``ok`` is true only when there are zero violations. The builder blocks the
    save/submit whenever ``ok`` is false — there is no override path.
    """

    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "violations": [v.to_dict() for v in self.violations],
        }


# ---------------------------------------------------------------------------
# §4.2 deny-pattern rules
#
# Each rule is (name, compiled-regex). Rules are applied line-by-line so the
# error can name the exact line (rule 6, the size limit, is whole-content and
# handled separately). The starting list here is the spec's floor — do NOT
# ship with fewer protections (task open item #1). Additional HAPM-specific
# entries may be appended, never removed, without renewed security review.
# ---------------------------------------------------------------------------

# Rule 1 — Secret / credential patterns.
_RULE1_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                       # OpenAI-style key
    re.compile(r"AKIA[0-9A-Z]{16}"),                          # AWS access key id
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                # GitHub token
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),              # Slack token
    # Literal credential-ish terms.
    re.compile(
        r"\b(?:api[_-]?key|secret|token|password|passwd|credential)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\.env\b"),
    re.compile(r"~/\.ssh\b"),
    re.compile(r"\bid_rsa\b"),
]

# Rule 2 — Forbidden config keys (taken 1:1 from OQ-2). Matched as tokens so a
# phrase like "read telegram.bot_token from config.yaml" is caught.
_RULE2_PATTERNS = [
    re.compile(r"\bmodel\.provider\b", re.IGNORECASE),
    re.compile(r"\bmodel\.base_url\b", re.IGNORECASE),
    re.compile(r"\bmodel\.api_key\b", re.IGNORECASE),
    re.compile(r"\b[\w.*]*\.api_key\b", re.IGNORECASE),        # *.api_key
    re.compile(r"\bsecurity\.[\w.*]+", re.IGNORECASE),         # security.*
    re.compile(r"\bweb\.[\w.*]+", re.IGNORECASE),              # web.* keys
    re.compile(r"\bterminal\.[\w.*]+", re.IGNORECASE),         # terminal.*
    re.compile(r"\bdashboard\.[\w.*]+", re.IGNORECASE),        # dashboard.*
    # Platform tokens / config namespaces.
    re.compile(
        r"\b(?:telegram|discord|slack|matrix|mattermost|whatsapp)\."
        r"[\w.*]*(?:token|key|secret|webhook|bot)[\w.*]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:telegram|discord|slack|matrix|mattermost|whatsapp)"
        r"[._](?:bot_token|token|api_key|webhook)\b",
        re.IGNORECASE,
    ),
]

# Rule 3 — Exfiltration / bypass instructions. Conservative deny list; false
# positives yield a clear, actionable error (never a silent fail).
_RULE3_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"ignore\s+(?:the\s+)?(?:above|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"\bsend\b[^.\n]{0,60}\bto\b\s+\S+", re.IGNORECASE),
    re.compile(r"\bpost\b[^.\n]{0,40}\bpublicly\b", re.IGNORECASE),
    re.compile(r"\bexfiltrat", re.IGNORECASE),
    re.compile(r"disable\s+confirmation", re.IGNORECASE),
    re.compile(r"without\s+asking", re.IGNORECASE),
    re.compile(r"auto[-\s]?approve", re.IGNORECASE),
    re.compile(r"approve\s+automatically", re.IGNORECASE),
    re.compile(r"override\s+safety", re.IGNORECASE),
    re.compile(r"\bbypass\b", re.IGNORECASE),
    re.compile(r"disable\s+(?:the\s+)?(?:safety|guard|guardrail|security)", re.IGNORECASE),
]

# Rule 4 — Path / environment references outside the addon's own profile.
_RULE4_PATTERNS = [
    re.compile(r"\$HERMES_HOME\b"),
    re.compile(r"\$HERMES_[A-Z_]+"),
    re.compile(r"\.\./"),                                      # parent traversal
    re.compile(r"(?<![\w./])/(?:etc|root|home|usr|var|opt|bin|sys|proc)(?:/|\b)"),
    re.compile(r"\bos\.environ\b"),
    re.compile(r"\bprocess\.env\b"),
    re.compile(r"~/(?!$)"),                                    # any home ref
]

# Rule 5 — Executable code / shell. Fenced code blocks with execution intent
# and raw shell commands. SOUL.md is plain prompt text.
_RULE5_FENCE_LANG = re.compile(r"^\s*```+\s*(bash|sh|shell|zsh|python|py|ruby|perl|node|js|javascript)\b", re.IGNORECASE)
_RULE5_PATTERNS = [
    re.compile(r"\bsubprocess\b"),
    re.compile(r"\bos\.system\b"),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"(?<![\w-])curl\s+\S", re.IGNORECASE),
    re.compile(r"(?<![\w-])wget\s+\S", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"(?<![\w-])chmod\s+\S"),
]

# Rule 7 — HTML / script tags.
_RULE7_PATTERNS = [
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"<iframe\b", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
]

_RULE_NAMES = {
    1: "Secret/credential pattern",
    2: "Forbidden config key",
    3: "Exfiltration/bypass instruction",
    4: "Path/environment reference",
    5: "Executable code/shell",
    6: "Size limit exceeded",
    7: "HTML/script tag",
}


def _scan_line_rules(line: str, lineno: int, field_name: str) -> list[Violation]:
    """Apply the per-line deny-pattern rules (1–5, 7) to a single line."""
    out: list[Violation] = []

    def hit(rule_no: int, m: re.Match) -> None:
        out.append(
            Violation(
                rule=_RULE_NAMES[rule_no],
                line=lineno,
                detail=m.group(0)[:80],
                field=field_name,
            )
        )

    for pat in _RULE1_PATTERNS:
        m = pat.search(line)
        if m:
            hit(1, m)
            break
    for pat in _RULE2_PATTERNS:
        m = pat.search(line)
        if m:
            hit(2, m)
            break
    for pat in _RULE3_PATTERNS:
        m = pat.search(line)
        if m:
            hit(3, m)
            break
    for pat in _RULE4_PATTERNS:
        m = pat.search(line)
        if m:
            hit(4, m)
            break
    if _RULE5_FENCE_LANG.search(line):
        out.append(
            Violation(
                rule=_RULE_NAMES[5], line=lineno,
                detail=line.strip()[:80], field=field_name,
            )
        )
    else:
        for pat in _RULE5_PATTERNS:
            m = pat.search(line)
            if m:
                hit(5, m)
                break
    for pat in _RULE7_PATTERNS:
        m = pat.search(line)
        if m:
            hit(7, m)
            break
    return out


def check_content(
    text: str,
    field_name: str = "soul",
    max_chars: int = SOUL_BLOCK_MAX_CHARS,
) -> SanitizeResult:
    """Run the §4.2 deny-pattern linter over free-text markdown content.

    Applies rules 1–5 and 7 line-by-line (so each hit names its 1-based line)
    and rule 6 (size limit) over the whole content. Used for both the SOUL.md
    block body and, per §4.3, the inline skill body (same linter 1:1).

    ``text`` is the *editable body only* — never include the fixed marker lines
    or the ``## Addon:`` heading (those are structural, added by the server).
    """
    result = SanitizeResult()

    # Rule 6 — size limit (whole content, no single line).
    if len(text) > max_chars:
        result.violations.append(
            Violation(
                rule=_RULE_NAMES[6],
                line=0,
                detail=f"{len(text)} > {max_chars} characters",
                field=field_name,
            )
        )

    for idx, line in enumerate(text.splitlines(), start=1):
        result.violations.extend(_scan_line_rules(line, idx, field_name))

    return result


def check_forbidden_soul_headings(text: str, field_name: str = "soul") -> list[Violation]:
    """§4.1: reject reserved core/preset headings and foreign marker blocks.

    The builder's own block is a plain ``## Addon: {Name}`` section; it must
    never (re)create a reserved heading or wrap another addon's/preset's marker.
    Reported under the structural rule name so the UI shows a clear reason.
    """
    out: list[Violation] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        low = stripped.lower()
        for heading in FORBIDDEN_SOUL_HEADINGS:
            if low == heading.lower() or low.startswith(heading.lower() + " "):
                out.append(
                    Violation(
                        rule=f"Reserved SOUL.md section ({heading})",
                        line=idx,
                        detail=stripped[:80],
                        field=field_name,
                    )
                )
        # Foreign marker blocks (any HAPM:addon / HAPM:preset marker inside the
        # user body is forbidden — the server owns the one wrapping marker).
        if re.search(r"<!--\s*HAPM:(?:addon|preset):", stripped, re.IGNORECASE):
            out.append(
                Violation(
                    rule="Reserved HAPM marker in body",
                    line=idx,
                    detail=stripped[:80],
                    field=field_name,
                )
            )
    return out


# ---------------------------------------------------------------------------
# §4.1 structural whitelist — fixed path enumeration
# ---------------------------------------------------------------------------

_ADDON_ID_RE = re.compile(r"^community-[A-Za-z0-9._-]+$")


def slugify(name: str) -> str:
    """Turn an addon name into the slug used in the generated addon id.

    Lowercase, spaces/underscores → hyphen, drop everything not
    ``[a-z0-9-]``, collapse repeats, trim leading/trailing hyphens.
    """
    s = name.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def make_addon_id(git_username: str, name: str) -> str:
    """Compose ``community-<git-username>-<slug-of-name>`` (§6 / §Screen step1).

    The id is server-generated and read-only in the UI: it prevents id
    collisions and name-spoofing of core addons (which are ``core-*``).
    """
    user_slug = slugify(git_username) or "unknown"
    name_slug = slugify(name)
    if not name_slug:
        raise SanitizeError("addon name produces an empty slug; choose a name with letters/digits")
    return f"community-{user_slug}-{name_slug}"


def validate_addon_id(addon_id: str) -> None:
    """Ensure an id is a well-formed community id (never a core-* spoof)."""
    if not _ADDON_ID_RE.fullmatch(addon_id):
        raise SanitizeError(
            f"invalid community addon id {addon_id!r}: must match "
            "'community-<user>-<slug>' using [A-Za-z0-9._-]"
        )


def skill_dir_name(addon_id: str) -> str:
    """The single allowed skill dir name for an addon (§4.3 namespacing)."""
    return f"{SKILL_NAME_PREFIX}{addon_id}"


def enumerate_targets(addon_id: str, has_soul: bool, has_skill: bool) -> list[str]:
    """Return the ONLY repo-relative file paths the builder may write.

    Derived exclusively from ``addon_id`` (§4.1 fixed path enumeration). There
    is no other legal write target. Manifest is always written; the SOUL block
    lives *inside* the manifest/soul_block file, the skill inside its dir.
    """
    validate_addon_id(addon_id)
    targets = [f"addons/{addon_id}/manifest.json"]
    if has_soul:
        targets.append(f"addons/{addon_id}/soul_block.md")
    if has_skill:
        targets.append(f"addons/{addon_id}/skills/{skill_dir_name(addon_id)}/SKILL.md")
    return targets


def assert_target_allowed(addon_id: str, path: str, has_soul: bool, has_skill: bool) -> None:
    """Reject any write path not in the fixed enumeration (server-side).

    This is the guard behind the acceptance criterion "the builder can never
    write a file path outside the two targets ... verified by attempting to
    force a different path via direct API call". It normalizes the path first
    so ``../`` / absolute / redundant-segment tricks cannot escape.
    """
    allowed = set(enumerate_targets(addon_id, has_soul, has_skill))
    # Reject absolute paths and traversal outright before normalization games.
    if path != path.strip() or path.startswith("/") or "\\" in path or ".." in PurePosixPath(path).parts:
        raise SanitizeError(f"illegal write path {path!r}: outside the allowed addon targets")
    normalized = PurePosixPath(path).as_posix()
    if normalized not in allowed:
        raise SanitizeError(
            f"illegal write path {path!r}: the builder may only write "
            f"{sorted(allowed)}"
        )


# ---------------------------------------------------------------------------
# §4.3 skill contribution validation
# ---------------------------------------------------------------------------

def validate_inline_skill(skill: dict) -> list[Violation]:
    """Validate an inline-authored skill contribution.

    Enforces §4.3: plain markdown only, no ``scripts/``/``references/``/
    ``assets/`` subfolders, no file smuggling outside the SKILL.md body, and
    the §4.2 linter applied 1:1 to the body. Returns a list of violations
    (empty == ok). Structural problems (extra files) raise :class:`SanitizeError`
    since they are not user-fixable text issues but attempted smuggling.
    """
    # Any key that would carry extra files is forbidden structurally.
    forbidden_file_keys = {"files", "scripts", "references", "assets", "attachments", "extra_files"}
    present = forbidden_file_keys & set(skill.keys())
    if present:
        raise SanitizeError(
            "inline skills may only contain a SKILL.md body in v1.2; "
            f"extra file container(s) not allowed: {sorted(present)}"
        )
    body = str(skill.get("body", ""))
    return check_content(body, field_name="skill").violations


def validate_curated_skill(source_ref: str) -> None:
    """§4.3: a curated-list skill selection must be from the fixed whitelist."""
    if source_ref not in CURATED_SKILL_SOURCES:
        raise SanitizeError(
            f"skill source {source_ref!r} is not in the curated whitelist "
            f"({sorted(CURATED_SKILL_SOURCES)}); free-text sources are not allowed"
        )


# ---------------------------------------------------------------------------
# Whole-addon check (draft-save AND pre-PR final check)
# ---------------------------------------------------------------------------

def check_addon(draft: dict) -> SanitizeResult:
    """Run the full server-side check on a draft dict.

    This is the single authority both the draft-save endpoint and the PR
    submit endpoint call — there is no path to persist/submit content that
    skips it (§4.2 "runs synchronously on every draft save AND as a final,
    non-overridable check before PR creation").

    Expected draft shape (subset)::

        {
          "name": str, "description": str,
          "soul": {"enabled": bool, "body": str},
          "skill": {"enabled": bool, "source": "inline"|"curated-list-ref",
                    "body": str, "source_ref": str, ...},
        }

    Returns a :class:`SanitizeResult`. Structural whitelist violations
    (reserved sections, extra files, bad ids, bad curated source) are surfaced
    as violations too so the caller can return one uniform blocking response.
    """
    result = SanitizeResult()

    name = str(draft.get("name", "")).strip()
    description = str(draft.get("description", "")).strip()
    if len(name) > NAME_MAX_CHARS:
        result.violations.append(
            Violation(rule="Name too long", line=0,
                      detail=f"{len(name)} > {NAME_MAX_CHARS}", field="name")
        )
    if len(description) > DESCRIPTION_MAX_CHARS:
        result.violations.append(
            Violation(rule="Description too long", line=0,
                      detail=f"{len(description)} > {DESCRIPTION_MAX_CHARS}", field="description")
        )

    soul = draft.get("soul") or {}
    skill = draft.get("skill") or {}
    soul_on = bool(soul.get("enabled"))
    skill_on = bool(skill.get("enabled"))

    if soul_on:
        body = str(soul.get("body", ""))
        result.violations.extend(check_content(body, field_name="soul").violations)
        result.violations.extend(check_forbidden_soul_headings(body, field_name="soul"))

    if skill_on:
        source = str(skill.get("source", "inline"))
        if source == "curated-list-ref":
            try:
                validate_curated_skill(str(skill.get("source_ref", "")))
            except SanitizeError as exc:
                result.violations.append(
                    Violation(rule="Skill source not whitelisted", line=0,
                              detail=str(exc), field="skill")
                )
        else:
            try:
                result.violations.extend(validate_inline_skill(skill))
            except SanitizeError as exc:
                result.violations.append(
                    Violation(rule="Illegal skill contribution", line=0,
                              detail=str(exc), field="skill")
                )

    return result
