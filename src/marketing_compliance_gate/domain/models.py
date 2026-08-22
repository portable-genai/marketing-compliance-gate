"""Domain models for Marketing Compliance and Brand Governance (D6).

This module is the heart of the hexagon. It has **no dependency on Google Cloud,
ADK, FastAPI, or any framework** (only the Python standard library). Every adapter
(GCP, remote-platform, or on-prem placeholder) speaks in terms of these types, which
is what lets the managed-service stack be swapped for an on-premise one without
touching domain logic (no vendor lock-in, ports and adapters).

D6 is deliberately **generic marketing compliance**, not a bank-specific tool:

* ``Vertical`` is a configurable enum (banking, online retail), so the same rule
  engine reviews a deposit-account financial promotion and an e-commerce ``70% OFF``
  sale claim.
* ``Market`` is a configurable enum (JP, AU, SG) carrying its residency region and
  locales, so JP, AU and SG are first-class config and seed, never hard-coded.

The deterministic engines are the heart of the system: a rule engine that checks the
claims, permissions and brand of a marketing :class:`MarketingAsset` against the
per-market, per-vertical :class:`RuleSet` (advertising + consumer-protection + consent
rules), and a consent engine. The LLM only narrates the already-decided findings; every
finding carries a :class:`Citation` to the rule it failed, and every consequential
:class:`Review` sets ``requires_human_review`` — the marketing maker-checker gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from agent_eval_kit.report import EvalMetricResult as EvalMetricResult
from agent_eval_kit.report import EvalReport as EvalReport
from hex_service_kit import StrEnum
from hex_service_kit.observability import TokenUsage

from .kernel import ThinkingLevel


def utcnow() -> datetime:
    """Timezone-aware UTC now (the single clock the domain uses)."""
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Vertical and Market — the two configurable axes (generic, multi-vertical, APAC)
# --------------------------------------------------------------------------- #
class Vertical(StrEnum):
    """The business vertical the review is scoped to.

    D6 is generic: banking is ONE vertical and online retail is another. The rule
    engine selects the per-vertical rule set; no bank-only logic is baked into the
    domain. Banking's financial-promotion rules are one configured rule set among
    others, not the only frame.
    """

    BANKING = "banking"
    ONLINE_RETAIL = "online_retail"


class Market(StrEnum):
    """A supported market (Japan, Australia, Singapore).

    The residency region, locales and per-market rule sets are config + seed (see
    :data:`MARKET_PROFILES` and ``config/settings.yaml``), never hard-coded into a
    branch. Adding a market is a config + seed change, not a code change.
    """

    JP = "JP"
    AU = "AU"
    SG = "SG"


@dataclass(frozen=True, slots=True)
class MarketProfile:
    """Static, fictional-data-friendly metadata for a market.

    The residency ``region`` is a GCP regional id selectable at deploy and validated;
    ``locales`` drives bilingual (ja + en) review. These defaults can be overridden in
    ``config/settings.yaml`` so the catalog stays config-driven.
    """

    market: Market
    region: str  # GCP residency region, e.g. "asia-northeast1" (Tokyo)
    display_name: str
    locales: tuple[str, ...]  # e.g. ("ja", "en") or ("en",)
    currency: str = ""


# Built-in defaults. Region/locale are config + seed: settings.yaml may override these.
MARKET_PROFILES: dict[Market, MarketProfile] = {
    Market.JP: MarketProfile(
        market=Market.JP,
        region="asia-northeast1",
        display_name="Japan",
        locales=("ja", "en"),
        currency="JPY",
    ),
    Market.AU: MarketProfile(
        market=Market.AU,
        region="australia-southeast1",
        display_name="Australia",
        locales=("en",),
        currency="AUD",
    ),
    Market.SG: MarketProfile(
        market=Market.SG,
        region="asia-southeast1",
        display_name="Singapore",
        locales=("en",),
        currency="SGD",
    ),
}


# --------------------------------------------------------------------------- #
# Shared severity scale (declared early: rules reference it)
# --------------------------------------------------------------------------- #
class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


# --------------------------------------------------------------------------- #
# Provenance / citation
# --------------------------------------------------------------------------- #
class SourceType(StrEnum):
    """Every citation names which kind of evidence/authority it points to."""

    RULE = "rule"  # a rule in the per-market/vertical rule set (the KB)
    REGULATION = "regulation"  # the underlying statute / regulator standard
    GUIDELINE = "guideline"  # an internal brand / marketing guideline
    POLICY = "policy"  # an internal compliance policy
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Citation:
    """Provenance attached to every finding in a review.

    A compliance officer must be able to trace every finding back to the exact rule
    and its underlying authority: an auditable review is the whole point of D6.
    """

    source_id: str  # the rule id (e.g. "SG-BANK-APR-001")
    source_type: SourceType
    title: str
    url: str = ""
    page: int | None = None
    published_date: str | None = None  # ISO date the rule/authority was published
    snippet: str = ""
    score: float | None = None


# --------------------------------------------------------------------------- #
# The rule set (per market + per vertical) — the File Search KB, config + seed
# --------------------------------------------------------------------------- #
class RuleKind(StrEnum):
    """What aspect of a marketing asset a rule governs (generic across verticals)."""

    CLAIM = "claim"  # advertising-claim substantiation / prohibited wording
    PERMISSION = "permission"  # required permission / approval / disclosure to run
    BRAND = "brand"  # brand-guideline / tone / trademark usage
    CONSENT = "consent"  # consumer-consent / marketing-permission (PDPA etc.)
    GREEN_CLAIM = "green_claim"  # environmental / sustainability claim (anti-greenwashing)


class GreenClaimCategory(StrEnum):
    """The category vocabulary for environmental / sustainability claims (B5).

    A green claim is only assessable once it is classified: "carbon neutral" and
    "recyclable" are substantiated by completely different evidence. The categories are a
    closed, validated vocabulary; the PHRASES that map copy to a category, and the evidence
    each category requires, are jurisdiction-parameterised data in the green-claim rule
    pack (``rulepacks/green_claims.yaml``), never a branch in engine code.
    """

    CARBON_NEUTRAL = "carbon_neutral"
    NET_ZERO = "net_zero"
    RECYCLABLE = "recyclable"
    BIODEGRADABLE = "biodegradable"
    RENEWABLE_ENERGY = "renewable_energy"
    SUSTAINABLE_SOURCING = "sustainable_sourcing"
    ESG_FUND_LABEL = "esg_fund_label"
    GREEN_FINANCE_PROCEEDS = "green_finance_proceeds"


class EvidenceKind(StrEnum):
    """The kinds of substantiation evidence a green claim can be supported by (B5)."""

    EMISSIONS_INVENTORY = "emissions_inventory"
    CARBON_OFFSET_RETIREMENT = "carbon_offset_retirement"
    LIFECYCLE_ASSESSMENT = "lifecycle_assessment"
    THIRD_PARTY_CERTIFICATION = "third_party_certification"
    ACCREDITED_TEST_REPORT = "accredited_test_report"
    RENEWABLE_ENERGY_ATTRIBUTE = "renewable_energy_attribute"
    SUPPLIER_ATTESTATION = "supplier_attestation"
    FUND_ESG_DISCLOSURE = "fund_esg_disclosure"
    USE_OF_PROCEEDS_REPORT = "use_of_proceeds_report"
    TRANSITION_PLAN = "transition_plan"


class CheckType(StrEnum):
    """How a rule's deterministic check is evaluated against an asset.

    The check itself is pure code (replayable); the rule data (patterns, fields,
    thresholds) is config + seed, so adding a rule is a data change, not a code change.
    """

    FORBIDDEN_PHRASE = "forbidden_phrase"  # body must NOT contain any pattern
    REQUIRED_DISCLOSURE = "required_disclosure"  # body MUST contain a pattern
    REQUIRED_FIELD = "required_field"  # asset metadata MUST set a field (truthy)
    NUMERIC_MAX = "numeric_max"  # a numeric asset field must be <= a limit
    CONSENT_REQUIRED = "consent_required"  # a consent purpose must be granted


@dataclass(frozen=True, slots=True)
class Rule:
    """One deterministic compliance rule, scoped to a market and vertical.

    Rules are data: the engine's behaviour is fixed code, while ``patterns`` /
    ``field`` / ``limit`` / ``consent_purpose`` come from the seeded rule KB
    (config + seed). ``citation`` points at the underlying authority.
    """

    id: str
    kind: RuleKind
    check: CheckType
    description: str
    market: Market
    vertical: Vertical
    severity: Severity
    patterns: tuple[str, ...] = ()  # for FORBIDDEN_PHRASE / REQUIRED_DISCLOSURE
    field: str = ""  # for REQUIRED_FIELD / NUMERIC_MAX
    limit: float | None = None  # for NUMERIC_MAX
    consent_purpose: str = ""  # for CONSENT_REQUIRED
    remediation: str = ""  # how a marketer fixes the finding
    citation: Citation | None = None
    # GREEN_CLAIM rules only: the claim categories that make this rule applicable. Empty
    # means unconditional. A "state the offsetting basis" disclosure rule must NOT fire on
    # an asset that makes no carbon claim, so applicability is data on the rule and the
    # SELECTION happens before the engine runs (the engine itself stays generic).
    applies_to_categories: tuple[GreenClaimCategory, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleSet:
    """The set of rules in force for a (market, vertical).

    This is the per-market + per-vertical advertising + consumer-protection + consent
    rule KB (File Search store on GCP; SQLite FTS5 locally). Generic and APAC: the
    banking financial-promotion rules are one set among others.
    """

    market: Market
    vertical: Vertical
    rules: tuple[Rule, ...] = ()

    def by_kind(self, kind: RuleKind) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.kind is kind)


# --------------------------------------------------------------------------- #
# The asset under review (Campaign / Creative / Offer) — generic across verticals
# --------------------------------------------------------------------------- #
class AssetType(StrEnum):
    CAMPAIGN = "campaign"
    CREATIVE = "creative"
    OFFER = "offer"


@dataclass(frozen=True, slots=True)
class MarketingAsset:
    """A Campaign, Creative or Offer submitted for compliance review.

    ``body`` is the human-readable marketing copy the claim/brand checks scan.
    ``fields`` holds structured metadata (e.g. ``{"apr": "4.50", "disclaimer": "..."}``
    for banking, or ``{"discount_pct": "70", "stock_on_hand": "0"}`` for retail) that
    the permission / numeric checks evaluate. ``granted_consents`` lists the consent
    purposes the audience has granted. All generic across verticals.
    """

    id: str
    asset_type: AssetType
    title: str
    body: str
    market: Market
    vertical: Vertical
    fields: dict[str, str] = field(default_factory=dict)
    granted_consents: tuple[str, ...] = ()
    submitted_by: str = ""


# --------------------------------------------------------------------------- #
# Generation (LLM) — the LLM only narrates findings; never decides them
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class WebCitation:
    """Provenance for a public-web grounded fact returned by the model."""

    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class LlmMessage:
    role: str  # "user" | "model" | "system"
    content: str


@dataclass(frozen=True, slots=True)
class LlmRequest:
    messages: tuple[LlmMessage, ...]
    system_instruction: str | None = None
    model: str | None = None  # None => adapter default from config
    thinking: ThinkingLevel = ThinkingLevel.MEDIUM
    temperature: float = 0.2
    max_output_tokens: int = 4096
    response_schema: dict | None = None  # JSON schema for structured output


# ``TokenUsage`` is NOT declared here; it is imported from the commons at the top of this
# module and used by :class:`LlmResponse` below. See that import for why.


@dataclass(frozen=True, slots=True)
class LlmResponse:
    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    web_citations: tuple[WebCitation, ...] = ()
    raw: dict | None = None


# --------------------------------------------------------------------------- #
# Safety (guardrail) — A1 Guardrail Gateway concern
# --------------------------------------------------------------------------- #
class GuardrailCategory(StrEnum):
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    SENSITIVE_DATA = "sensitive_data"
    MALICIOUS_URL = "malicious_url"
    OTHER = "other"


class Direction(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class GuardrailFinding:
    category: GuardrailCategory
    confidence: str  # "low" | "medium" | "high"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GuardrailVerdict:
    allowed: bool
    direction: Direction
    findings: tuple[GuardrailFinding, ...] = ()
    sanitized_text: str | None = None
    reason: str = ""


# --------------------------------------------------------------------------- #
# Audit and observability — A5 Observability, Audit and FinOps concern
# --------------------------------------------------------------------------- #
class Decision(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    ESCALATED = "escalated"  # routed to a human (maker-checker)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable, WORM-stored record of one compliance-review interaction."""

    action: str  # "review" | "approve" | "rule_lookup"
    actor: str  # authenticated reviewer / service identity
    decision: Decision
    prompt: str = ""
    response: str = ""
    citations: tuple[Citation, ...] = ()
    resource: str = "marketing-compliance-gate"
    trace_id: str | None = None
    timestamp: datetime = field(default_factory=utcnow)
    metadata: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Evaluation gate — A4 AI Quality and Model-Risk concern
