"""eu_acquire.py — EU CELEX acquisition lane (Cellar → content-addressed farchive).

``cellar.py`` already FETCHES authentic EUR-Lex bytes (FRBR content negotiation,
NOTICE → Expression(by language) → Manifestation(by format) → Item, multilingual
selection). It only dumped loose files. This module wires that fetch into the
content-addressed witness store, mirroring the battle-tested FI HE
(:mod:`lawvm.finland.he_acquisition`) and UK (:mod:`lawvm.uk_legislation.uk_acquire`)
acquisition lanes.

Stores RAW WITNESS BYTES (the tree notice + the selected Formex item), not parsed
IR.

Per-jurisdiction acquisition convention
----------------------------------------
Farchive name: ``data/eu_cellar.farchive`` (isolated from FI/UK archives).

Locator convention (keyed on identity, NOT a filesystem path)::

    cellar://celex/{CELEX}/{consolidation_date}/{lang}/{format}

e.g. ``cellar://celex/32016R0679/20160504/fin/fmx4`` for consolidated FI GDPR
Formex, and ``cellar://celex/32016R0679/20160504/fin/notice`` for the FRBR tree
notice that selected it. The Work identity is ``celex:{CELEX}`` — minted through
:mod:`lawvm.eu_lex.celex` so the EU side and the FI reference frontier provably
agree; NEVER a Finland- or language-specific work id. ``language`` is the
Expression; ``consolidation_date`` + ``format`` complete the manifestation key.

Universe honesty
----------------
Each acquisition records a :class:`~lawvm.substrate.corpus_totality.CorpusTotalityUniverse`
with ``closed_world_claim=false``: citation-closure / demand mode is the only
honest claim without an external regulation enumeration. The universe is a
parameter so an enumeration-driven mode can set a stronger claim later.

AGENTS.md compliance
--------------------
* No silent target hijacking / no source-lane disappearance: a blob that is not
  real XML (HTTP error page / empty / bot-block) is rejected with a typed
  :class:`CelexAcquisitionFailure`, never silently stored.
* Typed primitives, no stringly-typed dicts crossing phase boundaries:
  :class:`CelexAcquisitionMetadata` carries the FRBR provenance.
* Determinism: no ``datetime.now()`` inside pure logic — the fetch timestamp is
  passed in by the caller / CLI.

Phase: Acquire. Out of scope: FMX4 → IR extraction (``cellar.extract_fmx4_structure``
and downstream parsing handle that off the stored witness).
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from lawvm.eu import cellar
from lawvm.eu_lex.celex import celex_to_canonical_id, is_well_formed_celex
from lawvm.substrate.corpus_totality import CorpusTotalityUniverse

_DEFAULT_FARCHIVE = "data/eu_cellar.farchive"

# Locator scheme prefix. Identity-keyed (Work=CELEX, Expression=language,
# Manifestation=consolidation_date+format), NOT a filesystem path.
_LOCATOR_SCHEME = "cellar://celex"

# The format slug used for the FRBR tree notice witness (alongside fmx4/xhtml
# manifestation slugs). The notice is the metadata witness that selected the item.
_NOTICE_FORMAT_SLUG = "notice"


# ---------------------------------------------------------------------------
# Typed primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CelexAcquisitionMetadata:
    """Typed FRBR provenance for one acquired CELEX manifestation.

    Stored alongside the witness blob in the farchive metadata table. Mirrors
    :class:`lawvm.finland.he_acquisition.HEAcquisitionMetadata`.
    """

    celex: str
    """Raw CELEX, e.g. '32016R0679'."""
    work_canonical_id: str
    """Language-neutral Work id: 'celex:{CELEX}' (via eu_lex.celex)."""
    work_uri: str
    """FRBR Work URI from the notice, e.g. the cellar/celex resource URI."""
    expression_language: str
    """Expression language code, e.g. 'fin' / 'FIN'."""
    manifestation_uri: str
    """FRBR Manifestation URI (the selected format manifestation)."""
    item_uri: str
    """The concrete Item URL fetched (the bytes' provenance)."""
    fmt: str
    """Manifestation format slug, e.g. 'fmx4'."""
    consolidation_date: str
    """Consolidation date 'YYYYMMDD', or 'enacted' for the non-consolidated act."""
    fetched_at: datetime
    """Caller-supplied fetch timestamp (UTC). Never datetime.now() in logic."""
    source_sha256: str
    """sha256 of the witness bytes (content address)."""
    eli: str = ""
    """ELI id from the notice, if present."""
    corrigendum_celexes: tuple[str, ...] = ()
    """Folded corrigendum/amendment CELEX ids extractable from the notice."""
    corrigenda_extracted: bool = False
    """True iff corrigendum relations were looked for in a notice; honesty flag."""

    def to_metadata_dict(self) -> dict[str, str]:
        """Flat string dict for the farchive metadata table."""
        return {
            "celex": self.celex,
            "work_canonical_id": self.work_canonical_id,
            "work_uri": self.work_uri,
            "expression_language": self.expression_language,
            "manifestation_uri": self.manifestation_uri,
            "item_uri": self.item_uri,
            "format": self.fmt,
            "consolidation_date": self.consolidation_date,
            "fetched_at": self.fetched_at.isoformat(),
            "source_sha256": self.source_sha256,
            "eli": self.eli,
            "corrigendum_celexes": ",".join(self.corrigendum_celexes),
            "corrigenda_extracted": "true" if self.corrigenda_extracted else "false",
            "source_surface": "eu-cellar-frbr",
        }


@dataclass(frozen=True, slots=True)
class CelexAcquisitionFailure:
    """Typed record for a CELEX acquisition failure. Never silently dropped.

    Mirrors :class:`lawvm.finland.he_acquisition.HEAcquisitionFailure`.
    """

    rule_id: str
    """Stable rule identifier, e.g. 'EU_ACQ.NOT_XML'."""
    phase: str
    """Pipeline phase: 'acquisition' or 'verify'."""
    family: str
    """AGENTS.md heuristic family tag."""
    celex: str
    expression_language: str | None
    fmt: str | None
    locator: str
    reason: str
    detail: str
    strict_disposition: str
    """'abort' in strict mode, 'record' in quirks mode."""


@dataclass(frozen=True, slots=True)
class CelexAcquisitionNote:
    """Typed non-failure accounting note for one CELEX acquisition step.

    Distinct from :class:`CelexAcquisitionFailure`: a note records something
    that was *handled* (a witness was still stored) but carries residual detail
    that must not be silently dropped — e.g. a ZIP-wrapped manifestation whose
    primary Formex XML was extracted and stored, with any additional archive
    members (annexes / binary attachments) recorded for total-accounting.
    """

    rule_id: str
    """Stable rule identifier, e.g. 'EU_ACQ.ITEM_ZIP_EXTRACTED'."""
    celex: str
    expression_language: str | None
    fmt: str | None
    locator: str
    detail: str


@dataclass
class CelexIngestRun:
    """Provenance and counts for one CELEX acquisition run."""

    celex: str
    consolidation_date: str
    expression_language: str
    fetched_at: datetime
    farchive_path: str
    added: int = 0
    skipped: int = 0
    failed: int = 0
    stored_locators: list[str] = field(default_factory=list)
    failures: list[CelexAcquisitionFailure] = field(default_factory=list)
    notes: list[CelexAcquisitionNote] = field(default_factory=list)
    metadata: CelexAcquisitionMetadata | None = None
    universe: CorpusTotalityUniverse | None = None


# ---------------------------------------------------------------------------
# Locator convention
# ---------------------------------------------------------------------------


def celex_locator(celex: str, consolidation_date: str, language: str, fmt: str) -> str:
    """Canonical content-addressed locator for one CELEX witness.

    Keyed on FRBR identity (Work=CELEX, Expression=language, Manifestation key =
    consolidation_date + format), NOT a filesystem path.

    Example: ``cellar://celex/32016R0679/20160504/fin/fmx4``.
    """
    return f"{_LOCATOR_SCHEME}/{celex}/{consolidation_date}/{language}/{fmt}"


def _ingest_run_locator(celex: str, language: str, fetched_at: datetime) -> str:
    # The ingest run is per (celex, LANGUAGE): each expression-language fetch of
    # a CELEX produces its own run record (own failures/counts/locators). The
    # locator MUST include the language — otherwise the two languages of one
    # CELEX, which the corpus loop fetches under a single second-granularity
    # ``fetched_at``, map to the same snapshot key and the second language
    # collides ("Same-timestamp digest change ...") and is lost as a spurious
    # ACQUIRE_RAISED gap (masking the real NO_MANIFESTATION accounting).
    ts = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    lang = str(language or "").lower() or "unknown"
    return f"_ingest_runs/eu_cellar/{celex}/{lang}/{ts}"


# ---------------------------------------------------------------------------
# Verify-before-store
# ---------------------------------------------------------------------------


def _xml_root_tag(data: bytes) -> str | None:
    """Return the XML root tag, or None if the bytes are not parseable XML."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    tag = root.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return str(tag)


def verify_xml_witness(data: bytes) -> tuple[bool, str]:
    """Verify a witness blob is real XML, not an HTTP error page / empty / bot-block.

    Returns ``(ok, reason)``. ``reason`` is a short diagnostic when ``ok`` is False.
    """
    if not data:
        return False, "empty payload"
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    lower_head = head[:64].lower()
    if lower_head.startswith((b"<!doctype html", b"<html")):
        return False, "HTML page (likely an error page or bot-block), not XML"
    if head[:1] != b"<":
        return False, f"does not begin with '<' (first bytes: {head[:32]!r})"
    if _xml_root_tag(data) is None:
        return False, "bytes are not parseable XML"
    return True, ""


# ---------------------------------------------------------------------------
# Corrigenda / amendment extraction from the notice (best-effort, honest)
# ---------------------------------------------------------------------------

# Relation tags in a tree notice that point at corrigendum / consolidation /
# amendment Works. EUR-Lex names them with the ``RESOURCE_LEGAL_..._RESOURCE_LEGAL``
# verb form (verified on the GDPR tree notice), so the hints match those verbs
# rather than the abbreviated GR.CORRIG / INFO.CONSLEG forms that appear in other
# notice dialects. ``CORRECTED_BY`` is the corrigendum relation; ``AMENDMENT`` /
# ``AMEND`` cover amendment relations; ``CONSLEG`` / ``CONSOLIDATED`` cover
# consolidation; ``CORRIG`` keeps the abbreviated dialect.
_CORRIGENDUM_RELATION_HINTS = (
    "CORRECTED_BY",
    "CORRIG",
    "AMENDMENT",
    "AMENDED_BY",
    "AMENDS",
    "AMEND",
)


def extract_corrigendum_celexes(notice_bytes: bytes) -> tuple[tuple[str, ...], bool]:
    """Extract folded corrigendum/amendment/consolidation CELEX ids from a notice.

    Returns ``(celexes, looked)``. ``looked`` is True iff the notice parsed (we
    did scan it), so an empty tuple then honestly means "scanned, none found"
    rather than "never looked". Best-effort: scans relation elements whose tag
    matches a corrigendum/amendment/consolidation hint and harvests CELEX
    identifiers (including the corrigendum ``...R(NN)`` suffix form, which is not
    a well-formed *act* CELEX but is a legitimate corrigendum id, so the strict
    act-CELEX well-formedness gate is NOT applied here).
    """
    try:
        root = ET.fromstring(notice_bytes)
    except ET.ParseError:
        return (), False

    found: set[str] = set()
    for el in root.iter():
        tag = el.tag
        local = tag.rsplit("}", 1)[-1] if isinstance(tag, str) and "}" in tag else str(tag)
        upper = local.upper()
        if not any(hint in upper for hint in _CORRIGENDUM_RELATION_HINTS):
            continue
        # Harvest CELEX identifiers under this relation element.
        for uri in el.iter("URI"):
            type_el = uri.find("TYPE")
            ident_el = uri.find("IDENTIFIER")
            if type_el is None or ident_el is None:
                continue
            if (type_el.text or "").strip().lower() == "celex":
                ident = (ident_el.text or "").strip()
                # Reject manifestation-keyed ids (a '.' suffix such as
                # '...FIN.fmx4'): those are expression/manifestation identifiers,
                # not the Work-level CELEX of a related act.
                if ident and "." not in ident:
                    found.add(ident)
    return tuple(sorted(found)), True


@dataclass(frozen=True, slots=True)
class CorrigendumResourceRef:
    """A corrigendum Work named by a base act's ``CORRECTED_BY`` relation.

    The ``celex`` is the corrigendum's ``…R(NN)`` id (a legitimate corrigendum
    id but NOT a resolvable act-CELEX — the Cellar ``/celex/`` path 404s on it,
    verified live). The ``cellar_uuid`` is the addressable FRBR Work resource
    (``/cellar/{uuid}``) the byte lane MUST use to fetch the corrigendum's own
    tree notice + Formex item. Both are harvested from the SAME relation
    element's sibling ``<URI>`` children so the pairing is unambiguous.
    """

    celex: str
    cellar_uuid: str


#: A Cellar FRBR resource UUID (36-char hyphenated). The addressable Work id.
#: A witness_only source-plane locator shape (validates an already-acquired
#: identifier from a notice URI cell), NOT a classifier over statute prose — so
#: it is matched inline via :func:`re.fullmatch`, not the classifier wrap.
_CELLAR_UUID_PATTERN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def _is_cellar_uuid(ident: str) -> bool:
    """True iff ``ident`` is a Cellar FRBR resource UUID (source-plane locator)."""
    return re.fullmatch(_CELLAR_UUID_PATTERN, ident) is not None  # lawvm-regex: witness_only shape-validates an already-acquired Cellar resource id from a notice URI cell (source-plane locator census), not a semantic recognizer over statute text


def extract_corrigendum_resources(
    notice_bytes: bytes,
) -> tuple[tuple[CorrigendumResourceRef, ...], bool]:
    """Extract ``(celex, cellar_uuid)`` corrigendum resources from a base notice.

    Returns ``(resources, looked)``. Distinct from
    :func:`extract_corrigendum_celexes` (which harvests bare CELEX strings across
    ALL corrigendum/amendment/consolidation relation hints): this scans ONLY the
    ``…CORRECTED_BY…`` relation elements and pairs each corrigendum's ``celex``
    URI with the SIBLING ``cellar`` UUID URI in the same element — the resolvable
    Work resource the byte lane fetches (the ``R(NN)`` CELEX is not addressable
    via ``/celex/``; the UUID via ``/cellar/`` is). An element missing either the
    celex or the cellar uuid is skipped (no fabricated pairing). ``looked`` is
    True iff the notice parsed.
    """
    try:
        root = ET.fromstring(notice_bytes)
    except ET.ParseError:
        return (), False

    resources: dict[str, CorrigendumResourceRef] = {}
    for el in root.iter():
        tag = el.tag
        local = tag.rsplit("}", 1)[-1] if isinstance(tag, str) and "}" in tag else str(tag)
        if "CORRECTED_BY" not in local.upper():
            continue
        celex = ""
        uuid = ""
        for uri in el.iter("URI"):
            type_el = uri.find("TYPE")
            ident_el = uri.find("IDENTIFIER")
            if type_el is None or ident_el is None:
                continue
            kind = (type_el.text or "").strip().lower()
            ident = (ident_el.text or "").strip()
            if kind == "celex" and ident and "." not in ident:
                celex = ident
            elif kind == "cellar" and _is_cellar_uuid(ident):
                uuid = ident
        # A corrigendum relation without BOTH a celex and a resolvable cellar
        # uuid is not actionable for the byte lane — skip it (honest gap), never
        # fabricate a pairing.
        if celex and uuid:
            resources[celex] = CorrigendumResourceRef(celex=celex, cellar_uuid=uuid)
    return tuple(resources[k] for k in sorted(resources)), True


# ---------------------------------------------------------------------------
# Idempotent store
# ---------------------------------------------------------------------------


def _store_if_new(
    farchive: Any,
    locator: str,
    data: bytes,
    *,
    storage_class: str,
    metadata: dict[str, str],
    observed_at: datetime,
) -> bool:
    """Store only if content digest differs from what is already stored.

    Returns True if a new state was stored, False if the existing head matched
    (in which case the existing state is re-observed, not re-stored). Mirrors
    :func:`lawvm.uk_legislation.uk_acquire._store_if_new`.
    """
    digest = hashlib.sha256(data).hexdigest()
    spans = farchive.history(locator)
    if spans and spans[-1].digest == digest:
        farchive.observe(locator, digest, observed_at=observed_at)
        return False
    farchive.store(
        locator,
        data,
        storage_class=storage_class,
        metadata=metadata,
        observed_at=observed_at,
    )
    return True


def _store_ingest_run(farchive: Any, run: CelexIngestRun) -> None:
    """Store run provenance as a JSON blob (mirror HE ``_store_ingest_run``)."""
    run_data = {
        "celex": run.celex,
        "consolidation_date": run.consolidation_date,
        "expression_language": run.expression_language,
        "fetched_at": run.fetched_at.isoformat(),
        "farchive_path": run.farchive_path,
        "added": run.added,
        "skipped": run.skipped,
        "failed": run.failed,
        "stored_locators": list(run.stored_locators),
        "universe": (
            run.universe.to_canonical_dict() if run.universe is not None else None
        ),
        "failures": [
            {
                "rule_id": f.rule_id,
                "phase": f.phase,
                "family": f.family,
                "celex": f.celex,
                "expression_language": f.expression_language,
                "format": f.fmt,
                "locator": f.locator,
                "reason": f.reason,
                "detail": f.detail,
            }
            for f in run.failures
        ],
        "notes": [
            {
                "rule_id": n.rule_id,
                "celex": n.celex,
                "expression_language": n.expression_language,
                "format": n.fmt,
                "locator": n.locator,
                "detail": n.detail,
            }
            for n in run.notes
        ],
    }
    ingest_locator = _ingest_run_locator(
        run.celex, run.expression_language, run.fetched_at
    )
    # Idempotent within a run: a (celex, language, fetched_at) may be acquired
    # more than once in one corpus pass — e.g. an act reached first via another
    # act's --with-closure DAG (added=1) and again from the primary window, where
    # its content is now present so the second run records added=0/skipped=1. The
    # two run blobs differ, so re-storing at the SAME locator+timestamp would
    # raise "Same-timestamp digest change". The FIRST record (the real
    # acquisition) stands; a later same-second revisit is redundant provenance.
    # Skip it rather than colliding (which upstream mislabels as a spurious GAP
    # and would lose the act).
    try:
        if farchive.history(ingest_locator):
            return
    except Exception:  # pragma: no cover - history errors are non-fatal here
        pass
    farchive.store(
        ingest_locator,
        json.dumps(run_data, ensure_ascii=False).encode("utf-8"),
        storage_class="json",
        metadata={"source_surface": "ingest_run_provenance"},
        observed_at=run.fetched_at,
    )


# ---------------------------------------------------------------------------
# Default universe declaration
# ---------------------------------------------------------------------------


def default_universe() -> CorpusTotalityUniverse:
    """Honest default universe for a demand/citation-closure acquisition.

    ``curated_slice`` (a CELEX requested on demand) with
    ``closed_world_claim=false``: we hold no external regulation enumeration, so
    the only honest claim is "complete for the requested slice", not "all of EU
    law". An enumeration-driven mode supplies a stronger universe via the
    ``universe`` parameter of :func:`acquire_celex`.
    """
    return CorpusTotalityUniverse(
        universe_kind="curated_slice",
        enumeration_source_refs=(),
        enumeration_policy_id="lawvm.enumeration.eu_cellar.demand.v0",
        closed_world_claim=False,
    )


# ---------------------------------------------------------------------------
# Acquisition entry point
# ---------------------------------------------------------------------------


def acquire_celex(
    celex: str,
    *,
    fetched_at: datetime,
    language: str = "fin",
    consolidation: str | None = None,
    fmt: str = "fmx4",
    farchive_path: str | None = None,
    universe: CorpusTotalityUniverse | None = None,
    timeout_s: int = cellar.DEFAULT_TIMEOUT_S,
    farchive: Any = None,
    _fetch_notice: Any = None,
    _fetch_item: Any = None,
) -> CelexIngestRun:
    """Run the EU Cellar acquisition lane for one CELEX.

    Fetches (a) the FRBR tree notice and (b) the selected manifestation item
    (default Formex / fmx4 for ``language``), VERIFIES each is real XML, and
    stores both as separate content-addressed witnesses under their locators.
    Idempotent: a re-run of identical bytes re-observes (does not re-store).

    Parameters
    ----------
    celex:
        Raw CELEX, e.g. ``'32016R0679'``. Must be well-formed (eu_lex.celex).
    fetched_at:
        Caller-supplied fetch timestamp (UTC). NEVER ``datetime.now()`` here.
    language:
        Expression language, ISO 639-3, e.g. ``'fin'``. Used for the notice
        decode language AND the manifestation selection.
    consolidation:
        Consolidation date 'YYYYMMDD' for the manifestation key, or None to use
        ``'enacted'`` (the non-consolidated act).
    fmt:
        Manifestation format slug, e.g. ``'fmx4'`` (Formex) or ``'xhtml'``.
    farchive_path:
        Farchive path. Default: resolved ``eu_cellar.farchive``.
    universe:
        Universe declaration. Default: :func:`default_universe`
        (``curated_slice``, ``closed_world_claim=false``).
    farchive:
        Open ``farchive.Farchive`` to write into. If None, one is opened at
        ``farchive_path`` and closed at the end.
    _fetch_notice / _fetch_item:
        Test seams. ``_fetch_notice(celex, language, timeout_s) -> (bytes, meta)``
        and ``_fetch_item(item_url, timeout_s) -> (bytes, meta)``. When None, the
        live ``cellar`` fetch path is used.

    Returns
    -------
    CelexIngestRun with provenance, counts, stored locators, and typed failures.
    """
    if not is_well_formed_celex(celex):
        raise ValueError(
            f"not a well-formed CELEX id: {celex!r}; refusing to acquire a "
            "non-aligning witness from malformed input"
        )

    consolidation_date = consolidation or "enacted"
    work_canonical_id = celex_to_canonical_id(celex)
    run_universe = universe if universe is not None else default_universe()

    notice_locator = celex_locator(
        celex, consolidation_date, language, _NOTICE_FORMAT_SLUG
    )
    item_locator = celex_locator(celex, consolidation_date, language, fmt)

    run = CelexIngestRun(
        celex=celex,
        consolidation_date=consolidation_date,
        expression_language=language,
        fetched_at=fetched_at,
        farchive_path=farchive_path or _DEFAULT_FARCHIVE,
        universe=run_universe,
    )

    fetch_notice = _fetch_notice or _live_fetch_notice
    fetch_item = _fetch_item or _live_fetch_item

    owns_farchive = farchive is None
    if owns_farchive:
        from farchive import Farchive

        from lawvm.corpus_store import (
            resolve_farchive_path,
            validate_farchive_create_path,
        )

        if farchive_path is None:
            dest_path, _rule = resolve_farchive_path("eu_cellar.farchive")
            # Default-resolved path: apply the data-root check with the
            # explicit-env override channel so LAWVM_FARCHIVE_DB pointing
            # at an out-of-tree target is honoured (operator trust).
            dest_explicit_env: str | None = "LAWVM_FARCHIVE_DB"
        else:
            dest_path = Path(farchive_path)
            # Caller-supplied path (test fixture, ad-hoc ingest): caller is
            # the operator-in-trust. Pass explicit_env=None so the data-root
            # check stays opt-in (Security M2 §4 — backwards-compatible).
            dest_explicit_env = None
        validate_farchive_create_path(
            dest_path, explicit_env=dest_explicit_env
        )
        run.farchive_path = str(dest_path)
        farchive = Farchive(str(dest_path))

    try:
        _acquire_into(
            run,
            farchive=farchive,
            celex=celex,
            language=language,
            consolidation_date=consolidation_date,
            fmt=fmt,
            work_canonical_id=work_canonical_id,
            notice_locator=notice_locator,
            item_locator=item_locator,
            fetched_at=fetched_at,
            timeout_s=timeout_s,
            fetch_notice=fetch_notice,
            fetch_item=fetch_item,
        )
        _store_ingest_run(farchive, run)
    finally:
        if owns_farchive:
            farchive.close()

    return run


def _live_fetch_notice(
    celex: str, language: str, timeout_s: int
) -> tuple[bytes, dict[str, Any]]:
    """Fetch the FRBR tree notice via cellar.py (read-only reuse)."""
    notice = cellar.NoticeRequest(
        celex=celex,
        notice_format="xml",
        notice_type="tree",
        decode_language=language,
    )
    return cellar._request_notice(notice, timeout_s=timeout_s)


def _live_fetch_item(item_url: str, timeout_s: int) -> tuple[bytes, dict[str, Any]]:
    """Fetch a manifestation item URL via cellar.py (read-only reuse)."""
    return cellar._request_url(item_url, timeout_s=timeout_s)


def _record_failure(
    run: CelexIngestRun,
    *,
    rule_id: str,
    phase: str,
    family: str,
    celex: str,
    language: str | None,
    fmt: str | None,
    locator: str,
    reason: str,
    detail: str,
) -> None:
    run.failures.append(
        CelexAcquisitionFailure(
            rule_id=rule_id,
            phase=phase,
            family=family,
            celex=celex,
            expression_language=language,
            fmt=fmt,
            locator=locator,
            reason=reason,
            detail=detail,
            strict_disposition="abort",
        )
    )
    run.failed += 1


def _record_zip_extracted_note(
    run: CelexIngestRun,
    *,
    celex: str,
    language: str | None,
    fmt: str | None,
    locator: str,
    item_url: str,
    extracted: cellar.ExtractedFormexZip,
) -> None:
    """Account a ZIP-wrapped manifestation whose primary Formex XML was stored.

    Records the primary member stored and the OTHER members present (annexes,
    toc/doc wrappers, binary attachments). The additional members are NOT stored
    as separate witnesses in this increment; the note keeps them accounted (the
    split-manifestation/annex-member case is a known EU acquisition-completeness
    item), never silently dropped.
    """
    other = extracted.other_members
    detail = (
        f"unwrapped zip -> stored primary member {extracted.primary_member!r} "
        f"(root {extracted.primary_root_tag}); "
        f"{len(other)} other member(s) present, not stored as separate "
        f"witnesses: {list(other)} url={item_url}"
    )
    run.notes.append(
        CelexAcquisitionNote(
            rule_id="EU_ACQ.ITEM_ZIP_EXTRACTED",
            celex=celex,
            expression_language=language,
            fmt=fmt,
            locator=locator,
            detail=detail,
        )
    )


def _acquire_into(
    run: CelexIngestRun,
    *,
    farchive: Any,
    celex: str,
    language: str,
    consolidation_date: str,
    fmt: str,
    work_canonical_id: str,
    notice_locator: str,
    item_locator: str,
    fetched_at: datetime,
    timeout_s: int,
    fetch_notice: Any,
    fetch_item: Any,
) -> None:
    # --- 1. Fetch + verify + store the FRBR tree notice ---------------------
    try:
        notice_bytes, _notice_meta = fetch_notice(celex, language, timeout_s)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        _record_failure(
            run,
            rule_id="EU_ACQ.NOTICE_FETCH_FAILED",
            phase="acquisition",
            family="transport_cleanup",
            celex=celex,
            language=language,
            fmt=_NOTICE_FORMAT_SLUG,
            locator=notice_locator,
            reason="Cellar tree-notice fetch failed",
            detail=f"{exc.__class__.__name__}: {exc}",
        )
        return

    ok, why = verify_xml_witness(notice_bytes)
    if not ok:
        _record_failure(
            run,
            rule_id="EU_ACQ.NOTICE_NOT_XML",
            phase="verify",
            family="source_pathology",
            celex=celex,
            language=language,
            fmt=_NOTICE_FORMAT_SLUG,
            locator=notice_locator,
            reason="Cellar tree notice is not real XML; rejecting (no silent store)",
            detail=why,
        )
        return

    eli = ""
    work_uri = ""
    try:
        # summarize_notice reads from a path; write to a tmp and summarize is
        # overkill — instead parse cheaply for work_uri/eli from the notice bytes.
        eli, work_uri = _notice_work_ids(notice_bytes)
    except ET.ParseError:
        pass

    corrigendum_celexes, corrigenda_extracted = extract_corrigendum_celexes(
        notice_bytes
    )

    notice_meta = CelexAcquisitionMetadata(
        celex=celex,
        work_canonical_id=work_canonical_id,
        work_uri=work_uri,
        expression_language=language,
        manifestation_uri="",
        item_uri="",
        fmt=_NOTICE_FORMAT_SLUG,
        consolidation_date=consolidation_date,
        fetched_at=fetched_at,
        source_sha256=hashlib.sha256(notice_bytes).hexdigest(),
        eli=eli,
        corrigendum_celexes=corrigendum_celexes,
        corrigenda_extracted=corrigenda_extracted,
    )
    if _store_if_new(
        farchive,
        notice_locator,
        notice_bytes,
        storage_class="xml",
        metadata=notice_meta.to_metadata_dict(),
        observed_at=fetched_at,
    ):
        run.added += 1
        run.stored_locators.append(notice_locator)
    else:
        run.skipped += 1

    # --- 2. Select the manifestation item from the notice -------------------
    item_url, manifestation_uri = _select_item_from_notice(notice_bytes, language, fmt)
    if item_url is None:
        _record_failure(
            run,
            rule_id="EU_ACQ.NO_MANIFESTATION",
            phase="acquisition",
            family="source_pathology",
            celex=celex,
            language=language,
            fmt=fmt,
            locator=item_locator,
            reason=(
                f"No {fmt} manifestation item for language={language!r} in the "
                "tree notice"
            ),
            detail="select_manifestation_option found no matching item URL",
        )
        return

    # --- 3. Fetch + verify + store the manifestation item -------------------
    try:
        item_bytes, _item_meta = fetch_item(item_url, timeout_s)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        _record_failure(
            run,
            rule_id="EU_ACQ.ITEM_FETCH_FAILED",
            phase="acquisition",
            family="transport_cleanup",
            celex=celex,
            language=language,
            fmt=fmt,
            locator=item_locator,
            reason="Cellar manifestation-item fetch failed",
            detail=f"{exc.__class__.__name__}: {exc} url={item_url}",
        )
        return

    # Some Cellar FMX4 manifestation items arrive as a ZIP archive of Formex
    # members (the OJ L-series packaging: primary ACT + toc/doc wrappers +
    # optional ANNEX members + binary attachments) rather than a bare XML
    # document. Detect the zip and unwrap it to the primary Formex XML BEFORE
    # the XML witness check, so these acts enter the corpus instead of being
    # typed-rejected as ITEM_NOT_XML (a real acquisition-completeness gain).
    if cellar.looks_like_zip(item_bytes):
        try:
            extracted = cellar.extract_primary_formex_from_zip(
                item_bytes, archive_hint=item_url
            )
        except zipfile.BadZipFile as exc:
            _record_failure(
                run,
                rule_id="EU_ACQ.ITEM_NOT_XML",
                phase="verify",
                family="source_pathology",
                celex=celex,
                language=language,
                fmt=fmt,
                locator=item_locator,
                reason=(
                    "Cellar manifestation item has ZIP magic but is not a "
                    "readable archive; rejecting (no silent store)"
                ),
                detail=f"BadZipFile: {exc} url={item_url}",
            )
            return
        if extracted is None:
            # A zip with no parseable-XML member — no primary Formex document to
            # store. Typed rejection preserves the no-silent-store discipline.
            _record_failure(
                run,
                rule_id="EU_ACQ.ITEM_ZIP_NO_XML",
                phase="verify",
                family="source_pathology",
                celex=celex,
                language=language,
                fmt=fmt,
                locator=item_locator,
                reason=(
                    "Cellar manifestation item is a ZIP with no parseable Formex "
                    "XML member; rejecting (no silent store)"
                ),
                detail=f"zip members had no XML root url={item_url}",
            )
            return
        # Replace the raw entropic zip bytes with the primary Formex XML member;
        # store the CONTENTS, not the archive. Additional members (annexes,
        # toc/doc wrappers, binary attachments) are recorded but not yet stored
        # as separate witnesses — the split-manifestation/annex-member case is a
        # known EU acquisition-completeness item, accounted here (not silently
        # dropped) via a typed EU_ACQ.ITEM_ZIP_EXTRACTED note.
        item_bytes = extracted.primary_xml
        _record_zip_extracted_note(
            run,
            celex=celex,
            language=language,
            fmt=fmt,
            locator=item_locator,
            item_url=item_url,
            extracted=extracted,
        )

    # Wrong-manifestation-item upgrade (#9): the notice lists the ACT body, its
    # ANNEX members, a DOC publication envelope, and binary attachments as
    # sibling ``…/DOC_N`` items of ONE manifestation, and the first-with-url
    # selection above can land on an envelope / annex / TIFF instead of the act.
    # If the fetched item is real XML but NOT an act-body root, probe the sibling
    # DOC_N members for the ``ACT``/``CORR`` body and store THAT instead. Purely
    # additive: an item that is already act-rooted is untouched. Skipped when the
    # item is not real XML (that stays a typed ITEM_NOT_XML rejection below).
    if (
        fmt.lower() == "fmx4"
        and _xml_root_tag(item_bytes) not in _ACT_BODY_ROOTS
    ):
        upgraded, upgraded_url = resolve_act_body(
            item_url, item_bytes, fetch_item=fetch_item, timeout_s=timeout_s
        )
        if upgraded is not None:
            item_bytes = upgraded
            item_url = upgraded_url

    ok, why = verify_xml_witness(item_bytes)
    if not ok:
        _record_failure(
            run,
            rule_id="EU_ACQ.ITEM_NOT_XML",
            phase="verify",
            family="source_pathology",
            celex=celex,
            language=language,
            fmt=fmt,
            locator=item_locator,
            reason=(
                "Cellar manifestation item is not real XML; rejecting "
                "(no silent store)"
            ),
            detail=f"{why} url={item_url}",
        )
        return

    item_meta = CelexAcquisitionMetadata(
        celex=celex,
        work_canonical_id=work_canonical_id,
        work_uri=work_uri,
        expression_language=language,
        manifestation_uri=manifestation_uri,
        item_uri=item_url,
        fmt=fmt,
        consolidation_date=consolidation_date,
        fetched_at=fetched_at,
        source_sha256=hashlib.sha256(item_bytes).hexdigest(),
        eli=eli,
        corrigendum_celexes=corrigendum_celexes,
        corrigenda_extracted=corrigenda_extracted,
    )
    run.metadata = item_meta
    if _store_if_new(
        farchive,
        item_locator,
        item_bytes,
        storage_class="xml",
        metadata=item_meta.to_metadata_dict(),
        observed_at=fetched_at,
    ):
        run.added += 1
        run.stored_locators.append(item_locator)
    else:
        run.skipped += 1


def _notice_work_ids(notice_bytes: bytes) -> tuple[str, str]:
    """Return ``(eli, work_uri)`` from a tree notice's WORK element."""
    root = ET.fromstring(notice_bytes)
    work = root.find("WORK")
    if work is None:
        return "", ""
    work_uri = ""
    uri_el = work.find("URI")
    if uri_el is not None:
        val = uri_el.find("VALUE")
        if val is not None and val.text:
            work_uri = val.text.strip()
    eli = ""
    for sameas in work.findall("SAMEAS"):
        uri = sameas.find("URI")
        if uri is None:
            continue
        type_el = uri.find("TYPE")
        val_el = uri.find("VALUE")
        if (
            type_el is not None
            and (type_el.text or "").strip().lower() == "eli"
            and val_el is not None
            and val_el.text
        ):
            eli = val_el.text.strip()
            break
    return eli, work_uri


