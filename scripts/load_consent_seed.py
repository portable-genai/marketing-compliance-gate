#!/usr/bin/env python3
"""Load the shipped fictional consent seed into the managed Firestore store.

The marketing journey reads consent over HTTP from this service, and on a deployment that leg
was empty: nothing created the Firestore databases the managed adapters bind to, and nothing
ever put a record in one. Three BigQuery books can be loaded for the three marketing systems
and the journey still stops at the gate, because a subject with no consent record on file is a
subject nobody may be sent anything, which is the correct answer to an empty store and the
wrong demo.

This writes the SAME seven subjects the offline profile serves
(``adapters/local/_consent_seed.py``), through the MANAGED adapter's own write methods rather
than a second serializer. That matters: a loader with its own document shape is a second
description of the store, and the one that drifts is always the one nobody reads.

Three things it will not do.

**It will not overwrite a store it did not write.** It refuses unless the store is empty or its
own manifest document says what it holds is fictional. Consent records are the records a
regulator asks for; a demo loader must never be the thing that replaces them.

**It will not decide the tenant.** On a deployment the tenant is whatever the identity adapter
resolves, and a record under any other value is invisible to every real caller because the
tenant predicate is the authorisation. ``--tenant`` is required.

**It will not fold the second tenant in.** ``other-brand`` holds a subject with a perfectly good
grant so that a cross-tenant read which "worked" would be visibly wrong rather than merely
empty. It is stamped ``<tenant>-other``.

Usage::

    python scripts/load_consent_seed.py --project <id> --tenant <hosted-domain> --dry-run
    python scripts/load_consent_seed.py --project <id> --tenant <hosted-domain> --market SG

``--dry-run`` prints what it would write and stops: no credentials, no ``[gcp]`` extra, because
the Firestore import is inside the adapter it never constructs.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from marketing_compliance_gate.adapters.local import _consent_seed as seed  # noqa: E402
from marketing_compliance_gate.config import Settings  # noqa: E402
from marketing_compliance_gate.domain.models import Market  # noqa: E402

#: The manifest document the overwrite guard reads. One document in its own collection, so a
#: store holding a demo seed announces itself without a scan.
MANIFEST_COLLECTION = "mkt6_book_manifest"
MANIFEST_DOCUMENT = "current"

#: The suffix the second tenant keeps. See the module docstring.
OTHER_TENANT_SUFFIX = "-other"


def _retenant(value: str, tenant: str) -> str:
    """The tenant a seeded row is written under, keeping the second tenant separate."""
    if value == seed.OTHER_TENANT:
        return f"{tenant}{OTHER_TENANT_SUFFIX}"
    return tenant


def seeded_rows(tenant: str) -> dict[str, list[Any]]:
    """Every seeded object, retenanted, grouped by the port method that writes it."""
    if not tenant.strip():
        raise SystemExit("--tenant is required: a record under no tenant is unreachable")
    return {
        "put_record": [
            dataclasses.replace(row, tenant=_retenant(row.tenant, tenant))
            for row in seed.SEED_RECORDS
        ],
        "put_preference": [
            dataclasses.replace(row, tenant=_retenant(row.tenant, tenant))
            for row in seed.SEED_PREFERENCES
        ],
        "put_suppression": [
            dataclasses.replace(row, tenant=_retenant(row.tenant, tenant))
            for row in seed.SEED_SUPPRESSIONS
        ],
        "put_cap": [
            dataclasses.replace(row, tenant=_retenant(row.tenant, tenant)) for row in seed.SEED_CAPS
        ],
    }


def _manifest(tenant: str) -> dict[str, Any]:
    return {
        "book_version": "1.0.0",
        "fictional": True,
        "loaded_at": datetime.now(UTC).isoformat(),
        "tenant": tenant,
        "note": (
            "Synthetic consent seed loaded by scripts/load_consent_seed.py. Every subject is "
            "invented and every address is at a .example domain."
        ),
    }


def _may_overwrite(client: Any) -> bool:
    """Empty, or already declaring itself fictional. Anything else is somebody's real book."""
    manifest = client.collection(MANIFEST_COLLECTION).document(MANIFEST_DOCUMENT).get()
    if manifest.exists:
        return bool((manifest.to_dict() or {}).get("fictional") is True)
    # No manifest: proceed only if the store holds nothing at all.
    for collection in ("mkt6_consent_records", "mkt6_channel_preferences"):
        if any(True for _ in client.collection(collection).limit(1).stream()):
            return False
    return True


def load(settings: Settings, rows: dict[str, list[Any]], tenant: str) -> None:
    from marketing_compliance_gate.adapters.gcp.firestore_consent import (  # noqa: PLC0415
        FirestoreConsentStoreAdapter,
    )

    adapter = FirestoreConsentStoreAdapter(settings)
    client = adapter._get_client()  # noqa: SLF001 - the loader writes through the adapter
    if not _may_overwrite(client):
        raise SystemExit(
            f"refusing to load: {MANIFEST_COLLECTION}/{MANIFEST_DOCUMENT} does not declare "
            "what this store holds fictional, and the store is not empty. Point this at an "
            "empty database or one holding a previous demo seed."
        )
    for method, objects in rows.items():
        for obj in objects:
            getattr(adapter, method)(obj)
        print(f"  {method}: {len(objects)}")
    client.collection(MANIFEST_COLLECTION).document(MANIFEST_DOCUMENT).set(_manifest(tenant))
    print(f"  {MANIFEST_COLLECTION}: 1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default="", help="the GCP project holding the store")
    parser.add_argument(
        "--market",
        default="",
        choices=("", *(m.value for m in Market)),
        help="the market whose residency database to write to (default: the active market)",
    )
    parser.add_argument(
        "--tenant",
        required=True,
        help=(
            "the owning tenant to stamp on every seeded record. On a deployment this is what "
            "the identity adapter resolves; a record under any other value is invisible because "
            "the tenant predicate is the authorisation."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be written and stop (no credentials, no [gcp] extra)",
    )
    args = parser.parse_args(argv)

    settings = Settings.load()
    if args.market:
        # `active_market` is a PROPERTY over the `market` field, so the field is what is
        # replaced. Getting this wrong raised rather than silently writing to the wrong
        # region's database, which is the failure mode worth having.
        settings = dataclasses.replace(settings, market=Market(args.market).value)
    if args.project:
        settings = dataclasses.replace(settings, project_id=args.project)

    rows = seeded_rows(args.tenant)
    from marketing_compliance_gate.adapters.gcp._region import (  # noqa: PLC0415
        database_for,
        resolve_region,
    )

    region = resolve_region(settings, market=settings.active_market)
    database = database_for(region)
    total = sum(len(objects) for objects in rows.values())

    if args.dry_run:
        print(
            f"dry run: {total} records for {database} (region {region}, "
            f"tenant {args.tenant!r}), not written"
        )
        for method, objects in rows.items():
            tenants = sorted({getattr(obj, "tenant", "") for obj in objects})
            print(f"  {method}: {len(objects)} under {tenants}")
        return 0

    if not settings.project_id or settings.project_id.startswith("your-"):
        raise SystemExit("--project is required (or set GOOGLE_CLOUD_PROJECT)")
    print(
        f"loading the consent seed into {settings.project_id}/{database} as tenant {args.tenant!r}"
    )
    load(settings, rows, args.tenant)
    print(
        "done. Verify: the mkt journey's gate step must now DECIDE rather than refuse for "
        "want of a record on file."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
