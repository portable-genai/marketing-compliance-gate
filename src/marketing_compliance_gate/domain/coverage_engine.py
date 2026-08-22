"""CoverageEngine — the deterministic green-claim substantiation decision.

The second heart of Mkt6, and the consequential half of the green-claims gate: given the
marketing copy, the jurisdiction's :class:`~marketing_compliance_gate.domain.models.GreenClaimPack`
and the substantiation evidence on file, it decides **which environmental claims the asset
makes** and **whether the evidence on file carries them**. Both the verdict and the coverage
number are pure code: an LLM may narrate the result for a reviewer, but it never classifies a
claim, never decides a verdict and never produces the coverage figure. A regulator or an
internal auditor must be able to re-run this and get the same answer, byte for byte.

Purity and determinism
----------------------
Stdlib only, no clock and no I/O. The ageing date (``as_of``) is an explicit parameter, so a
replay of last quarter's assessment reproduces last quarter's verdict. Claims are emitted at
most once per category, in category order; evidence ids and gaps are sorted.

Fail-closed
-----------
Every uncertainty resolves against the claim, because an unsupported environmental claim is
the harm this gate exists to prevent:

* a category with no configured :class:`SubstantiationRequirement` is UNSUBSTANTIATED,
* a requirement listing no evidence kinds is UNSUBSTANTIATED (it can never be met on purpose),
* evidence that does not name the claim's category does not count towards it,
* undated, expired or too-old evidence does not count, and
* where the jurisdiction requires independent verification, self-declared evidence does not
  count.

Jurisdiction parameterisation (B4)
----------------------------------
Nothing here knows what Australia or Japan require. The required evidence kinds, the maximum
evidence age and the independent-verification flag are data on the requirement, loaded from
the green-claim pack, so tuning a jurisdiction is a config change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import (
    ClaimCoverage,
    EvidenceKind,
    GreenClaim,
    GreenClaimCategory,
    GreenClaimPack,
    Market,
    MarketingAsset,
    SubstantiationEvidence,
    SubstantiationRequirement,
    SubstantiationVerdict,
)

# Worst-first ordering, used to roll per-claim verdicts up into the assessment verdict.
_VERDICT_RANK: dict[SubstantiationVerdict, int] = {
    SubstantiationVerdict.UNSUBSTANTIATED: 0,
    SubstantiationVerdict.PARTIALLY_SUBSTANTIATED: 1,
    SubstantiationVerdict.SUBSTANTIATED: 2,
    SubstantiationVerdict.NOT_APPLICABLE: 3,
}


def parse_iso_date(value: str) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` date, returning ``None`` for blank or malformed input."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class CoverageEngine:
    """Pure, deterministic green-claim detector and substantiation-coverage calculator."""

    # ------------------------------------------------------------------ #
    # Claim detection (pure: phrase matching over the pack's vocabulary)
    # ------------------------------------------------------------------ #
    def detect_claims(self, asset: MarketingAsset, pack: GreenClaimPack) -> tuple[GreenClaim, ...]:
        """Classify the asset's copy into the green-claim categories it asserts.

        At most one claim per category (the first matching phrase in pack order), so an
        asset repeating "carbon neutral" three times is one carbon-neutral claim, not three.
        The title is searched as well as the body: a green claim in the headline is a claim.
        """
        title = asset.title or ""
        body = asset.body or ""
        claims: list[GreenClaim] = []
        for category, phrases in pack.phrases_for(asset.market).items():
            for phrase in phrases:
                needle = phrase.casefold()
                if needle and needle in title.casefold():
                    claims.append(GreenClaim(category=category, phrase=phrase, location="title"))
                    break
                if needle and needle in body.casefold():
                    claims.append(GreenClaim(category=category, phrase=phrase, location="body"))
                    break
        claims.sort(key=lambda c: (c.category.value, c.phrase))
        return tuple(claims)

    # ------------------------------------------------------------------ #
    # Coverage (pure: the consequential decision)
    # ------------------------------------------------------------------ #
    def cover(
        self,
        claim: GreenClaim,
        pack: GreenClaimPack,
        market: Market,
        evidence: tuple[SubstantiationEvidence, ...],
        as_of: date,
    ) -> ClaimCoverage:
        """Decide whether ``claim`` is carried by ``evidence`` under ``market``'s requirement."""
        requirement = pack.requirement(market, claim.category)
        if requirement is None:
            return ClaimCoverage(
                claim=claim,
                verdict=SubstantiationVerdict.UNSUBSTANTIATED,
                coverage=0.0,
                gaps=(
                    f"no substantiation requirement is configured for "
                    f"'{claim.category.value}' in {market.value}; the claim cannot be "
                    "assessed and is treated as unsubstantiated",
                ),
                remediation=(
                    "Configure the requirement for this category in the green-claim pack, "
                    "or remove the claim."
                ),
            )
        citations = (requirement.citation,) if requirement.citation is not None else ()
        if not requirement.required_kinds:
            return ClaimCoverage(
                claim=claim,
                verdict=SubstantiationVerdict.UNSUBSTANTIATED,
                coverage=0.0,
                gaps=(
                    f"the configured requirement for '{claim.category.value}' lists no "
                    "evidence kinds, so it can never be satisfied",
                ),
                remediation=requirement.remediation,
                citations=citations,
            )

        relevant = tuple(e for e in evidence if claim.category in e.categories)
        satisfied: list[EvidenceKind] = []
        missing: list[EvidenceKind] = []
        counted_ids: set[str] = set()
        expired_ids: set[str] = set()
        gaps: list[str] = []

        for kind in requirement.required_kinds:
            of_kind = tuple(e for e in relevant if e.kind is kind)
            usable = tuple(e for e in of_kind if self._usable(e, requirement, as_of))
            expired_ids.update(e.id for e in of_kind if not self._in_date(e, requirement, as_of))
            if usable:
                satisfied.append(kind)
                counted_ids.update(e.id for e in usable)
                continue
            missing.append(kind)
            gaps.append(self._gap(kind, of_kind, requirement, as_of))

        coverage = round(len(satisfied) / len(requirement.required_kinds), 4)
        if coverage >= 1.0:
            verdict = SubstantiationVerdict.SUBSTANTIATED
        elif coverage <= 0.0:
            verdict = SubstantiationVerdict.UNSUBSTANTIATED
        else:
            verdict = SubstantiationVerdict.PARTIALLY_SUBSTANTIATED
        return ClaimCoverage(
            claim=claim,
            verdict=verdict,
            coverage=coverage,
            required_kinds=tuple(requirement.required_kinds),
            satisfied_kinds=tuple(satisfied),
            missing_kinds=tuple(missing),
            evidence_ids=tuple(sorted(counted_ids)),
            expired_evidence_ids=tuple(sorted(expired_ids)),
            gaps=tuple(gaps),
            remediation=""
            if verdict is SubstantiationVerdict.SUBSTANTIATED
            else (requirement.remediation),
            citations=citations,
        )

    def assess(
        self,
        asset: MarketingAsset,
        pack: GreenClaimPack,
        evidence: tuple[SubstantiationEvidence, ...],
        as_of: date,
    ) -> tuple[tuple[ClaimCoverage, ...], SubstantiationVerdict, float]:
        """Cover every detected claim and roll the results up (claims, verdict, coverage).

        The overall coverage is the mean of the per-claim coverages and the overall verdict
        is the WORST per-claim verdict: one unsupported claim is enough to hold the asset,
        which is the whole point of the gate. An asset making no green claim is
        ``NOT_APPLICABLE`` with coverage 1.0 rather than trivially "substantiated".
        """
        claims = self.detect_claims(asset, pack)
        if not claims:
            return (), SubstantiationVerdict.NOT_APPLICABLE, 1.0
        covered = tuple(self.cover(claim, pack, asset.market, evidence, as_of) for claim in claims)
        coverage = round(sum(c.coverage for c in covered) / len(covered), 4)
        verdict = min((c.verdict for c in covered), key=lambda v: _VERDICT_RANK[v])
        return covered, verdict, coverage

    def categories(self, claims: tuple[GreenClaim, ...]) -> frozenset[GreenClaimCategory]:
        """The set of categories claimed, used to select the applicable green-claim rules."""
        return frozenset(c.category for c in claims)

    # ------------------------------------------------------------------ #
    # Per-evidence predicates (pure and total)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _in_date(
        evidence: SubstantiationEvidence, requirement: SubstantiationRequirement, as_of: date
    ) -> bool:
        """True when the evidence is dated, unexpired and within the jurisdiction's max age."""
        issued = parse_iso_date(evidence.issued_date)
        if issued is None:
            return False  # undated evidence cannot be aged, so it cannot be relied on
        if issued > as_of:
            return False  # future-dated evidence is not evidence at ``as_of``
        expires = parse_iso_date(evidence.valid_until)
        if expires is not None and expires < as_of:
            return False
        max_age = requirement.max_evidence_age_days
        return not (max_age > 0 and (as_of - issued).days > max_age)

    @classmethod
    def _usable(
        cls,
        evidence: SubstantiationEvidence,
        requirement: SubstantiationRequirement,
        as_of: date,
    ) -> bool:
        if requirement.requires_independent_verification and not evidence.independently_verified:
            return False
        return cls._in_date(evidence, requirement, as_of)

    @classmethod
    def _gap(
        cls,
        kind: EvidenceKind,
        of_kind: tuple[SubstantiationEvidence, ...],
        requirement: SubstantiationRequirement,
        as_of: date,
    ) -> str:
        """Explain, for the reviewer, exactly why a required evidence kind is not met."""
        if not of_kind:
            return f"no {kind.value} evidence is on file for this claim"
        stale = [e.id for e in of_kind if not cls._in_date(e, requirement, as_of)]
        unverified = [
            e.id
            for e in of_kind
            if requirement.requires_independent_verification and not e.independently_verified
        ]
        if stale:
            return (
                f"{kind.value} evidence {', '.join(sorted(stale))} is undated, expired or "
                f"older than the {requirement.max_evidence_age_days}-day limit at {as_of}"
            )
        if unverified:
            return (
                f"{kind.value} evidence {', '.join(sorted(unverified))} is self-declared, "
                "and this jurisdiction requires independent verification"
            )
        return f"no usable {kind.value} evidence is on file for this claim"