# --------------------------------------------------------------------------- #
# ``EvalMetricResult`` / ``EvalReport`` are NOT declared here either; they are imported
# from ``agent_eval_kit.report`` at the top of this module and re-exported, so every import
# of ``domain.models`` still reaches them at the same name.
#
# The commons ``EvalReport`` carries the same three fields this module used to declare plus
# the run-evidence fields a promotion verdict has to be replayable from (``run_id``,
# ``dataset_version``, ``dataset_digest``, ``evaluator``, ``schema_version``, ``trace_id``,
# ``correlation_id``, ``artifact_refs``, ``attested``), all defaulted, so nothing that
# constructed the old three-field report has to change. ``passed`` is the identical
# fail-closed rule: ``all(())`` is vacuously True, so a report that scored nothing must not
# report PASSED, and ``eval/run_eval.py`` exits 0 on this property.


# --------------------------------------------------------------------------- #
# Governance — A3 Agent Registry and the MCP tool catalog
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AgentSkill:
    id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class AgentCard:
    """Minimal A2A-style agent card published at /.well-known/agent-card.json."""

    name: str
    description: str
    url: str
    version: str
    skills: tuple[AgentSkill, ...] = ()
    provider: str = "marketing-compliance-gate"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A governed, least-privilege tool exposed to the agent (typically via MCP)."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Findings — the deterministic output of the rule + consent engines
