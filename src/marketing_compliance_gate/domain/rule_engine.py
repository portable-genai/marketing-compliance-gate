"""RuleEngine — the deterministic claim / permission / brand / consent checker.

The heart of D6 (see the ``deterministic-domain-service`` skill): pure, stdlib-only,
replayable. Given a marketing :class:`MarketingAsset` and the per-market, per-vertical
:class:`RuleSet`, it evaluates every rule with a fixed, transparent check and returns a
stable, severity-ordered tuple of :class:`ClaimFinding` plus the :class:`ConsentCheck`
results. This is the consequential "does this asset comply?" decision a regulator /
auditor must be able to re-run, so it is code, not an LLM call. The LLM only narrates the
resulting findings afterwards; it never decides whether a check passes.

Determinism: same (asset, rule set) -> same findings, byte for byte. Each
:class:`CheckType` maps to one pure predicate over the asset's ``body`` / ``fields`` /
``granted_consents``; severity comes from the rule data; findings are ordered by
(failed-first, severity desc, rule id) so the output never drifts between runs.

Generic + APAC: the engine has NO market- or vertical-specific branch. All locale,
market and vertical specificity lives in the seeded :class:`RuleSet` (config + seed), so
the same code reviews a JP banking financial promotion and an AU online-retail sale.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .models import (
    CheckType,
    ClaimFinding,
    ConsentCheck,
    FindingStatus,
    MarketingAsset,
    Rule,
    RuleKind,
    RuleSet,
    Severity,
)

_SEVERITY_RANK = {
    Severity.CRITICAL: 3,
    Severity.HIGH: 2,
    Severity.MEDIUM: 1,
    Severity.LOW: 0,
}


def _contains(haystack: str, needle: str) -> bool:
    """Case-insensitive substring match (the rule patterns are plain phrases)."""
    return needle.casefold() in haystack.casefold()


def _to_float(value: str) -> float | None:
    """Parse a numeric asset field, tolerating ``%``, commas and surrounding spaces."""
    if value is None:
        return None
    cleaned = value.strip().replace(",", "").rstrip("%").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class RuleEngine:
    """Pure, deterministic compliance checker over an asset and its rule set."""

    def check(self, asset: MarketingAsset, rule_set: RuleSet) -> tuple[ClaimFinding, ...]:
        """Evaluate every non-consent rule and return ordered findings.

        Consent rules are evaluated separately by :meth:`consent_checks` (they yield
        :class:`ConsentCheck` results as well as findings), so this method covers the
        CLAIM / PERMISSION / BRAND rules. The result is deterministically ordered.
        """
        findings = [
            self._evaluate(asset, rule)
            for rule in rule_set.rules
            if rule.kind is not RuleKind.CONSENT
        ]
        findings.sort(key=self._sort_key)
        return tuple(findings)

    def consent_checks(
        self, asset: MarketingAsset, rule_set: RuleSet
    ) -> tuple[tuple[ConsentCheck, ...], tuple[ClaimFinding, ...]]:
        """Evaluate consent rules for an ASSET, returning both the checks and their findings.

        The consent decision is pure code: a purpose is granted iff it appears in the
        asset's ``granted_consents``. Each required-but-not-granted purpose yields a
        failing :class:`ClaimFinding` so consent gaps show up in the same audit trail.

        Thin wrapper over :meth:`consent_checks_for`, which is the same evaluation driven by
        a granted-purpose set from anywhere: the asset path passes the asset's declared
        consents, and the consent and preference store passes the purposes a data subject's
        stored records actually grant at a given instant. One rule engine, one set of rule
        citations, two callers.
        """
        return self.consent_checks_for(asset.granted_consents, rule_set, asset=asset)

    def consent_checks_for(
        self,
        granted_purposes: Iterable[str],
        rule_set: RuleSet,
        *,
        asset: MarketingAsset | None = None,
    ) -> tuple[tuple[ConsentCheck, ...], tuple[ClaimFinding, ...]]:
        """Evaluate the rule set's consent rules against an explicit granted-purpose set.

        Pure and total: a purpose is granted iff it is in ``granted_purposes`` (case-folded).
        Anything not in that set is NOT granted, so an unknown consent state fails closed
        here exactly as it does in the consent store's own engine.

        ``asset`` is optional because the two callers ask different questions. The
        asset-review path supplies one, so a CONSENT-kind rule carrying a copy- or
        field-based check (a required consent disclosure in the creative, say) still runs.
        The consent and preference store asks about a data SUBJECT, where there is no copy to
        scan: such a rule is not applicable to that question rather than unknown, so it is
        omitted rather than being scored against an empty asset, which would manufacture a
        failing finding out of a rule that was never about the subject.
        """
        checks: list[ConsentCheck] = []
        findings: list[ClaimFinding] = []
        granted = {c.casefold() for c in granted_purposes}
        for rule in rule_set.by_kind(RuleKind.CONSENT):
            if rule.check is not CheckType.CONSENT_REQUIRED:
                # A consent-kind rule with another check type still runs its check, but only
                # where there is an asset for it to run against (see the docstring).
                if asset is not None:
                    findings.append(self._evaluate(asset, rule))
                continue
            is_granted = rule.consent_purpose.casefold() in granted
            checks.append(
                ConsentCheck(
                    purpose=rule.consent_purpose,
                    required=True,
                    granted=is_granted,
                    rule_id=rule.id,
                    citations=self._citations(rule),
                )
            )
            findings.append(
                ClaimFinding(
                    rule_id=rule.id,
                    rule_kind=RuleKind.CONSENT,
                    status=FindingStatus.PASS if is_granted else FindingStatus.FAIL,
                    severity=rule.severity,
                    message=(
                        f"Consent '{rule.consent_purpose}' is granted."
                        if is_granted
                        else f"Required consent '{rule.consent_purpose}' is NOT granted."
                    ),
                    evidence="" if is_granted else f"missing consent: {rule.consent_purpose}",
                    remediation="" if is_granted else rule.remediation,
                    citations=self._citations(rule),
                )
            )
        findings.sort(key=self._sort_key)
        return tuple(checks), tuple(findings)

    # ------------------------------------------------------------------ #
    # Per-rule evaluation (one pure predicate per CheckType)
    # ------------------------------------------------------------------ #
    def _evaluate(self, asset: MarketingAsset, rule: Rule) -> ClaimFinding:
        passed, evidence, message = self._run_check(asset, rule)
        return ClaimFinding(
            rule_id=rule.id,
            rule_kind=rule.kind,
            status=FindingStatus.PASS if passed else FindingStatus.FAIL,
            severity=rule.severity,
            message=message,
            evidence="" if passed else evidence,
            remediation="" if passed else rule.remediation,
            citations=self._citations(rule),
        )

    def _run_check(self, asset: MarketingAsset, rule: Rule) -> tuple[bool, str, str]:
        """Return (passed, evidence, message) for one rule. Pure and total."""
        # Claim / brand checks scan the title AND the body: a prohibited claim placed only
        # in the headline is still a violation, and a disclosure may legitimately sit in
        # either, so the searchable copy is title + body.
        copy = f"{asset.title}\n{asset.body}"

        if rule.check is CheckType.FORBIDDEN_PHRASE:
            hit = next((p for p in rule.patterns if _contains(copy, p)), None)
            if hit is None:
                return True, "", f"No prohibited phrase for {rule.id}."
            return False, f"prohibited phrase: '{hit}'", rule.description

        if rule.check is CheckType.REQUIRED_DISCLOSURE:
            missing = tuple(p for p in rule.patterns if not _contains(copy, p))
            if not missing:
                return True, "", f"Required disclosure present for {rule.id}."
            return False, f"missing disclosure: {', '.join(missing)}", rule.description

        if rule.check is CheckType.REQUIRED_FIELD:
            value = asset.fields.get(rule.field, "").strip()
            if value:
                return True, "", f"Required field '{rule.field}' is set."
            return False, f"missing field: '{rule.field}'", rule.description

        if rule.check is CheckType.NUMERIC_MAX:
            number = _to_float(asset.fields.get(rule.field, ""))
            if number is None:
                # An absent / unparseable numeric field cannot be vouched safe: fail.
                return (
                    False,
                    f"field '{rule.field}' is absent or not numeric",
                    rule.description,
                )
            limit = rule.limit if rule.limit is not None else float("inf")
            if number <= limit:
                return True, "", f"'{rule.field}' {number:g} within limit {limit:g}."
            return (
                False,
                f"'{rule.field}' {number:g} exceeds limit {limit:g}",
                rule.description,
            )

        if rule.check is CheckType.CONSENT_REQUIRED:
            granted = {c.casefold() for c in asset.granted_consents}
            ok = rule.consent_purpose.casefold() in granted
            if ok:
                return True, "", f"Consent '{rule.consent_purpose}' granted."
            return (
                False,
                f"missing consent: {rule.consent_purpose}",
                rule.description,
            )

        # Unknown check type: fail closed so an unmodelled rule never silently passes.
        return False, f"unsupported check type: {rule.check}", rule.description

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _citations(rule: Rule) -> tuple:
        return (rule.citation,) if rule.citation is not None else ()

    @staticmethod
    def _sort_key(finding: ClaimFinding) -> tuple[int, int, str]:
        # Failures first, then severity descending, then rule id for stability.
        return (
            0 if finding.failed else 1,
            -_SEVERITY_RANK[finding.severity],
            finding.rule_id,
        )
