#!/usr/bin/env python3
"""The half a rule cannot score: is the substantiation NARRATIVE any good, judged, against a floor.

This service is deliberately almost all deterministic code. The verdict, the coverage and every
rule finding are computed, and `run_eval.py` scores them by rules, which is right: those are
questions with answers.

The substantiation narrative is the one thing a model contributes, and nothing measured it. That
is the odd shape this closes: in a service whose whole purpose is to stop an unsubstantiated
claim reaching the public, the single generative output was the one thing the gate did not score.
A narrative can sit under a correct verdict and still assert an absolute the evidence does not
carry, read as a publishing decision, or claim completeness over evidence that has an age. Those
are the three things a marketing reviewer overrides a verdict on.

The whole run is ``agent_eval_kit.narrative_main``. This file supplies only what is specific to
this service: where the table is, where the floors are, the profiles each narrative is written
in, and which of them is the deliberate control.

    make eval-narrative     # offline, no model, no credentials, no network

The judge is chosen HERE, on the command line, and never from the environment: a gate whose
scorer a stray variable could swap is not a gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_eval_kit import narrative_main

_REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = _REPO_ROOT / "eval" / "datasets" / "narrative_golden.jsonl"
FLOORS = _REPO_ROOT / "config" / "quality-floors.toml"

PROFILES = ("managed", "reduced", "regressed")
CONTROL = "regressed"


if __name__ == "__main__":
    raise SystemExit(
        narrative_main(
            dataset=DATASET,
            floors=FLOORS,
            profiles=PROFILES,
            control=CONTROL,
            description="Substantiation narrative quality, judged against the model-risk floors.",
            argv=sys.argv[1:],
        )
    )
