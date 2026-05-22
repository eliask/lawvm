"""UK legislation effect-feed records and acquisition helpers."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_LEG_BASE = "https://www.legislation.gov.uk"
_USER_AGENT = "LawVM UK replay/0.1 (+https://github.com/lawvm)"

# Effect types that directly imply textual changes we can extract.
STRUCTURAL_EFFECT_TYPES = frozenset(
    {
        "inserted",
        "entry inserted",
        "words inserted",
        "word inserted",
        "words substituted",
        "substituted for words",
        "word substituted",
        "substituted",
        "words repealed",
        "word repealed",
        "repealed",
        "entry repealed",
        "repealed in part",
        "words omitted",
        "word omitted",
        "omitted",
        "entry omitted",
    }
)

_COMMENCEMENT_EFFECT_TYPES = frozenset(
    {
        "appointed day(s)",
        "coming into force",
        "commencement order",
    }
)


def _is_uk_renumber_effect_type(effect_type: str) -> bool:
    return bool(re.search(r"\brenumbered\s+as\b", str(effect_type or ""), flags=re.I))


def _is_uk_repealed_by_effect_type(effect_type: str) -> bool:
    return str(effect_type or "").strip().lower().startswith("repealed by ")


def uk_nonstructural_replay_candidate_family(
    effect: "UKEffectRecord",
    *,
    applicability_mode: str = "effective_date_plus_feed_applied",
) -> str:
    """Return the nonstructural effect row family that may still replay."""
    if not effect.is_applicable_for_replay(applicability_mode=applicability_mode):
        return ""
    effect_type = (effect.effect_type or "").strip().lower()
    if effect_type.startswith("substituted for"):
        return "substituted_for_series"
    if effect_type.startswith("revoked"):
        return "revoked_repeal"
    if effect_type.startswith("ceases to have effect"):
        return "ceases_to_have_effect_repeal"
    if effect_type == "added":
        return "added_source_structural_insert"
    return ""


def uk_effect_requires_affecting_source_for_replay(
    effect: "UKEffectRecord",
    *,
    applicability_mode: str = "effective_date_plus_feed_applied",
) -> bool:
    """Return True when replay can legitimately need the affecting source XML."""
    return effect.is_structural_for_replay(
        applicability_mode=applicability_mode
    ) or bool(
        uk_nonstructural_replay_candidate_family(
            effect,
            applicability_mode=applicability_mode,
        )
    )


@dataclass
class UKEffectRecord:
    """A single structured effect entry from the effects feed."""

    effect_id: str
    effect_type: str
    applied: bool
    requires_applied: bool
    modified: str  # ISO date of last editorial modification

    # Affected (the statute being changed)
    affected_uri: str
    affected_class: str
    affected_year: str
    affected_number: str
    affected_provisions: str  # e.g. "s. 21", "Sch. 1"

    # Affecting (the act making the change)
    affecting_uri: str
    affecting_class: str
    affecting_year: str
    affecting_number: str
    affecting_provisions: str  # e.g. "Sch. 2 para. 2(2)"
    affecting_title: str

    in_force_dates: list[dict[str, Any]] = field(default_factory=list)
    metadata_only: bool = False  # True if this effect was only found in XML metadata, not the Atom feed.
    comments: str = ""
    affected_title: str = ""

    @property
    def affecting_act_id(self) -> str:
        """Canonical web path for the affecting act, e.g. 'ukpga/2023/28'."""
        cls = self.affecting_class
        cls_map = {
            "UnitedKingdomPublicGeneralAct": "ukpga",
            "UnitedKingdomStatutoryInstrument": "uksi",
            "WelshParliamentAct": "asc",
            "WelshStatutoryInstrument": "wsi",
            "ScottishAct": "asp",
            "ScottishStatutoryInstrument": "ssi",
            "NorthernIrelandAssemblyMeasure": "mnia",
            "NorthernIrelandParliamentAct": "apni",
            "NorthernIrelandStatutoryRule": "nisr",
            "UnitedKingdomChurchInstrument": "ukci",
            "UnitedKingdomMinisterialOrder": "ukmo",
            "EuropeanUnionRegulation": "eur",
            "EuropeanUnionDecision": "eudn",
            "EuropeanUnionDirective": "eudr",
        }
        slug = cls_map.get(cls, cls.lower())
        return f"{slug}/{self.affecting_year}/{self.affecting_number}"

    @property
    def effective_date(self) -> str:
        """Return the best non-empty, non-prospective in-force date, or '' if none."""
        real: str = ""
        any_date: str = ""
        for d in self.in_force_dates:
            dt = d.get("date", "")
            if not dt:
                continue
            if not any_date:
                any_date = dt
            if d.get("prospective", "false").lower() != "true":
                real = dt
                break
        return real or any_date

    @property
    def is_structural(self) -> bool:
        return (self.applied or self.metadata_only) and (
            self.effect_type in STRUCTURAL_EFFECT_TYPES
            or self.effect_type == ""
            or _is_uk_renumber_effect_type(self.effect_type)
            or _is_uk_repealed_by_effect_type(self.effect_type)
        )

    def is_applicable_for_replay(
        self,
        *,
        applicability_mode: str = "effective_date_plus_feed_applied",
    ) -> bool:
        mode = str(applicability_mode or "effective_date_plus_feed_applied")
        if mode == "effective_date_only":
            return True
        if mode == "effective_date_plus_requires_applied":
            return bool(self.applied) or not bool(self.requires_applied) or bool(self.metadata_only)
        return bool(self.applied) or bool(self.metadata_only)

    def is_structural_for_replay(
        self,
        *,
        applicability_mode: str = "effective_date_plus_feed_applied",
    ) -> bool:
        if (
            self.effect_type not in STRUCTURAL_EFFECT_TYPES
            and self.effect_type != ""
            and not _is_uk_renumber_effect_type(self.effect_type)
            and not _is_uk_repealed_by_effect_type(self.effect_type)
        ):
            return False
        return self.is_applicable_for_replay(applicability_mode=applicability_mode)

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "effect_type": self.effect_type,
            "applied": self.applied,
            "requires_applied": self.requires_applied,
            "metadata_only": self.metadata_only,
            "replay_applicable": self.is_applicable_for_replay(),
            "structural": self.is_structural,
            "structural_for_replay": self.is_structural_for_replay(),
            "affected_provisions": self.affected_provisions,
            "affected_title": self.affected_title,
            "affecting_act_id": self.affecting_act_id,
            "affecting_provisions": self.affecting_provisions,
            "affecting_title": self.affecting_title,
            "modified": self.modified,
            "in_force_date": self.effective_date,
            "in_force_dates": self.in_force_dates,
        }


def parse_effects_from_feeds(
    feed_files: list[Path],
    *,
    parse_rejections_out: Optional[list[dict[str, Any]]] = None,
) -> list[UKEffectRecord]:
    """Parse all effect feed pages into a list of UKEffectRecord."""
    if parse_rejections_out is not None:
        feed_bytes_list: list[bytes] = []
        feed_locators: list[str] = []
        for feed_index, ff in enumerate(feed_files):
            if not ff.exists():
                parse_rejections_out.append(
                    {
                        "rule_id": "uk_effect_feed_file_missing_rejected",
                        "family": "source_pathology",
                        "phase": "acquisition",
                        "feed_index": feed_index,
                        "feed_path": str(ff),
                        "reason": "UK local effect feed file was listed but missing on disk.",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    }
                )
                continue
            feed_bytes_list.append(ff.read_bytes())
            feed_locators.append(str(ff))
        return parse_effects_from_bytes(
            feed_bytes_list,
            parse_rejections_out=parse_rejections_out,
            feed_locators=feed_locators,
        )

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "ukm": "http://www.legislation.gov.uk/namespaces/metadata",
    }
    records = []
    for ff in feed_files:
        root = ET.parse(ff).getroot()
        for entry in root.findall("atom:entry", ns):
            effect = entry.find(".//ukm:Effect", ns)
            if effect is None:
                continue
            in_force_dates = []
            for inf in effect.findall(".//ukm:InForceDates/ukm:InForce", ns):
                in_force_dates.append(
                    {
                        "date": inf.get("Date", ""),
                        "applied": inf.get("Applied", ""),
                        "prospective": inf.get("Prospective", "false"),
                    }
                )
            records.append(
                UKEffectRecord(
                    effect_id=effect.get("EffectId", ""),
                    effect_type=effect.get("Type", ""),
                    applied=(effect.get("Applied", "false").lower() == "true"),
                    requires_applied=(effect.get("RequiresApplied", "false").lower() == "true"),
                    modified=effect.get("Modified", "")[:10],
                    affected_uri=effect.get("AffectedURI", ""),
                    affected_class=effect.get("AffectedClass", ""),
                    affected_year=effect.get("AffectedYear", ""),
                    affected_number=effect.get("AffectedNumber", ""),
                    affected_provisions=effect.get("AffectedProvisions", ""),
                    affecting_uri=effect.get("AffectingURI", ""),
                    affecting_class=effect.get("AffectingClass", ""),
                    affecting_year=effect.get("AffectingYear", ""),
                    affecting_number=effect.get("AffectingNumber", ""),
                    affecting_provisions=effect.get("AffectingProvisions", ""),
                    affecting_title=effect.findtext("ukm:AffectingTitle", default="", namespaces=ns),
                    in_force_dates=in_force_dates,
                    affected_title=effect.findtext("ukm:AffectedTitle", default="", namespaces=ns),
                )
            )
    return records


def parse_effects_from_bytes(
    feed_bytes_list: list[bytes],
    *,
    parse_rejections_out: Optional[list[dict[str, Any]]] = None,
    feed_locators: Optional[list[str]] = None,
) -> list[UKEffectRecord]:
    """Parse effect feed pages from raw bytes into a list of UKEffectRecord."""
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "ukm": "http://www.legislation.gov.uk/namespaces/metadata",
    }
    records = []
    for feed_index, raw in enumerate(feed_bytes_list):
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            if parse_rejections_out is not None:
                rejection: dict[str, Any] = {
                    "rule_id": "uk_effect_feed_xml_parse_rejected",
                    "family": "source_pathology",
                    "phase": "parse",
                    "feed_index": feed_index,
                    "reason": "UK effect feed page is not well-formed XML.",
                    "parse_error": str(exc),
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                }
                if feed_locators is not None and feed_index < len(feed_locators):
                    rejection["feed_locator"] = feed_locators[feed_index]
                parse_rejections_out.append(rejection)
            continue
        for entry_index, entry in enumerate(root.findall("atom:entry", ns)):
            effect = entry.find(".//ukm:Effect", ns)
            if effect is None:
                if parse_rejections_out is not None:
                    rejection = {
                        "rule_id": "uk_effect_feed_entry_missing_effect_rejected",
                        "family": "source_pathology",
                        "phase": "parse",
                        "feed_index": feed_index,
                        "entry_index": entry_index,
                        "entry_id": entry.findtext("atom:id", default="", namespaces=ns),
                        "entry_title": entry.findtext("atom:title", default="", namespaces=ns),
                        "reason": "UK effect feed entry did not contain a ukm:Effect payload.",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    }
                    if feed_locators is not None and feed_index < len(feed_locators):
                        rejection["feed_locator"] = feed_locators[feed_index]
                    parse_rejections_out.append(rejection)
                continue
            in_force_dates = []
            for inf in effect.findall(".//ukm:InForceDates/ukm:InForce", ns):
                in_force_dates.append(
                    {
                        "date": inf.get("Date", ""),
                        "applied": inf.get("Applied", ""),
                        "prospective": inf.get("Prospective", "false"),
                    }
                )
            records.append(
                UKEffectRecord(
                    effect_id=effect.get("EffectId", ""),
                    effect_type=effect.get("Type", ""),
                    applied=(effect.get("Applied", "false").lower() == "true"),
                    requires_applied=(effect.get("RequiresApplied", "false").lower() == "true"),
                    modified=effect.get("Modified", "")[:10],
                    affected_uri=effect.get("AffectedURI", ""),
                    affected_class=effect.get("AffectedClass", ""),
                    affected_year=effect.get("AffectedYear", ""),
                    affected_number=effect.get("AffectedNumber", ""),
                    affected_provisions=effect.get("AffectedProvisions", ""),
                    affecting_uri=effect.get("AffectingURI", ""),
                    affecting_class=effect.get("AffectingClass", ""),
                    affecting_year=effect.get("AffectingYear", ""),
                    affecting_number=effect.get("AffectingNumber", ""),
                    affecting_provisions=effect.get("AffectingProvisions", ""),
                    affecting_title=effect.findtext("ukm:AffectingTitle", default="", namespaces=ns),
                    in_force_dates=in_force_dates,
                    affected_title=effect.findtext("ukm:AffectedTitle", default="", namespaces=ns),
                )
            )
    return records


def load_effects_for_statute_from_archive(
    statute_id: str,
    archive: Any,
    *,
    parse_rejections_out: Optional[list[dict[str, Any]]] = None,
) -> list[UKEffectRecord]:
    """Load effects for a statute from a Farchive."""
    pattern = f"%/changes/affected/{statute_id}/%"
    rows = archive._conn.execute(
        "SELECT DISTINCT locator FROM locator_span WHERE locator LIKE ?",
        (pattern,),
    ).fetchall()

    if not rows and parse_rejections_out is not None:
        parse_rejections_out.append(
            {
                "rule_id": "uk_effect_feed_pages_absent_recorded",
                "family": "source_pathology",
                "phase": "acquisition",
                "statute_id": statute_id,
                "feed_pattern": pattern,
                "reason": "No UK effect feed page locators were present in the archive for this statute.",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            }
        )

    feed_bytes_list: list[bytes] = []
    feed_locators: list[str] = []
    for (url,) in rows:
        data = archive.get(url)
        if data:
            feed_bytes_list.append(data)
            feed_locators.append(url)
            continue
        if parse_rejections_out is not None:
            parse_rejections_out.append(
                {
                    "rule_id": "uk_effect_feed_locator_payload_missing_rejected",
                    "family": "source_pathology",
                    "phase": "acquisition",
                    "statute_id": statute_id,
                    "feed_locator": url,
                    "reason": "UK effect feed locator was indexed but payload bytes were missing from the archive.",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                }
            )

    return parse_effects_from_bytes(
        feed_bytes_list,
        parse_rejections_out=parse_rejections_out,
        feed_locators=feed_locators,
    )


def get_affecting_act_xml_from_archive(
    act_id: str,
    archive: Any,
) -> Optional[bytes]:
    """Fetch affecting act XML bytes from archive."""
    url = f"{_LEG_BASE}/{act_id}/data.xml"
    return archive.get(url)


def get_affecting_act_enacted_xml_from_archive(
    act_id: str,
    archive: Any,
) -> Optional[bytes]:
    """Fetch enacted affecting act XML bytes from archive."""
    url = f"{_LEG_BASE}/{act_id}/enacted/data.xml"
    archive_get = getattr(archive, "get", None)
    if not callable(archive_get):
        return None
    return archive_get(url)


def parse_effects_from_metadata(
    xml_path: Path,
    *,
    parse_rejections_out: Optional[list[dict[str, Any]]] = None,
    statute_id: str = "",
) -> list[UKEffectRecord]:
    """Parse effects from the <ukm:UnappliedEffects> section of legislation XML."""
    records = []
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        if parse_rejections_out is not None:
            rejection: dict[str, Any] = {
                "rule_id": "uk_metadata_xml_parse_failed_rejected",
                "family": "source_pathology",
                "phase": "parse",
                "metadata_path": str(xml_path),
                "reason": "UK legislation metadata XML was not well-formed; metadata-only effects were not parsed.",
                "exception_type": type(exc).__name__,
                "parse_error": str(exc),
                "blocking": True,
                "strict_disposition": "block",
                "quirks_disposition": "record",
            }
            if statute_id:
                rejection["statute_id"] = statute_id
            parse_rejections_out.append(rejection)
        return []

    for effect in root.findall(".//{*}UnappliedEffect"):
        in_force_dates = []
        for inf in effect.findall(".//{*}InForce"):
            in_force_dates.append(
                {
                    "date": inf.get("Date", ""),
                    "applied": inf.get("Applied", "false").lower() == "true",
                    "prospective": inf.get("Prospective", "false").lower() == "true",
                }
            )

        prov_parts = []
        for prov in effect.findall(".//{*}AffectedProvisions/{*}Section"):
            prov_parts.append(prov.get("Ref", ""))
        prov_str = ", ".join(prov_parts)

        records.append(
            UKEffectRecord(
                effect_id=effect.get("EffectId", ""),
                effect_type=effect.get("Type", ""),
                applied=(effect.get("Applied", "false").lower() == "true"),
                requires_applied=(effect.get("RequiresApplied", "false").lower() == "true"),
                modified=effect.get("Modified", "")[:10],
                affected_uri=effect.get("AffectedURI", ""),
                affected_class=effect.get("AffectedClass", ""),
                affected_year=effect.get("AffectedYear", ""),
                affected_number=effect.get("AffectedNumber", ""),
                affected_provisions=prov_str,
                affecting_uri=effect.get("AffectingURI", ""),
                affecting_class=effect.get("AffectingClass", ""),
                affecting_year=effect.get("AffectingYear", ""),
                affecting_number=effect.get("AffectingNumber", ""),
                affecting_provisions="",
                affecting_title=effect.findtext("{*}AffectingTitle") or "",
                in_force_dates=in_force_dates,
                metadata_only=True,
                comments=effect.get("Comments", ""),
                affected_title=effect.findtext("{*}AffectedTitle") or "",
            )
        )
    return records


def fetch_effects_for_statute(statute_id: str, dest_dir: Path) -> int:
    """Fetch all pages of an effects feed for a given statute."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = dest_dir / "pages"
    pages_dir.mkdir(exist_ok=True)

    base_url = f"{_LEG_BASE}/changes/affected/{statute_id}/data.feed?results-count=50&sort=modified"
    p1_file = dest_dir / "data.feed"

    print(f"Fetching page 1: {base_url}")
    _download_file(base_url, p1_file)

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "leg": "http://www.legislation.gov.uk/namespaces/legislation",
    }
    try:
        root = ET.parse(p1_file).getroot()
    except ET.ParseError as e:
        print(f"Warning: could not parse effects feed XML: {e}")
        return 1
    try:
        total_pages_el = root.find(".//leg:totalPages", ns)
        if total_pages_el is None or not total_pages_el.text:
            return 1
        total_pages = int(total_pages_el.text)
    except (ValueError, TypeError) as e:
        print(f"Warning: could not parse total pages: {e}")
        return 1

    if total_pages <= 1:
        return 1

    print(f"Found {total_pages} pages in total.")
    for p in range(2, total_pages + 1):
        url = f"{base_url}&page={p}"
        dest = pages_dir / f"page-{p}.feed"
        print(f"Fetching page {p}/{total_pages}: {url}")
        _download_file(url, dest)

    return total_pages


