"""Maryland-specific Open Law corpus metadata helpers."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from itertools import pairwise
from typing import Tuple

from lawvm.open_law.codify import parse_open_law_codify_ops
from lawvm.open_law.local_git import MarylandLocalRepos, maryland_repos_identity_to_jsonable

MARYLAND_SOURCE_REPO = "maryland-dsd/law-xml"
MARYLAND_CODIFIED_REPO = "maryland-dsd/law-xml-codified"

_PUBLICATION_DATE_RE = re.compile(r"^publication/(\d{4}-\d{2}-\d{2})(?:[.-](.*))?$")


@dataclass(frozen=True)
class MarylandPublicationMetadata:
    """Metadata from a Maryland codified publication branch."""

    branch: str
    publication: str
    source_repo: str
    source_commit: str
    platform_version: str
    platform_reproducible: bool
    build_date: str
    codified_date: str
    included_editorial_actions: Tuple[str, ...]


@dataclass(frozen=True)
class MarylandInventory:
    """Inventory summary for public Maryland Open Law repositories."""

    publication_branches: Tuple[MarylandPublicationMetadata, ...]
    source_editorial_actions: Tuple[str, ...]
    operation_counts: Tuple[Tuple[str, int], ...]


@dataclass(frozen=True)
class MarylandTransitionPlan:
    """One conservative before/after publication transition."""

    before_branch: str
    after_branch: str
    new_editorial_actions: Tuple[str, ...]


def build_maryland_inventory(repos: MarylandLocalRepos) -> MarylandInventory:
    """Build a public Maryland Open Law corpus inventory."""

    branches = tuple(branch for branch in repos.codified.list_branches() if branch.startswith("publication/"))
    publication_branches = tuple(
        sorted(
            (parse_publication_index(repos.codified.read_text(branch, "index.xml"), branch=branch) for branch in branches),
            key=lambda item: _publication_sort_key(item.branch),
        )
    )
    source_tree = repos.source.list_tree("main")
    editorial_actions = tuple(
        sorted(entry.path for entry in source_tree if entry.type == "blob" and entry.path.startswith("editorial-actions/") and entry.path.endswith(".xml"))
    )
    counts: dict[str, int] = {}
    for path in editorial_actions:
        xml_text = repos.source.read_text("main", path)
        for op in parse_open_law_codify_ops(xml_text, source_id=path):
            counts[op.action.value] = counts.get(op.action.value, 0) + 1
    return MarylandInventory(
        publication_branches=publication_branches,
        source_editorial_actions=editorial_actions,
        operation_counts=tuple(sorted(counts.items())),
    )


def plan_maryland_publication_transitions(inventory: MarylandInventory) -> Tuple[MarylandTransitionPlan, ...]:
    """Plan durable publication transitions from codified branch metadata.

    Unsuffixed ``publication/YYYY-MM-DD`` refs can be rolling/current views.
    When a suffixed snapshot exists for the same base publication, use the
    suffixed refs for corpus replay and skip the rolling ref.
    """

    branches_by_publication: dict[str, list[MarylandPublicationMetadata]] = {}
    for item in inventory.publication_branches:
        publication = item.publication or _publication_base(item.branch)
        branches_by_publication.setdefault(publication, []).append(item)

    candidates: list[MarylandPublicationMetadata] = []
    for item in inventory.publication_branches:
        publication = item.publication or _publication_base(item.branch)
        siblings = branches_by_publication[publication]
        sibling_has_snapshot = any(_publication_suffix(sibling.branch) for sibling in siblings)
        if sibling_has_snapshot and not _publication_suffix(item.branch):
            continue
        candidates.append(item)

    transitions: list[MarylandTransitionPlan] = []
    for before, after in pairwise(candidates):
        before_actions = set(before.included_editorial_actions)
        after_actions = set(after.included_editorial_actions)
        if not before_actions.issubset(after_actions):
            continue
        new_actions = tuple(path for path in after.included_editorial_actions if path not in before_actions)
        if not new_actions:
            continue
        transitions.append(
            MarylandTransitionPlan(
                before_branch=before.branch,
                after_branch=after.branch,
                new_editorial_actions=new_actions,
            )
        )
    return tuple(transitions)


def parse_publication_index(xml_text: str, *, branch: str) -> MarylandPublicationMetadata:
    """Parse `index.xml` metadata from one codified publication branch."""

    root = ET.fromstring(xml_text)
    source_repo = ""
    source_commit = ""
    for element in root.iter():
        if _local_name(element.tag) == "repository" and element.attrib.get("name") == MARYLAND_SOURCE_REPO:
            source_repo = element.attrib.get("name", "")
            source_commit = element.attrib.get("commit", "")
            break
    platform_version = ""
    platform_reproducible = False
    for element in root.iter():
        if _local_name(element.tag) == "platform":
            platform_version = element.attrib.get("version", "")
            platform_reproducible = element.attrib.get("reproducible", "").lower() == "true"
            break
    included_actions = tuple(
        sorted(
            href.removeprefix("./")
            for element in root.iter()
            if _local_name(element.tag) == "include"
            for href in (element.attrib.get("href", ""),)
            if href.startswith("./editorial-actions/") and href.endswith(".xml")
        )
    )
    return MarylandPublicationMetadata(
        branch=branch,
        publication=_first_text(root, "publication"),
        source_repo=source_repo,
        source_commit=source_commit,
        platform_version=platform_version,
        platform_reproducible=platform_reproducible,
        build_date=_first_text(root, "build-date"),
        codified_date=_first_text(root, "codified-date"),
        included_editorial_actions=included_actions,
    )


def metadata_to_jsonable(item: MarylandPublicationMetadata) -> dict[str, object]:
    return {
        "branch": item.branch,
        "publication": item.publication,
        "source_repo": item.source_repo,
        "source_commit": item.source_commit,
        "platform_version": item.platform_version,
        "platform_reproducible": item.platform_reproducible,
        "build_date": item.build_date,
        "codified_date": item.codified_date,
        "included_editorial_actions": list(item.included_editorial_actions),
    }


def inventory_to_jsonable(inventory: MarylandInventory) -> dict[str, object]:
    return {
        "publication_branches": [metadata_to_jsonable(item) for item in inventory.publication_branches],
        "source_editorial_actions": list(inventory.source_editorial_actions),
        "operation_counts": dict(inventory.operation_counts),
    }


def maryland_manifest_to_jsonable(inventory: MarylandInventory, *, repos: MarylandLocalRepos) -> dict[str, object]:
    manifest = inventory_to_jsonable(inventory)
    manifest["local_repositories"] = maryland_repos_identity_to_jsonable(repos)
    return manifest


def _publication_sort_key(branch: str) -> tuple[str, str]:
    match = _PUBLICATION_DATE_RE.match(branch)
    if match is None:
        return branch, ""
    return match.group(1), match.group(2) or ""


def _publication_base(branch: str) -> str:
    match = _PUBLICATION_DATE_RE.match(branch)
    if match is None:
        return branch
    return f"publication/{match.group(1)}"


def _publication_suffix(branch: str) -> str:
    match = _PUBLICATION_DATE_RE.match(branch)
    if match is None:
        return ""
    return match.group(2) or ""


def _first_text(root: ET.Element, local_name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) == local_name:
            return " ".join("".join(element.itertext()).split())
    return ""


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag
