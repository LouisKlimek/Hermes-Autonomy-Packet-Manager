"""Regression contract for dropdown-selected addon compatibility previews.

Run with: python3 dashboard/tests/test_selected_preset_addon_preview.py
"""

from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "dist" / "index.js").read_text(
    encoding="utf-8"
)


def _section(start: str, end: str) -> str:
    return SOURCE[SOURCE.index(start) : SOURCE.index(end, SOURCE.index(start))]


def test_addon_request_targets_selected_preset_not_profile_name() -> None:
    load_addons = _section("var loadAddons = useCallback", "useEffect(\n      function () {\n        loadProfiles()")
    assert "function (profileName, presetSlug)" in load_addons
    assert "encodeURIComponent(presetSlug)" in load_addons
    assert '"&profile=" +\n        encodeURIComponent(profileName)' in load_addons
    assert "encodeURIComponent(profileName) +\n        \"&profile=\"" not in load_addons


def test_dropdown_change_reloads_preview_and_preserves_stale_response_guard() -> None:
    root = _section("var selectedStatus = selected ? statuses[selected]", "var onApplied = useCallback")
    assert "selectedPresetSelection.profile === selected" in root
    assert "[selected, selectedPreset, loadAddons]" in root
    assert "loadAddons(selected, selectedPreset)" in root
    assert SOURCE.count("if (requestSequence !== addonRequestSequence.current) return;") == 2


def test_preview_controls_are_non_mutating_until_selected_preset_is_active() -> None:
    assert "var addonMutationsDisabled =" in SOURCE
    assert "selectedPreset !== selectedStatus.active_preset" in SOURCE
    assert "disabled: props.busy || props.mutationsDisabled" in SOURCE
    assert "if (!selected || addonMutationsDisabled) return;" in SOURCE
    assert "COPY.addonsPreviewOnly" in SOURCE


def test_active_preset_mutations_use_the_preset_target() -> None:
    enable = _section("var onEnableAddon = useCallback", "// v1.1: confirmed guided resolution")
    assert "target: selectedPreset" in enable
    assert "loadAddons(selected, selectedPreset)" in enable
    resolve = _section("var onConfirmConflict = useCallback", "// v1.1: cancel")
    assert "loadAddons(selected, selectedPreset)" in resolve


if __name__ == "__main__":
    tests = [
        test_addon_request_targets_selected_preset_not_profile_name,
        test_dropdown_change_reloads_preview_and_preserves_stale_response_guard,
        test_preview_controls_are_non_mutating_until_selected_preset_is_active,
        test_active_preset_mutations_use_the_preset_target,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed, 0 failed")
