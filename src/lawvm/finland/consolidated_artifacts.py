"""Shared helpers for Finland consolidated-oracle artifact identity.

These helpers separate:
- locator claim: the finlex:// path we fetched or imported
- embedded identity: what the XML payload says via FRBR metadata

Callers should use the embedded identity to derive canonical versioned aliases,
while retaining the original locator when it is useful as an observation claim.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import re
from enum import Enum
from typing import Iterable
from typing import Optional

from lxml import etree

from lawvm.finland.helpers import _parse_iso_date


_CONSOLIDATED_LOCATOR_RE = re.compile(
    r"^finlex://(?P<namespace>sd-cons|sd-cons-old)/(?P<sid>\d{4}/[^/]+)/(?P<lang>[^@/]+)"
    r"(?:@(?P<version>[^/]+))?/(?P<rest>.+)$"
)
_FRBRTHIS_VERSION_RE = re.compile(r"/(?P<lang>[a-z]{3})@(?P<version>\d{8})/")


@dataclass(frozen=True)
class ConsolidatedLocatorParts:
    namespace: str
    sid: str
    lang: str
    version: str
    rest: str


@dataclass(frozen=True)
class ConsolidatedXmlIdentity:
    embedded_frbrthis: str = ""
    embedded_version_tag: str = ""
    date_consolidated: Optional[dt.date] = None


@dataclass(frozen=True)
class ConsolidatedArtifactRecord:
    locator: str
    namespace: str
    sid: str
    lang: str
    path_version: str
    embedded_version_tag: str
    date_consolidated: Optional[dt.date]


class ConsolidatedSelectionMode(str, Enum):
    """Explicit selector modes for consolidated-oracle comparison use."""

    EXACT_EMBEDDED_VERSION = "exact_embedded_version"
    LATEST_CACHED_EDITORIAL = "latest_cached_editorial"
    BENCH_COMPARABLE = "bench_comparable"
    DATE_CONSOLIDATED_AT_OR_BEFORE = "date_consolidated_at_or_before"


@dataclass(frozen=True)
class ConsolidatedArtifactSelector:
    """Typed selector for cached consolidated artifacts.

    The selector uses payload identity fields only:
    - embedded_version_tag for amendment/version identity
    - date_consolidated for consolidation cutoffs

    Path suffixes may still be recorded as observations, but they do not
    determine which artifact is authoritative.
    """

    mode: ConsolidatedSelectionMode
    version_tag: str = ""
    date_consolidated: Optional[dt.date] = None

    @classmethod
    def exact_embedded_version(cls, version_tag: str) -> "ConsolidatedArtifactSelector":
        if not version_tag:
            raise ValueError("version_tag is required for exact consolidated selection")
        return cls(
            mode=ConsolidatedSelectionMode.EXACT_EMBEDDED_VERSION,
            version_tag=version_tag,
        )

    @classmethod
    def latest_cached_editorial(cls) -> "ConsolidatedArtifactSelector":
        return cls(mode=ConsolidatedSelectionMode.LATEST_CACHED_EDITORIAL)

    @classmethod
    def bench_comparable(cls) -> "ConsolidatedArtifactSelector":
        """Prefer the latest self-comparable cached editorial artifact.

        Bench comparison wants an oracle artifact whose embedded amendment id is
        actually commensurable with that artifact's ``dateConsolidated``. The
        concrete fallback policy lives in the Finland access layer, not in raw
        path ordering.
        """
        return cls(mode=ConsolidatedSelectionMode.BENCH_COMPARABLE)

    @classmethod
    def date_consolidated_at_or_before(
        cls,
        cutoff: dt.date,
    ) -> "ConsolidatedArtifactSelector":
        return cls(
            mode=ConsolidatedSelectionMode.DATE_CONSOLIDATED_AT_OR_BEFORE,
            date_consolidated=cutoff,
        )


def parse_consolidated_locator(locator: str) -> ConsolidatedLocatorParts | None:
    """Return parsed parts for a consolidated finlex:// locator."""
    match = _CONSOLIDATED_LOCATOR_RE.match(locator)
    if match is None:
        return None
    return ConsolidatedLocatorParts(
        namespace=match.group("namespace"),
        sid=match.group("sid"),
        lang=match.group("lang"),
        version=match.group("version") or "",
        rest=match.group("rest"),
    )


