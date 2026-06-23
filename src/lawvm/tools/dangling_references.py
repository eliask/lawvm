"""``dangling-refs`` — corpus-wide DANGLING-REFERENCE report over ``fi_refs``.

WHAT THIS IS. ``fi_refs.jsonl`` / ``.parquet`` is LawVM's published corpus-wide
resolved-reference projection: one row per extracted reference mention, carrying
``cite_confidence`` (exact / statute_only / ambiguous / open / ...), the source
provision, and — for a RESOLVED row — a concrete ``target_statute_id`` +
``target_provision_ref_str``. A *resolved* reference asserts a specific target
provision. Some of those targets DO NOT EXIST in the materialized corpus
(repealed-and-emptied, renumbered, or never-existed) — a dangling / broken
cross-reference. This tool reads that projection and classifies every resolved
reference into a THREE-way status against the best available existence oracle,
emitting a typed report (counts + the DANGLING rows with full provenance).

The artifact answers, corpus-wide and externally-legibly: *which published
resolved cross-references in Finnish law point at a target provision that is not
present in the corpus?* — a full-accounting (täyslaskenta) projection, not a
legal conclusion.

THE CORE DISCIPLINE — TAG, DON'T GUESS (the cardinal rule). Every checked
reference lands in exactly one of THREE statuses, never silently:

* ``PRESENT`` — the target provision is confidently FOUND in the target act's
  current consolidated text-state.
* ``DANGLING`` — the target provision is confidently ABSENT: the target act IS
  in the corpus, its body IS materialized, yet the cited section does not
  resolve to any element. The reference points at an address the act does not
  hold.
* ``EXISTENCE_UNKNOWN`` — existence could NOT be determined: the target act is
  not in the local corpus, OR its body is a ``contentAbsent`` placeholder (a
  blocked / unmaterialized act), OR the target_provision_ref carries no
  statute identity. This is an honest NON-DETERMINATION, NOT a defect and NOT
  dangling. Reporting an EXISTENCE_UNKNOWN as DANGLING would be a false-positive
  "broken" claim — the cardinal sin this tool refuses to commit.

SCOPE — RESOLVED references only. Only ``cite_confidence`` values that assert a
specific target PROVISION are checked: ``exact`` and ``approximate``. The honest
non-resolutions — ``statute_only`` (act known, provision deferred), ``ambiguous``
(multiple plausible targets), ``open`` (vague catch-all by construction),
``unresolved``, ``broken`` (already typed broken upstream) — are EXCLUDED from the
existence check and counted separately. They are not defects; they declined to
name a single target, so there is nothing to check existence of.

THE BITEMPORAL BOUNDARY (declared, constructive-invariant pattern). The existence
oracle checks the target provision against the target act's CURRENT consolidated
text-state (the Finlex consolidated oracle, read for free — NO point-in-time
replay). So this is an **AS-OF-NOW** check: a DANGLING verdict means "absent in
the current consolidated text-state". The DECLARED RESIDUAL is the AS-OF-CITING
question: a reference whose target existed WHEN THE CITATION WAS WRITTEN but has
since been repealed/renumbered is a dangling-as-of-now finding here, but it is
NOT necessarily a dangling-as-of-writing defect (the citing text may have been
correct when enacted). This tool does NOT do the as-of-citing (replay) check —
that is the heavier ``broken-refs --provenance`` path. The as-of-now / as-of-
citing distinction is therefore the declared boundary of the DANGLING claim.

A second declared bound on DANGLING: existence is resolved at the ELEMENT level
(the AKN section resolver). A few Finlex acts render a renumbered range as a
single merged section element (e.g. a ``<num>3 a-4 §</num>`` heading), so a
citation to a sub-member of that range (``§4``) resolves to no discrete element
and reads DANGLING here even though the text is materially present inside the
merged span. That is element-resolution granularity, declared as part of the
section-granularity boundary -- not a false claim that the law is broken.

WHY A SEPARATE TOOL FROM ``broken-refs``. ``broken-refs`` sweeps the corpus per
citing statute from the live graph and classifies a statute-lifecycle /
provision-presence broken set with its own (rich, replay-capable) machinery. THIS
tool is a read-only PROJECTION over the PUBLISHED ``fi_refs`` artifact with an
explicit three-way PRESENT / DANGLING / EXISTENCE_UNKNOWN status, and it is wired
into the claim-surface backbone (``CLAIM_DANGLING_REFERENCE``) — so the public
"broken cross-references in Finnish law" number is claim-backed and accounted.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from lawvm.core.locator import HierarchicalLocator, LocatorSegment
from lawvm.substrate.canonical_json import JsonValue, nfc

_SCHEMA_DANGLING_ROW = "lawvm.dangling_reference_row.v1"
_SCHEMA_DANGLING_REPORT = "lawvm.dangling_reference_report.v1"

# AKN namespace + the placeholder element a non-materialized body carries.
_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_CONTENT_ABSENT = "contentAbsent"

# ---------------------------------------------------------------------------
# The closed THREE-way status set (tag-don't-guess; an out-of-set value is a
# typed finding, never a silently widened set).
# ---------------------------------------------------------------------------
STATUS_PRESENT = "PRESENT"
STATUS_DANGLING = "DANGLING"
STATUS_EXISTENCE_UNKNOWN = "EXISTENCE_UNKNOWN"

#: The closed status set. A computed status outside it is a typed finding.
DANGLING_STATUSES: frozenset[str] = frozenset(
    {STATUS_PRESENT, STATUS_DANGLING, STATUS_EXISTENCE_UNKNOWN}
)

#: The ``cite_confidence`` values that assert a specific target PROVISION and are
#: therefore IN SCOPE for the existence check (a RESOLVED target).
RESOLVED_CONFIDENCES: frozenset[str] = frozenset({"exact", "approximate"})

#: Honest non-resolutions — EXCLUDED from the existence check, counted separately.
#: These declined to name one target provision (not a defect, not dangling).
NON_RESOLVED_CONFIDENCES: frozenset[str] = frozenset(
    {"statute_only", "ambiguous", "open", "unresolved", "broken"}
)


class DanglingReferenceError(ValueError):
    """A dangling-reference object violates a v1 invariant (out-of-set status)."""


# ---------------------------------------------------------------------------
# Reason vocabulary for an EXISTENCE_UNKNOWN / DANGLING verdict (closed set).
# ---------------------------------------------------------------------------
REASON_PRESENT = "target_provision_present"
REASON_DANGLING_ABSENT = "target_provision_absent_in_current_text"
REASON_UNKNOWN_ACT_ABSENT = "target_act_absent_from_corpus"
REASON_UNKNOWN_CONTENT_ABSENT = "target_act_body_not_materialized"
REASON_UNKNOWN_NO_STATUTE_ID = "target_has_no_statute_identity"
REASON_UNKNOWN_UNPARSEABLE_XML = "target_act_xml_unparseable"


@dataclass(frozen=True, slots=True)
class DanglingReferenceRow:
    """``lawvm.dangling_reference_row.v1`` — one classified resolved reference.

    Carries full provenance: the citing (source) provision, the resolved target
    provision, the in-scope ``cite_confidence``, the computed three-way
    ``status``, and the closed-set ``reason`` for the verdict.
    """

    source_statute_id: str
    source_provision_ref_str: str
    target_statute_id: str
    target_provision_ref_str: str
    cite_confidence: str
    cite_kind: str
    existence_status: str
    reason: str
    valid_at_start: Optional[str] = None
    valid_at_end: Optional[str] = None

    def __post_init__(self) -> None:
        if self.existence_status not in DANGLING_STATUSES:
            raise DanglingReferenceError(
                f"DanglingReferenceRow.existence_status must be one of "
                f"{sorted(DANGLING_STATUSES)!r}, got {self.existence_status!r} — an "
                f"out-of-set status is a typed finding, never a silently widened set "
                f"(source {self.source_statute_id} -> target "
                f"{self.target_provision_ref_str})"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_DANGLING_ROW,
            "source_statute_id": nfc(self.source_statute_id),
            "source_provision_ref_str": nfc(self.source_provision_ref_str),
            "target_statute_id": nfc(self.target_statute_id),
            "target_provision_ref_str": nfc(self.target_provision_ref_str),
            "cite_confidence": nfc(self.cite_confidence),
            "cite_kind": nfc(self.cite_kind),
            "existence_status": self.existence_status,
            "reason": nfc(self.reason),
            "valid_at_start": self.valid_at_start,
            "valid_at_end": self.valid_at_end,
        }


@dataclass(frozen=True, slots=True)
class DanglingReferenceReport:
    """``lawvm.dangling_reference_report.v1`` — the corpus-wide three-way report.

    Fields:

    * ``total_rows`` — every ``fi_refs`` row read.
    * ``resolved_checked`` — rows in scope (RESOLVED confidence) that were
      classified into the three-way status.
    * ``excluded_non_resolved`` — honest non-resolutions counted but not checked,
      broken out by ``cite_confidence``.
    * ``present`` / ``dangling`` / ``existence_unknown`` — the three-way counts
      over ``resolved_checked`` (they sum to it — totality).
    * ``unknown_by_reason`` / ``dangling_by_reason`` — the closed-set reason
      breakdown.
    * ``dangling_rows`` — the DANGLING witnesses (full provenance), sorted.
    """

    total_rows: int
    resolved_checked: int
    excluded_non_resolved: dict[str, int]
    present: int
    dangling: int
    existence_unknown: int
    unknown_by_reason: dict[str, int]
    dangling_by_reason: dict[str, int]
    dangling_rows: tuple[DanglingReferenceRow, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # TOTALITY: every RESOLVED-in-scope reference lands in exactly one of the
        # three-way statuses; the three counts must sum to resolved_checked. A
        # mismatch means a row escaped classification — a typed finding, not a
        # silent drop.
        triple = self.present + self.dangling + self.existence_unknown
        if triple != self.resolved_checked:
            raise DanglingReferenceError(
                "DanglingReferenceReport totality violated: present+dangling+"
                f"existence_unknown={triple} != resolved_checked="
                f"{self.resolved_checked}; a resolved reference escaped the "
                "three-way status (a row was silently dropped)"
            )
        if len(self.dangling_rows) != self.dangling:
            raise DanglingReferenceError(
                f"DanglingReferenceReport dangling_rows count "
                f"{len(self.dangling_rows)} != dangling tally {self.dangling}"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_DANGLING_REPORT,
            "total_rows": self.total_rows,
            "resolved_checked": self.resolved_checked,
            "excluded_non_resolved": dict(sorted(self.excluded_non_resolved.items())),
            "present": self.present,
            "dangling": self.dangling,
            "existence_unknown": self.existence_unknown,
            "unknown_by_reason": dict(sorted(self.unknown_by_reason.items())),
            "dangling_by_reason": dict(sorted(self.dangling_by_reason.items())),
            "dangling_rows": [r.to_canonical_dict() for r in self.dangling_rows],
        }


# ---------------------------------------------------------------------------
# The existence oracle — as-of-NOW, over the current consolidated text-state.
# ---------------------------------------------------------------------------


def _provision_ref_to_locator(
    target_statute_id: str, target_provision_ref_str: str
) -> Optional[HierarchicalLocator]:
    """Parse a ``target_provision_ref_str`` into a section/chapter locator.

    ``target_provision_ref_str`` is the ``ProvisionRef.serialized()`` form:
    ``statute_id[/chN]/section[/momentti][/kLABEL][/sLABEL]``. The STATUTE ID
    MAY ITSELF CONTAIN A SLASH (e.g. ``1987/627``), so the prefix is stripped by
    the explicit ``target_statute_id`` — NOT by splitting on the first ``/``
    (that would mis-strip ``1987/627/17`` into ``627/17``). The momentti / kohta
    / alakohta tail is below section granularity and intentionally dropped: the
    existence check resolves to SECTION (and embedded CHAPTER) granularity only
    (declared in the claim's boundary). Returns ``None`` when the ref carries no
    in-act provision (act-level only).
    """
    rest = target_provision_ref_str
    prefix = target_statute_id + "/"
    if rest.startswith(prefix):
        rest = rest[len(prefix) :]
    elif rest == target_statute_id:
        return None  # act-level only — no in-act provision to resolve
    # else: ref does not carry the statute prefix; treat what remains as the path.
    tokens = [t for t in rest.split("/") if t]
    segments: list[LocatorSegment] = []
    for tok in tokens:
        if tok.startswith("ch"):
            segments.append(LocatorSegment(kind="chapter", label=tok[2:]))
        elif tok.startswith("k") or tok.startswith("s"):
            # kohta (k) / alakohta (s) — below section granularity; not resolved.
            continue
        elif tok.isdigit() and segments and segments[-1].kind == "section":
            # bare momentti (subsection) — below section granularity.
            continue
        else:
            segments.append(LocatorSegment(kind="section", label=tok))
    if not segments:
        return None
    return HierarchicalLocator(segments=tuple(segments))


def _body_is_content_absent(root: Any) -> bool:
    """True iff the act XML is a ``contentAbsent`` placeholder (no materialized body).

    Finlex emits ``<hcontainer name="contentAbsent"/>`` for an act whose body was
    not materialized (blocked / repealed-stub / never-fetched). Such an act EXISTS
    in the corpus but carries no provisions to check, so any cited provision is
    EXISTENCE_UNKNOWN, never DANGLING (the cardinal no-false-positive rule).
    The check is conservative: it only treats the body as absent when the
    placeholder is present AND no real section element exists.
    """
    has_section = False
    has_absent = False
    for el in root.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        eid = el.get("eId") or ""
        if "sec_" in eid:
            has_section = True
        if tag.endswith("hcontainer") and el.get("name") == _CONTENT_ABSENT:
            has_absent = True
    return has_absent and not has_section


class CurrentStateExistenceOracle:
    """As-of-NOW provision-existence oracle over the current consolidated oracle XML.

    Reads the target act's current consolidated XML from the corpus store (the
    Finlex oracle, free — NO replay), parses it, and resolves the cited provision
    with the Finnish AKN section resolver. Returns one of the closed three-way
    statuses + a closed-set reason. Per-act XML is cached so a hot dead target
    cited from many statutes is parsed once.
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        # statute_id -> (parsed_root_or_None, content_absent_bool, parse_ok_bool)
        self._cache: dict[str, tuple[Any, bool, bool]] = {}
        from lawvm.finland.section_resolver import FinnishAKNResolver

        self._resolver = FinnishAKNResolver()

    def _load(self, statute_id: str) -> tuple[Any, bool, bool]:
        cached = self._cache.get(statute_id)
        if cached is not None:
            return cached
        from lxml import etree

        try:
            xml = self._store.read_oracle(statute_id)
        except Exception:
            xml = None
        if xml is None:
            result = (None, False, False)  # act absent from corpus
            self._cache[statute_id] = result
            return result
        try:
            root = etree.fromstring(xml)
        except Exception:
            result = (None, False, True)  # present but unparseable XML
            # parse_ok False is encoded by root None + xml-was-present; use a
            # dedicated 4th-state? No — keep it (None, content_absent=False,
            # had_xml=True) and disambiguate via a sentinel below.
            self._cache[statute_id] = (None, False, True)
            return self._cache[statute_id]
        content_absent = _body_is_content_absent(root)
        result = (root, content_absent, True)
        self._cache[statute_id] = result
        return result

    def classify(
        self, target_statute_id: str, target_provision_ref_str: str
    ) -> tuple[str, str]:
        """Return ``(status, reason)`` for one resolved target.

        Three-way, fail-loud: an act not in the corpus or with an unmaterialized
        body is EXISTENCE_UNKNOWN (never DANGLING). DANGLING is returned ONLY when
        the act is in the corpus, its body IS materialized, and the cited section
        resolves to no element.
        """
        if not target_statute_id:
            return (STATUS_EXISTENCE_UNKNOWN, REASON_UNKNOWN_NO_STATUTE_ID)

        root, content_absent, had_xml = self._load(target_statute_id)
        if not had_xml:
            return (STATUS_EXISTENCE_UNKNOWN, REASON_UNKNOWN_ACT_ABSENT)
        if root is None:
            # had XML but parse failed.
            return (STATUS_EXISTENCE_UNKNOWN, REASON_UNKNOWN_UNPARSEABLE_XML)
        if content_absent:
            return (STATUS_EXISTENCE_UNKNOWN, REASON_UNKNOWN_CONTENT_ABSENT)

        locator = _provision_ref_to_locator(
            target_statute_id, target_provision_ref_str
        )
        if locator is None:
            # Act-level reference and the act IS materialized -> the cited unit
            # (the whole act) is present.
            return (STATUS_PRESENT, REASON_PRESENT)

        element = self._resolver.resolve(root, locator)
        if element is not None:
            return (STATUS_PRESENT, REASON_PRESENT)

        # Last-resort num-text match for a top-level bare-section locator (the
        # resolver's own fallback for ``section:N`` against ``<num>`` text).
        if (
            len(locator.segments) == 1
            and locator.segments[-1].kind == "section"
        ):
            by_num = self._resolver._find_by_num_text(  # noqa: SLF001 (intended reuse)
                root, locator.segments[-1].label
            )
            if by_num is not None:
                return (STATUS_PRESENT, REASON_PRESENT)

        # Act materialized, provision does not resolve -> confidently absent.
        return (STATUS_DANGLING, REASON_DANGLING_ABSENT)


