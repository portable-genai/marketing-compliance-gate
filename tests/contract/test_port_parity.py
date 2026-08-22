"""Contract tests: the ``onprem`` and ``local`` adapters are structural parity of the ports.

For every port the catalog declares, this iterates the adapter map and, for both the
``onprem`` and ``local`` profiles, imports + constructs the bound class (which must build
cleanly with **no Google Cloud SDK** installed), then asserts:

  1. the constructed instance satisfies its runtime_checkable Protocol (isinstance), and
  2. every method/property the Protocol declares actually exists on the instance.

It additionally proves the two profiles' distinct contracts:

* ``onprem`` is the fail-fast migration target: every method raises ``NotImplementedError``
  (proven on a representative port), and
* ``local`` is a WORKING offline stack: the same ports construct and answer in-process.

This is the proof of the ports-and-adapters / no-lock-in promise: the on-prem migration
target and the offline local stack implement the exact same interface as the managed GCP
stack.
"""

from __future__ import annotations

import importlib
from typing import Protocol, get_type_hints

import pytest

from marketing_compliance_gate import config, ports
from marketing_compliance_gate.config import LocalSettings, Settings, instantiate

CONFIG_PATH = "config/settings.yaml"

PORT_PROTOCOLS: dict[str, type] = {
    "rule_provider": ports.RuleProviderPort,
    "evidence_store": ports.EvidenceStorePort,
    "consent_store": ports.ConsentStorePort,
    "llm": ports.LlmPort,
    "guardrail": ports.GuardrailPort,
    "audit": ports.AuditSinkPort,
    "tracer": ports.ObservabilityTracerPort,
    "evaluation": ports.EvaluationGatePort,
    "agent_registry": ports.AgentRegistryPort,
    "tool_catalog": ports.ToolCatalogPort,
    "identity": ports.IdentityPort,
    "review_router": ports.ReviewRouterPort,
}

# Profiles whose adapters must construct + satisfy the Protocols with no GCP SDK.
SDK_FREE_PROFILES = ("onprem", "local")


def _settings(profile: str) -> Settings:
    base = Settings.load(CONFIG_PATH)
    return Settings(
        project_id=base.project_id,
        region=base.region,
        profile=profile,
        vertical=base.vertical,
        market=base.market,
        grounding_enabled=base.grounding_enabled,
        models=base.models,
        knowledge_base=base.knowledge_base,
        model_armor=base.model_armor,
        logging=base.logging,
        agent_engine=base.agent_engine,
        green_claims=base.green_claims,
        local=LocalSettings(
            db_path=":memory:",
            audit_path=":memory:",
            evidence_path=":memory:",
            consent_path=":memory:",
        ),
        markets=base.markets,
        adapters=base.adapters,
    )


def _protocol_members(protocol: type) -> set[str]:
    members = set(getattr(protocol, "__protocol_attrs__", set()))
    if not members:
        members |= set(get_type_hints(protocol).keys())
        for name in dir(protocol):
            if name.startswith("_"):
                continue
            members.add(name)
    return {m for m in members if not m.startswith("_")}


def test_port_protocols_matches_settings_adapters():
    """Reverse drift guard: the port map and the settings bindings are the SAME SET.

    The forward check below proves every port this suite knows about has adapters. This one
    proves the converse: an ``adapters:`` key added to ``config/settings.yaml`` without being
    registered here would otherwise ship a port with NO contract test at all, quietly exempt
    from the Protocol, single-arg-construction and fail-fast checks.
    """
    settings = Settings.load(CONFIG_PATH)
    assert set(settings.adapters) == set(PORT_PROTOCOLS), (
        "config/settings.yaml adapters and PORT_PROTOCOLS have drifted apart: "
        f"unregistered in the test: {sorted(set(settings.adapters) - set(PORT_PROTOCOLS))}; "
        f"missing from settings: {sorted(set(PORT_PROTOCOLS) - set(settings.adapters))}"
    )


def test_every_port_has_an_explicit_binding_for_every_profile():
    settings = Settings.load(CONFIG_PATH)
    for port_name in PORT_PROTOCOLS:
        binding = settings.adapters.get(port_name, {})
        missing = set(config.RUNTIME_PROFILES) - set(binding)
        assert not missing, f"port '{port_name}' has no explicit bindings for {sorted(missing)}"


@pytest.mark.parametrize("profile", SDK_FREE_PROFILES)
@pytest.mark.parametrize("port_name", sorted(PORT_PROTOCOLS))
def test_adapter_satisfies_protocol(profile: str, port_name: str):
    settings = _settings(profile)
    protocol = PORT_PROTOCOLS[port_name]
    dotted = settings.adapters[port_name][profile]

    adapter = instantiate(dotted, settings)

    assert isinstance(adapter, protocol), (
        f"{dotted} does not structurally satisfy {protocol.__name__}"
    )

    members = _protocol_members(protocol)
    declared = set().union(*(vars(klass) for klass in type(adapter).__mro__))
    for member in members:
        assert member in declared, (
            f"{dotted} is missing port method/attr '{member}' of {protocol.__name__}"
        )


@pytest.mark.parametrize("profile", SDK_FREE_PROFILES)
@pytest.mark.parametrize("port_name", sorted(PORT_PROTOCOLS))
def test_adapter_constructs_with_single_settings_arg(profile: str, port_name: str):
    """The build contract: every adapter is ``Adapter(settings: Settings)``."""
    settings = _settings(profile)
    dotted = settings.adapters[port_name][profile]
    module_path, _, class_name = dotted.partition(":")

    cls = getattr(importlib.import_module(module_path), class_name)
    instance = cls(settings)
    assert instance is not None


