"""Server-side authorization tests for every repository-policy mutation route."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from starlette.requests import Request

_DASHBOARD = Path(__file__).resolve().parents[1]
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

import plugin_api  # noqa: E402


REPOSITORY = "LouisKlimek/Hermes-Autonomy-Packet-Manager"


def _request(actor: str | None = None) -> Request:
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    if actor is not None:
        request.state.authenticated_user = actor
    return request


def _status(result) -> tuple[int, dict]:
    if hasattr(result, "body"):
        return result.status_code, json.loads(result.body)
    return 200, result


def test_policy_mutations_deny_missing_or_non_admin_actor() -> None:
    with tempfile.TemporaryDirectory() as td:
        previous = os.environ.get("HAPM_REPOSITORY_POLICY")
        os.environ["HAPM_REPOSITORY_POLICY"] = str(Path(td) / "policy.json")
        try:
            attempts = (
                lambda request: plugin_api.replace_repository_policy_route(request, {"repositories": [REPOSITORY]}),
                lambda request: plugin_api.add_repository_policy_route(request, {"repository": REPOSITORY}),
                lambda request: plugin_api.remove_repository_policy_route(request, {"repository": REPOSITORY}),
            )
            for invoke in attempts:
                for actor in (None, "fullstack-developer"):
                    status, body = _status(invoke(_request(actor)))
                    assert status == 403
                    assert body["error"] == "policy_admin_required"
        finally:
            if previous is None:
                os.environ.pop("HAPM_REPOSITORY_POLICY", None)
            else:
                os.environ["HAPM_REPOSITORY_POLICY"] = previous


def test_policy_mutations_allow_only_server_authenticated_admin() -> None:
    with tempfile.TemporaryDirectory() as td:
        previous = os.environ.get("HAPM_REPOSITORY_POLICY")
        os.environ["HAPM_REPOSITORY_POLICY"] = str(Path(td) / "policy.json")
        try:
            admin = _request("ceo-orchestrator")
            status, body = _status(plugin_api.replace_repository_policy_route(admin, {"repositories": [REPOSITORY]}))
            assert status == 200 and body["repositories"] == [REPOSITORY]
            status, body = _status(plugin_api.remove_repository_policy_route(admin, {"repository": REPOSITORY}))
            assert status == 200 and body["repositories"] == []
            status, body = _status(plugin_api.add_repository_policy_route(admin, {"repository": REPOSITORY}))
            assert status == 200 and body["repositories"] == [REPOSITORY]
        finally:
            if previous is None:
                os.environ.pop("HAPM_REPOSITORY_POLICY", None)
            else:
                os.environ["HAPM_REPOSITORY_POLICY"] = previous


def _run_all() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