# ---------------------------------------------------------------------------
# fi_refs reader + the corpus-wide classification.
# ---------------------------------------------------------------------------


def _iter_fi_refs_rows(path: str) -> Any:
    """Yield fi_refs rows from a ``.jsonl`` (one JSON object per line) or ``.parquet``."""
    if path.endswith(".parquet"):
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        for batch in table.to_pylist():
            yield batch
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def build_dangling_report(
    fi_refs_path: str,
    oracle: CurrentStateExistenceOracle,
) -> DanglingReferenceReport:
    """Classify every RESOLVED reference in ``fi_refs_path`` into the three-way status.

    Reads the projection, filters to RESOLVED confidence rows, classifies each
    against the existence ``oracle``, and folds the result into a typed report.
    Non-resolved rows are counted (by ``cite_confidence``) but not checked. The
    report carries EVERY dangling witness (no cap, no silent reclassification) so
    the three-way totality invariant (present + dangling + existence_unknown ==
    resolved_checked) is exact; display capping is the CLI's concern (``--top``).
    """
    total = 0
    present = 0
    dangling = 0
    unknown = 0
    excluded: Counter[str] = Counter()
    unknown_reasons: Counter[str] = Counter()
    dangling_reasons: Counter[str] = Counter()
    dangling_rows: list[DanglingReferenceRow] = []

    for row in _iter_fi_refs_rows(fi_refs_path):
        total += 1
        confidence = row.get("cite_confidence") or ""
        if confidence not in RESOLVED_CONFIDENCES:
            excluded[confidence or "(missing)"] += 1
            continue

        target_statute_id = row.get("target_statute_id") or ""
        target_ref = row.get("target_provision_ref_str") or ""
        status, reason = oracle.classify(target_statute_id, target_ref)

        if status == STATUS_PRESENT:
            present += 1
        elif status == STATUS_DANGLING:
            dangling += 1
            dangling_reasons[reason] += 1
            dangling_rows.append(
                DanglingReferenceRow(
                    source_statute_id=row.get("source_statute_id") or "",
                    source_provision_ref_str=row.get("source_provision_ref_str")
                    or "",
                    target_statute_id=target_statute_id,
                    target_provision_ref_str=target_ref,
                    cite_confidence=confidence,
                    cite_kind=row.get("cite_kind") or "",
                    existence_status=status,
                    reason=reason,
                    valid_at_start=row.get("valid_at_start"),
                    valid_at_end=row.get("valid_at_end"),
                )
            )
        elif status == STATUS_EXISTENCE_UNKNOWN:
            unknown += 1
            unknown_reasons[reason] += 1
        else:  # pragma: no cover — the oracle only returns closed-set statuses
            raise DanglingReferenceError(
                f"existence oracle returned out-of-set status {status!r} for "
                f"target {target_ref!r} — a status outside "
                f"{sorted(DANGLING_STATUSES)!r} is a typed finding"
            )

    dangling_rows.sort(
        key=lambda r: (
            r.source_statute_id,
            r.target_statute_id,
            r.source_provision_ref_str,
            r.target_provision_ref_str,
        )
    )

    return DanglingReferenceReport(
        total_rows=total,
        resolved_checked=present + dangling + unknown,
        excluded_non_resolved=dict(excluded),
        present=present,
        dangling=dangling,
        existence_unknown=unknown,
        unknown_by_reason=dict(unknown_reasons),
        dangling_by_reason=dict(dangling_reasons),
        dangling_rows=tuple(dangling_rows),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_text(report: DanglingReferenceReport, top: int) -> str:
    lines: list[str] = []
    lines.append("\n=== dangling-refs (corpus DANGLING-reference report, fi) ===")
    lines.append(
        "  (RESOLVED refs only; three-way PRESENT / DANGLING / EXISTENCE_UNKNOWN;"
        "\n   DANGLING = absent in the target act's CURRENT consolidated text-state"
        "\n   (as-of-NOW). An act absent / not-materialized is EXISTENCE_UNKNOWN,"
        "\n   NEVER dangling. Surface fact, not a legal conclusion.)"
    )
    lines.append(f"  fi_refs rows read           : {report.total_rows}")
    lines.append(f"  resolved refs checked       : {report.resolved_checked}")
    lines.append(f"    PRESENT                   : {report.present}")
    lines.append(f"    DANGLING                  : {report.dangling}")
    lines.append(f"    EXISTENCE_UNKNOWN         : {report.existence_unknown}")
    lines.append("\n  excluded (non-resolved, not checked) by confidence:")
    if report.excluded_non_resolved:
        for conf, n in sorted(report.excluded_non_resolved.items()):
            lines.append(f"    {n:8}  {conf}")
    else:
        lines.append("    (none)")
    lines.append("\n  EXISTENCE_UNKNOWN by reason:")
    if report.unknown_by_reason:
        for reason, n in sorted(report.unknown_by_reason.items()):
            lines.append(f"    {n:8}  {reason}")
    else:
        lines.append("    (none)")
    lines.append("\n  DANGLING by reason:")
    if report.dangling_by_reason:
        for reason, n in sorted(report.dangling_by_reason.items()):
            lines.append(f"    {n:8}  {reason}")
    else:
        lines.append("    (none)")
    lines.append(f"\n  DANGLING witnesses (showing up to {top}):")
    if report.dangling_rows:
        for r in report.dangling_rows[:top]:
            lines.append(
                f"    {r.source_statute_id} {r.source_provision_ref_str} -> "
                f"{r.target_provision_ref_str}  [{r.cite_confidence}/{r.cite_kind}]"
            )
    else:
        lines.append("    (none)")
    return "\n".join(lines)


def _resolve_store() -> Any:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    return TransparentCorpusStore(Farchive(_archive_path(), readonly=True))


def _default_fi_refs_path() -> str:
    """Default projection path, mirroring the export's default data dir."""
    import os

    for candidate in (
        os.path.join(".tmp", "projections", "fi_refs.jsonl"),
        os.path.join(".tmp", "projections", "fi_refs__deterministic_only.jsonl"),
        os.path.join("data", "fi", "v1", "fi_refs.jsonl"),
    ):
        if os.path.exists(candidate):
            return candidate
    return os.path.join(".tmp", "projections", "fi_refs.jsonl")


def main(args: argparse.Namespace) -> None:
    fi_refs_path: str = getattr(args, "fi_refs", None) or _default_fi_refs_path()
    out_path: Optional[str] = getattr(args, "out", None)
    as_json: bool = bool(getattr(args, "json", False))
    top: int = int(getattr(args, "top", 20) or 20)

    import os

    if not os.path.exists(fi_refs_path):
        raise SystemExit(
            f"ERROR: fi_refs projection not found at {fi_refs_path!r}. "
            "Generate it first (lawvm export-fi-refs / export_fi_refs) or pass "
            "--fi-refs PATH."
        )

    print(
        f"dangling-refs: classifying resolved references in {fi_refs_path} "
        "against the current-state existence oracle...",
        file=sys.stderr,
    )
    oracle = CurrentStateExistenceOracle(_resolve_store())
    report = build_dangling_report(fi_refs_path, oracle)

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(report.to_canonical_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(f"dangling-refs: wrote report -> {out_path}", file=sys.stderr)

    if as_json:
        json.dump(report.to_canonical_dict(), sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return
    print(_render_text(report, top))


__all__ = [
    "DANGLING_STATUSES",
    "NON_RESOLVED_CONFIDENCES",
    "RESOLVED_CONFIDENCES",
    "STATUS_DANGLING",
    "STATUS_EXISTENCE_UNKNOWN",
    "STATUS_PRESENT",
    "CurrentStateExistenceOracle",
    "DanglingReferenceError",
    "DanglingReferenceReport",
    "DanglingReferenceRow",
    "build_dangling_report",
    "main",
]
