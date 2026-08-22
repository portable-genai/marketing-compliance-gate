"""The supply-chain claim each lockfile header makes about itself, actually asserted.

Both lockfile headers name this file: "tag -> commit, and `tests/unit/test_repo_artifacts.py`
proves each line against the tag `pyproject.toml` names and against the other lockfile". The
file did not exist, so the header cited a proof that was never run and the reproducible-install
claim rested on a sentence. That is the failure mode the whole catalog is written against: a
claim with no command behind it.

The claim has three parts, and each is a test below. `pyproject.toml` names a movable release
TAG, which is the half a human can review in a diff; the lockfiles pin the 40-character COMMIT
that tag pointed at, which is the half that cannot move; and the header carries the map tying
the two together. Two homes for one pin is one home too many unless something holds them equal.

Named `test_repo_artifacts.py` because that is the name the headers cite. It proves the pins
only. The roadmap tier carries a fuller file under this name that also asserts the required
artifact set; this one is deliberately scoped to the claim actually made here, and can grow.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _discovered_lockfiles() -> tuple[str, ...]:
    """Every lockfile this repository actually carries, found by glob rather than by list.

    A hardcoded two-file list is how a blind spot gets baked into the generator. An auxiliary
    lockfile that no regeneration path and no guard enumerates sits on a superseded commons pin
    indefinitely, and that is not always cosmetic: a job that installs an auxiliary lock ON TOP
    of the dev lock pulls a kit BACKWARDS on every run, so browser evidence is gathered against
    the very fail-open a newer release closed.

    A render ships two lockfiles, which is the only reason a two-name list would not break
    anything here. A repository that grows a third would silently stop being checked, and
    "silently stop being checked" is indistinguishable from "checked and fine" in a gate summary.
    """
    return tuple(sorted(path.name for path in REPO_ROOT.glob("requirements-*.lock")))


_LOCKFILES = _discovered_lockfiles()

#: The ``#   <package>  v<tag> = <commit>`` map a lockfile header carries, so the commit it
#: pins can be checked against the tag pyproject names with NO network call.
_TAG_COMMIT_LINE = re.compile(
    r"^#\s+(?P<package>[a-z0-9-]+)\s+(?P<tag>v[0-9][^\s]*)\s*=\s*(?P<commit>[0-9a-f]{40})\s*$"
)

#: A directory holding clones of the commons repos, one per package name. Set it when the
#: checkouts are not siblings of this repo. Read two-state deliberately: it names a search path
#: and grants nothing, and an emptied value simply finds no store, which is the same as unset.
_CHECKOUT_ROOT_ENV = "COMMONS_GIT_CHECKOUT_ROOT"


def _commons_refs(text: str) -> dict[str, str]:
    """Map package -> pinned ref, from any file that carries ``git+https`` direct references."""
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().strip('",')
        if line.startswith("#") or "git+https://github.com/portable-genai/" not in line:
            continue
        name = line.split("@ git+", 1)[0].strip().split("[", 1)[0].strip().strip('"')
        found[name] = line.rsplit("@", 1)[1]
    return found


def test_the_lockfile_discovery_actually_finds_the_lockfiles() -> None:
    """A glob that matches nothing turns every test parametrized on it into a silent pass.

    This is the failure mode the glob was introduced to avoid, reappearing one level up: an
    empty `_LOCKFILES` makes `pytest.mark.parametrize` generate zero cases, and zero cases is
    reported as success. The dev lock is the one every repository has.
    """
    assert _LOCKFILES, "no requirements-*.lock found; the lockfile guards would assert nothing"
    assert "requirements-dev.lock" in _LOCKFILES, (
        f"discovered {_LOCKFILES}, which does not include the dev lock every repository carries"
    )


def _tag_commit_map(text: str) -> dict[str, tuple[str, str]]:
    """Map package -> (tag, commit) from a lockfile header."""
    found: dict[str, tuple[str, str]] = {}
    for raw in text.splitlines():
        match = _TAG_COMMIT_LINE.match(raw)
        if match:
            found[match["package"]] = (match["tag"], match["commit"])
    return found


def _git(store: Path, *args: str) -> str | None:
    """Run git in ``store``; ``None`` when git is absent or the command failed."""
    if shutil.which("git") is None:  # pragma: no cover - git is present in the gate
        return None
    completed = subprocess.run(
        ["git", "-C", str(store), *args], capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _installed_source_dir(package: str) -> Path | None:
    """The local directory a package was installed FROM, when it was installed from one.

    PEP 610 records an install's provenance in ``direct_url.json``. An editable install and a
    ``git+file://`` install both record a ``file://`` URL, which in this workspace is the
    commons repo's git work tree. A ``git+https://`` install records the remote and no local
    path, so this returns None and the search falls through.
    """
    try:
        raw = metadata.distribution(package).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    url = str(json.loads(raw).get("url", ""))
    if not url.startswith("file://"):
        return None
    return Path(unquote(urlparse(url).path))


def object_stores(package: str) -> list[Path]:
    """Every local git work tree that might hold ``package``'s objects, best guess first."""
    candidates: list[Path | None] = []
    root = os.environ.get(_CHECKOUT_ROOT_ENV, "").strip()
    if root:
        candidates.append(Path(root) / package)
    # The polyrepo workspace: every catalog repo sits next to the commons it pins.
    candidates.append(REPO_ROOT.parent / package)
    candidates.append(_installed_source_dir(package))
    return [
        path
        for path in candidates
        if path is not None and path.is_dir() and _git(path, "rev-parse", "--git-dir") is not None
    ]


