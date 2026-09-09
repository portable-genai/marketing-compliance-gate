# firestore.tf : the two tenant-owned Firestore stores this service reads and writes.
#
# THIS FILE DID NOT EXIST. `adapters/gcp/firestore_consent.py` and
# `adapters/gcp/firestore_evidence.py` have both been bound as the managed implementations of
# their ports since the repository was written, and nothing here created a database, enabled the
# API, granted a role or bound a key. The consent leg of the marketing journey would have failed
# at its first request on a deployment, and the substantiation evidence a compliance officer is
# meant to pull up months later had nowhere to be.
#
# ## Why there is one database PER RESIDENCY REGION
#
# A Firestore database's location is fixed when it is created and cannot be changed afterwards,
# and a project's DEFAULT database is a single one of them. This service supports three markets
# with three residency regions (JP asia-northeast1, AU australia-southeast1, SG asia-southeast1),
# and `adapters/gcp/_region.py` resolves and validates the region for every managed call.
#
# That validation used to reach nothing: both adapters called `resolve_region(...)` and DISCARDED
# the result, then built `firestore.Client(project=...)` against the default database. A JP
# request passed the residency check and then read and wrote whichever single region the default
# database happened to sit in. The check was real and the wire was not.
#
# So each enabled residency region gets a NAMED database, `mkt6-<region>`, and the adapters
# select it from the region they resolved (`_region.database_for`). A contract test holds the
# names here against that function, because a database this file does not create is a region the
# adapters can resolve and then fail on.
#
# General Principle map:
#   P-03 (residency): one database per in-country region, and the adapter picks by market.
#   P-09 (CMEK explicit): each database encrypts under the regional key. CMEK does not cascade,
#         so the Firestore service-agent binding is declared in kms.tf alongside it.
#   P-04 (data minimisation): the composite indexes below are exactly the queries the adapters
#         run. Firestore creates single-field indexes on its own; a COMPOSITE query with no
#         declared index fails at request time with FAILED_PRECONDITION, which is a failure that
#         only ever appears on a deployment.

# The residency regions this installation enables. Defaults to the deployment's own region: a
# single-market install creates one database and pays for one. An installation serving all three
# markets lists all three, and the adapters route by market.
variable "residency_regions" {
  description = <<-EOT
    Residency regions to create a Firestore database in, one per in-country market this
    installation serves. Must be a subset of the regions var.region is validated against.
    Defaults to [var.region], so a single-market deployment provisions exactly one store.
  EOT
  type        = list(string)
  default     = null

  validation {
    condition = var.residency_regions == null || alltrue([
      for region in coalesce(var.residency_regions, []) :
      contains(["asia-southeast1", "asia-northeast1", "australia-southeast1"], region)
    ])
    error_message = "residency_regions must be in-country regions: asia-southeast1, asia-northeast1 or australia-southeast1."
  }
}

locals {
  # A null (unset) list means "this deployment's own region", NOT "all of them": provisioning a
  # store in a country nobody is serving is standing cost and a residency surface with no user.
  firestore_regions = coalesce(var.residency_regions, [var.region])
  # Must match `_region.database_for` exactly; a contract test holds the two together.
  firestore_databases = { for region in local.firestore_regions : region => "mkt6-${region}" }
}

resource "google_firestore_database" "store" {
  for_each = local.firestore_databases

  project     = var.project_id
  name        = each.value
  location_id = each.key
  type        = "FIRESTORE_NATIVE"

  # Consent records and substantiation evidence are the records a regulator asks for months
  # later. Deleting the stack must not take them, and a protected database refuses `terraform
  # destroy` until an operator says otherwise in the same breath.
  deletion_policy = "ABANDON"

  cmek_config {
    kms_key_name = google_kms_crypto_key.mkt_gov.id # CMEK does not cascade (P-09)
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.firestore,
  ]
}

# ---------------------------------------------------------------------------------------- #
# The composite indexes, one per composite query the adapters run
# ---------------------------------------------------------------------------------------- #
# Firestore builds single-field indexes automatically. A query with more than one equality
# field, or an equality plus a range, needs a composite index declared, and without one the
# query fails at REQUEST time rather than at deploy time: the failure appears the first time a
# compliance officer opens a subject, on the deployment, and nowhere else.
locals {
  # collection -> the ordered fields its composite query filters on.
  firestore_indexes = {
    # snapshot(): tenant AND subject_id, on four of the five consent collections.
    mkt6_consent_records      = ["tenant", "subject_id"]
    mkt6_channel_preferences  = ["tenant", "subject_id"]
    mkt6_consent_suppressions = ["tenant", "subject_id"]
    # count_sends(): three equalities and a range on sent_at, which must come last.
    mkt6_consent_sends = ["tenant", "subject_id", "channel", "sent_at"]
    # list_for_asset(): tenant AND asset_id.
    mkt6_substantiation_evidence = ["tenant", "asset_id"]
  }
  # mkt6_frequency_caps is deliberately absent: its query filters on `tenant` alone, which is a
  # single-field index Firestore maintains itself. Declaring one would be dead configuration.
  firestore_index_pairs = flatten([
    for database in keys(local.firestore_databases) : [
      for collection, fields in local.firestore_indexes : {
        key        = "${database}/${collection}"
        database   = database
        collection = collection
        fields     = fields
      }
    ]
  ])
}

resource "google_firestore_index" "composite" {
  for_each = { for pair in local.firestore_index_pairs : pair.key => pair }

  project    = var.project_id
  database   = google_firestore_database.store[each.value.database].name
  collection = each.value.collection

  dynamic "fields" {
    for_each = each.value.fields
    content {
      field_path = fields.value
      order      = "ASCENDING"
    }
  }
}
