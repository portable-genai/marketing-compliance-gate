"""Where this stack's Terraform state lives, and that every offline path still needs none.

Pinned here, and each was watched failing first:

* **the state lived on one laptop.** ``providers.tf`` declared no backend, so an apply wrote
  ``terraform.tfstate`` beside the code: the only record of the Firestore database, its indexes
  and the KMS key ring that exist in the deployment, held in a gitignored file on whichever
  machine ran the apply. Every other deployed stack keeps its state in the shared GCS bucket
  under its own prefix. The backend is partial, ``backend "gcs" {}``, so the bucket and the
  prefix are init inputs rather than code;
* **a partial backend must not reach the offline proof.** ``make tf-validate`` initialises with
  ``-backend=false``, as the CI runner does; an init that configured the backend there would
  need a bucket and credentials, which the gate contract forbids;
* **a documented init that names no backend is an instruction to start from empty state.** An
  empty prefix plans the database and the key ring as new, and both creates fail because both
  already exist. Every ``terraform init`` the Makefile and the deploy docs show is either
  backend-free or names the bucket and this stack's prefix, and the deploy docs carry the
  one-time ``-migrate-state`` step that moves the existing local state instead.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_TF_DIR = REPO_ROOT / "infra" / "terraform"
_PREFIX = "marketing-compliance-gate"
_RUNBOOK = REPO_ROOT / "docs" / "runbook.md"
_TF_README = _TF_DIR / "README.md"

#: Every file that runs or shows a ``terraform init`` for this stack.
_INIT_SOURCES = (
    REPO_ROOT / "Makefile",
    _RUNBOOK,
    _TF_README,
    _TF_DIR / "tests" / "shared_project_declines.tftest.hcl",
)

_INIT = re.compile(r"\bterraform(?:\s+-chdir=\S+)?\s+init\b")
_PREFIX_FLAG = re.compile(rf"-backend-config=prefix={re.escape(_PREFIX)}(?![\w/.-])")


def _code(text: str) -> str:
    """HCL with ``#`` comments removed, so a commented-out block does not count."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _join_continuations(text: str) -> str:
    return re.sub(r"\\\n\s*", " ", text)


def _init_commands(text: str) -> list[str]:
    return [line.strip() for line in _join_continuations(text).splitlines() if _INIT.search(line)]


def _recipe(target: str) -> str:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(target)}:.*\n((?:\t.*(?:\n|$))+)", makefile, re.MULTILINE)
    assert match, f"the Makefile has no {target} target"
    return _join_continuations(match.group(1))


def test_the_stack_declares_exactly_one_partial_gcs_backend() -> None:
    backends = [
        (tf.name, kind, body)
        for tf in sorted(_TF_DIR.glob("*.tf"))
        for kind, body in re.findall(
            r'\bbackend\s+"([^"]+)"\s*\{([^}]*)\}', _code(tf.read_text(encoding="utf-8"))
        )
    ]
    assert [(name, kind) for name, kind, _ in backends] == [("providers.tf", "gcs")], (
        "infra/terraform must declare one backend, a gcs block in providers.tf. Without it an "
        f"apply writes the deployment's only state to a local file. Found: {backends}"
    )
    assert backends[0][2].strip() == "", (
        "the gcs backend must stay partial: the bucket and the prefix are init inputs, never "
        f"code. Found a body: {backends[0][2].strip()!r}"
    )


def test_the_offline_terraform_proof_never_initialises_the_backend() -> None:
    recipe = _recipe("tf-validate")
    assert "terraform init -backend=false" in recipe, recipe
    assert "terraform validate" in recipe, recipe
    assert "terraform test" in recipe, recipe


def test_the_plan_target_refuses_without_a_state_bucket() -> None:
    recipe = _recipe("tf-plan")
    assert "-backend-config=bucket=$${TF_STATE_BUCKET:?" in recipe, (
        "make tf-plan must refuse when TF_STATE_BUCKET is unset rather than initialise with "
        f"an empty bucket. Recipe: {recipe!r}"
    )
    assert _PREFIX_FLAG.search(recipe), recipe


def test_every_terraform_init_is_offline_or_names_this_stacks_state() -> None:
    for source in _INIT_SOURCES:
        commands = _init_commands(source.read_text(encoding="utf-8"))
        where = source.relative_to(REPO_ROOT)
        assert commands, f"{where} shows no terraform init, so this check read nothing there"
        for command in commands:
            offline = "-backend=false" in command
            remote = "-backend-config=bucket=" in command and bool(_PREFIX_FLAG.search(command))
            assert offline != remote, (
                f"{where}: every terraform init is either backend-free (-backend=false) or names "
                f"the state bucket and the {_PREFIX} prefix, never neither and never both: "
                f"{command!r}"
            )


def test_the_deploy_docs_migrate_the_existing_state_rather_than_recreate_it() -> None:
    for source in (_RUNBOOK, _TF_README):
        migrations = [
            command
            for command in _init_commands(source.read_text(encoding="utf-8"))
            if "-migrate-state" in command
        ]
        assert migrations, (
            f"{source.relative_to(REPO_ROOT)} must show the one-time terraform init "
            "-migrate-state that moves the existing local state into the bucket: re-creating "
            "the Firestore database that already exists fails"
        )