def git_object_type(store: Path, sha: str) -> str | None:
    """``commit`` / ``tag`` / ..., or ``None`` when this store does not have the object.

    A store that has never fetched the object is NO evidence, in either direction, so it is
    reported as absent and the caller keeps looking. Only a positive answer decides anything.
    """
    return _git(store, "cat-file", "-t", sha)


def pin_verdict(package: str, sha: str, tag: str) -> tuple[str, str] | None:
    """``(verdict, detail)`` from the first store that knows ``sha``, or None if none does."""
    for store in object_stores(package):
        kind = git_object_type(store, sha)
        if kind is None:
            continue
        if kind != "commit":
            return "not-a-commit", f"{store} says {sha} is a {kind} object, not a commit"
        # `rev-list -n 1` dereferences an annotated tag to its commit, which `rev-parse` does
        # not, and that difference is the whole defect.
        dereferenced = _git(store, "rev-list", "-n", "1", tag)
        if dereferenced is not None and dereferenced != sha:
            return "wrong-commit", f"{store} says {tag} is {dereferenced}, not {sha}"
        return "commit", str(store)
    return None


@pytest.mark.parametrize("name", _LOCKFILES)
def test_the_lockfile_pins_a_commit_and_not_a_movable_tag(name: str) -> None:
    """A tag is a pointer somebody can move; a lockfile that pins one is not a lock.

    A re-pushed tag changes what installs with NO diff in the lockfile and nothing in the repo
    to notice it, which is the reproducible-install claim failing exactly where it is supposed
    to be strongest.

    SHAPE ONLY, and not enough alone: an ANNOTATED TAG OBJECT's sha is also 40 hex characters,
    so this passes a lockfile pinned at ``git rev-parse <tag>`` output, which is the mistake
    that was once swept out of several repos. The check that tells them apart asks git, below.
    """
    locked = _commons_refs((REPO_ROOT / name).read_text(encoding="utf-8"))
    assert locked, f"{name} pins no commons at all; this check is reading the wrong thing"
    for package, ref in sorted(locked.items()):
        assert re.fullmatch(r"[0-9a-f]{40}", ref), (
            f"{name} pins {package} at {ref!r}, which is not a 40-character commit sha. "
            "Dereference the tag with `git rev-list -n 1 <tag>` (NOT `git rev-parse <tag>`, "
            "which returns the annotated tag object) and pin the commit."
        )