def fetch_metadata_for_statute(statute_id: str, dest_file: Path) -> None:
    """Fetch the current XML for a statute to acquire UnappliedEffects metadata."""
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    url = f"{_LEG_BASE}/{statute_id}/data.xml"
    print(f"Fetching metadata XML: {url}")
    _download_file(url, dest_file)


def load_effects_for_statute(
    statute_id: str,
    base_dir: Path,
    *,
    parse_rejections_out: Optional[list[dict[str, Any]]] = None,
) -> list[UKEffectRecord]:
    """Load effects from both Atom feed and XML metadata, then merge them."""
    stat_dir = base_dir / statute_id
    pages_dir = stat_dir / "pages"

    feed_files = list(pages_dir.glob("*.feed"))
    if (stat_dir / "data.feed").exists():
        feed_files.append(stat_dir / "data.feed")

    atom_effects = parse_effects_from_feeds(
        feed_files,
        parse_rejections_out=parse_rejections_out,
    )

    meta_file = stat_dir / "metadata.xml"
    if not meta_file.exists():
        alt_meta = stat_dir / "current" / "data.xml"
        if alt_meta.exists():
            meta_file = alt_meta

    meta_effects = []
    if meta_file.exists():
        meta_effects = parse_effects_from_metadata(
            meta_file,
            parse_rejections_out=parse_rejections_out,
            statute_id=statute_id,
        )

    seen_ids = {e.effect_id for e in atom_effects if e.effect_id}
    merged = list(atom_effects)

    backfilled = 0
    for me in meta_effects:
        if me.effect_id not in seen_ids:
            merged.append(me)
            backfilled += 1

    if backfilled > 0:
        print(f"Backfilled {backfilled} effects from XML metadata for {statute_id}.")

    return merged


