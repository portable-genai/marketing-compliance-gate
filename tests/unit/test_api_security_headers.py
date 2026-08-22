from types import SimpleNamespace

from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from marketing_compliance_gate.api import app as app_module


def test_api_security_headers_are_complete_and_hsts_is_profile_gated(monkeypatch) -> None:
    client = TestClient(app_module.app, client=LOOPBACK_PEER)
    monkeypatch.setattr(
        app_module,
        "get_container",
        lambda: SimpleNamespace(settings=SimpleNamespace(exposure_profile="local")),
    )
    local = client.get("/not-found")
    assert local.headers["x-content-type-options"] == "nosniff"
    assert local.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in local.headers

    monkeypatch.setattr(
        app_module,
        "get_container",
        lambda: SimpleNamespace(settings=SimpleNamespace(exposure_profile="gcp")),
    )
    assert (
        client.get("/not-found").headers["strict-transport-security"].startswith("max-age=63072000")
    )
