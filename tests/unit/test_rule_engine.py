"""Unit tests for the deterministic RuleEngine — the heart of D6.

These tests construct domain objects directly (no adapters) and pin the engine's
behaviour: each CheckType, determinism (same inputs -> same output), severity-ordered
findings, the consent split, and the edge cases an auditor cares about (missing numeric
field fails closed, exactly-at-the-limit passes, case-insensitive matching).
"""

from __future__ import annotations

from marketing_compliance_gate.domain.models import (
    AssetType,
    CheckType,
    Citation,
    FindingStatus,
    Market,
    MarketingAsset,
    Rule,
    RuleKind,
    RuleSet,
    Severity,
    SourceType,
    Vertical,
)
from marketing_compliance_gate.domain.rule_engine import RuleEngine


def _rule(rule_id: str, check: CheckType, **kw) -> Rule:
    return Rule(
        id=rule_id,
        kind=kw.pop("kind", RuleKind.CLAIM),
        check=check,
        description=kw.pop("description", f"rule {rule_id}"),
        market=Market.SG,
        vertical=Vertical.BANKING,
        severity=kw.pop("severity", Severity.HIGH),
        citation=Citation(source_id=rule_id, source_type=SourceType.RULE, title=rule_id),
        **kw,
    )


def _asset(body: str = "", fields: dict | None = None, consents: tuple = ()) -> MarketingAsset:
    return MarketingAsset(
        id="a",
        asset_type=AssetType.CREATIVE,
        title="t",
        body=body,
        market=Market.SG,
        vertical=Vertical.BANKING,
        fields=fields or {},
        granted_consents=consents,
    )


def _engine() -> RuleEngine:
    return RuleEngine()


def test_forbidden_phrase_fails_case_insensitively():
    rs = RuleSet(
        Market.SG,
        Vertical.BANKING,
        (_rule("R1", CheckType.FORBIDDEN_PHRASE, patterns=("Guaranteed Returns",)),),
    )
    findings = _engine().check(_asset(body="Get GUARANTEED returns today"), rs)
    assert len(findings) == 1
    assert findings[0].failed
    assert "guaranteed returns" in findings[0].evidence.lower()


def test_forbidden_phrase_passes_when_absent():
    rs = RuleSet(
        Market.SG,
        Vertical.BANKING,
        (_rule("R1", CheckType.FORBIDDEN_PHRASE, patterns=("guaranteed returns",)),),
    )
    findings = _engine().check(_asset(body="A balanced, compliant message."), rs)
    assert findings[0].status is FindingStatus.PASS
    assert not findings[0].evidence


def test_forbidden_phrase_scans_title_not_just_body():
    """Regression: a prohibited claim placed only in the title must still be caught."""
    rs = RuleSet(
        Market.SG,
        Vertical.BANKING,
        (_rule("R1", CheckType.FORBIDDEN_PHRASE, patterns=("guaranteed returns",)),),
    )
    asset = MarketingAsset(
        id="a",
        asset_type=AssetType.CREATIVE,
        title="Guaranteed returns inside!",
        body="See terms.",
        market=Market.SG,
        vertical=Vertical.BANKING,
    )
    findings = _engine().check(asset, rs)
    assert findings[0].failed


def test_required_disclosure_satisfied_by_title():
    """A required disclosure present in the title (not body) still satisfies the rule."""
    rs = RuleSet(
        Market.SG,
        Vertical.BANKING,
        (_rule("R1", CheckType.REQUIRED_DISCLOSURE, patterns=("per annum",)),),
    )
    asset = MarketingAsset(
        id="a",
        asset_type=AssetType.CREATIVE,
        title="4.10% per annum",
        body="Open an account today.",
        market=Market.SG,
        vertical=Vertical.BANKING,
    )
    assert not _engine().check(asset, rs)[0].failed


def test_required_disclosure_fails_when_missing():
    rs = RuleSet(
        Market.SG,
        Vertical.BANKING,
        (_rule("R1", CheckType.REQUIRED_DISCLOSURE, patterns=("per annum",)),),
    )
    assert _engine().check(_asset(body="4.10% rate"), rs)[0].failed
    assert not _engine().check(_asset(body="4.10% per annum"), rs)[0].failed