def _select_item_from_notice(
    notice_bytes: bytes, language: str, fmt: str
) -> tuple[str | None, str]:
    """Select the (item_url, manifestation_uri) for ``(language, fmt)``.

    Reuses ``cellar.list_manifestation_options`` (which reads from a Path) by
    writing the notice to a temp path. Returns the FIRST ``(language, fmt)``
    option that actually carries a non-empty item URL.

    A consolidated act exposes MANY manifestations per (language, fmt) — one per
    consolidated-version expression — and several of them carry empty item lists
    in the tree notice. ``cellar.select_manifestation_option`` returns the first
    type/language match regardless of items, so we walk the options ourselves and
    skip the empty ones (deterministic first-with-item selection). Returns
    ``(None, "")`` if no matching option carries an item.
    """
    import tempfile

    want_language = language.upper()
    want_type = fmt.lower()
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=True) as tmp:
        tmp.write(notice_bytes)
        tmp.flush()
        options = cellar.list_manifestation_options(Path(tmp.name))

    for option in options:
        if option["language"] != want_language:
            continue
        if option["manifestation_type"].lower() != want_type:
            continue
        items = option.get("items") or []
        for item in items:
            item_url = item["uri"].get("value", "")
            if item_url:
                manifestation_uri = option.get("manifestation_uri", {}).get(
                    "value", ""
                )
                return item_url, manifestation_uri
    return None, ""


