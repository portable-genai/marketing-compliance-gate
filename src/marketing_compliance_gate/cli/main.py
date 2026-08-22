"""``mkt-gov`` — the Typer CLI for the D6 Marketing Compliance and Brand Governance system.

A thin presentation layer over the domain services. It owns no business logic: every
command builds the wiring from :func:`marketing_compliance_gate.api.deps.make_review_service`,
invokes one domain service, and pretty-prints the cited result.

Design constraints honoured here:

* **Import-safe.** Importing this module (the ``[project.scripts]`` entry point, or
  ``--help``) must never pull in FastAPI, uvicorn or the Google Cloud SDKs. They are
  imported lazily inside command bodies, so the on-prem / local / test profile (which
  installs no Google Cloud SDK) can still load the CLI.
* **Profile-aware.** ``MKT_GOV_PROFILE`` selects the adapter stack. The ``onprem``
  profile binds placeholder adapters that raise ``NotImplementedError``; the CLI then fails
  clearly (exit code 2) naming the migration target.
* **Citations are first-class.** Every finding prints with the rule (and authority) it
  cites, and the maker-checker "human review required" banner is shown when applicable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..domain.models import Citation, Review, SubstantiationAssessment

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "D6 Marketing Compliance and Brand Governance — deterministic claim / permission / "
        "brand / consent review of Campaigns, Creatives and Offers against per-market, "
        "per-vertical rules, the marketing maker-checker gate, generic across banking and "
        "online retail and the JP/AU/SG markets."
    ),
)

_PROFILE_EXIT = 2
_RUNTIME_EXIT = 1
_CLI_ACTOR = "cli:operator"


def _fail(message: str, *, code: int = _RUNTIME_EXIT) -> Any:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _run(action: str, fn: Any) -> Any:
    """Execute ``fn`` and translate adapter failures into clean CLI errors."""
    from ..config import Settings

    profile = Settings.load().profile
    try:
        return fn()
    except NotImplementedError as exc:
        detail = str(exc) or "method not implemented"
        _fail(
            f"'{action}' is not available under profile '{profile}'. "
            f"This profile uses placeholder adapters (migration target): {detail}",
            code=_PROFILE_EXIT,
        )
    except KeyError as exc:
        _fail(f"'{action}' has no adapter wired for profile '{profile}': {exc}", code=_PROFILE_EXIT)
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI boundary: no tracebacks to operators
        _fail(f"'{action}' failed: {type(exc).__name__}: {exc}", code=_RUNTIME_EXIT)


# --------------------------------------------------------------------------- #
# Pretty-printing
# --------------------------------------------------------------------------- #
def _fmt_citation(c: Citation) -> str:
    page = f" p.{c.page}" if c.page is not None else ""
    st = c.source_type.value if hasattr(c.source_type, "value") else str(c.source_type)
    url = f" — {c.url}" if c.url else ""
    return f"[{c.source_id}, {st}{page}]{url}"


def _echo_citations(citations: tuple[Citation, ...], indent: str = "  ") -> None:
    if not citations:
        typer.secho(f"{indent}(no citations)", fg=typer.colors.YELLOW)
        return
    typer.secho(f"{indent}Citations:", bold=True)
    for c in citations:
        typer.echo(f"{indent}  - {_fmt_citation(c)}")


def _echo_review_banner(requires_review: bool) -> None:
    if requires_review:
        typer.secho(
            "  [HUMAN REVIEW REQUIRED] maker-checker gate — do not run this asset until a "
            "qualified compliance officer signs off.",
            fg=typer.colors.YELLOW,
            bold=True,
        )


_SEV_COLOR = {
    "critical": typer.colors.RED,
    "high": typer.colors.RED,
    "medium": typer.colors.YELLOW,
    "low": typer.colors.CYAN,
}


def _echo_review(review: Review) -> None:
    typer.secho(f"\nCOMPLIANCE REVIEW: {review.asset_id}", bold=True)
    typer.echo(f"  id        : {review.id}")
    typer.echo(f"  asset type: {review.asset_type.value}")
    typer.echo(f"  market    : {review.market.value}")
    typer.echo(f"  vertical  : {review.vertical.value}")
    color = typer.colors.GREEN if review.outcome.value == "compliant" else typer.colors.RED
    typer.secho(f"  outcome   : {review.outcome.value}", fg=color, bold=True)
    _echo_review_banner(review.requires_human_review)

    typer.secho("\n  Summary:", bold=True)
    typer.echo(f"    {review.summary}")

    typer.secho("\n  Findings:", bold=True)
    for f in review.findings:
        mark = "FAIL" if f.failed else "pass"
        sev_color = _SEV_COLOR.get(f.severity.value, typer.colors.WHITE)
        typer.secho(
            f"    - [{mark}] {f.rule_id} ({f.rule_kind.value}/{f.severity.value}): {f.message}",
            fg=sev_color if f.failed else None,
        )
        if f.evidence:
            typer.echo(f"        evidence: {f.evidence}")
        if f.remediation:
            typer.echo(f"        fix     : {f.remediation}")

    if review.consent_checks:
        typer.secho("\n  Consent checks:", bold=True)
        for chk in review.consent_checks:
            state = "granted" if chk.granted else "MISSING"
            typer.echo(f"    - {chk.purpose}: {state} (rule {chk.rule_id})")

    typer.secho("", nl=True)
    _echo_citations(review.citations)


_VERDICT_COLOR = {
    "substantiated": typer.colors.GREEN,
    "partially_substantiated": typer.colors.YELLOW,
    "unsubstantiated": typer.colors.RED,
    "not_applicable": typer.colors.CYAN,
}


def _echo_assessment(assessment: SubstantiationAssessment) -> None:
    typer.secho(f"\nGREEN-CLAIM SUBSTANTIATION: {assessment.asset_id}", bold=True)
    typer.echo(f"  id       : {assessment.id}")
    typer.echo(f"  tenant   : {assessment.tenant}")
    typer.echo(f"  market   : {assessment.market.value}")
    typer.echo(f"  vertical : {assessment.vertical.value}")
    typer.echo(f"  as of    : {assessment.as_of}")
    color = _VERDICT_COLOR.get(assessment.verdict.value, typer.colors.WHITE)
    typer.secho(
        f"  verdict  : {assessment.verdict.value} (coverage {assessment.coverage})",
        fg=color,
        bold=True,
    )
    _echo_review_banner(assessment.requires_human_review)

    typer.secho("\n  Narrative:", bold=True)
    typer.echo(f"    {assessment.narrative}")

    typer.secho("\n  Claims:", bold=True)
    if not assessment.claims:
        typer.echo("    (no environmental claim detected in this copy)")
    for coverage in assessment.claims:
        claim_color = _VERDICT_COLOR.get(coverage.verdict.value, typer.colors.WHITE)
        typer.secho(
            f"    - {coverage.claim.category.value} "
            f'("{coverage.claim.phrase}" in the {coverage.claim.location}): '
            f"{coverage.verdict.value}, coverage {coverage.coverage}",
            fg=claim_color,
        )
        if coverage.evidence_ids:
            typer.echo(f"        evidence: {', '.join(coverage.evidence_ids)}")
        for gap in coverage.gaps:
            typer.echo(f"        gap     : {gap}")
        if coverage.remediation:
            typer.echo(f"        fix     : {coverage.remediation}")

    typer.secho("\n  Green-claim rules:", bold=True)
    if not assessment.findings:
        typer.echo("    (no green-claim rule applies to this asset)")
    for f in assessment.findings:
        mark = "FAIL" if f.failed else "pass"
        sev_color = _SEV_COLOR.get(f.severity.value, typer.colors.WHITE)
        typer.secho(
            f"    - [{mark}] {f.rule_id} ({f.severity.value}): {f.message}",
            fg=sev_color if f.failed else None,
        )
        if f.evidence:
            typer.echo(f"        evidence: {f.evidence}")
        if f.remediation:
            typer.echo(f"        fix     : {f.remediation}")

    typer.secho("", nl=True)
    _echo_citations(assessment.citations)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@app.command()
def review(
    body: str = typer.Argument(..., help="The marketing copy to review."),
    title: str = typer.Option("", "--title", "-t", help="Asset title."),
    market: str = typer.Option("SG", "--market", "-m", help="Market: JP | AU | SG."),
    vertical: str = typer.Option(
        "banking", "--vertical", "-v", help="Vertical: banking | online_retail."
    ),
    asset_type: str = typer.Option(
        "creative", "--type", help="Asset type: campaign | creative | offer."
    ),
    asset_id: str = typer.Option("asset-1", "--id", help="Asset id."),
    field: list[str] = typer.Option(  # noqa: B008 - Typer's documented Option-in-default idiom
        None, "--field", "-f", help="Structured field as key=value (repeatable)."
    ),
    consent: list[str] = typer.Option(  # noqa: B008 - Typer Option-in-default idiom
        None, "--consent", "-c", help="A granted consent purpose (repeatable)."
    ),
) -> None:
    """Review a Campaign / Creative / Offer against its per-market, per-vertical rule set."""
    from ..api.deps import make_review_service
    from ..domain.models import AssetType, Market, MarketingAsset, ReviewRequest, Vertical

    fields: dict[str, str] = {}
    for item in field or ():
        key, _, value = item.partition("=")
        fields[key.strip()] = value.strip()

    def go() -> Review:
        asset = MarketingAsset(
            id=asset_id,
            asset_type=AssetType(asset_type),
            title=title or asset_id,
            body=body,
            market=Market(market),
            vertical=Vertical(vertical),
            fields=fields,
            granted_consents=tuple(consent or ()),
            submitted_by=_CLI_ACTOR,
        )
        return make_review_service().review(ReviewRequest(asset=asset), actor=_CLI_ACTOR)

    result = _run("review", go)
    _echo_review(result)


@app.command()
def substantiate(
    body: str = typer.Argument(..., help="The marketing copy to assess for green claims."),
    title: str = typer.Option("", "--title", "-t", help="Asset title."),
    market: str = typer.Option("AU", "--market", "-m", help="Market: JP | AU | SG."),
    vertical: str = typer.Option(
        "banking", "--vertical", "-v", help="Vertical: banking | online_retail."
    ),
    asset_type: str = typer.Option(
        "campaign", "--type", help="Asset type: campaign | creative | offer."
    ),
    asset_id: str = typer.Option(
        "camp-green-au-001", "--id", help="Asset id (the evidence is filed against it)."
    ),
    tenant: str = typer.Option(
        "demo-brand", "--tenant", help="Tenant whose evidence is read (local profile only)."
    ),
    as_of: str = typer.Option(
        "", "--as-of", help="ISO date to age the evidence against; empty means today."
    ),
    field: list[str] = typer.Option(  # noqa: B008 - Typer's documented Option-in-default idiom
        None, "--field", "-f", help="Structured field as key=value (repeatable)."
    ),
) -> None:
    """Assess an asset's green claims against the substantiation evidence on file.

    The tenant is a CLI convenience for the offline local profile, where there is no IdP:
    over HTTP the tenant always comes from the verified principal and can never be supplied
    by the caller.
    """
    from datetime import date

    from ..api.deps import make_substantiation_service
    from ..domain.identity import Principal
    from ..domain.models import AssetType, Market, MarketingAsset, Vertical

    fields: dict[str, str] = {}
    for item in field or ():
        key, _, value = item.partition("=")
        fields[key.strip()] = value.strip()

    def go() -> SubstantiationAssessment:
        asset = MarketingAsset(
            id=asset_id,
            asset_type=AssetType(asset_type),
            title=title or asset_id,
            body=body,
            market=Market(market),
            vertical=Vertical(vertical),
            fields=fields,
            submitted_by=_CLI_ACTOR,
        )
        principal = Principal(subject=_CLI_ACTOR, tenant=tenant, source="cli")
        return make_substantiation_service().assess(
            asset, principal, as_of=date.fromisoformat(as_of) if as_of else None
        )

    _echo_assessment(_run("substantiate", go))


@app.command()
def rules(
    market: str = typer.Option("SG", "--market", "-m", help="Market: JP | AU | SG."),
    vertical: str = typer.Option(
        "banking", "--vertical", "-v", help="Vertical: banking | online_retail."
    ),
) -> None:
    """List the rules in force for a market and vertical (the rule KB)."""
    from ..api.deps import get_container
    from ..domain.models import Market, Vertical

    def go() -> Any:
        return get_container().rule_provider.rule_set(Market(market), Vertical(vertical))

    rule_set = _run("rules", go)
    typer.secho(f"\nRULE SET: {market}/{vertical}  ({len(rule_set.rules)} rule(s))", bold=True)
    for r in rule_set.rules:
        tag = f"{r.kind.value}/{r.check.value}/{r.severity.value}"
        typer.echo(f"  - {r.id} [{tag}] {r.description}")


if __name__ == "__main__":  # pragma: no cover
    app()