def consolidated_family_key(locator: str) -> tuple[str, str, str] | None:
    """Return the source-family key used to keep sibling assets aligned."""
    parts = parse_consolidated_locator(locator)
    if parts is None:
        return None
    return (parts.sid, parts.lang, parts.version)


def _preferred_langs(preferred_lang: str | None) -> tuple[str, ...]:
    ordered: list[str] = []
    if preferred_lang:
        ordered.append(preferred_lang)
    if "fin" not in ordered:
        ordered.append("fin")
    return tuple(ordered)


def extract_consolidated_xml_identity(
    xml_bytes: bytes,
    *,
    preferred_lang: str | None = None,
) -> ConsolidatedXmlIdentity:
    """Extract embedded identity fields from a consolidated main.xml payload."""
    try:
        root = etree.fromstring(xml_bytes)
    except Exception:
        return ConsolidatedXmlIdentity()

    embedded_frbrthis = ""
    embedded_version_tag = ""
    date_consolidated: Optional[dt.date] = None
    preferred_langs = _preferred_langs(preferred_lang)

    frbrthis_candidates: list[tuple[str, str, str]] = []
    for element in root.findall(".//{*}FRBRthis"):
        value = (element.get("value") or "").strip()
        if not value:
            continue
        match = _FRBRTHIS_VERSION_RE.search(value)
        if match is None:
            continue
        frbrthis_candidates.append((match.group("lang"), match.group("version"), value))

    for lang in preferred_langs:
        for candidate_lang, candidate_version, candidate_value in frbrthis_candidates:
            if candidate_lang == lang:
                embedded_frbrthis = candidate_value
                embedded_version_tag = candidate_version
                break
        if embedded_version_tag:
            break

    if not embedded_version_tag and frbrthis_candidates:
        candidate_lang, candidate_version, candidate_value = frbrthis_candidates[0]
        del candidate_lang
        embedded_frbrthis = candidate_value
        embedded_version_tag = candidate_version

    if not embedded_version_tag:
        expression_candidates: list[tuple[str, str]] = []
        for expression in root.findall(".//{*}FRBRExpression"):
            lang_el = expression.find(".//{*}FRBRlanguage")
            version_el = expression.find(".//{*}FRBRversionNumber")
            if lang_el is None or version_el is None:
                continue
            lang = (lang_el.get("language") or "").strip()
            value = (version_el.get("value") or "").strip()
            if not (lang and value.isdigit() and len(value) == 8):
                continue
            expression_candidates.append((lang, value))

        for lang in preferred_langs:
            for candidate_lang, candidate_version in expression_candidates:
                if candidate_lang == lang:
                    embedded_version_tag = candidate_version
                    break
            if embedded_version_tag:
                break

        if not embedded_version_tag and expression_candidates:
            embedded_version_tag = expression_candidates[0][1]

    for element in root.findall(".//{*}FRBRdate"):
        if element.get("name") != "dateConsolidated":
            continue
        date_consolidated = _parse_iso_date(element.get("date"))
        break

    return ConsolidatedXmlIdentity(
        embedded_frbrthis=embedded_frbrthis,
        embedded_version_tag=embedded_version_tag,
        date_consolidated=date_consolidated,
    )


def canonical_consolidated_locator(
    locator: str,
    *,
    version_tag: str,
) -> str:
    """Return the canonical versioned sd-cons locator for a consolidated artifact."""
    parts = parse_consolidated_locator(locator)
    if parts is None:
        raise ValueError(f"not a consolidated finlex locator: {locator}")
    if not version_tag:
        raise ValueError(f"missing consolidated embedded version for locator: {locator}")
    return build_canonical_consolidated_locator(
        sid=parts.sid,
        lang=parts.lang,
        version_tag=version_tag,
        rest=parts.rest,
    )


