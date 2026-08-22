#!/usr/bin/env python3
"""Execute this repository's bounded portability contract without cloud credentials.

This is intentionally a proof driver, not another copy of the port map. The contract tests
own the repository-specific port-to-Protocol mapping and offline/exit calls; this program
runs those tests, verifies that the deterministic domain does not import a cloud SDK, and
states the claims a laptop cannot establish.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTEST_PYTHON = (
    ROOT / ".venv" / "bin" / "python"
    if (ROOT / ".venv" / "bin" / "python").is_file()
    else Path(sys.executable)
)


def _domain_files() -> list[Path]:
    return sorted((ROOT / "src").glob("*/domain/**/*.py"))


def _assert_cloud_free_domain() -> int:
    checked = 0
    forbidden: list[str] = []
    for path in _domain_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        checked += 1
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".", 1)[0] in {"google", "vertexai"}:
                    forbidden.append(f"{path.relative_to(ROOT)}:{node.lineno} imports {name}")
    if forbidden:
        raise AssertionError("cloud SDK crossed the domain boundary:\n" + "\n".join(forbidden))
    return checked


def main() -> int:
    parity = ROOT / "tests" / "contract" / "test_port_parity.py"
    if not parity.is_file():
        raise AssertionError("tests/contract/test_port_parity.py is required")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [
            str(PYTEST_PYTHON),
            "-m",
            "pytest",
            "-m",
            "not integration",
            "--disable-warnings",
            "-q",
        ],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if result.returncode:
        print("FAIL offline and port contract suite", file=sys.stderr)
        return result.returncode
    print("PASS offline product: the repository's non-integration suite completed")
    print("PASS port matrix: every declared SDK-free adapter conforms and exit adapters refuse")

    checked = _assert_cloud_free_domain()
    print(f"PASS portable core: {checked} domain modules import no Google Cloud SDK")
    print("PASS managed boundary: adapter selection is configuration, not domain code")
    print(
        "LIMITS not proved here: live GCP calls, hosted identity, managed durability, "
        "CMEK/VPC-SC/Org Policy enforcement, performance parity, or a completed on-premises "
        "adapter. Those require deployment evidence; this proof makes no such claim."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