# --------------------------------------------------------------------------- #
class FindingStatus(StrEnum):
    PASS = "pass"  # the rule was checked and the asset complies
    FAIL = "fail"  # the rule was checked and the asset violates it


@dataclass(frozen=True, slots=True)
class ClaimFinding:
    """One deterministic finding: the outcome of evaluating one rule on the asset.

    Named ``ClaimFinding`` because claim checks are the headline case, but the same
    type carries permission, brand and consent findings (the ``rule_kind`` says which).
    Every finding cites the rule (and its authority) so it is fully auditable.
    """

    rule_id: str
    rule_kind: RuleKind
    status: FindingStatus
    severity: Severity
    message: str
    evidence: str = ""  # the offending phrase / missing field, for the reviewer
    remediation: str = ""
    citations: tuple[Citation, ...] = ()

    @property
    def failed(self) -> bool:
        return self.status is FindingStatus.FAIL


@dataclass(frozen=True, slots=True)
class ConsentCheck:
    """The deterministic consent check for one required marketing-permission purpose.

    Generic across verticals and markets (PDPA in SG, the Privacy Act / APP in AU, APPI
    in JP are seeded as consent rules). ``granted`` is decided by code from the asset's
    granted consents; the LLM never decides it.
    """

    purpose: str
    required: bool
    granted: bool
    rule_id: str = ""
    citations: tuple[Citation, ...] = ()

    @property
    def satisfied(self) -> bool:
        return (not self.required) or self.granted


