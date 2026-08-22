"""No CI step may gate itself on a hardcoded calendar date.

This repo shipped a `TEMPORARY: expires 2026-08-06` npm-audit exception whose whole
enforcement was `[[ "$(date -u +%F)" > "2026-08-06" ]]`. That shape fails in both
directions and fails quietly. Before the date, the gate is not the gate anyone reads: it
waves through whatever the inline allowlist happens to name. After the date, nothing
announces the change; the build simply starts failing on a workday for a reason recorded
only in a comment nobody has read since it was written. In this repo's case it was worse
still: the advisory that actually failed the gate (nanoid GHSA-2v37-7h3g-55p8) was never
on the allowlist, so the exception had stopped permitting anything at all, while the
`overrides` block it relied on pinned postcss AT a version GHSA-fxqj-rqcc-2cmp flags and
blocked the resolver from moving past it.

The repair is a plain hard gate with no date in it. This test is the thing that keeps it
that way: a dated exception can only be reintroduced by deleting an assertion, which is a
visible act, rather than by adding a comment, which is not.

Scope: every file under `.github/workflows/`. The check is for a YYYY-MM-DD literal
appearing in a file that also reads the current date, which is the comparison shape;
a date literal in prose (a changelog reference, an action pin comment) is not a gate and
is not flagged.

Driven RED before being trusted: reintroducing the single line
`if [[ "$(date -u +%F)" > "2026-08-06" ]]; then` into `.github/workflows/ci.yaml` made it
fail with the file, the line number and the offending text.
"""

from __future__ import annotations

import re
from pathlib import Path

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# A bare YYYY-MM-DD, quoted or not.
_DATE_LITERAL = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# The ways a shell step can ask what today is. `date` with any format, GitHub's own
# timestamp contexts, and Python/node one-liners that reach for now().
_READS_TODAY = re.compile(
    r"\$\(\s*date\b|`date\b|\bdate -u\b|\bdate \+|Date\.now\(\)|new Date\(\)"
    r"|datetime\.(now|today|utcnow)|github\.event\.repository\.pushed_at"
)


def _workflow_files() -> list[Path]:
    return sorted(p for p in _WORKFLOWS.rglob("*") if p.is_file())


def test_workflows_exist() -> None:
    """Guard the guard: an empty glob would make every assertion below vacuous."""
    files = _workflow_files()
    assert files, f"no workflow files found under {_WORKFLOWS}"


def test_no_workflow_gates_on_a_date_literal() -> None:
    """No workflow compares a hardcoded date against the current date."""
    offenders: list[str] = []
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _READS_TODAY.search(text):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _DATE_LITERAL.search(line):
                offenders.append(
                    f"{path.relative_to(_WORKFLOWS.parents[1])}:{number}: {line.strip()}"
                )

    assert not offenders, (
        "A workflow gates on a hardcoded calendar date. Time-boxed CI exceptions rot "
        "silently: fix the underlying finding instead.\n  " + "\n  ".join(offenders)
    )
