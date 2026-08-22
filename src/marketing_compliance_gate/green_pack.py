"""Loader for the jurisdiction green-claim rule pack (config into domain values).

Lives OUTSIDE ``domain/`` because it reads YAML from disk, and the domain core stays pure
stdlib with no I/O. It turns the reference pack shipped at
``marketing_compliance_gate/rulepacks/green_claims.yaml`` (or an adopter's own file, selected by
``green_claims.pack_path`` in ``config/settings.yaml``) into one immutable
:class:`~marketing_compliance_gate.domain.models.GreenClaimPack` value object that the coverage
engine and the substantiation service take as a parameter.

Why a pack file rather than constants in the engine: which evidence a jurisdiction demands,
how old that evidence may be, and which wording is forbidden are policy, not algorithm
(practice B4). Tuning them, or adding a jurisdiction's rules, must be a config change that a
compliance officer can read and diff. The engine never learns a jurisdiction's name.

Loading is fail-closed: an unreadable file, an unknown category / evidence kind / check
type, a rule that cites a citation id the pack does not define, or a rule with no citation
at all raises :class:`GreenClaimPackError`. A green-claim gate running on a silently empty
or partly-parsed pack would wave greenwashing through, so it must not start at all.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .domain.errors import GreenClaimPackError
from .domain.models import (
    CheckType,
    Citation,
    EvidenceKind,
    GreenClaimCategory,
    GreenClaimPack,
    Market,
    Rule,
    RuleKind,
    Severity,
    SourceType,
    SubstantiationRequirement,
    Vertical,
)

DEFAULT_PACK_PATH = Path(__file__).resolve().parent / "rulepacks" / "green_claims.yaml"

# The check types a GREEN_CLAIM rule may use. Substantiation coverage is decided by the
# CoverageEngine from the evidence on file, NOT by a rule check, so the pack deliberately
# cannot declare a "substantiated" check that would duplicate that decision.
_ALLOWED_CHECKS = (
    CheckType.FORBIDDEN_PHRASE,
    CheckType.REQUIRED_DISCLOSURE,
    CheckType.REQUIRED_FIELD,
)


def _fail(message: str) -> Any:
    raise GreenClaimPackError(f"green-claim pack: {message}")


def _enum(cls: Any, value: Any, what: str) -> Any:
    try:
        return cls(str(value))
    except ValueError:
        permitted = ", ".join(sorted(m.value for m in cls))
        return _fail(f"unknown {what} {value!r}; permitted: {permitted}")


def _citation(raw: dict[str, Any], citation_id: str) -> Citation:
    return Citation(
        source_id=citation_id,
        source_type=_enum(SourceType, raw.get("source_type", "regulation"), "source type"),
        title=str(raw.get("title", "")).strip(),
        url=str(raw.get("url", "") or ""),
        published_date=str(raw.get("published_date", "")) or None,
        snippet=" ".join(str(raw.get("snippet", "")).split()),
    )


def _citations(doc: dict[str, Any]) -> dict[str, Citation]:
    raw = doc.get("citations") or {}
    if not isinstance(raw, dict) or not raw:
        _fail("no 'citations' block; every rule must name the instrument it comes from")
    out: dict[str, Citation] = {}
    for citation_id, spec in raw.items():
        if not isinstance(spec, dict) or not str(spec.get("title", "")).strip():
            _fail(f"citation {citation_id!r} has no title (a citation must name its instrument)")
        out[str(citation_id)] = _citation(spec, str(citation_id))
    return out


def _phrases(doc: dict[str, Any], jurisdiction: dict[str, Any]) -> dict[GreenClaimCategory, tuple]:
    """Merge the base detection vocabulary with the jurisdiction's locale-specific extras."""
    merged: dict[GreenClaimCategory, list[str]] = {}
    for source in (doc.get("categories") or {}, jurisdiction.get("categories") or {}):
        if not isinstance(source, dict):
            _fail("'categories' must be a mapping of category -> list of phrases")
        for name, phrases in source.items():
            category = _enum(GreenClaimCategory, name, "green-claim category")
            if not isinstance(phrases, list):
                _fail(f"category {name!r} must map to a list of phrases")
            for phrase in phrases:
                text = str(phrase).strip()
                if text and text not in merged.setdefault(category, []):
                    merged[category].append(text)
    return {category: tuple(phrases) for category, phrases in merged.items()}


def _requirements(
    market: Market, jurisdiction: dict[str, Any], citations: dict[str, Citation]
) -> dict[tuple[Market, GreenClaimCategory], SubstantiationRequirement]:
    raw = jurisdiction.get("requirements") or {}
    if not isinstance(raw, dict) or not raw:
        _fail(f"{market.value} declares no substantiation requirements")
    out: dict[tuple[Market, GreenClaimCategory], SubstantiationRequirement] = {}
    for name, spec in raw.items():
        category = _enum(GreenClaimCategory, name, "green-claim category")
        if not isinstance(spec, dict):
            _fail(f"{market.value}/{name}: requirement must be a mapping")
        kinds = spec.get("required_evidence") or []
        if not isinstance(kinds, list) or not kinds:
            _fail(f"{market.value}/{name}: 'required_evidence' must be a non-empty list")
        citation_id = str(spec.get("citation", "") or "")
        if citation_id and citation_id not in citations:
            _fail(f"{market.value}/{name}: unknown citation id {citation_id!r}")
        if "max_evidence_age_days" not in spec:
            _fail(
                f"{market.value}/{name}: 'max_evidence_age_days' must be explicit; use 0 only "
                "for a deliberately reviewed no-expiry policy"
            )
        max_age = spec["max_evidence_age_days"]
        if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age < 0:
            _fail(f"{market.value}/{name}: 'max_evidence_age_days' must be a non-negative integer")
        out[(market, category)] = SubstantiationRequirement(
            market=market,
            category=category,
            required_kinds=tuple(
                _enum(EvidenceKind, k, "evidence kind")
                for k in kinds  # type: ignore[misc]
            ),
            max_evidence_age_days=max_age,
            requires_independent_verification=bool(
                spec.get("requires_independent_verification", False)
            ),
            remediation=" ".join(str(spec.get("remediation", "")).split()),
            citation=citations.get(citation_id) if citation_id else None,
        )
    return out