# --------------------------------------------------------------------------- #
# Green claims and substantiation (the anti-greenwashing gate)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class GreenClaim:
    """One environmental claim detected in the asset copy by pure code.

    Detection is deterministic phrase matching against the jurisdiction's green-claim
    pack: the LLM never classifies a claim, because the classification decides which
    evidence the claim needs and therefore whether the asset may run.
    """

    category: GreenClaimCategory
    phrase: str  # the matched phrase, verbatim from the pack (the reviewer's evidence)
    location: str = "body"  # "title" | "body"


@dataclass(frozen=True, slots=True)
class SubstantiationEvidence:
    """One piece of evidence on file that may substantiate a green claim.

    Tenant-scoped: ``tenant`` is the isolation boundary, and every read is authorized
    against the VERIFIED principal's tenant (never a client-supplied value).
    """

    id: str
    tenant: str
    asset_id: str
    kind: EvidenceKind
    title: str
    categories: tuple[GreenClaimCategory, ...] = ()
    issued_date: str = ""  # ISO date the evidence was issued ("" => undated, fails closed)
    valid_until: str = ""  # ISO date the evidence expires ("" => no stated expiry)
    issuer: str = ""
    independently_verified: bool = False  # verified by an independent third party
    reference: str = ""  # document locator (URI / DMS id)