# ---------------------------------------------------------------------------
# ACT-body resolution across a multi-DOC manifestation
# ---------------------------------------------------------------------------

#: XML roots that ARE a self-contained act / corrigendum body worth storing at
#: the ``enacted`` locator. ``ACT`` is an amending regulation; ``CORR`` is a
#: corrigendum body; ``CONS.ACT`` / ``CONS.DOC`` are consolidated acts (kept for
#: parity, though the enacted lane fetches non-consolidated Works). Deliberately
#: EXCLUDES ``DOC`` (a publication envelope / table-of-contents) and ``ANNEX`` (a
#: separate annex member) — the two wrong-manifestation shapes the first
#: acquisition run stored in lieu of the act body.
_ACT_BODY_ROOTS = ("ACT", "CORR", "CONS.ACT", "CONS.DOC")

#: How many sibling ``DOC_N`` members to probe for the act body when the
#: notice-selected item is a publication envelope / annex member (mirrors
#: :data:`lawvm.eu.eu_consolidation_oracle._MAX_SIBLING_DOC_PROBES`).
_MAX_SIBLING_DOC_PROBES = 12

#: Match a Cellar ``…/DOC_N`` item URL so its sibling members can be derived.
_DOC_N_PATTERN = r"(?P<stem>.+/)DOC_(?P<n>\d+)"


def _unwrap_item_to_xml(item_bytes: bytes, item_url: str) -> bytes | None:
    """Unwrap a fetched manifestation item to its primary Formex XML, or None.

    A ZIP is unwrapped to its primary Formex member; a bare XML item is returned
    as-is. Returns None when the item is a ZIP with no parseable Formex member
    (the caller records a typed gap — never a silent store).
    """
    if cellar.looks_like_zip(item_bytes):
        try:
            extracted = cellar.extract_primary_formex_from_zip(
                item_bytes, archive_hint=item_url
            )
        except zipfile.BadZipFile:
            return None
        if extracted is None:
            return None
        return extracted.primary_xml
    return item_bytes


def resolve_act_body(
    item_url: str,
    item_bytes: bytes,
    *,
    fetch_item: Any,
    timeout_s: int,
    accept_roots: tuple[str, ...] = _ACT_BODY_ROOTS,
) -> tuple[bytes | None, str]:
    """Resolve the notice-selected item to a self-contained ACT-body XML.

    The wrong-manifestation-item fix (#9): a consolidated- OR multi-DOC
    non-consolidated notice bundles the act's ``ACT`` body, its ``ANNEX``
    members, a ``DOC`` publication envelope and (for corrigenda) a ``CORR`` body
    across sibling ``…/DOC_N`` items of ONE manifestation, and
    ``_select_item_from_notice`` returns the FIRST item with a URL — often an
    annex, an envelope, or even a binary TIFF attachment (verified live:
    32016R0646 selected ``DOC_9``, a TIFF; the ``ACT`` body was ``DOC_2``). This
    walks the sibling ``DOC_N`` members and returns the first that unwraps to a
    root in ``accept_roots``.

    ``accept_roots`` narrows the acceptable body for a specialised lane — a
    corrigendum acquisition passes ``("CORR",)`` so a co-bundled ``CONS.ACT``
    sibling in the corrigendum's notice is NOT stored under the corrigendum's
    locator (a wrong-manifestation store the general set would admit).

    Returns ``(body_xml, selected_url)`` — the resolved XML bytes and the URL it
    came from — or ``(None, "")`` if no sibling carries an accepted body.
    Idempotent / network-cheap: probes stop after two consecutive missing DOC
    indices.
    """
    body = _unwrap_item_to_xml(item_bytes, item_url)
    if body is not None and cellar._xml_root_local_tag(body) in accept_roots:
        return body, item_url

    m = re.match(_DOC_N_PATTERN + r"$", item_url)  # lawvm-regex: witness_only shape-parses an already-acquired Cellar item URL (source-plane locator census) to derive sibling DOC member URLs, not a semantic recognizer over statute text
    if not m:
        return None, ""
    stem = m.group("stem")
    selected_n = int(m.group("n"))
    misses = 0
    for n in range(1, _MAX_SIBLING_DOC_PROBES + 1):
        if n == selected_n:
            continue
        sibling_url = f"{stem}DOC_{n}"
        try:
            sibling_bytes, _ = fetch_item(sibling_url, timeout_s)
        except (HTTPError, URLError, TimeoutError, OSError):
            # A missing sibling index is expected (the DOC_N series is finite);
            # two consecutive misses end the probe.
            misses += 1
            if misses >= 2:
                break
            continue
        misses = 0
        sibling_body = _unwrap_item_to_xml(sibling_bytes, sibling_url)
        if (
            sibling_body is not None
            and cellar._xml_root_local_tag(sibling_body) in accept_roots
        ):
            return sibling_body, sibling_url
    return None, ""


