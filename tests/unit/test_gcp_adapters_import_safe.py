"""GCP adapter family: import-safe, region-validated, structurally Protocol-parity.

These run in the default gate (no ``integration`` marker) because they need **no live
cloud and no google-cloud-* package**: they prove the managed-stack adapters can be
imported and constructed with the Google SDKs absent (lazy imports), that they
structurally satisfy the same port Protocols as the local / onprem families, and that the
residency-region validation accepts the JP/AU/SG regions and rejects anything outside the
per-market allow-list. The live-cloud method calls are exercised under ``-m integration``.
"""

from __future__ import annotations

import dataclasses
import sys

import pytest

from marketing_compliance_gate import ports
from marketing_compliance_gate.config import Settings
from marketing_compliance_gate.domain.errors import UnsupportedMarketError
from marketing_compliance_gate.domain.models import Market

CONFIG_PATH = "config/settings.yaml"

# port name -> (dotted adapter, Protocol). The full GCP adapter family.
GCP_ADAPTERS: dict[str, tuple[str, type]] = {
    "rule_provider": (
        "marketing_compliance_gate.adapters.gcp.file_search_rules:FileSearchRuleProviderAdapter",
        ports.RuleProviderPort,
    ),
    "llm": (
        "marketing_compliance_gate.adapters.gcp.gemini_llm:GeminiLLMAdapter",
        ports.LlmPort,
    ),
    "guardrail": (
        "marketing_compliance_gate.adapters.gcp.model_armor_guardrail:ModelArmorGuardrailAdapter",
        ports.GuardrailPort,
    ),
    "audit": (
        "marketing_compliance_gate.adapters.gcp.cloud_logging_audit:CloudLoggingAuditAdapter",
        ports.AuditSinkPort,
    ),
    "tracer": (
        "marketing_compliance_gate.adapters.gcp.cloud_trace_tracer:CloudTraceTracerAdapter",
        ports.ObservabilityTracerPort,
    ),
    "evaluation": (
        "marketing_compliance_gate.adapters.gcp.genai_eval:GenAiEvalAdapter",
        ports.EvaluationGatePort,
    ),
    "agent_registry": (
        "marketing_compliance_gate.adapters.gcp.a2a_registry:A2ARegistryAdapter",
        ports.AgentRegistryPort,
    ),
    "tool_catalog": (
        "marketing_compliance_gate.adapters.gcp.mcp_tool_catalog:McpToolCatalogAdapter",
        ports.ToolCatalogPort,
    ),
}


def _settings() -> Settings:
    return Settings.load(CONFIG_PATH)


@pytest.mark.parametrize("port_name", sorted(GCP_ADAPTERS))
def test_gcp_adapter_constructs_and_satisfies_protocol(port_name: str):
    dotted, protocol = GCP_ADAPTERS[port_name]
    from marketing_compliance_gate.config import instantiate

    adapter = instantiate(dotted, _settings())
    assert isinstance(adapter, protocol), f"{dotted} does not satisfy {protocol.__name__}"


def test_importing_gcp_family_does_not_pull_in_google():
    """Lazy-import contract: importing the GCP modules must not import ``google``."""
    import importlib

    for dotted, _ in GCP_ADAPTERS.values():
        module_path = dotted.partition(":")[0]
        importlib.import_module(module_path)
    assert "google" not in sys.modules, "a GCP adapter imported `google` at module load time"


def test_config_bindings_cover_gcp_for_every_port():
    settings = _settings()
    for port_name in GCP_ADAPTERS:
        assert "gcp" in settings.adapters.get(port_name, {}), (
            f"port '{port_name}' has no gcp adapter binding"
        )


def test_region_validation_accepts_each_apac_market():
    from marketing_compliance_gate.adapters.gcp._region import resolve_region

    settings = _settings()
    expected = {
        Market.JP: "asia-northeast1",
        Market.AU: "australia-southeast1",
        Market.SG: "asia-southeast1",
    }
    for market, region in expected.items():
        assert resolve_region(settings, market) == region


def test_region_validation_rejects_out_of_residency_region():
    from marketing_compliance_gate.adapters.gcp._region import resolve_region

    settings = dataclasses.replace(_settings(), region="us-central1")
    with pytest.raises(UnsupportedMarketError):
        resolve_region(settings)
