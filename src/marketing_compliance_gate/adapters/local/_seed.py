"""Built-in, OBVIOUSLY-FICTIONAL synthetic seed for the ``local`` profile.

This is the offline rule KB that makes a local run grounded out of the box: the
per-market, per-vertical advertising + consumer-protection + consent rule sets, spanning
BOTH verticals (banking AND online retail) across ALL THREE markets (JP, AU, SG). The
rule ids and authority titles are illustrative and obviously synthetic; nothing here is
legal advice or a verbatim statute. Every demo URL points at ``example.test``.

The data is keyed by (market, vertical) so the local RuleProviderPort serves vertical-
and market-specific rules, proving D6 is generic and APAC without any hard-coded branch
in the rule engine. Real markets cited as the *frame* for the synthetic rules:

* JP — Act on Specified Commercial Transactions; Premiums and Representations Act; APPI.
* AU — Australian Consumer Law / ASIC guidance; Privacy Act (APPs).
* SG — PDPA; advertising standards (the banking set adds MAS-style promotion rules).

Banking is ONE configured vertical here, NOT the only frame: online retail has its own
sale-claim, stock and consent rules.
"""

from __future__ import annotations

from ...domain.models import (
    CheckType,
    Citation,
    EvidenceKind,
    GreenClaimCategory,
    Market,
    Rule,
    RuleKind,
    RuleSet,
    Severity,
    SourceType,
    SubstantiationEvidence,
    Vertical,
)

_Key = tuple[Market, Vertical]


def _cit(rule_id: str, title: str, authority: str) -> Citation:
    return Citation(
        source_id=rule_id,
        source_type=SourceType.REGULATION,
        title=title,
        url=f"https://rules.example.test/{rule_id.lower()}",
        page=1,
        published_date="2026-01-01",
        snippet=f"{authority} (FICTIONAL synthetic rule).",
        score=1.0,
    )


