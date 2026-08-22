"""The CSP ``frame-ancestors`` allowlist resolved in THREE states, never two.

Red before the fix: ``MKT_GOV_FRAME_ANCESTORS=""`` (a Terraform variable that renders to
nothing, a Cloud Run env var declared with no value, a ``.env`` line left as ``VAR=``) went
through ``os.environ.get(name, "'self'")``, which only sees "absent". A present-but-empty
variable therefore reached the middleware verbatim and the response carried
``Content-Security-Policy: frame-ancestors `` with an EMPTY directive. Browsers discard a
directive with no value as a parse error, so no framing policy applied. Worse, the
``if _FRAME_ANCESTORS == "'self'"`` branch was also skipped, so ``X-Frame-Options`` was not
emitted as the legacy fallback either: the clickjacking control disappeared entirely, silently,
in exactly the deployment shape that looks configured.

Green after: unset keeps the restrictive documented default, set-and-empty is REFUSED at boot
(the resolver runs at import, so the process never starts serving with no framing policy), and
set-and-valid is used.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from hex_service_kit.netdefaults import ConfiguredEmptyError
from tests.conftest import LOOPBACK_PEER

from marketing_compliance_gate.api import app as app_module

_ENV = "MKT_GOV_FRAME_ANCESTORS"


def test_unset_keeps_the_restrictive_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nobody expressed an intent, so the documented default stands."""
    monkeypatch.delenv(_ENV, raising=False)
    assert app_module._frame_ancestors() == "'self'"


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_set_and_blank_is_refused(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    """An intent WAS expressed and it names nothing: refuse rather than emit an empty directive."""
    monkeypatch.setenv(_ENV, blank)
    with pytest.raises(ConfiguredEmptyError) as excinfo:
        app_module._frame_ancestors()
    assert _ENV in str(excinfo.value)


def test_set_and_valid_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "https://portal.client.example https://admin.client.example")
    assert app_module._frame_ancestors() == (
        "https://portal.client.example https://admin.client.example"
    )


def test_blank_refuses_at_import_so_the_service_never_serves_unprotected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal is a BOOT refusal, not a per-request one.

    Importing the app module with the variable set and blank must fail, so a misconfigured
    deployment cannot come up and quietly serve every response without a framing policy.
    """
    monkeypatch.setenv(_ENV, "")
    try:
        with pytest.raises(ConfiguredEmptyError):
            importlib.reload(app_module)
    finally:
        monkeypatch.delenv(_ENV, raising=False)
        importlib.reload(app_module)


def test_the_emitted_directive_is_never_empty() -> None:
    """Whatever survives the resolver, the header a browser receives carries a real value."""
    client = TestClient(app_module.app, client=LOOPBACK_PEER)
    response = client.get("/healthz")
    csp = response.headers["content-security-policy"]
    directive = csp.split("frame-ancestors", 1)[1].strip()
    assert directive, "an empty frame-ancestors directive is discarded by browsers"
    if directive == "'self'":
        assert response.headers.get("x-frame-options") == "SAMEORIGIN"
