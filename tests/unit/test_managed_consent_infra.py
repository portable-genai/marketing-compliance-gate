"""Mkt6's Cloud Run and application gates share one reviewed OIDC contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / "infra" / "terraform"


def test_custom_audience_drives_cloud_run_and_the_application_verifier() -> None:
    cloud_run = (TF / "cloud_run.tf").read_text(encoding="utf-8")
    app = (ROOT / "src/marketing_compliance_gate/api/app.py").read_text(encoding="utf-8")

    assert "custom_audiences = [var.s2s_audience]" in cloud_run
    assert 'name  = "MKT6_S2S_AUDIENCE"' in cloud_run
    assert "value = var.s2s_audience" in cloud_run
    assert 'audience_env="MKT6_S2S_AUDIENCE"' in app
    assert "MKT6_S2S_TOKEN" not in cloud_run


def test_one_caller_variable_drives_both_cloud_run_iam_and_the_app_allowlist() -> None:
    cloud_run = (TF / "cloud_run.tf").read_text(encoding="utf-8")
    variables = (TF / "variables.tf").read_text(encoding="utf-8")

    assert 'variable "mkt5_caller_service_account"' in variables
    assert 'name  = "MKT6_S2S_ALLOWED_CALLERS"' in cloud_run
    assert "value = var.mkt5_caller_service_account" in cloud_run
    assert 'role     = "roles/run.invoker"' in cloud_run
    assert 'member   = "serviceAccount:${var.mkt5_caller_service_account}"' in cloud_run