# ---------------------------------------------------------------------------
# Amender + corrigendum byte acquisition (durable, ACT-body-correct)
# ---------------------------------------------------------------------------


def acquire_amender_act(
    farchive: Any,
    celex: str,
    *,
    fetched_at: datetime,
    language: str = "eng",
    timeout_s: int = cellar.DEFAULT_TIMEOUT_S,
    _fetch_notice: Any = None,
    _fetch_item: Any = None,
) -> dict[str, Any]:
    """Acquire an amending act's ACT-body FMX4 into the ``enacted`` locator.

    Fixes the two acquisition classes of #9 at their root: (1) a truly-missing
    amender (never fetched) and (2) a wrong-manifestation-item store (a ``DOC``
    envelope / ``ANNEX`` member / binary attachment stored in lieu of the act).
    Fetches the amender's tree notice, selects the ``(language, fmx4)`` item,
    then routes it through :func:`resolve_act_body` so the sibling ``DOC_N``
    that actually carries the ``ACT`` body is stored — overwriting any prior
    wrong-manifestation state at the same locator (``_store_if_new`` records the
    new digest). Returns a typed status dict; never silently stores a non-act.
    """
    fetch_notice = _fetch_notice or _live_fetch_notice
    fetch_item = _fetch_item or _live_fetch_item
    locator = celex_locator(celex, "enacted", language, "fmx4")
    result: dict[str, Any] = {"celex": celex, "locator": locator, "acquire_status": "", "root": ""}

    try:
        notice_bytes, _ = fetch_notice(celex, language, timeout_s)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        result["acquire_status"] = f"NOTICE_FETCH_FAILED:{type(exc).__name__}:{exc}"
        return result

    ok, why = verify_xml_witness(notice_bytes)
    if not ok:
        result["acquire_status"] = f"NOTICE_NOT_XML:{why}"
        return result

    item_url, manifestation_uri = _select_item_from_notice(notice_bytes, language, "fmx4")
    if not item_url:
        result["acquire_status"] = f"NO_MANIFESTATION:no {language} fmx4 item in notice"
        return result

    try:
        item_bytes, _ = fetch_item(item_url, timeout_s)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        result["acquire_status"] = f"ITEM_FETCH_FAILED:{type(exc).__name__}:{exc}"
        return result

    body, selected_url = resolve_act_body(
        item_url, item_bytes, fetch_item=fetch_item, timeout_s=timeout_s
    )
    if body is None:
        result["acquire_status"] = (
            f"NO_ACT_BODY:no sibling DOC member under {item_url} carries an "
            "ACT-rooted body"
        )
        return result

    root = cellar._xml_root_local_tag(body) or ""
    eli, work_uri = ("", "")
    try:
        eli, work_uri = _notice_work_ids(notice_bytes)
    except ET.ParseError:
        pass
    corrigendum_celexes, corrigenda_extracted = extract_corrigendum_celexes(notice_bytes)

    meta = CelexAcquisitionMetadata(
        celex=celex,
        work_canonical_id=celex_to_canonical_id(celex),
        work_uri=work_uri,
        expression_language=language,
        manifestation_uri=manifestation_uri,
        item_uri=selected_url,
        fmt="fmx4",
        consolidation_date="enacted",
        fetched_at=fetched_at,
        source_sha256=hashlib.sha256(body).hexdigest(),
        eli=eli,
        corrigendum_celexes=corrigendum_celexes,
        corrigenda_extracted=corrigenda_extracted,
    )
    stored = _store_if_new(
        farchive,
        locator,
        body,
        storage_class="xml",
        metadata=meta.to_metadata_dict(),
        observed_at=fetched_at,
    )
    result["acquire_status"] = "STORED" if stored else "RE_OBSERVED"
    result["root"] = root
    result["bytes"] = len(body)
    result["item_url"] = selected_url
    return result


