"""The API image the deployment promotes is patched, not merely reproducible.

Hosted CI never runs ``docker build``, and nothing else in this repository reads the root
``Dockerfile``, so the two properties below were lost without a single check going red. Both were
found by running the scan the other promoted images in this organization pass,
``trivy image --exit-code 1 --ignore-unfixed --severity HIGH,CRITICAL``, which reported 30 fixable
HIGH from the pinned Debian base and 2 from Python packages:

* **a digest pin freezes the base image, which freezes its unpatched packages too.** Reproducible
  and patched are independent properties and the pin buys only the first, while being cited as the
  supply-chain evidence for both. The runtime stage has to apply the distribution's security
  updates on top, which is what the sibling stacks beside this deployment do and why their images
  pass the same scan;
* **pip VENDORS its dependencies.** ``msgpack`` and ``setuptools`` live inside ``pip/_vendor`` and
  are pinned by ``pip/_vendor/vendor.txt``, so a scanner reports pip's bundled copies as installed
  packages. Both Python findings came from there. Neither package appears in any lockfile here and
  no lock move can reach them, because they were never resolved: they arrive inside pip. A serving
  container installs nothing anyway, so the package manager is an install capability an attacker
  can use and the application never can.

The assertions read the RUNTIME stage only. The builder legitimately carries a compiler, git and
pip, and none of it is copied forward.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _runtime_stage() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    marker = "AS runtime"
    assert marker in text, "the API Dockerfile has no runtime stage, so nothing was checked"
    return text[text.index(marker) :]


def test_the_runtime_stage_applies_the_distributions_security_updates() -> None:
    runtime = _runtime_stage()
    assert re.search(r"apt-get\s+upgrade\b", runtime), (
        "the runtime stage never upgrades: a digest-pinned base ships its unpatched packages, "
        "and the promotion scan counts every one of them"
    )
    assert "rm -rf /var/lib/apt/lists/*" in runtime, (
        "the apt lists must be removed in the same layer, or the image carries the index too"
    )


def test_the_runtime_image_carries_no_package_manager() -> None:
    runtime = _runtime_stage()
    # Both prefixes: the system interpreter's own pip AND the copied venv's. Removing one leaves
    # the other's vendored msgpack and setuptools on disk for the scanner to find.
    #
    # Matched as a WHOLE path, not as a substring. A plain `in` check passes on a Dockerfile that
    # deletes only `.../site-packages/pip-*.dist-info` and leaves the package itself, because the
    # metadata path contains the package path. Proved: dropping the venv's `pip` line while keeping
    # its `pip-*.dist-info` line left a substring assertion green, and the image would still have
    # shipped pip and both of its vendored findings.
    for removed in (
        "/usr/local/lib/python3.14/site-packages/pip",
        "/opt/venv/lib/python3.14/site-packages/pip",
        "/usr/local/bin/pip",
        "/opt/venv/bin/pip",
    ):
        assert re.search(rf"{re.escape(removed)}(?![-\w/])", runtime), (
            f"the runtime stage leaves {removed} in the image"
        )


def test_the_runtime_image_runs_as_a_non_root_user() -> None:
    runtime = _runtime_stage()
    assert re.search(r"^USER\s+(?!root\b)\S+", runtime, flags=re.MULTILINE), (
        "the serving container must drop root"
    )


def test_every_base_image_is_pinned_by_digest() -> None:
    images = re.findall(
        r"^FROM\s+(\S+)", DOCKERFILE.read_text(encoding="utf-8"), flags=re.MULTILINE
    )
    assert images, "no FROM line found, so nothing was checked"
    for image in images:
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"{image} is not digest-pinned"
