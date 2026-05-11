"""Local git repository reader for Open Law XML corpora."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class GitTreeEntry:
    """One git tree entry."""

    path: str
    type: str
    sha: str = ""
    size: int = 0


@dataclass(frozen=True)
class LocalGitRemote:
    """One configured git remote URL."""

    name: str
    url: str


@dataclass(frozen=True)
class LocalGitRepoIdentity:
    """Reproducibility identity for a local git checkout."""

    label: str
    head_commit: str
    current_branch: str
    branch_count: int
    remotes: Tuple[LocalGitRemote, ...] = ()


@dataclass(frozen=True)
class LocalGitRepo:
    """Read a local git checkout without mutating it."""

    path: Path

    def list_branches(self) -> Tuple[str, ...]:
        output = self._git("for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")
        branches: set[str] = set()
        for line in output.splitlines():
            branch = line.strip()
            if not branch or branch.endswith("/HEAD"):
                continue
            if branch.startswith("origin/"):
                branch = branch.removeprefix("origin/")
            branches.add(branch)
        return tuple(sorted(branches))

    def read_text(self, ref: str, path: str) -> str:
        commit = self.resolve_ref(ref)
        return self._git("show", f"{commit}:{path}")

    def list_tree(self, ref: str) -> Tuple[GitTreeEntry, ...]:
        commit = self.resolve_ref(ref)
        output = self._git("ls-tree", "-r", "-l", commit)
        entries: list[GitTreeEntry] = []
        for line in output.splitlines():
            meta, path = line.split("\t", 1)
            mode, entry_type, sha, size_raw = meta.split(maxsplit=3)
            size = int(size_raw) if size_raw.isdigit() else 0
            entries.append(GitTreeEntry(path=path, type=entry_type, sha=sha, size=size))
        return tuple(entries)

    def resolve_ref(self, ref: str) -> str:
        if _looks_like_commit(ref):
            return ref
        if self._ref_exists(ref):
            return self._git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()
        origin_ref = f"origin/{ref}"
        return self._git("rev-parse", "--verify", f"{origin_ref}^{{commit}}").strip()

    def identity(self, *, label: str) -> LocalGitRepoIdentity:
        """Return local checkout identity for evidence-pack reproducibility."""

        return LocalGitRepoIdentity(
            label=label,
            head_commit=self._git("rev-parse", "HEAD").strip(),
            current_branch=self._git("branch", "--show-current").strip(),
            branch_count=len(self.list_branches()),
            remotes=self._remotes(),
        )

    def _ref_exists(self, ref: str) -> bool:
        result = subprocess.run(
            ("git", "-C", str(self.path), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.returncode == 0

    def _git(self, *args: str) -> str:
        return subprocess.check_output(("git", "-C", str(self.path), *args), text=True)

    def _remotes(self) -> Tuple[LocalGitRemote, ...]:
        result = subprocess.run(
            ("git", "-C", str(self.path), "config", "--get-regexp", r"^remote\..*\.url$"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        remotes: list[LocalGitRemote] = []
        for line in result.stdout.splitlines():
            key, url = line.split(maxsplit=1)
            parts = key.split(".")
            if len(parts) == 3:
                remotes.append(LocalGitRemote(name=parts[1], url=url))
        return tuple(sorted(remotes, key=lambda remote: remote.name))


@dataclass(frozen=True)
class MarylandLocalRepos:
    """Local checkouts required for Maryland Open Law corpus audit."""

    source: LocalGitRepo
    codified: LocalGitRepo


def make_maryland_repos(source_repo: str | Path, codified_repo: str | Path) -> MarylandLocalRepos:
    return MarylandLocalRepos(
        source=LocalGitRepo(Path(source_repo)),
        codified=LocalGitRepo(Path(codified_repo)),
    )


def maryland_repos_identity_to_jsonable(repos: MarylandLocalRepos) -> dict[str, object]:
    return {
        "source": _repo_identity_to_jsonable(repos.source.identity(label="maryland-dsd/law-xml")),
        "codified": _repo_identity_to_jsonable(repos.codified.identity(label="maryland-dsd/law-xml-codified")),
    }


def _repo_identity_to_jsonable(identity: LocalGitRepoIdentity) -> dict[str, object]:
    return {
        "label": identity.label,
        "head_commit": identity.head_commit,
        "current_branch": identity.current_branch,
        "branch_count": identity.branch_count,
        "remotes": [{"name": remote.name, "url": remote.url} for remote in identity.remotes],
    }


def _looks_like_commit(ref: str) -> bool:
    return len(ref) >= 7 and all(char in "0123456789abcdefABCDEF" for char in ref)