def _download_file(url: str, dest: Path) -> None:
    """Download a file with User-Agent header."""
    req = Request(url)
    req.add_header("User-Agent", _USER_AGENT)
    try:
        with urlopen(req) as response:
            with open(dest, "wb") as f:
                f.write(response.read())
    except HTTPError as e:
        print(f"HTTP Error {e.code}: {url}")
        raise
    except URLError as e:
        print(f"URL Error: {e.reason}")
        raise


def load_effects_for_statute_from_raw(raw_dir: Path) -> list[UKEffectRecord]:
    """Load all effects for a statute from its effects data directory."""
    feed_files = [raw_dir / "data.feed"]
    pages_dir = raw_dir / "pages"
    if pages_dir.exists():
        feed_files += sorted(pages_dir.glob("*.feed"))
    return parse_effects_from_feeds([f for f in feed_files if f.exists()])


def build_acquisition_manifest(
    effects: list[UKEffectRecord],
    repo_root: Path,
) -> dict[str, Any]:
    """Build a JSON manifest of affecting act URLs to fetch for replay."""
    structural = [e for e in effects if e.is_structural]

    acts_seen: dict[str, dict[str, Any]] = {}
    for e in structural:
        act_id = e.affecting_act_id
        if act_id not in acts_seen:
            acts_seen[act_id] = {
                "act_id": act_id,
                "class": e.affecting_class,
                "year": e.affecting_year,
                "number": e.affecting_number,
                "title": e.affecting_title,
                "effect_count": 0,
                "effects": [],
            }
        acts_seen[act_id]["effect_count"] += 1
        acts_seen[act_id]["effects"].append(e.to_dict())

    sources = []
    for act_id, info in sorted(acts_seen.items()):
        rel_path = f"uk/data/raw/affecting_acts/{act_id.replace('/', '_')}/data.xml"
        dest = repo_root / rel_path
        url = f"{_LEG_BASE}/{act_id}/data.xml"
        sources.append(
            {
                "label": info["title"] or act_id,
                "act_id": act_id,
                "effect_count": info["effect_count"],
                "effects": info["effects"],
                "artifacts": [
                    {
                        "url": url,
                        "path": rel_path,
                    }
                ],
                "already_fetched": dest.exists(),
            }
        )

    return {
        "kind": "uk_affecting_acts_manifest",
        "total_structural_effects": len(structural),
        "affecting_acts": len(sources),
        "sources": [s for s in sources if not s["already_fetched"]],
        "_all_sources": sources,
    }
