"""Source-contract tests for the destructive preset-apply confirmation UI.

The dashboard is shipped as a dependency-free IIFE, so these focused checks
exercise the user-visible control contract without a browser test dependency.
Run: python3 dashboard/tests/test_preset_confirmation.py
"""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "dist" / "index.js").read_text(encoding="utf-8")


def _section(start: str, end: str) -> str:
    return SOURCE.split(start, 1)[1].split(end, 1)[0]


def test_apply_is_gated_by_confirmation() -> None:
    panel = _section("function PresetSection(props)", "// Small read-only status summary")
    assert "onClick: openConfirm" in panel
    assert "onClick: doApply" not in panel
    assert "onConfirm: doApply" in panel
    assert panel.index("setDialogOpen(true)") < panel.index('apiPost("/apply"')


def test_warning_matches_the_apply_contract() -> None:
    assert "will overwrite SOUL.md, skills, and the allowed configuration fields" in SOURCE
    assert "backed up beforehand" in SOURCE
    assert "Incompatible addons are automatically disabled" not in SOURCE


def test_cancel_and_close_have_no_apply_path() -> None:
    dialog = _section("function ConfirmDialog(props)", "// v1.1 Addon")
    assert dialog.count("props.onCancel") >= 3  # Cancel, close, and Escape.
    assert "if (e.target === e.currentTarget && !props.busy) props.onCancel();" in dialog
    assert 'onClick: props.onConfirm' in dialog
    assert dialog.index("props.onCancel") < dialog.index("props.onConfirm")


def test_confirmation_is_accessible_and_responsive() -> None:
    dialog = _section("function ConfirmDialog(props)", "// v1.1 Addon")
    assert 'role: "dialog"' in dialog
    assert '"aria-modal": "true"' in dialog
    assert '"aria-labelledby": titleId' in dialog
    assert 'if (e.key === "Escape" || e.key === "Esc")' in dialog
    assert "cancelRef.current.focus()" in dialog
    assert 'flexWrap: "wrap"' in dialog


def test_confirm_is_single_flight_and_retains_error_ui() -> None:
    panel = _section("function PresetSection(props)", "// Small read-only status summary")
    assert "applyInFlightRef.current" in panel
    assert 'apiPost("/apply"' in panel
    assert "setDialogErr(applyErrorNode(err))" in panel
    assert "setDialogOpen(false)" in panel


def _run() -> int:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run())
