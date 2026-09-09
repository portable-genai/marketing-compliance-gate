"""The two Firestore stores: provisioned, region-routed, and indexed for the queries they run.

Pinned here, and each was watched failing first:

* **nothing created either store.** ``firestore_consent.py`` and ``firestore_evidence.py`` have
  both been bound as the managed implementations of their ports since the repository was
  written, and ``infra/terraform/`` created no database, enabled no API, granted no role and
  bound no key. The consent leg of the marketing journey would have failed at its first request
  on a deployment, and the substantiation evidence a compliance officer is meant to pull up
  months later had nowhere to be;
* **the residency validation reached nothing.** Both adapters called ``resolve_region(...)`` and
  DISCARDED the result, then built ``firestore.Client(project=...)`` against the project's
  DEFAULT database. A database's location is fixed at creation and a project has one default, so
  a JP request passed the residency check and then read and wrote whichever single region that
  database sat in. The check was real and the wire was not;
* **a composite query with no declared index fails at REQUEST time.** Firestore maintains
  single-field indexes itself; ``FAILED_PRECONDITION`` on a two-equality query appears the first
  time a compliance officer opens a subject, on the deployment, and nowhere else. Every
  composite query the two adapters run is held against a declared index here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from marketing_compliance_gate.adapters.gcp import _region
from marketing_compliance_gate.adapters.gcp import firestore_consent as consent
from marketing_compliance_gate.adapters.gcp import firestore_evidence as evidence

REPO_ROOT = Path(__file__).resolve().parents[2]
_TF_DIR = REPO_ROOT / "infra" / "terraform"
_FIRESTORE_TF = _TF_DIR / "firestore.tf"

#: The composite queries the two adapters run, as (collection, ordered fields). A query with one
#: equality field is NOT here: Firestore indexes those itself and declaring one is dead config.
_COMPOSITE_QUERIES: dict[str, tuple[str, ...]] = {
    consent._RECORDS: ("tenant", "subject_id"),
    consent._PREFERENCES: ("tenant", "subject_id"),
    consent._SUPPRESSIONS: ("tenant", "subject_id"),
    consent._SENDS: ("tenant", "subject_id", "channel", "sent_at"),
    evidence._COLLECTION: ("tenant", "asset_id"),
}


def _tf() -> str:
    assert _FIRESTORE_TF.exists(), (
        "infra/terraform/firestore.tf is missing. Both managed adapters bind to Firestore and "
        "nothing creates a database, so the managed profile fails at the first request."
    )
    return _FIRESTORE_TF.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Provisioned at all
# --------------------------------------------------------------------------- #
def test_the_store_the_adapters_bind_to_is_actually_created() -> None:
    text = _tf()
    assert 'resource "google_firestore_database" "store"' in text
    apis = (_TF_DIR / "apis.tf").read_text(encoding="utf-8")
    assert '"firestore.googleapis.com"' in apis, "the Firestore API is not enabled"
    iam = (_TF_DIR / "iam.tf").read_text(encoding="utf-8")
    assert "roles/datastore.user" in iam, "the runtime identity cannot read or write documents"
    kms = (_TF_DIR / "kms.tf").read_text(encoding="utf-8")
    assert "gcp-sa-firestore.iam.gserviceaccount.com" in kms, (
        "no CMEK binding for Firestore, so a deployment admitted to the CMEK allowlist could "
        "not encrypt under its own key even after asking to"
    )


def test_firestore_cmek_is_off_by_default_because_it_is_allowlist_gated() -> None:
    """The one place the strict setting is not the default, and it is not a preference.

    Firestore customer-managed encryption is allowlist-gated by Google: a project that has not
    been admitted cannot create a CMEK database, and the apply FAILS rather than degrading. The
    reference deployment is not on that allowlist, which
    ``org-metadata/docs/deployment-posture.md`` records as externally blocked. So the key is a
    variable defaulting to empty, and a deployment that has been admitted sets it.
    """
    text = _tf()
    assert 'variable "firestore_cmek_key"' in text
    assert 'default     = ""' in text
    assert "dynamic \"cmek_config\"" in text, (
        "an unconditional cmek_config block fails the apply on any project that is not on the "
        "Firestore CMEK allowlist"
    )


def test_the_records_survive_a_destroy() -> None:
    """Consent records are what a regulator asks for months later."""
    assert 'deletion_policy = "ABANDON"' in _tf()


# --------------------------------------------------------------------------- #
# The residency boundary, which used to stop at a discarded return value
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("region", ["asia-southeast1", "asia-northeast1", "australia-southeast1"])
def test_every_residency_region_names_its_own_database(region: str) -> None:
    """Terraform builds these names from the same regions; the two must agree exactly."""
    assert _region.database_for(region) == f"mkt6-{region}"
    assert "name        = each.value" in _tf()
    assert "location_id = each.key" in _tf()
    # The Terraform derives the same name from the same region; a divergence here is a
    # database the adapter asks for and nothing creates.
    assert 'region => "mkt6-${region}"' in _tf()


def test_an_unresolved_region_refuses_rather_than_falling_back_to_the_default() -> None:
    """A fallback here is how a validated residency boundary becomes a comment."""
    with pytest.raises(Exception, match="no residency region was resolved"):
        _region.database_for("  ")


@pytest.mark.parametrize("module", [consent, evidence])
def test_the_adapter_selects_the_database_it_resolved(module: object) -> None:
    """Watched failing against the shipped ``firestore.Client(project=...)``.

    Asserted on the source rather than a live client, because what is under test is which
    database the adapter ASKS for, and constructing one needs credentials this gate does not
    have. The call it used to make is the one this forbids.
    """
    source = (
        REPO_ROOT
        / "src"
        / "marketing_compliance_gate"
        / "adapters"
        / "gcp"
        / f"{module.__name__.rsplit('.', 1)[1]}.py"
    ).read_text(encoding="utf-8")
    body = source[source.index("def _get_client") :]
    assert "database=database" in body, (
        "the adapter builds a client without naming a database, so it talks to the project "
        "default whatever region it just validated"
    )
    assert re.search(r"region\s*=\s*resolve_region", body), "the resolved region is discarded again"


# --------------------------------------------------------------------------- #
# The indexes, one per composite query
# --------------------------------------------------------------------------- #
def _declared_indexes() -> dict[str, tuple[str, ...]]:
    text = _tf()
    block = re.search(r"firestore_indexes = \{(.*?)\n  \}", text, flags=re.DOTALL)
    assert block is not None, "the index map moved; this guard cannot see it"
    out: dict[str, tuple[str, ...]] = {}
    for name, fields in re.findall(r"(\w+)\s*=\s*\[([^\]]*)\]", block.group(1)):
        out[name] = tuple(re.findall(r'"(\w+)"', fields))
    return out


def test_every_composite_query_has_a_declared_index() -> None:
    declared = _declared_indexes()
    for collection, fields in _COMPOSITE_QUERIES.items():
        assert collection in declared, (
            f"{collection} is queried on {list(fields)} and has no composite index. Firestore "
            "fails that query at REQUEST time, on the deployment and nowhere else."
        )
        assert declared[collection] == fields, (
            f"{collection}: index {list(declared[collection])} vs query {list(fields)}"
        )


def test_the_single_field_query_has_no_index_of_its_own() -> None:
    """Firestore maintains single-field indexes itself; declaring one is dead configuration."""
    assert consent._CAPS not in _declared_indexes()


def test_an_index_exists_for_every_database_rather_than_only_the_first() -> None:
    """An index is per DATABASE. Three regions and one index is two regions that fail."""
    text = _tf()
    assert "for database in keys(local.firestore_databases)" in text
    assert "database   = google_firestore_database.store[each.value.database].name" in text