# --------------------------------------------------------------------------- #
# Rule definitions, per (market, vertical). Every rule is data: the engine code is
# fixed; adding a rule is a change here, not in rule_engine.py.
# --------------------------------------------------------------------------- #
_RULES: dict[_Key, tuple[Rule, ...]] = {
    # ----------------------- Singapore — banking ----------------------- #
    (Market.SG, Vertical.BANKING): (
        Rule(
            id="SG-BANK-CLAIM-GUARANTEED",
            kind=RuleKind.CLAIM,
            check=CheckType.FORBIDDEN_PHRASE,
            description="Financial promotions must not imply guaranteed or risk-free returns.",
            market=Market.SG,
            vertical=Vertical.BANKING,
            severity=Severity.CRITICAL,
            patterns=("guaranteed returns", "risk-free", "risk free", "guaranteed profit"),
            remediation="Remove absolute-return wording; state that returns are not guaranteed.",
            citation=_cit(
                "SG-BANK-CLAIM-GUARANTEED",
                "Fair-dealing financial-promotion standard (SG)",
                "MAS-style advertising guidance",
            ),
        ),
        Rule(
            id="SG-BANK-APR-DISCLOSURE",
            kind=RuleKind.CLAIM,
            check=CheckType.REQUIRED_DISCLOSURE,
            description="An advertised deposit rate must disclose that it is a per-annum rate.",
            market=Market.SG,
            vertical=Vertical.BANKING,
            severity=Severity.HIGH,
            patterns=("per annum",),
            remediation="Add a 'per annum' (p.a.) qualifier next to the advertised rate.",
            citation=_cit(
                "SG-BANK-APR-DISCLOSURE",
                "Deposit-rate disclosure standard (SG)",
                "MAS-style advertising guidance",
            ),
        ),
        Rule(
            id="SG-BANK-RISK-WARNING",
            kind=RuleKind.PERMISSION,
            check=CheckType.REQUIRED_FIELD,
            description="An investment promotion must carry an approved risk-warning field.",
            market=Market.SG,
            vertical=Vertical.BANKING,
            severity=Severity.HIGH,
            field="risk_warning",
            remediation="Attach the approved risk-warning statement before publishing.",
            citation=_cit(
                "SG-BANK-RISK-WARNING",
                "Risk-warning requirement (SG)",
                "MAS-style advertising guidance",
            ),
        ),
        Rule(
            id="SG-BANK-CONSENT-PDPA",
            kind=RuleKind.CONSENT,
            check=CheckType.CONSENT_REQUIRED,
            description="Direct-marketing messages require PDPA marketing consent.",
            market=Market.SG,
            vertical=Vertical.BANKING,
            severity=Severity.HIGH,
            consent_purpose="marketing",
            remediation="Send only to recipients who granted marketing consent under PDPA.",
            citation=_cit(
                "SG-BANK-CONSENT-PDPA",
                "PDPA marketing-consent requirement (SG)",
                "Personal Data Protection Act",
            ),
        ),
    ),
    # ------------------------- Japan — banking ------------------------- #
    (Market.JP, Vertical.BANKING): (
        Rule(
            id="JP-BANK-CLAIM-ABSOLUTE",
            kind=RuleKind.CLAIM,
            check=CheckType.FORBIDDEN_PHRASE,
            description="Misleading superlatives about returns are prohibited (Keihyo Act).",
            market=Market.JP,
            vertical=Vertical.BANKING,
            severity=Severity.CRITICAL,
            patterns=("guaranteed returns", "no risk", "絶対に儲かる"),
            remediation="Remove absolute-benefit wording; add a balanced risk statement.",
            citation=_cit(
                "JP-BANK-CLAIM-ABSOLUTE",
                "Premiums and Representations Act standard (JP)",
                "Act against Unjustifiable Premiums and Misleading Representations",
            ),
        ),
        Rule(
            id="JP-BANK-CONTACT-DISCLOSURE",
            kind=RuleKind.CLAIM,
            check=CheckType.REQUIRED_DISCLOSURE,
            description="A solicitation must disclose the provider's contact (Tokushoho).",
            market=Market.JP,
            vertical=Vertical.BANKING,
            severity=Severity.MEDIUM,
            patterns=("contact",),
            remediation="Include the provider's contact details in the creative.",
            citation=_cit(
                "JP-BANK-CONTACT-DISCLOSURE",
                "Specified Commercial Transactions disclosure (JP)",
                "Act on Specified Commercial Transactions",
            ),
        ),
        Rule(
            id="JP-BANK-CONSENT-APPI",
            kind=RuleKind.CONSENT,
            check=CheckType.CONSENT_REQUIRED,
            description="Use of personal data for marketing requires APPI consent.",
            market=Market.JP,
            vertical=Vertical.BANKING,
            severity=Severity.HIGH,
            consent_purpose="marketing",
            remediation="Confirm APPI marketing consent before targeting.",
            citation=_cit(
                "JP-BANK-CONSENT-APPI",
                "APPI marketing-consent requirement (JP)",
                "Act on the Protection of Personal Information",
            ),
        ),
    ),
    # ----------------------- Australia — banking ----------------------- #
    (Market.AU, Vertical.BANKING): (
        Rule(
            id="AU-BANK-CLAIM-MISLEADING",
            kind=RuleKind.CLAIM,
            check=CheckType.FORBIDDEN_PHRASE,
            description="No misleading or deceptive representations (ACL / ASIC).",
            market=Market.AU,
            vertical=Vertical.BANKING,
            severity=Severity.CRITICAL,
            patterns=("guaranteed returns", "best rate ever", "risk-free"),
            remediation="Substantiate or remove the absolute claim.",
            citation=_cit(
                "AU-BANK-CLAIM-MISLEADING",
                "Australian Consumer Law misleading-conduct standard",
                "ACL s18 / ASIC advertising guidance",
            ),
        ),
        Rule(
            id="AU-BANK-COMPARISON-RATE",
            kind=RuleKind.CLAIM,
            check=CheckType.REQUIRED_DISCLOSURE,
            description="A credit-rate advertisement must show a comparison rate.",
            market=Market.AU,
            vertical=Vertical.BANKING,
            severity=Severity.HIGH,
            patterns=("comparison rate",),
            remediation="Add the comparison rate alongside the advertised rate.",
            citation=_cit(
                "AU-BANK-COMPARISON-RATE",
                "Comparison-rate disclosure (AU)",
                "National Consumer Credit Protection rules",
            ),
        ),
        Rule(
            id="AU-BANK-CONSENT-SPAM",
            kind=RuleKind.CONSENT,
            check=CheckType.CONSENT_REQUIRED,
            description="Electronic marketing requires recipient consent (Spam / Privacy Act).",
            market=Market.AU,
            vertical=Vertical.BANKING,
            severity=Severity.HIGH,
            consent_purpose="marketing",
            remediation="Only message consented recipients; include an unsubscribe facility.",
            citation=_cit(
                "AU-BANK-CONSENT-SPAM",
                "Electronic-marketing consent (AU)",
                "Spam Act / Privacy Act (APPs)",
            ),
        ),
    ),
    # -------------------- Singapore — online retail -------------------- #
    (Market.SG, Vertical.ONLINE_RETAIL): (
        Rule(
            id="SG-RETAIL-CLAIM-LOWEST",
            kind=RuleKind.CLAIM,
            check=CheckType.FORBIDDEN_PHRASE,
            description="Unqualified 'lowest price' / 'cheapest' superlatives are prohibited.",
            market=Market.SG,
            vertical=Vertical.ONLINE_RETAIL,
            severity=Severity.HIGH,
            patterns=("lowest price guaranteed", "cheapest anywhere", "guaranteed cheapest"),
            remediation="Qualify the price claim or remove the superlative.",
            citation=_cit(
                "SG-RETAIL-CLAIM-LOWEST",
                "Price-claim fairness standard (SG)",
                "Consumer Protection (Fair Trading) Act",
            ),
        ),
        Rule(
            id="SG-RETAIL-DISCOUNT-MAX",
            kind=RuleKind.CLAIM,
            check=CheckType.NUMERIC_MAX,
            description="An advertised discount must not exceed the approved 80% ceiling.",
            market=Market.SG,
            vertical=Vertical.ONLINE_RETAIL,
            severity=Severity.MEDIUM,
            field="discount_pct",
            limit=80.0,
            remediation="Cap the advertised discount at 80% or attach a special approval.",
            citation=_cit(
                "SG-RETAIL-DISCOUNT-MAX",
                "Discount-representation standard (SG)",
                "Consumer Protection (Fair Trading) Act",
            ),
        ),
        Rule(
            id="SG-RETAIL-STOCK",
            kind=RuleKind.PERMISSION,
            check=CheckType.REQUIRED_FIELD,
            description="A promoted offer must declare available stock to avoid bait advertising.",
            market=Market.SG,
            vertical=Vertical.ONLINE_RETAIL,
            severity=Severity.HIGH,
            field="stock_on_hand",
            remediation="Set the stock_on_hand field to the genuine available quantity.",
            citation=_cit(
                "SG-RETAIL-STOCK",
                "Bait-advertising standard (SG)",
                "Consumer Protection (Fair Trading) Act",
            ),
        ),
        Rule(
            id="SG-RETAIL-CONSENT-PDPA",
            kind=RuleKind.CONSENT,
            check=CheckType.CONSENT_REQUIRED,
            description="Marketing email/SMS requires PDPA consent.",
            market=Market.SG,
            vertical=Vertical.ONLINE_RETAIL,
            severity=Severity.HIGH,
            consent_purpose="marketing",
            remediation="Send only to recipients who granted PDPA marketing consent.",
            citation=_cit(
                "SG-RETAIL-CONSENT-PDPA",
                "PDPA marketing-consent requirement (SG)",
                "Personal Data Protection Act",
            ),
        ),
    ),
    # ---------------------- Japan — online retail ---------------------- #
    (Market.JP, Vertical.ONLINE_RETAIL): (
        Rule(
            id="JP-RETAIL-CLAIM-NO1",
            kind=RuleKind.CLAIM,
            check=CheckType.FORBIDDEN_PHRASE,
            description="Unsubstantiated 'No.1' / superlative claims are prohibited (Keihyo).",
            market=Market.JP,
            vertical=Vertical.ONLINE_RETAIL,
            severity=Severity.HIGH,
            patterns=("no.1 in japan", "業界最安", "world's best"),
            remediation="Substantiate the ranking claim with a cited survey, or remove it.",
            citation=_cit(
                "JP-RETAIL-CLAIM-NO1",
                "Premiums and Representations Act standard (JP)",
                "Act against Unjustifiable Premiums and Misleading Representations",
            ),
        ),
        Rule(
            id="JP-RETAIL-PRICE-DISCLOSURE",
            kind=RuleKind.CLAIM,
            check=CheckType.REQUIRED_DISCLOSURE,
            description="A price must be shown tax-included (sogaku hyoji).",
            market=Market.JP,
            vertical=Vertical.ONLINE_RETAIL,
            severity=Severity.MEDIUM,
            patterns=("tax included",),
            remediation="Display the total tax-included price.",
            citation=_cit(
                "JP-RETAIL-PRICE-DISCLOSURE",
                "Total-price display rule (JP)",
                "Act on Specified Commercial Transactions",
            ),
        ),
        Rule(
            id="JP-RETAIL-CONSENT-APPI",
            kind=RuleKind.CONSENT,
            check=CheckType.CONSENT_REQUIRED,
            description="Marketing use of personal data requires APPI consent.",
            market=Market.JP,
            vertical=Vertical.ONLINE_RETAIL,
            severity=Severity.HIGH,
            consent_purpose="marketing",
            remediation="Confirm APPI marketing consent before targeting.",
            citation=_cit(
                "JP-RETAIL-CONSENT-APPI",
                "APPI marketing-consent requirement (JP)",
                "Act on the Protection of Personal Information",
            ),
        ),
    ),
    # -------------------- Australia — online retail -------------------- #
    (Market.AU, Vertical.ONLINE_RETAIL): (
        Rule(
            id="AU-RETAIL-WAS-NOW",
            kind=RuleKind.CLAIM,
            check=CheckType.FORBIDDEN_PHRASE,
            description="No false 'was/now' two-price claims (ACL).",
            market=Market.AU,
            vertical=Vertical.ONLINE_RETAIL,
            severity=Severity.HIGH,
            patterns=("was $999 now", "biggest sale ever", "lowest price guaranteed"),
            remediation="Use a genuine prior price, or remove the two-price comparison.",
            citation=_cit(
                "AU-RETAIL-WAS-NOW",
                "Two-price / was-now standard (AU)",
                "Australian Consumer Law",
            ),
        ),
        Rule(
            id="AU-RETAIL-DISCOUNT-MAX",
            kind=RuleKind.CLAIM,
            check=CheckType.NUMERIC_MAX,
            description="An advertised discount must not exceed the approved 75% ceiling.",
            market=Market.AU,
            vertical=Vertical.ONLINE_RETAIL,
            severity=Severity.MEDIUM,
            field="discount_pct",
            limit=75.0,
            remediation="Cap the advertised discount at 75% or attach a special approval.",
            citation=_cit(
                "AU-RETAIL-DISCOUNT-MAX",
                "Discount-representation standard (AU)",
                "Australian Consumer Law",
            ),
        ),
        Rule(
            id="AU-RETAIL-CONSENT-SPAM",
            kind=RuleKind.CONSENT,
            check=CheckType.CONSENT_REQUIRED,
            description="Electronic marketing requires recipient consent (Spam Act).",
            market=Market.AU,
            vertical=Vertical.ONLINE_RETAIL,
            severity=Severity.HIGH,
            consent_purpose="marketing",
            remediation="Only message consented recipients; include an unsubscribe facility.",
            citation=_cit(
                "AU-RETAIL-CONSENT-SPAM",
                "Electronic-marketing consent (AU)",
                "Spam Act",
            ),
        ),
    ),
}


