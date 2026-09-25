"""Platform ReviewRouterPort: submit the routed review to human-review-console via ``review-kit``.

Builds the kit review from the escalated compliance review and submits it to the
human-review-console service intake (``POST /v1/service/reviews``). The base URL comes from
``HUMAN_REVIEW_URL`` and the signed actor from ``S2S_SIGNING_KEY``; the bearer depends on how the
console is reached:

* **Through the portal's IAP edge** (``gcp``): the deployed console is an embedded app behind
  `journey-portal`, so ``HUMAN_REVIEW_URL`` is its edge path
  (``https://<edge-host>/apps/human-review-console/api``) and the edge accepts only a
  Google-signed ID token minted for the IAP OAuth client id, named by
  ``HUMAN_REVIEW_IAP_AUDIENCE``. The router mints one per submission with this service's
  workload identity (:func:`._s2s.fetch_id_token`), so an expiring token is never reused. The
  console authenticates this service from the IAP assertion the edge forwards, not from the
  portal's bearer that replaces this one.
* **Directly** (audience unset): the static ``S2S_TOKEN`` bearer from the shared platform env
  vars.

``HUMAN_REVIEW_IAP_AUDIENCE`` is read in three states: unset keeps the static bearer, emptied
refuses at construction, and a backend-service path pasted where the client id belongs refuses
by name. Under ``gcp`` the boot check in :mod:`marketing_compliance_gate.config` requires it
beside the URL while routing is on. The kit itself uses stdlib ``urllib``; ``google-auth`` is
imported only when a token is minted.
"""

from __future__ import annotations

from review_kit import ReviewClient

from ...config import HUMAN_REVIEW_IAP_AUDIENCE_ENV, Settings, iap_audience_or_refuse
from ...domain.consent import ConsentRecord
from ...domain.models import Review, SubstantiationAssessment
from ...envread import optional_setting, read_env_setting
from .._review_payload import (
    assessment_to_kit_review,
    consent_grant_to_kit_review,
    review_to_kit_review,
)
from . import _s2s
from ._s2s import SIGNING_KEY_ENV, TOKEN_ENV


class PlatformReviewRouter:
    """Submit escalated compliance reviews to human-review-console (rule R8), reusing the shared
    client.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        audience = optional_setting(HUMAN_REVIEW_IAP_AUDIENCE_ENV)
        self._audience = (
            None
            if audience is None
            else iap_audience_or_refuse(HUMAN_REVIEW_IAP_AUDIENCE_ENV, audience)
        )

    def _client(self) -> ReviewClient:
        base_url = read_env_setting("HUMAN_REVIEW_URL").value
        if not base_url:
            raise RuntimeError(
                "HUMAN_REVIEW_URL must be set to route reviews to human-review-console"
            )
        audience = self._audience
        if audience is None:
            return ReviewClient(base_url, token_env=TOKEN_ENV, signing_key_env=SIGNING_KEY_ENV)
        return ReviewClient(
            base_url,
            token_env=TOKEN_ENV,
            signing_key_env=SIGNING_KEY_ENV,
            bearer_provider=lambda: _s2s.fetch_id_token(audience),
        )

    def route(self, review: Review, *, maker: str, tenant: str = "") -> None:
        self._client().submit(
            review_to_kit_review(review, maker=maker, tenant=tenant),
            actor="mkt6-compliance-governance",
        )

    def route_assessment(  # pragma: no cover - needs live human-review-console
        self, assessment: SubstantiationAssessment, *, maker: str, tenant: str = ""
    ) -> None:
        self._client().submit(
            assessment_to_kit_review(assessment, maker=maker, tenant=tenant),
            actor="mkt6-compliance-governance",
        )

    def route_consent_grant(  # pragma: no cover - needs live human-review-console
        self, record: ConsentRecord, *, reason: str, maker: str, tenant: str = ""
    ) -> None:
        self._client().submit(
            consent_grant_to_kit_review(record, reason=reason, maker=maker, tenant=tenant),
            actor="mkt6-compliance-governance",
        )
