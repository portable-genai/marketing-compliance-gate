"""Platform ReviewRouterPort: submit the routed review to Hrz7 via ``review-kit``.

Builds the kit review from the escalated compliance review and submits it to the Hrz7 service
intake (``POST /v1/service/reviews``), S2S-authenticated. The Hrz7 base URL comes from
``MKT6_HRZ7_URL`` and the S2S credentials from the shared platform env vars (``HRZ_S2S_TOKEN`` /
``HRZ_S2S_SIGNING_KEY``), set on the deployed service. No cloud SDK is involved (the kit uses
stdlib ``urllib`` plus the wire-compatible S2S headers), so this module imports cleanly with no GCP
SDK; it is bound under the ``gcp`` and ``platform`` profiles because it makes a real network call
to a sibling service.
"""

from __future__ import annotations

from review_kit import ReviewClient

from ...config import Settings
from ...domain.consent import ConsentRecord
from ...domain.models import Review, SubstantiationAssessment
from ...envread import read_env_setting
from .._review_payload import (
    assessment_to_kit_review,
    consent_grant_to_kit_review,
    review_to_kit_review,
)
from ._s2s import SIGNING_KEY_ENV, TOKEN_ENV


class PlatformReviewRouter:
    """Submit escalated compliance reviews to Hrz7 (rule R8), reusing the shared client."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> ReviewClient:
        base_url = read_env_setting("MKT6_HRZ7_URL").value
        if not base_url:
            raise RuntimeError("MKT6_HRZ7_URL must be set to route reviews to Hrz7")
        return ReviewClient(base_url, token_env=TOKEN_ENV, signing_key_env=SIGNING_KEY_ENV)

    def route(  # pragma: no cover - needs live Hrz7
        self, review: Review, *, maker: str, tenant: str = ""
    ) -> None:
        self._client().submit(
            review_to_kit_review(review, maker=maker, tenant=tenant),
            actor="mkt6-compliance-governance",
        )

    def route_assessment(  # pragma: no cover - needs live Hrz7
        self, assessment: SubstantiationAssessment, *, maker: str, tenant: str = ""
    ) -> None:
        self._client().submit(
            assessment_to_kit_review(assessment, maker=maker, tenant=tenant),
            actor="mkt6-compliance-governance",
        )

    def route_consent_grant(  # pragma: no cover - needs live Hrz7
        self, record: ConsentRecord, *, reason: str, maker: str, tenant: str = ""
    ) -> None:
        self._client().submit(
            consent_grant_to_kit_review(record, reason=reason, maker=maker, tenant=tenant),
            actor="mkt6-compliance-governance",
        )
