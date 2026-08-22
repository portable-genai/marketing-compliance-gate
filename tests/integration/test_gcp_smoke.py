"""Integration smoke test — requires live Google Cloud credentials (deselected by default).

Run with ``pytest -m integration`` and the ``gcp`` profile when the ``[gcp]`` extra is
installed and credentials are configured. The default gate uses ``-m 'not integration'``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_gcp_research_smoke():
    pytest.skip("requires live Google Cloud credentials and the [gcp] extra; phase 2")