def acquire_corrigendum(
    farchive: Any,
    resource: CorrigendumResourceRef,
    *,
    fetched_at: datetime,
    language: str = "eng",
    timeout_s: int = cellar.DEFAULT_TIMEOUT_S,
    _fetch_notice: Any = None,
    _fetch_item: Any = None,
) -> dict[str, Any]:
    """Acquire a corrigendum's ``CORR`` body via its Cellar UUID resource.

    Implements #9 class 3 — the corrigendum BYTE acquisition the module only
    *detected* before. The corrigendum ``…R(NN)`` CELEX is not resolvable via
    ``/celex/`` (verified: 404), so this fetches the corrigendum's own tree
    notice by its addressable Cellar UUID (``/cellar/{uuid}``), selects the
    ``(language, fmx4)`` item, and routes it through :func:`resolve_act_body`
    (the ``CORR`` root is an :data:`_ACT_BODY_ROOTS` member). Stored at the
    identity locator keyed on the corrigendum's OWN CELEX so the replay/touch
    lane can find it: ``cellar://celex/{R(NN)-celex}/enacted/{lang}/fmx4``.
    Idempotent + never a silent non-corrigendum store.
    """
    fetch_notice = _fetch_notice or _live_fetch_notice_by_uuid
    fetch_item = _fetch_item or _live_fetch_item
    locator = celex_locator(resource.celex, "enacted", language, "fmx4")
    result: dict[str, Any] = {
        "celex": resource.celex,
        "cellar_uuid": resource.cellar_uuid,
        "locator": locator,
        "acquire_status": "",
        "root": "",
    }

    try:
        notice_bytes, _ = fetch_notice(resource.cellar_uuid, language, timeout_s)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        result["acquire_status"] = f"NOTICE_FETCH_FAILED:{type(exc).__name__}:{exc}"
        return result

    ok, why = verify_xml_witness(notice_bytes)
    if not ok:
        result["acquire_status"] = f"NOTICE_NOT_XML:{why}"
        return result

    item_url, manifestation_uri = _select_item_from_notice(notice_bytes, language, "fmx4")
    if not item_url:
        result["acquire_status"] = f"NO_MANIFESTATION:no {language} fmx4 item in notice"
        return result

    try:
        item_bytes, _ = fetch_item(item_url, timeout_s)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        result["acquire_status"] = f"ITEM_FETCH_FAILED:{type(exc).__name__}:{exc}"
        return result

    # A corrigendum's own body is ``CORR``; a co-bundled ``CONS.ACT``/``ACT``
    # sibling in the corrigendum notice is a DIFFERENT Work — never store it
    # under the corrigendum's locator. Restrict the accepted root to ``CORR``.
    body, selected_url = resolve_act_body(
        item_url,
        item_bytes,
        fetch_item=fetch_item,
        timeout_s=timeout_s,
        accept_roots=("CORR",),
    )
    if body is None:
        result["acquire_status"] = (
            f"NO_ACT_BODY:no sibling DOC member under {item_url} carries a "
            "CORR-rooted body"
        )
        return result

    root = cellar._xml_root_local_tag(body) or ""
    meta = {
        "celex": resource.celex,
        "cellar_uuid": resource.cellar_uuid,
        "expression_language": language,
        "manifestation_uri": manifestation_uri,
        "item_uri": selected_url,
        "format": "fmx4",
        "consolidation_date": "enacted",
        "fetched_at": fetched_at.isoformat(),
        "source_sha256": hashlib.sha256(body).hexdigest(),
        "relation_kind": "corrects",
        "source_surface": "eu-cellar-frbr-corrigendum",
    }
    stored = _store_if_new(
        farchive,
        locator,
        body,
        storage_class="xml",
        metadata=meta,
        observed_at=fetched_at,
    )
    result["acquire_status"] = "STORED" if stored else "RE_OBSERVED"
    result["root"] = root
    result["bytes"] = len(body)
    result["item_url"] = selected_url
    return result


def _live_fetch_notice_by_uuid(
    cellar_uuid: str, language: str, timeout_s: int
) -> tuple[bytes, dict[str, Any]]:
    """Fetch a FRBR tree notice by its Cellar resource UUID (read-only reuse).

    A corrigendum's ``…R(NN)`` CELEX is not resolvable via the ``/celex/`` path;
    the Cellar UUID (from the base act's ``CORRECTED_BY`` relation) IS, via
    ``NoticeRequest``'s UUID branch (``/cellar/{uuid}``).
    """
    notice = cellar.NoticeRequest(
        celex=cellar_uuid,
        notice_format="xml",
        notice_type="tree",
        decode_language=language,
    )
    return cellar._request_notice(notice, timeout_s=timeout_s)