def test_onprem_rule_provider_fails_fast():
    """The on-prem stubs are fail-fast: a representative port raises NotImplementedError."""
    from marketing_compliance_gate.domain.models import Market, Vertical

    settings = _settings("onprem")
    adapter = instantiate(settings.adapters["rule_provider"]["onprem"], settings)
    with pytest.raises(NotImplementedError):
        adapter.rule_set(Market.SG, Vertical.BANKING)


def test_onprem_evidence_store_fails_fast():
    """An unimplemented evidence store must raise, never answer "no evidence on file".

    Returning empty would read as "this green claim needs nothing", which is exactly the
    silent failure the green-claims gate exists to prevent.
    """
    settings = _settings("onprem")
    adapter = instantiate(settings.adapters["evidence_store"]["onprem"], settings)
    with pytest.raises(NotImplementedError):
        adapter.list_for_asset("demo-brand", "asset-1")
    with pytest.raises(NotImplementedError):
        adapter.get("ev-demo-0001")


def test_local_evidence_store_is_tenant_scoped():
    """The local stack is WORKING, and its tenant filter is in the store, not the caller."""
    settings = _settings("local")
    adapter = instantiate(settings.adapters["evidence_store"]["local"], settings)
    demo = adapter.list_for_asset("demo-brand", "camp-green-au-001")
    other = adapter.list_for_asset("other-brand", "camp-green-au-001")
    assert demo, "local evidence store returned nothing for the seeded tenant"
    assert all(r.tenant == "demo-brand" for r in demo)
    assert all(r.tenant == "other-brand" for r in other)
    assert adapter.list_for_asset("", "camp-green-au-001") == ()


def test_onprem_consent_store_fails_fast():
    """An unimplemented consent store must raise, never answer with an empty snapshot.

    An empty snapshot is a VALID answer meaning "this subject granted nothing", on which the
    engine correctly denies. A placeholder that returned one would therefore look like a
    working, conservative store while in fact recording no withdrawal, suppression or cap at
    all. Raising is the only honest answer.
    """
    settings = _settings("onprem")
    adapter = instantiate(settings.adapters["consent_store"]["onprem"], settings)
    with pytest.raises(NotImplementedError):
        adapter.snapshot("demo-brand", "subj-000101")
    with pytest.raises(NotImplementedError):
        adapter.get_record("cr-demo-0001")


def test_local_consent_store_is_tenant_scoped():
    """The local consent stack is WORKING, and its tenant filter is in the store."""
    settings = _settings("local")
    adapter = instantiate(settings.adapters["consent_store"]["local"], settings)
    demo = adapter.snapshot("demo-brand", "subj-000101")
    assert demo.records, "local consent store returned nothing for the seeded subject"
    assert all(r.tenant == "demo-brand" for r in demo.records)
    # The other tenant holds a perfectly good grant for its own subject, and none for this
    # one: a read that spanned tenants would be visibly wrong rather than merely empty.
    assert adapter.snapshot("other-brand", "subj-000101").records == ()
    assert adapter.snapshot("other-brand", "subj-000201").records
    assert adapter.snapshot("", "subj-000101").records == ()


def test_local_rule_provider_returns_real_rules():
    """The local stack is WORKING: the rule provider returns real, cited rules offline."""
    from marketing_compliance_gate.domain.models import Market, Vertical

    settings = _settings("local")
    adapter = instantiate(settings.adapters["rule_provider"]["local"], settings)
    rule_set = adapter.rule_set(Market.SG, Vertical.BANKING)
    assert rule_set.rules, "local rule provider returned no rules for the seeded KB"
    assert all(r.citation is not None for r in rule_set.rules)


def test_shared_boundary_types_are_the_commons_objects_not_copies():
    """The drift guard: these names must BE the commons objects, not equal-looking copies.

    ``isinstance`` against a structural Protocol cannot see this. A hand-copied
    ``ObservabilityTracerPort`` that had lost ``record_token_usage``, or a local
    ``EvalReport`` missing the run-evidence fields, still satisfies every other test in this
    file while being a different type from the one the rest of the fleet compiles against.
    Identity is the only assertion that fails the moment someone redeclares one of these
    here, which is exactly how sixteen repositories drifted apart the first time.
    """
    import agent_eval_kit
    import hex_service_kit.identity
    import hex_service_kit.observability

    from marketing_compliance_gate.domain import models

    assert ports.ObservabilityTracerPort is hex_service_kit.observability.ObservabilityTracerPort
    assert ports.TokenUsage is hex_service_kit.observability.TokenUsage
    assert ports.IdentityPort is hex_service_kit.identity.IdentityPort
    assert ports.EvaluationGatePort is agent_eval_kit.EvaluationGatePort
    assert models.TokenUsage is hex_service_kit.observability.TokenUsage
    assert models.EvalReport is agent_eval_kit.EvalReport
    assert models.EvalMetricResult is agent_eval_kit.EvalMetricResult


def test_all_protocols_are_runtime_checkable():
    for protocol in PORT_PROTOCOLS.values():
        assert issubclass(protocol, Protocol)  # type: ignore[arg-type]
        assert getattr(protocol, "_is_runtime_protocol", False), (
            f"{protocol.__name__} must be @runtime_checkable"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