def rule_sets() -> dict[_Key, RuleSet]:
    """Materialise the seeded rule sets keyed by (market, vertical)."""
    return {
        key: RuleSet(market=key[0], vertical=key[1], rules=rules) for key, rules in _RULES.items()
    }


# Flat list of every seeded rule, for the SQLite FTS5 local store.
ALL_RULES: tuple[Rule, ...] = tuple(rule for rules in _RULES.values() for rule in rules)
RULE_SETS: dict[_Key, RuleSet] = rule_sets()


# --------------------------------------------------------------------------- #
# Green-claim substantiation evidence (obviously fictional, two tenants)
# --------------------------------------------------------------------------- #
# The tenants match the seeded dev personas in ``local/identity.py`` (``demo-brand`` and
# ``other-brand``) so the offline demo and the tests can exercise the tenant boundary: a
# principal from ``other-brand`` must be refused ``demo-brand``'s evidence with a 403.
# Every issuer, reference and asset id below is invented; nothing here is a real
# certification, registry record or company.
SEED_EVIDENCE: tuple[SubstantiationEvidence, ...] = (
    # camp-green-au-001: a carbon-neutral campaign whose offset retirement record has
    # lapsed, so the claim is only PARTIALLY substantiated (the demo's headline case).
    SubstantiationEvidence(
        id="ev-demo-0001",
        tenant="demo-brand",
        asset_id="camp-green-au-001",
        kind=EvidenceKind.EMISSIONS_INVENTORY,
        title="FY2026 operational emissions inventory (FICTIONAL)",
        categories=(GreenClaimCategory.CARBON_NEUTRAL, GreenClaimCategory.NET_ZERO),
        issued_date="2026-03-31",
        issuer="Example Assurance Partners (FICTIONAL)",
        independently_verified=True,
        reference="dms://example.test/evidence/ev-demo-0001",
    ),
    SubstantiationEvidence(
        id="ev-demo-0002",
        tenant="demo-brand",
        asset_id="camp-green-au-001",
        kind=EvidenceKind.CARBON_OFFSET_RETIREMENT,
        title="Offset retirement statement, 2024 vintage (FICTIONAL)",
        categories=(GreenClaimCategory.CARBON_NEUTRAL,),
        issued_date="2024-06-30",
        valid_until="2025-06-30",
        issuer="Example Offset Registry (FICTIONAL)",
        independently_verified=True,
        reference="dms://example.test/evidence/ev-demo-0002",
    ),
    SubstantiationEvidence(
        id="ev-demo-0003",
        tenant="demo-brand",
        asset_id="camp-green-au-001",
        kind=EvidenceKind.THIRD_PARTY_CERTIFICATION,
        title="Carbon-neutral certification of the claimed boundary (FICTIONAL)",
        categories=(GreenClaimCategory.CARBON_NEUTRAL,),
        issued_date="2026-01-15",
        issuer="Example Certification Bureau (FICTIONAL)",
        independently_verified=True,
        reference="dms://example.test/evidence/ev-demo-0003",
    ),
    # camp-green-sg-002: a retail ESG fund creative that IS fully substantiated.
    SubstantiationEvidence(
        id="ev-demo-0004",
        tenant="demo-brand",
        asset_id="camp-green-sg-002",
        kind=EvidenceKind.FUND_ESG_DISCLOSURE,
        title="Retail ESG fund focus, strategy and criteria disclosure (FICTIONAL)",
        categories=(GreenClaimCategory.ESG_FUND_LABEL,),
        issued_date="2026-02-01",
        issuer="Example Fund Management (FICTIONAL)",
        independently_verified=False,
        reference="dms://example.test/evidence/ev-demo-0004",
    ),
    SubstantiationEvidence(
        id="ev-demo-0005",
        tenant="demo-brand",
        asset_id="camp-green-sg-002",
        kind=EvidenceKind.THIRD_PARTY_CERTIFICATION,
        title="Independent ESG assessment of the fund (FICTIONAL)",
        categories=(GreenClaimCategory.ESG_FUND_LABEL,),
        issued_date="2026-02-10",
        issuer="Example ESG Assessors (FICTIONAL)",
        independently_verified=True,
        reference="dms://example.test/evidence/ev-demo-0005",
    ),
    # A second tenant's evidence: the cross-tenant denial target. demo-brand must never
    # see this record, and other-brand must never see demo-brand's.
    SubstantiationEvidence(
        id="ev-other-0001",
        tenant="other-brand",
        asset_id="camp-green-au-001",
        kind=EvidenceKind.CARBON_OFFSET_RETIREMENT,
        title="Offset retirement statement, 2026 vintage (FICTIONAL)",
        categories=(GreenClaimCategory.CARBON_NEUTRAL,),
        issued_date="2026-04-01",
        issuer="Example Offset Registry (FICTIONAL)",
        independently_verified=True,
        reference="dms://example.test/evidence/ev-other-0001",
    ),
)
