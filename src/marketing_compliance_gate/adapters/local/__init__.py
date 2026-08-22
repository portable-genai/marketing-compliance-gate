"""Local deployment profile adapters — a WORKING, offline laptop stack.

The ``local`` profile is the third deployment option alongside ``gcp`` (managed Google
Cloud services) and ``onprem`` (fail-fast migration placeholders). Unlike ``onprem``, every
adapter here is a *real, deterministic* implementation that runs the whole compliance-review
pipeline end to end with **no Google Cloud, no API key, and no running emulators**:

* Rule KB / File Search (A2) -> a ``sqlite3`` **FTS5** index over the seeded fictional
  per-market, per-vertical rule sets (banking AND online retail across JP / AU / SG),
  returning fully-typed rules for the deterministic rule engine to evaluate.
* LLM (Gemini) -> a deterministic, schema-driven narrator of the findings (no model, no
  network).
* Guardrail (Model Armor) -> a heuristic that blocks prompt-injection / jailbreak text.
* Audit (Cloud Logging WORM) -> an append-only local store, read-back supported.
* Tracer (Cloud Trace) -> no-op spans.
* Agent registry (A3) / tool catalog (MCP) -> in-process stores.
* Evaluation (Gen AI eval / A4) -> delegates to the in-repo offline eval gate.

Everything is seedable so the test suite stays deterministic, and the default code path
imports **no google-cloud package at module top level**.
"""
