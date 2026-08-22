from marketing_compliance_gate.domain import kernel, models


def test_kernel_is_stable_and_excludes_vertical_aggregates() -> None:
    assert models.ThinkingLevel is kernel.ThinkingLevel
    assert {"Review", "MarketingAsset", "RuleSet", "SubstantiationAssessment"}.isdisjoint(
        kernel.__all__
    )
