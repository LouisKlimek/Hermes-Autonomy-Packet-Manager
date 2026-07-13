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

    def test_detail_content_uses_source_fields_and_truthful_fallbacks(self):
        self.assertIn('item.description || COPY.detailsDescriptionFallback', self.source)
        self.assertIn('addon.compatible_profiles_or_presets', self.source)
        self.assertIn('var contributes = addon.contributes || {};', self.source)
        self.assertIn('COPY.detailsNoEffects', self.source)

    def test_detail_dialog_has_no_mutating_api_calls(self):
        start = self.source.index('function DetailDialog(props)')
        end = self.source.index('// ---------------------------------------------------------------------------\n  // Confirmation dialog', start)
        dialog_source = self.source[start:end]
        self.assertNotIn('apiPost(', dialog_source)
        self.assertNotIn('apiGet(', dialog_source)


if __name__ == "__main__":
    unittest.main()
