"""An irreversible control never arrives by default, and neither does a standing hourly charge.

The audit bucket's lock is the one control in ``infra/terraform/`` that cannot be undone: a
locked Cloud Logging bucket refuses to be deleted or to have its window shortened for the whole
retention period, project owner or not. It used to be the literal ``locked = true``.

The standalone Cloud Run service is not irreversible, but it is the one resource here that bills
by the hour with nobody using it: an instance floor of 1 on a service only the next-best-action
consent hop calls. An installation embedding the console under a portal never reaches it.

Observed failing first: against the stack as it shipped, the lock test failed on the literal,
the no-default test failed because no ``worm_locked`` variable existed, and the standalone test
failed because the service had no switch at all. Adding ``default = true`` to ``worm_locked`` or
flipping ``standalone_service_enabled`` to ``true`` turns them red again.

The plan-level proof lives in ``infra/terraform/tests/``, run by ``make tf-validate``. This file is
the half that needs no terraform binary, so the offline gate holds it too.
"""

from __future__ import annotations

import re
from pathlib import Path

TF = Path(__file__).resolve().parents[2] / "infra" / "terraform"


def _variable_block(name: str) -> str:
    text = (TF / "variables.tf").read_text(encoding="utf-8")
    start = text.find(f'variable "{name}" {{')
    assert start != -1, f"variables.tf declares no {name!r}"
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unterminated variable block {name!r}")


def test_the_audit_bucket_lock_is_a_variable_not_a_literal() -> None:
    worm = (TF / "logging_worm.tf").read_text(encoding="utf-8")
    assert re.search(r"^\s*locked\s*=\s*var\.worm_locked\s*$", worm, re.MULTILINE)
    assert not re.search(r"^\s*locked\s*=\s*true\s*$", worm, re.MULTILINE)


def test_the_lock_has_no_default_so_every_plan_names_it() -> None:
    block = _variable_block("worm_locked")
    assert not re.search(r"^\s*default\s*=", block, re.MULTILINE), (
        "worm_locked must have no default: an irreversible control must never be taken because "
        "a deployment said nothing, and a fork must never lose it the same way"
    )


def test_the_standalone_service_is_opt_in() -> None:
    block = _variable_block("standalone_service_enabled")
    assert re.search(r"^\s*default\s*=\s*false\s*$", block, re.MULTILINE)
    cloud_run = (TF / "cloud_run.tf").read_text(encoding="utf-8")
    assert cloud_run.count("count    = var.standalone_service_enabled ? 1 : 0") == 2


def test_every_shared_project_decline_is_a_variable() -> None:
    for name in (
        "manage_org_policies",
        "manage_audit_config",
        "enable_vpc_sc",
        "model_armor_full_capabilities",
        "additional_serving_service_accounts",
    ):
        _variable_block(name)
    policies = (TF / "org_policy.tf").read_text(encoding="utf-8")
    assert policies.count('resource "google_org_policy_policy"') == policies.count(
        "count  = var.manage_org_policies ? 1 : 0"
    )
