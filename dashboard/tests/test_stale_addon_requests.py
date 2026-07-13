"""Source-contract regression coverage for stale dashboard addon responses.

Run with: python3 dashboard/tests/test_stale_addon_requests.py
"""

from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "dist" / "index.js").read_text(
    encoding="utf-8"
)


def test_stale_addon_requests_cannot_update_success_or_error_state() -> None:
    assert "var useRef = React.useRef;" in SOURCE
    assert "var addonRequestSequence = useRef(0);" in SOURCE
    assert "var requestSequence = ++addonRequestSequence.current;" in SOURCE
    assert SOURCE.count("if (requestSequence !== addonRequestSequence.current) return;") == 2


if __name__ == "__main__":
    test_stale_addon_requests_cannot_update_success_or_error_state()
    print("1 passed, 0 failed")