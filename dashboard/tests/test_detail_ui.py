"""Static contract checks for the read-only preset/addon detail UI."""

from pathlib import Path
import unittest


INDEX = Path(__file__).resolve().parents[1] / "dist" / "index.js"


class DetailUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = INDEX.read_text(encoding="utf-8")

    def test_details_are_available_for_preset_and_addon_rows(self):
        self.assertIn('function DetailDialog(props)', self.source)
        self.assertIn('key: "preset-details"', self.source)
        self.assertIn('key: "addon-details"', self.source)
        self.assertIn('COPY.details', self.source)

    def test_dialog_has_keyboard_close_and_accessible_modal_semantics(self):
        self.assertIn('role: "dialog"', self.source)
        self.assertIn('"aria-modal": "true"', self.source)
        self.assertIn('e.key === "Escape"', self.source)
        self.assertIn('closeRef.current.focus()', self.source)
        self.assertIn('trigger.focus()', self.source)

    def test_dialog_traps_tab_focus_and_handles_keys_from_dialog_content(self):
        start = self.source.index('function DetailDialog(props)')
        end = self.source.index('// ---------------------------------------------------------------------------\n  // Confirmation dialog', start)
        dialog_source = self.source[start:end]
        self.assertIn('function focusableElements()', dialog_source)
        self.assertIn('e.key !== "Tab"', dialog_source)
        self.assertIn('dialogRef.current.contains(active)', dialog_source)
        self.assertIn('ref: dialogRef', dialog_source)
        self.assertIn('onKeyDown: onKeyDown', dialog_source)

    def test_addon_details_keep_profile_compatible_controls_available_without_preset(self):
        self.assertIn('addonDetailNoPresetAvailable', self.source)
        self.assertIn('COPY.addonDetailNoPresetAvailable', self.source)
        self.assertNotIn(' : COPY.addonDetailDisabled;', self.source)

    def test_status_view_has_details_for_active_preset_and_addons(self):
        start = self.source.index('function StatusView(props)')
        end = self.source.index('// ---------------------------------------------------------------------------\n  // Root page.', start)
        status_source = self.source[start:end]
        self.assertIn('var detailState = useState(null);', status_source)
        self.assertIn('key: "status-preset-details"', status_source)
        self.assertIn('key: "status-addon-details"', status_source)
        self.assertIn('key: "status-details-dialog"', status_source)

    def test_detail_content_uses_source_fields_and_truthful_fallbacks(self):
        self.assertIn('item.description || COPY.detailsDescriptionFallback', self.source)
        self.assertIn('addon.compatible_profiles_or_presets', self.source)
        self.assertIn('var contributes = addon.contributes || {};', self.source)
        self.assertIn('COPY.detailsNoEffects', self.source)

    def test_preset_details_show_exact_registry_files_to_be_applied(self):
        self.assertIn('detailsAppliedContents', self.source)
        self.assertIn('application: selDesc.application', self.source)
        self.assertIn('application.soul_markdown', self.source)
        self.assertIn('application.skills', self.source)
        self.assertIn('application.config_fragment', self.source)

    def test_detail_dialog_has_no_mutating_api_calls(self):
        start = self.source.index('function DetailDialog(props)')
        end = self.source.index('// ---------------------------------------------------------------------------\n  // Confirmation dialog', start)
        dialog_source = self.source[start:end]
        self.assertNotIn('apiPost(', dialog_source)
        self.assertNotIn('apiGet(', dialog_source)


if __name__ == "__main__":
    unittest.main()