def build_canonical_consolidated_locator(
    *,
    sid: str,
    lang: str,
    version_tag: str,
    rest: str,
) -> str:
    """Build a canonical versioned sd-cons locator for a consolidated artifact."""
    if not version_tag:
        raise ValueError(f"missing consolidated embedded version for {sid}")
    return f"finlex://sd-cons/{sid}/{lang}@{version_tag}/{rest}"


def build_consolidated_main_locator(
    *,
    sid: str,
    lang: str,
    version_tag: str,
) -> str:
    """Build a canonical versioned consolidated main.xml locator."""
    return build_canonical_consolidated_locator(
        sid=sid,
        lang=lang,
        version_tag=version_tag,
        rest="main.xml",
    )


def build_consolidated_corrigendum_locator(
    *,
    sid: str,
    lang: str,
    version_tag: str,
    filename: str,
) -> str:
    """Build a canonical versioned consolidated corrigendum-media locator."""
    return build_canonical_consolidated_locator(
        sid=sid,
        lang=lang,
        version_tag=version_tag,
        rest=f"media/corrigenda/{filename}",
    )


def build_consolidated_family_glob(
    *,
    namespace: str = "sd-cons",
    sid: str | None = None,
) -> str:
    """Build a glob for one consolidated namespace or statute family."""
    prefix = f"finlex://{namespace}/{sid}" if sid else f"finlex://{namespace}"
    return f"{prefix}/%"


def build_versioned_consolidated_main_glob(
    *,
    namespace: str = "sd-cons",
    sid: str | None = None,
    lang: str = "fin",
) -> str:
    """Build a glob for versioned consolidated main.xml locators."""
    sid_part = sid if sid is not None else "%"
    return f"finlex://{namespace}/{sid_part}/{lang}@%/main.xml"


def build_versioned_consolidated_corrigendum_glob(
    *,
    namespace: str = "sd-cons",
    sid: str,
    lang: str = "fin",
    filename: str,
) -> str:
    """Build a glob for versioned consolidated corrigendum-media locators."""
    return (
        f"finlex://{namespace}/{sid}/{lang}@%/media/corrigenda/{filename}"
    )


def build_missing_consolidated_locator(
    *,
    sid: str,
    lang: str = "fin",
) -> str:
    """Build the negative-cache locator for a missing consolidated artifact."""
    return f"finlex://missing/sd-cons/{sid}/{lang}/main.xml"


def build_consolidated_listing_locator(sid: str) -> str:
    """Build the cache locator for one statute's consolidated PIT listing."""
    year, num = sid.split("/", 1)
    return f"finlex://sd-cons/{year}/{num}/pit-listing"


def parse_versioned_consolidated_main_locator(
    locator: str,
    *,
    namespace: str = "sd-cons",
    lang: str | None = None,
) -> ConsolidatedLocatorParts | None:
    """Parse a canonical versioned consolidated main.xml locator."""
    parts = parse_consolidated_locator(locator)
    if parts is None:
        return None
    if parts.namespace != namespace or parts.rest != "main.xml" or not parts.version:
        return None
    if lang is not None and parts.lang != lang:
        return None
    return parts


def parse_consolidated_corrigendum_locator(
    locator: str,
    *,
    namespace: str = "sd-cons",
    lang: str | None = None,
    filename: str | None = None,
) -> ConsolidatedLocatorParts | None:
    """Parse a canonical consolidated corrigendum-media locator."""
    parts = parse_consolidated_locator(locator)
    if parts is None or parts.namespace != namespace or not parts.version:
        return None
    if lang is not None and parts.lang != lang:
        return None
    if not parts.rest.startswith("media/corrigenda/"):
        return None
    if filename is not None and parts.rest != f"media/corrigenda/{filename}":
        return None
    return parts


def path_version_tag(locator: str) -> str:
    parts = parse_consolidated_locator(locator)
    if parts is None:
        return ""
    return parts.version