def _rules(
    market: Market, jurisdiction: dict[str, Any], citations: dict[str, Citation]
) -> tuple[Rule, ...]:
    raw = jurisdiction.get("rules") or []
    if not isinstance(raw, list) or not raw:
        _fail(f"{market.value} declares no green-claim rules")
    rules: list[Rule] = []
    for spec in raw:
        if not isinstance(spec, dict):
            _fail(f"{market.value}: each rule must be a mapping")
        rule_id = str(spec.get("id", "")).strip()
        if not rule_id:
            _fail(f"{market.value}: a rule has no id")
        check = _enum(CheckType, spec.get("check", ""), "check type")
        if check not in _ALLOWED_CHECKS:
            permitted = ", ".join(c.value for c in _ALLOWED_CHECKS)
            _fail(f"{rule_id}: check {check.value!r} is not permitted here; use one of {permitted}")
        citation_id = str(spec.get("citation", "") or "")
        if citation_id not in citations:
            _fail(
                f"{rule_id}: citation {citation_id!r} is not defined in the pack; every "
                "green-claim rule must state a named regulator instrument"
            )
        verticals = spec.get("verticals") or []
        if not isinstance(verticals, list) or not verticals:
            _fail(f"{rule_id}: 'verticals' must be a non-empty list")
        patterns = tuple(str(p) for p in (spec.get("patterns") or []))
        field_name = str(spec.get("field", "") or "")
        if check in (CheckType.FORBIDDEN_PHRASE, CheckType.REQUIRED_DISCLOSURE) and not patterns:
            _fail(f"{rule_id}: check {check.value!r} needs at least one pattern")
        if check is CheckType.REQUIRED_FIELD and not field_name:
            _fail(f"{rule_id}: check 'required_field' needs a 'field'")
        categories = tuple(
            _enum(GreenClaimCategory, c, "green-claim category")
            for c in (spec.get("applies_to") or [])
        )
        for vertical_name in verticals:
            rules.append(
                Rule(
                    id=rule_id,
                    kind=RuleKind.GREEN_CLAIM,
                    check=check,
                    description=" ".join(str(spec.get("description", "")).split()),
                    market=market,
                    vertical=_enum(Vertical, vertical_name, "vertical"),
                    severity=_enum(Severity, spec.get("severity", "high"), "severity"),
                    patterns=patterns,
                    field=field_name,
                    remediation=" ".join(str(spec.get("remediation", "")).split()),
                    citation=citations[citation_id],
                    applies_to_categories=categories,
                )
            )
    return tuple(rules)


def load_pack(path: str | Path | None = None) -> GreenClaimPack:
    """Load and validate a green-claim pack from ``path`` (default: the reference pack)."""
    pack_path = Path(path) if path else DEFAULT_PACK_PATH
    if not pack_path.exists():
        _fail(f"pack file {pack_path} does not exist; the green-claim gate cannot run without it")
    try:
        doc = yaml.safe_load(pack_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise GreenClaimPackError(
            f"green-claim pack: {pack_path} is not valid YAML: {exc}"
        ) from exc
    if not isinstance(doc, dict):
        _fail(f"{pack_path} must contain a mapping")

    citations = _citations(doc)
    jurisdictions = doc.get("jurisdictions") or {}
    if not isinstance(jurisdictions, dict) or not jurisdictions:
        _fail("no 'jurisdictions' block")

    phrases: dict[tuple[Market, GreenClaimCategory], tuple[str, ...]] = {}
    requirements: dict[tuple[Market, GreenClaimCategory], SubstantiationRequirement] = {}
    rules: list[Rule] = []
    for name, jurisdiction in jurisdictions.items():
        market = _enum(Market, name, "market")
        if not isinstance(jurisdiction, dict):
            _fail(f"jurisdiction {name!r} must be a mapping")
        for category, category_phrases in _phrases(doc, jurisdiction).items():
            phrases[(market, category)] = category_phrases
        requirements.update(_requirements(market, jurisdiction, citations))
        rules.extend(_rules(market, jurisdiction, citations))

    return GreenClaimPack(
        version=str(doc.get("version", "")),
        phrases=phrases,
        requirements=requirements,
        rules=tuple(sorted(rules, key=lambda r: (r.market.value, r.vertical.value, r.id))),
    )


@lru_cache(maxsize=4)
def _cached_pack(resolved: str) -> GreenClaimPack:
    return load_pack(resolved)


def pack_for(settings: Settings) -> GreenClaimPack:
    """Resolve the active pack for ``settings`` (adopter override, else the reference)."""
    configured = (settings.green_claims.pack_path or "").strip()
    return _cached_pack(configured or str(DEFAULT_PACK_PATH))