def test_required_field_fails_when_blank_or_absent():
    rs = RuleSet(
        Market.SG,
        Vertical.BANKING,
        (_rule("R1", CheckType.REQUIRED_FIELD, field="risk_warning"),),
    )
    assert _engine().check(_asset(fields={}), rs)[0].failed
    assert _engine().check(_asset(fields={"risk_warning": "  "}), rs)[0].failed
    assert not _engine().check(_asset(fields={"risk_warning": "x"}), rs)[0].failed


def test_numeric_max_boundary_and_missing():
    rs = RuleSet(
        Market.SG,
        Vertical.BANKING,
        (_rule("R1", CheckType.NUMERIC_MAX, field="discount_pct", limit=80.0),),
    )
    eng = _engine()
    assert not eng.check(_asset(fields={"discount_pct": "80"}), rs)[0].failed  # exactly at limit
    assert not eng.check(_asset(fields={"discount_pct": "80%"}), rs)[0].failed  # % tolerated
    assert eng.check(_asset(fields={"discount_pct": "81"}), rs)[0].failed
    # Missing / unparseable numeric field fails closed.
    assert eng.check(_asset(fields={}), rs)[0].failed
    assert eng.check(_asset(fields={"discount_pct": "abc"}), rs)[0].failed


def test_consent_required_split_and_finding():
    rule = _rule(
        "R1",
        CheckType.CONSENT_REQUIRED,
        kind=RuleKind.CONSENT,
        consent_purpose="marketing",
    )
    rs = RuleSet(Market.SG, Vertical.BANKING, (rule,))
    eng = _engine()
    # check() ignores consent-kind rules; consent_checks() handles them.
    assert eng.check(_asset(), rs) == ()
    checks, findings = eng.consent_checks(_asset(consents=()), rs)
    assert len(checks) == 1 and checks[0].required and not checks[0].granted
    assert findings[0].failed and findings[0].rule_kind is RuleKind.CONSENT
    checks2, findings2 = eng.consent_checks(_asset(consents=("marketing",)), rs)
    assert checks2[0].granted and checks2[0].satisfied
    assert not findings2[0].failed


def test_findings_are_severity_ordered_failures_first():
    rs = RuleSet(
        Market.SG,
        Vertical.BANKING,
        (
            _rule("LOW", CheckType.FORBIDDEN_PHRASE, patterns=("x",), severity=Severity.LOW),
            _rule(
                "CRIT", CheckType.FORBIDDEN_PHRASE, patterns=("bad",), severity=Severity.CRITICAL
            ),
            _rule("MED", CheckType.FORBIDDEN_PHRASE, patterns=("y",), severity=Severity.MEDIUM),
        ),
    )
    findings = _engine().check(_asset(body="this is bad x y"), rs)
    # All three fail; ordered by severity desc.
    assert [f.rule_id for f in findings] == ["CRIT", "MED", "LOW"]


def test_engine_is_deterministic():
    rs = RuleSet(
        Market.SG,
        Vertical.BANKING,
        (
            _rule("R1", CheckType.FORBIDDEN_PHRASE, patterns=("bad",)),
            _rule("R2", CheckType.REQUIRED_DISCLOSURE, patterns=("good",)),
        ),
    )
    asset = _asset(body="this is bad")
    a = _engine().check(asset, rs)
    b = _engine().check(asset, rs)
    assert a == b


def test_unknown_check_type_does_not_crash_only_known_types():
    # All CheckType members are handled; sanity-check the engine is total over them.
    for ct in CheckType:
        rs = RuleSet(
            Market.SG,
            Vertical.BANKING,
            (
                _rule(
                    "R",
                    ct,
                    patterns=("p",),
                    field="f",
                    limit=1.0,
                    consent_purpose="marketing",
                    kind=RuleKind.CONSENT if ct is CheckType.CONSENT_REQUIRED else RuleKind.CLAIM,
                ),
            ),
        )
        if ct is CheckType.CONSENT_REQUIRED:
            checks, findings = _engine().consent_checks(_asset(), rs)
            assert findings  # produced a finding
        else:
            assert _engine().check(_asset(), rs)  # produced a finding