@dataclass(frozen=True, slots=True)
class SubstantiationRequirement:
    """What a jurisdiction requires before a category of green claim may be made.

    Bank-owned policy numbers (B4): ``required_kinds``, ``max_evidence_age_days`` and
    ``requires_independent_verification`` are configuration in the green-claim rule pack,
    with the shipped defaults equal to the reference. The coverage engine reads them; it
    never hard-codes a jurisdiction's threshold.
    """

    market: Market
    category: GreenClaimCategory
    required_kinds: tuple[EvidenceKind, ...] = ()
    # Adopter packs must name this key explicitly. Zero is a reviewed no-expiry policy, not a
    # loader fallback for an omitted control.
    max_evidence_age_days: int = 0
    requires_independent_verification: bool = False
    remediation: str = ""
    citation: Citation | None = None


@dataclass(frozen=True, slots=True)
class GreenClaimPack:
    """The jurisdiction-parameterised green-claim rule pack (config + seed, never code).

    One immutable value object holding, per market: the detection ``phrases`` that classify
    copy into a :class:`GreenClaimCategory`, the :class:`SubstantiationRequirement` for each
    category, and the ``GREEN_CLAIM``-kind :class:`Rule` rows (forbidden phrase / required
    disclosure / required field), each carrying the citation of the regulator instrument it
    comes from. Loaded from the YAML pack by
    :mod:`marketing_compliance_gate.green_pack`; the engines take it as a parameter, so adding a
    jurisdiction or tuning a threshold is a config change, not an engine change.
    """

    version: str = ""
    phrases: dict[tuple[Market, GreenClaimCategory], tuple[str, ...]] = field(default_factory=dict)
    requirements: dict[tuple[Market, GreenClaimCategory], SubstantiationRequirement] = field(
        default_factory=dict
    )
    rules: tuple[Rule, ...] = ()

    def phrases_for(self, market: Market) -> dict[GreenClaimCategory, tuple[str, ...]]:
        """The detection vocabulary in force in ``market`` (deterministically ordered)."""
        return {
            category: tuple(phrases)
            for (m, category), phrases in sorted(self.phrases.items(), key=lambda kv: kv[0][1])
            if m is market
        }

    def requirement(
        self, market: Market, category: GreenClaimCategory
    ) -> SubstantiationRequirement | None:
        return self.requirements.get((market, category))

    def rules_for(
        self,
        market: Market,
        vertical: Vertical,
        categories: frozenset[GreenClaimCategory] | set[GreenClaimCategory],
    ) -> tuple[Rule, ...]:
        """The green-claim rules that APPLY: right market, right vertical, claim made.

        A rule scoped to categories is selected only when the asset actually makes a claim
        in one of them, so a "disclose the offsetting basis" rule never fires on an asset
        that says nothing about carbon.
        """
        selected = [
            rule
            for rule in self.rules
            if rule.market is market
            and rule.vertical is vertical
            and (
                not rule.applies_to_categories
                or any(c in categories for c in rule.applies_to_categories)
            )
        ]
        return tuple(sorted(selected, key=lambda r: r.id))


class SubstantiationVerdict(StrEnum):
    """The deterministic verdict on whether a claim is carried by the evidence on file."""

    NOT_APPLICABLE = "not_applicable"  # no green claim was made
    SUBSTANTIATED = "substantiated"
    PARTIALLY_SUBSTANTIATED = "partially_substantiated"
    UNSUBSTANTIATED = "unsubstantiated"


