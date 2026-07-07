"""SOUL.md addon-contribution blocks (FR-7).

Addons contribute SOUL.md content inside uniquely marked blocks so a block can
be surgically inserted, replaced, or removed without disturbing any text the
user added outside the markers. The marker format is fixed by the PRD::

    <!-- HAPM:addon:<id> START -->
    ...addon-contributed content...
    <!-- HAPM:addon:<id> END -->

Rules honored here:
* Each ``<id>`` appears at most once. ``upsert`` replaces an existing block in
  place (preserving surrounding text) or appends a new one at end of file.
* ``remove`` strips exactly the block (and its markers) and nothing else,
  leaving the rest of the file byte-identical apart from the removed block.
* Insertion/removal is whitespace-conservative: a blank-line separator is used
  to separate an appended block from preceding content, and that same
  separator is removed on delete so append-then-remove is byte-identical.
"""

from __future__ import annotations

import re

_MARKER_PREFIX = "HAPM:addon:"


class SoulBlockError(Exception):
    """Raised on malformed SOUL block operations (e.g. invalid addon id)."""


def addon_block_markers(addon_id: str) -> tuple[str, str]:
    """Return the (start, end) marker comment lines for an addon id."""
    _validate_id(addon_id)
    start = f"<!-- {_MARKER_PREFIX}{addon_id} START -->"
    end = f"<!-- {_MARKER_PREFIX}{addon_id} END -->"
    return start, end


def _validate_id(addon_id: str) -> None:
    if not addon_id or not re.fullmatch(r"[A-Za-z0-9._-]+", addon_id):
        raise SoulBlockError(
            f"invalid addon id {addon_id!r}: use [A-Za-z0-9._-]+"
        )


def _removal_pattern(addon_id: str) -> re.Pattern[str]:
    """Pattern matching the block plus any leading blank-line separator."""
    start, end = addon_block_markers(addon_id)
    # Optionally consume one blank-line separator that upsert may have added
    # before the block. DOTALL so content may span multiple lines.
    return re.compile(
        r"\n?\n"
        + re.escape(start)
        + r".*?"
        + re.escape(end),
        re.DOTALL,
    )


def has_addon_block(soul_text: str, addon_id: str) -> bool:
    """True if a block for ``addon_id`` is present in ``soul_text``."""
    start, _ = addon_block_markers(addon_id)
    return start in soul_text


def list_addon_blocks(soul_text: str) -> list[str]:
    """Return the addon ids that currently have a block in ``soul_text``."""
    return re.findall(
        r"<!-- " + re.escape(_MARKER_PREFIX) + r"([A-Za-z0-9._-]+) START -->",
        soul_text,
    )


def upsert_addon_block(soul_text: str, addon_id: str, content: str) -> str:
    """Insert or replace the addon's block, returning the new SOUL text.

    If a block for ``addon_id`` already exists it is replaced in place;
    otherwise a new block is appended at end of file, separated by one blank
    line from preceding content.
    """
    start, end = addon_block_markers(addon_id)
    body = content.rstrip("\n")
    block = f"{start}\n{body}\n{end}"

    if has_addon_block(soul_text, addon_id):
        # Replace existing block in place, preserving surrounding text.
        pat = re.compile(
            re.escape(start) + r".*?" + re.escape(end), re.DOTALL
        )
        return pat.sub(lambda _m: block, soul_text, count=1)

    if soul_text == "":
        return block + "\n"
    # Normalize so there is exactly one blank line before the appended block
    # and a trailing newline after it.
    base = soul_text.rstrip("\n")
    return f"{base}\n\n{block}\n"


def remove_addon_block(soul_text: str, addon_id: str) -> str:
    """Remove the addon's block (and its markers) from ``soul_text``.

    Idempotent: if no such block exists the text is returned unchanged. Removes
    exactly the block plus the single blank-line separator that ``upsert`` adds,
    so an upsert-then-remove round trip is byte-identical to the original.
    """
    if not has_addon_block(soul_text, addon_id):
        return soul_text
    result, n = _removal_pattern(addon_id).subn("", soul_text, count=1)
    if n == 0:
        # Block present but not preceded by the separator (e.g. at start of
        # file); fall back to removing just the markers+content.
        start, end = addon_block_markers(addon_id)
        pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
        result = pat.sub("", soul_text, count=1)
    return result