def artifact_record(locator: str, xml_bytes: bytes) -> ConsolidatedArtifactRecord:
    parts = parse_consolidated_locator(locator)
    identity = extract_consolidated_xml_identity(
        xml_bytes,
        preferred_lang=parts.lang if parts is not None else None,
    )
    return ConsolidatedArtifactRecord(
        locator=locator,
        namespace=parts.namespace if parts is not None else "",
        sid=parts.sid if parts is not None else "",
        lang=parts.lang if parts is not None else "",
        path_version=parts.version if parts is not None else "",
        embedded_version_tag=identity.embedded_version_tag,
        date_consolidated=identity.date_consolidated,
    )


def _selection_sort_key(
    record: ConsolidatedArtifactRecord,
    selector: ConsolidatedArtifactSelector,
) -> tuple[int, int, int, str]:
    """Return an ordering key for a record under an explicit selector."""
    namespace_score = int(record.namespace == "sd-cons")
    version_score = _version_score(record.embedded_version_tag)
    date_score = _date_score(record.date_consolidated)

    if selector.mode == ConsolidatedSelectionMode.EXACT_EMBEDDED_VERSION:
        return (date_score, namespace_score, version_score, record.locator)

    if selector.mode == ConsolidatedSelectionMode.DATE_CONSOLIDATED_AT_OR_BEFORE:
        return (date_score, version_score, namespace_score, record.locator)

    if selector.mode == ConsolidatedSelectionMode.BENCH_COMPARABLE:
        return (version_score, date_score, namespace_score, record.locator)

    return (version_score, date_score, namespace_score, record.locator)


def record_matches_selector(
    record: ConsolidatedArtifactRecord,
    selector: ConsolidatedArtifactSelector,
) -> bool:
    """Return True when the record is eligible for the explicit selector."""
    if selector.mode == ConsolidatedSelectionMode.EXACT_EMBEDDED_VERSION:
        return record.embedded_version_tag == selector.version_tag
    if selector.mode == ConsolidatedSelectionMode.DATE_CONSOLIDATED_AT_OR_BEFORE:
        return (
            record.date_consolidated is not None
            and selector.date_consolidated is not None
            and record.date_consolidated <= selector.date_consolidated
        )
    if selector.mode == ConsolidatedSelectionMode.BENCH_COMPARABLE:
        return bool(record.embedded_version_tag)
    return bool(record.embedded_version_tag)


def select_consolidated_record(
    records: Iterable[ConsolidatedArtifactRecord],
    selector: ConsolidatedArtifactSelector | None = None,
) -> ConsolidatedArtifactRecord | None:
    """Select one consolidated record by explicit authority rules."""
    selector = selector or ConsolidatedArtifactSelector.latest_cached_editorial()
    best: ConsolidatedArtifactRecord | None = None
    best_key: tuple[int, int, int, str] | None = None
    for record in records:
        if not record_matches_selector(record, selector):
            continue
        key = _selection_sort_key(record, selector)
        if best_key is None or key > best_key:
            best_key = key
            best = record
    return best


def _version_score(version: str) -> int:
    return int(version) if version.isdigit() else -1


def _date_score(date_value: Optional[dt.date]) -> int:
    return date_value.toordinal() if date_value is not None else -1


def consolidated_locator_sort_key(locator: str, xml_bytes: bytes | None) -> tuple[int, int, int, int, int, str]:
    """Sort consolidated artifacts by parsed identity and consolidation metadata."""
    record = artifact_record(locator, xml_bytes or b"")
    namespace_score = int(record.namespace == "sd-cons")
    path_matches_identity = int(
        bool(record.embedded_version_tag)
        and record.path_version == record.embedded_version_tag
    )
    return (
        _version_score(record.embedded_version_tag),
        _date_score(record.date_consolidated),
        namespace_score,
        path_matches_identity,
        _version_score(record.path_version),
        locator,
    )


def preferred_version_tag(locator: str, xml_bytes: bytes | None) -> str:
    if xml_bytes is not None:
        embedded_version = extract_consolidated_xml_identity(xml_bytes).embedded_version_tag
        if embedded_version:
            return embedded_version
    return ""