@dataclass(frozen=True, slots=True)
class ClaimCoverage:
    """The coverage of ONE green claim by the evidence on file (pure code decides this)."""

    claim: GreenClaim
    verdict: SubstantiationVerdict
    coverage: float  # satisfied required evidence kinds / required kinds, 0.0..1.0
    required_kinds: tuple[EvidenceKind, ...] = ()
    satisfied_kinds: tuple[EvidenceKind, ...] = ()
    missing_kinds: tuple[EvidenceKind, ...] = ()
    evidence_ids: tuple[str, ...] = ()  # the evidence that counted towards coverage
    expired_evidence_ids: tuple[str, ...] = ()  # on file but out of date at ``as_of``
    gaps: tuple[str, ...] = ()  # human-readable reasons the claim is not fully carried
    remediation: str = ""
    citations: tuple[Citation, ...] = ()

    @property
    def substantiated(self) -> bool:
        return self.verdict is SubstantiationVerdict.SUBSTANTIATED


@dataclass(frozen=True, slots=True)
class SubstantiationAssessment:
    """The cited, tenant-scoped green-claim substantiation of one marketing asset.

    Consequential: it decides whether an environmental claim may be published, so the
    verdict and ``coverage`` are pure code, the narrative is the only LLM contribution, and
    the assessment always requires human review when a green claim is present.
    """

    id: str
    asset_id: str
    tenant: str
    market: Market
    vertical: Vertical
    as_of: str  # the ISO date the evidence was aged against (replayability)
    verdict: SubstantiationVerdict
    coverage: float
    claims: tuple[ClaimCoverage, ...] = ()
    findings: tuple[ClaimFinding, ...] = ()  # GREEN_CLAIM rule findings
    narrative: str = ""
    citations: tuple[Citation, ...] = ()
    requires_human_review: bool = True
    generated_at: datetime = field(default_factory=utcnow)

    @property
    def failing_findings(self) -> tuple[ClaimFinding, ...]:
        return tuple(f for f in self.findings if f.failed)

    @property
    def unsupported_claims(self) -> tuple[ClaimCoverage, ...]:
        return tuple(c for c in self.claims if not c.substantiated)

    @property
    def highest_severity(self) -> Severity | None:
        failing = self.failing_findings
        if not failing:
            return None
        return max((f.severity for f in failing), key=lambda s: _SEVERITY_ORDER[s])


# --------------------------------------------------------------------------- #
# The maker-checker approval record
# --------------------------------------------------------------------------- #
class ApprovalDecision(StrEnum):
    PENDING = "pending"  # awaiting a human checker (maker-checker gate)
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """The marketing maker-checker gate for a review.

    The agent (maker) proposes the review; a qualified human (checker) disposes. A
    review with failing findings is never auto-approved: it starts PENDING and a human
    must approve or reject before the asset may run.
    """

    review_id: str
    decision: ApprovalDecision = ApprovalDecision.PENDING
    checker: str = ""  # the human who approved/rejected; empty while PENDING
    rationale: str = ""
    decided_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.decision in (ApprovalDecision.APPROVED, ApprovalDecision.REJECTED)


# --------------------------------------------------------------------------- #
# The top-level aggregate (the Review)
# --------------------------------------------------------------------------- #
class ReviewOutcome(StrEnum):
    COMPLIANT = "compliant"  # no failing findings
    NON_COMPLIANT = "non_compliant"  # at least one failing finding


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """The inbound request to review a marketing asset."""

    asset: MarketingAsset
    actor: str = ""


@dataclass(frozen=True, slots=True)
class Review:
    """A cited compliance review of one marketing asset — D6's top-level artifact.

    The consequential output: both a clear and a block affect whether regulated marketing
    may run, so every review **requires human review** (maker-checker). The agent proposes;
    a qualified compliance officer disposes before the asset may run.
    """

    id: str
    asset_id: str
    asset_type: AssetType
    market: Market
    vertical: Vertical
    outcome: ReviewOutcome
    findings: tuple[ClaimFinding, ...] = ()
    consent_checks: tuple[ConsentCheck, ...] = ()
    summary: str = ""
    citations: tuple[Citation, ...] = ()
    approval: ApprovalRecord | None = None
    requires_human_review: bool = True
    generated_at: datetime = field(default_factory=utcnow)

    @property
    def failing_findings(self) -> tuple[ClaimFinding, ...]:
        return tuple(f for f in self.findings if f.failed)

    @property
    def highest_severity(self) -> Severity | None:
        failing = self.failing_findings
        if not failing:
            return None
        return max((f.severity for f in failing), key=lambda s: _SEVERITY_ORDER[s])