@pytest.mark.parametrize("name", _LOCKFILES)
def test_the_header_map_agrees_with_the_pins_and_with_pyproject(name: str) -> None:
    """The three-way agreement the header claims: map commit = pin, map tag = pyproject."""
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    locked = _commons_refs(text)
    declared = _commons_refs((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    recorded = _tag_commit_map(text)
    assert declared, "pyproject pins no commons; this check is reading the wrong thing"
    assert set(recorded) == set(locked), (
        f"{name}: the header tag map covers {sorted(recorded)} but the file pins {sorted(locked)}"
    )
    for package, (tag, commit) in sorted(recorded.items()):
        assert package in declared, f"{name} pins {package}, which pyproject does not declare"
        assert commit == locked[package], (
            f"{name}: header records {package} at {commit}, the pin says {locked[package]}"
        )
        assert tag == declared[package], (
            f"{name}: header records {package} {tag}, pyproject declares {declared[package]}"
        )


def test_both_lockfiles_pin_the_commons_at_the_same_commits() -> None:
    """The dev gate and the shipped image must install the same commons, or the gate proves less."""
    dev, runtime = (
        _commons_refs((REPO_ROOT / name).read_text(encoding="utf-8")) for name in _LOCKFILES
    )
    shared = sorted(set(dev) & set(runtime))
    assert shared, "the two lockfiles have no commons in common; one of them is not being read"
    for package in shared:
        assert dev[package] == runtime[package], (
            f"{package}: dev lock pins {dev[package]}, runtime lock pins {runtime[package]}"
        )


def test_pyproject_names_a_tag_so_a_bump_stays_readable() -> None:
    """The tag is the reviewable half of the pin; a diff of two shas says nothing to a human."""
    declared = _commons_refs((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert declared, "pyproject pins no commons; this check is reading the wrong thing"
    for package, ref in sorted(declared.items()):
        assert re.fullmatch(r"v[0-9][0-9a-zA-Z.\-+]*", ref), (
            f"pyproject pins {package} at {ref!r}; it should name the release TAG, and the "
            "lockfiles should carry the commit that tag resolves to."
        )


@pytest.mark.parametrize("name", _LOCKFILES)
def test_each_locked_sha_is_a_commit_object_and_not_a_tag_object(name: str) -> None:
    """Ask git what the pinned object IS. A regular expression cannot, and never could.

    Skips only when no local object store can answer for any package at all: a check with no
    evidence has proved nothing and must say so rather than pass.
    """
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    locked = _commons_refs(text)
    recorded = _tag_commit_map(text)
    checked: list[str] = []
    unknown: list[str] = []
    for package, sha in sorted(locked.items()):
        tag = recorded.get(package, ("", ""))[0]
        verdict = pin_verdict(package, sha, tag)
        if verdict is None:
            unknown.append(package)
            continue
        kind, detail = verdict
        assert kind == "commit", (
            f"{name} pins {package} at {sha}, and {detail}. An annotated tag object's sha is "
            "also 40 hex characters, so it passes every shape check while installing nothing "
            "reproducible. Dereference with `git rev-list -n 1 <tag>`, never `git rev-parse`."
        )
        checked.append(package)
    if not checked:
        pytest.skip(
            f"no local git object store holds any of {unknown}, so the pinned objects' TYPE "
            f"cannot be established offline. Clone the commons next to this repo, or set "
            f"{_CHECKOUT_ROOT_ENV} to a directory holding them."
        )


def test_the_object_type_check_can_tell_a_tag_object_from_a_commit(tmp_path: Path) -> None:
    """The positive control, on a repo built here: a green tick nobody proved is decoration.

    Builds a throwaway repository with an ANNOTATED tag, the only kind that produces a second
    40-hex sha, and proves the helper distinguishes the two objects. Without this, a
    ``git_object_type`` that quietly returned None forever would skip its way to green
    everywhere.
    """
    if shutil.which("git") is None:  # pragma: no cover - git is present in the gate
        pytest.skip("git is not installed")
    store = tmp_path / "repo"
    store.mkdir()
    (store / "file.txt").write_text("synthetic", encoding="utf-8")
    for args in (
        ("init", "-q"),
        ("config", "user.email", "gate@example.invalid"),
        ("config", "user.name", "Gate"),
        ("add", "file.txt"),
        ("commit", "-q", "-m", "one"),
        ("tag", "-a", "v9.9.9", "-m", "annotated"),
    ):
        assert _git(store, *args) is not None, f"git {args[0]} failed building the fixture"
    commit = _git(store, "rev-list", "-n", "1", "v9.9.9")
    tag_object = _git(store, "rev-parse", "v9.9.9")
    assert commit and tag_object and commit != tag_object, (
        "the fixture did not produce an annotated tag, so it cannot prove the distinction"
    )
    assert git_object_type(store, commit) == "commit"
    assert git_object_type(store, tag_object) == "tag", (
        "`git rev-parse <annotated tag>` returns a TAG object whose sha is also 40 hex "
        "characters. That is the whole defect, and the check must be able to see it."
    )
    assert git_object_type(store, "0" * 40) is None, "an unknown object is not evidence"
