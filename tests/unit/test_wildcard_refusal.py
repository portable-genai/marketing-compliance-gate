"""The "never ``*``" rule the origin policy documents, actually enforced at boot.

Red before the fix: both allowlists were resolved with real care about the states an operator
can leave them in (unset keeps the restrictive default, set-and-empty is refused rather than
silently widened) and then handed on VERBATIM. The prohibition on ``*`` existed only in a
comment beside the resolver and in the runbook table, so
``MKT_GOV_FRAME_ANCESTORS="*"`` shipped ``Content-Security-Policy: frame-ancestors *``,
which permits ANY page anywhere to frame the console, and
``MKT_GOV_CORS_ORIGINS="*"`` handed the same blanket trust to the CORS middleware, which
serves credentialed responses. A comment does not fail a build.

Green after: the resolvers refuse a wildcard in either allowlist. The refusal is a BOOT
refusal, raised where the values are resolved at import, so a deployment that names ``*``
never comes up and answers requests under a policy that trusts everybody. Any token carrying
``*`` is refused, not just a bare one: ``https://*.client.example`` is a real CSP host-source
wildcard and grants every subdomain, including one an attacker gets to register.

The three states this repo already resolves are UNCHANGED. This file pins them alongside the
new case, because a wildcard check bolted on carelessly is exactly the kind of change that
quietly turns "unset" back into "default" for the wrong branch.
"""

from __future__ import annotations

import importlib

import pytest
from hex_service_kit.netdefaults import ConfiguredEmptyError

from marketing_compliance_gate.api import app as app_module

_FRAME_ENV = "MKT_GOV_FRAME_ANCESTORS"
_CORS_ENV = "MKT_GOV_CORS_ORIGINS"

#: Every way an operator, a template or a YAML quirk can spell "everybody", checked against
#: both allowlists. ``ui/lib/csp.mjs`` refuses the same set on the document half.
_WILDCARD_SPELLINGS = ["*", "'*'", "null", "*.*", "https://*.example", "*.example", "https://*"]


@pytest.fixture(autouse=True)
def _clean_origin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither variable leaks between cases; each test states the whole environment it means."""
    monkeypatch.delenv(_FRAME_ENV, raising=False)
    monkeypatch.delenv(_CORS_ENV, raising=False)


@pytest.mark.parametrize(
    "value",
    ["*", "'self' *", "* 'self'", "https://*.client.example", "'self' https://*.client.example"],
)
def test_a_wildcard_frame_ancestor_is_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """``frame-ancestors *`` is "anybody may frame this", which is no clickjacking policy."""
    monkeypatch.setenv(_FRAME_ENV, value)
    with pytest.raises(ValueError) as excinfo:
        app_module._frame_ancestors()
    assert "wildcard" in str(excinfo.value)
    assert _FRAME_ENV in str(excinfo.value)


@pytest.mark.parametrize(
    "value",
    ["*", "https://portal.client.example,*", "https://*.client.example"],
)
def test_a_wildcard_cors_origin_is_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """``*`` in the CORS allowlist grants every origin on the internet the same trust."""
    monkeypatch.setenv(_CORS_ENV, value)
    with pytest.raises(ValueError) as excinfo:
        app_module._cors_origins()
    assert "wildcard" in str(excinfo.value)
    assert _CORS_ENV in str(excinfo.value)


@pytest.mark.parametrize("entry", _WILDCARD_SPELLINGS)
def test_every_wildcard_spelling_is_refused_in_either_list(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """Behaviour, not spelling, decides: ``null`` carried no asterisk and was ACCEPTED by both.

    Measured before the fix, on this repo: every asterisk-bearing spelling here was already
    refused, because the check tested for the CHARACTER rather than for equality with ``*``.
    ``null`` was the hole. ``MKT_GOV_FRAME_ANCESTORS=null`` resolved to the string ``null``
    and shipped ``Content-Security-Policy: frame-ancestors null``, and
    ``MKT_GOV_CORS_ORIGINS=null`` put ``null`` in the credentialed CORS allowlist. A
    SANDBOXED iframe presents the origin ``null``, so naming it hands framing and cross-origin
    rights to any page that can open one, which is the framing this policy exists to refuse.
    It is a wildcard by behaviour while looking like an origin, so no asterisk test can see it.

    The asterisk-bearing spellings are pinned here too, against the rule that owns them: the
    exact-token set and the character test are one rule, and a later edit to either half must
    keep the whole set refused rather than leaving a spelling covered by luck.
    """
    monkeypatch.setenv(_FRAME_ENV, entry)
    with pytest.raises(ValueError, match="wildcard"):
        app_module._frame_ancestors()
    monkeypatch.delenv(_FRAME_ENV, raising=False)
    monkeypatch.setenv(_CORS_ENV, entry)
    with pytest.raises(ValueError, match="wildcard"):
        app_module._cors_origins()


def test_the_refusal_still_admits_a_real_tenant_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal that also turns away valid configuration is an outage, not a control.

    A port and a hyphen are the two shapes a hand-written token set gets wrong, and both are
    ordinary in a tenant origin: a hyphenated host is the norm and a non-443 port is how a
    staging portal is reached.
    """
    tenant = "https://portal.demo-bank.example:8443 https://admin-console.demo-bank.example"
    monkeypatch.setenv(_FRAME_ENV, tenant)
    assert app_module._frame_ancestors() == tenant
    monkeypatch.setenv(
        _CORS_ENV,
        "https://portal.demo-bank.example:8443,https://admin-console.demo-bank.example",
    )
    assert app_module._cors_origins() == [
        "https://portal.demo-bank.example:8443",
        "https://admin-console.demo-bank.example",
    ]


def test_a_legitimate_frame_ancestor_allowlist_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal must cost a correctly configured tenant nothing."""
    monkeypatch.setenv(_FRAME_ENV, "https://portal.client.example https://admin.client.example")
    assert app_module._frame_ancestors() == (
        "https://portal.client.example https://admin.client.example"
    )


def test_a_legitimate_cors_allowlist_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_CORS_ENV, "https://portal.client.example,https://admin.client.example")
    assert app_module._cors_origins() == [
        "https://portal.client.example",
        "https://admin.client.example",
    ]


def test_unset_and_emptied_behaviour_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wildcard case is an ADDITION; the three states resolve exactly as before.

    Unset keeps the documented restrictive default and emptied is still refused as a
    configured-empty value, with its own error type, so a caller distinguishing the two
    failures keeps working.
    """
    assert app_module._frame_ancestors() == "'self'"

    monkeypatch.setenv(_FRAME_ENV, "")
    with pytest.raises(ConfiguredEmptyError):
        app_module._frame_ancestors()

    monkeypatch.delenv(_FRAME_ENV, raising=False)
    monkeypatch.setenv(_CORS_ENV, "")
    assert app_module._cors_origins() == [], "an emptied CORS allowlist still denies everything"


@pytest.mark.parametrize("variable", [_FRAME_ENV, _CORS_ENV])
def test_the_wildcard_refusal_is_a_boot_refusal(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """Importing the app with a wildcard configured must fail.

    A per-request check would leave a misconfigured service running and answering; the point
    of resolving these at import is that the process refuses to come up at all.
    """
    monkeypatch.setenv(variable, "*")
    try:
        with pytest.raises(ValueError):
            importlib.reload(app_module)
    finally:
        monkeypatch.delenv(variable, raising=False)
        importlib.reload(app_module)
