"""Static contracts for marketing-compliance-gate's internal Cloud Run and shared perimeter
boundary.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / "infra" / "terraform"


def _read(name: str) -> str:
    return (TF / name).read_text(encoding="utf-8")


def test_mkt6_ingress_cannot_be_weakened_and_egress_uses_shared_vpc() -> None:
    cloud_run = _read("cloud_run.tf")
    network = _read("network.tf")
    variables = _read("variables.tf")

    assert 'ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"' in cloud_run
    assert "ingress = var.ingress" not in cloud_run
    assert 'variable "ingress"' not in variables
    assert 'egress = "ALL_TRAFFIC"' in cloud_run
    assert "network    = var.shared_vpc_network" in cloud_run
    assert "subnetwork = var.shared_vpc_subnetwork" in cloud_run
    assert "data.google_compute_subnetwork.shared_cloud_run.private_ip_google_access" in cloud_run
    cidr_guard = (
        'tonumber(split("/", '
        "data.google_compute_subnetwork.shared_cloud_run.ip_cidr_range)[1]) <= 26"
    )
    assert cidr_guard in cloud_run
    assert "data.google_project.this.number) == var.mkt6_project_number" in cloud_run
    assert (
        "data.google_project.shared_vpc_host.number) == var.shared_vpc_host_project_number"
        in cloud_run
    )
    assert "roles/compute.networkViewer" in network
    assert "roles/compute.networkUser" in network


def test_deploy_market_runtime_region_and_image_are_coupled() -> None:
    cloud_run = _read("cloud_run.tf")
    variables = _read("variables.tf")
    example = _read("terraform.tfvars.example")

    assert 'variable "deploy_market"' in variables
    assert 'name  = "MKT_MARKET"' in cloud_run
    assert "value = var.deploy_market" in cloud_run
    assert 'name  = "MKT_GOV_REGION"' in cloud_run
    assert "value = var.region" in cloud_run
    assert "var.region == local.market_regions[var.deploy_market]" in cloud_run
    assert 'variable "zone"' not in variables
    assert "@sha256:" in example
    assert 'startswith(var.container_image, "${var.region}-docker.pkg.dev/")' in cloud_run


def test_mkt6_owns_one_perimeter_containing_host_and_both_services() -> None:
    perimeter = _read("vpc_sc.tf")
    example = _read("terraform.tfvars.example")

    assert "var.enable_vpc_sc && var.manage_shared_vpc_sc_perimeter" in perimeter
    assert '"projects/${var.shared_vpc_host_project_number}"' in perimeter
    assert '"projects/${var.mkt5_project_number}"' in perimeter
    assert '"projects/${var.mkt6_project_number}"' in perimeter
    assert "resources           = local.shared_perimeter_resources" in perimeter
    assert "manage_shared_vpc_sc_perimeter = true" in example
    assert 'shared_vpc_sc_perimeter_name   = "mkt_marketing_sg"' in example


def test_shared_perimeter_restricts_union_of_mkt5_and_mkt6_managed_services() -> None:
    perimeter = _read("vpc_sc.tf")

    for service in (
        "aiplatform.googleapis.com",
        "bigquery.googleapis.com",
        "cloudkms.googleapis.com",
        "cloudtrace.googleapis.com",
        "discoveryengine.googleapis.com",
        "logging.googleapis.com",
        "modelarmor.googleapis.com",
        "run.googleapis.com",
    ):
        assert f'"{service}"' in perimeter
