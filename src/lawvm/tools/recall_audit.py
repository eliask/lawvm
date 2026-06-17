"""recall-audit — DETECTION-recall audit for Finnish reference extraction.

This tool measures *detection* coverage of the Finnish reference extractor:
"did we find that a citation exists in the text", as distinct from *resolution*
("did we map it to a target statute id"). It turns the informal claim "recall is
near 100%" into measured numbers and surfaces the tail of plausibly-missed
citations for inspection.

It has two independent measurements:

1. COVERAGE PROXY (corpus, upper-bound sweep).
   Over a corpus sample, an INDEPENDENT permissive regex sweep over
   ``decode_body_text`` finds CANDIDATE citation surfaces the detector should
   plausibly catch (id-cites, bare-section cites, EU forms, treaty series,
   statute-name heads). For each statute the sweep candidates are compared to
   the detector's captured ``surface_text`` spans, giving a per-surface-class
   CAPTURE RATE = detected / sweep-candidates.

   HONESTY CONTRACT (read before quoting any number):
   The sweep is an UPPER BOUND on the true citation population — it OVERCOUNTS.
   Many sweep hits are false positives (dates that look like ids, statute-name
   words used non-referentially, section numbers in non-citation context, hits
   the detector legitimately captured under a different surface). Therefore:
       * The capture rate is NOT recall. It LOWER-BOUNDS a precision-adjusted
         recall: true detection-recall >= capture rate, because the denominator
         is inflated by false positives.
       * The uncaptured list is "candidate misses (includes sweep false
         positives), for inspection" — NOT "confirmed misses". Each entry needs
         a human (or the gold harness) to confirm it is a real citation.

2. HAND-LABELED GOLD (in-tree, small, exact).
   A handful of short synthetic statute bodies with a KNOWN reference set
   (including tricky coordinated / range / by-name / EU-nickname / vague cases).
   The detector is run against them and exact precision/recall is reported. This
   gives a true (if small) recall number to complement the proxy.

Output is human text by default and ``--json`` for machine consumption. The proxy
section is corpus-gated: it is skipped (with a clear note) when the farchive is
absent. The gold section runs in any environment (no corpus needed).

Consumes ONLY public API:
    * ``lawvm.finland.references.ref_mention_extractor.extract_all_reference_mentions``
    * ``lawvm.finland.legal_surface.bundle.decode_body_text``
    * ``lawvm.tools.surface_lints._get_store`` / ``_read_body`` / ``_statute_ids``
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable

from lawvm.finland.legal_surface.bundle import decode_body_text
from lawvm.finland.references.ref_mention_extractor import (
    extract_all_reference_mentions,
)

# ---------------------------------------------------------------------------
# Surface classes the proxy sweep recognizes.
# ---------------------------------------------------------------------------
#
# Each class is an INDEPENDENT permissive recognizer. It is deliberately NOT the
# detector's grammar — the whole point is to find candidate surfaces from a
# different angle so the comparison is not circular. Each is bounded (AGENTS.md
# §1.11: no adjacent unbounded repeats) and compiled once at module scope.

CLASS_ID_CITE = "id_cite"  # (NUMBER/YEAR) Finnish statute id parenthetical
CLASS_BARE_SECTION = "bare_section"  # "12 §" bare section cite
CLASS_EU_FORM = "eu_form"  # (EY)/(EU)/(ETY)/(EUVL)/(N:o ...) / N:o NNN/YYYY
CLASS_TREATY = "treaty"  # SopS NN/YYYY treaty-series cite
CLASS_NAME_HEAD = "name_head"  # statute-name word head (...laki/asetus/direktiivi)

ALL_CLASSES = (
    CLASS_ID_CITE,
    CLASS_BARE_SECTION,
    CLASS_EU_FORM,
    CLASS_TREATY,
    CLASS_NAME_HEAD,
)

# (NUMBER/YEAR) — Finnish statute id parenthetical, e.g. (711/2022).
_SWEEP_ID_CITE_RE = re.compile(r"\(\s{0,3}\d{1,6}\s{0,3}/\s{0,3}\d{4}\s{0,3}\)")

# Bare section cite "12 §" / "12 a §". Bounded letter suffix.
_SWEEP_BARE_SECTION_RE = re.compile(r"\b\d{1,4}\s{0,2}[a-zA-Z\xe4\xf6]{0,2}\s{0,2}\xa7")

# EU citation forms:
#   parenthetical instrument markers (EY)/(EU)/(ETY)/(EUVL)
#   "N:o NNN/YYYY" reference-number form (with or without leading marker)
_SWEEP_EU_FORM_RE = re.compile(
    r"\((?:EY|EU|ETY|EUVL)\)"
    r"|N:o\s{0,2}\d{1,6}\s{0,3}/\s{0,3}\d{2,4}",
    re.IGNORECASE,
)

# Treaty series cite "SopS NN/YYYY".
_SWEEP_TREATY_RE = re.compile(r"SopS\s{0,3}\d{1,5}\s{0,3}/\s{0,3}\d{4}", re.IGNORECASE)

# Statute-name head: a word ending in a Finnish law/decree/directive stem in any
# of the common case forms. Bounded stem; alternation of bounded suffix strings.
# This deliberately matches the WORD (e.g. "ympäristönsuojelulain"), the surface
# a by-name / plain-text citation anchors on.
_SWEEP_NAME_HEAD_RE = re.compile(
    r"[a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5\-]{2,60}"
    r"(?:"
    r"laki|lain|lakia|laissa|laista|laiksi|laille|lailla|lailta"
    r"|asetus|asetuksen|asetusta|asetuksessa|asetuksesta|asetukseksi"
    r"|asetuksella|asetukselle|asetukselta"
    r"|direktiivi|direktiivin|direktiiviss\xe4|direktiivist\xe4|direktiiviksi"
    r"|direktiivill\xe4|direktiiville"
    r")\b",
    re.IGNORECASE,
)

_CLASS_PATTERNS: dict[str, re.Pattern[str]] = {
    CLASS_ID_CITE: _SWEEP_ID_CITE_RE,
    CLASS_BARE_SECTION: _SWEEP_BARE_SECTION_RE,
    CLASS_EU_FORM: _SWEEP_EU_FORM_RE,
    CLASS_TREATY: _SWEEP_TREATY_RE,
    CLASS_NAME_HEAD: _SWEEP_NAME_HEAD_RE,
}

# Substring guard per class — fast skip if a marker char/string is absent.
_CLASS_GUARDS: dict[str, tuple[str, ...]] = {
    CLASS_ID_CITE: ("/",),
    CLASS_BARE_SECTION: ("\xa7",),
    CLASS_EU_FORM: ("N:o", "(EY)", "(EU)", "(ETY)", "(EUVL)"),
    CLASS_TREATY: ("SopS",),
    CLASS_NAME_HEAD: ("lai", "lak", "aset", "direktiiv"),
}

_WS_RE = re.compile(r"\s+")

# EU target ids are encoded "eu/TYPE/YEAR/NUMBER"; the citation surface in text
# is "NUMBER/YEAR". This recovers the surface form so EU detections (whose
# surface_text the EU lane leaves empty) are still matchable against the sweep.
_EU_TARGET_RE = re.compile(r"^eu/[a-z]+/(\d{4})/(\d{1,6})$", re.IGNORECASE)


def _norm(s: str) -> str:
    """Whitespace-collapsed, lowercased surface for robust containment match."""
    return _WS_RE.sub(" ", s).strip().lower()


@dataclass(frozen=True)
class SweepHit:
    """One candidate citation surface found by the proxy sweep."""

    surface_class: str
    text: str
    start: int  # char offset into decoded body text
    context: str  # short snippet around the hit


# ---------------------------------------------------------------------------
# Proxy sweep
# ---------------------------------------------------------------------------


def sweep_candidates(body_text: str) -> list[SweepHit]:
    """Run the independent permissive sweep over decoded body text.

    Returns all candidate citation surfaces (one per regex hit per class). This
    is an UPPER BOUND — it overcounts (dates, non-referential name words, etc.).
    """
    if not body_text:
        return []
    hits: list[SweepHit] = []
    for cls, pat in _CLASS_PATTERNS.items():
        guards = _CLASS_GUARDS.get(cls, ())
        if guards and not any(g in body_text for g in guards):
            continue
        for m in pat.finditer(body_text):
            s, e = m.start(), m.end()
            ctx = body_text[max(0, s - 30) : min(len(body_text), e + 30)]
            ctx = _WS_RE.sub(" ", ctx).strip()
            hits.append(
                SweepHit(surface_class=cls, text=m.group(0), start=s, context=ctx)
            )
    return hits


def _mention_detection_tokens(mentions) -> list[str]:
    """Normalized detection signals for a list of mentions (the captured set).

    Each mention contributes:
      * its normalized ``surface_text`` (when non-empty), AND
      * a recovered ``NUMBER/YEAR`` token from its target statute id — both the
        domestic ``NUMBER/YEAR`` form and the EU ``eu/TYPE/YEAR/NUMBER`` form.

    The target-id token is what makes the comparison robust to the EU lane,
    which emits a real EU detection but leaves ``surface_text`` empty. Without
    it an EU id-cite the detector genuinely found would be miscounted as a miss.

    Coordinate-space-agnostic: matches on normalized surfaces, not byte/char
    offsets (avoids the byte-vs-char mismatch between ``xml_bytes`` and
    ``decode_body_text``).
    """
    out: list[str] = []
    for m in mentions:
        st = (m.surface_text or "").strip()
        if st:
            out.append(_norm(st))
        tp = m.target_provision_ref
        tid = tp.statute_id if tp is not None else ""
        if not tid:
            continue
        eu = _EU_TARGET_RE.match(tid)
        if eu:
            out.append(f"{int(eu.group(2))}/{eu.group(1)}")  # NUMBER/YEAR
        elif "/" in tid:
            out.append(_norm(tid))
    return out


def _captured_surface_set(xml_bytes: bytes, statute_id: str) -> list[str]:
    """Detector detection-signal set for one statute (surfaces + target ids)."""
    result = extract_all_reference_mentions(xml_bytes, statute_id)
    return _mention_detection_tokens(result.mentions)


def _is_captured(hit: SweepHit, captured_norm: list[str]) -> bool:
    """True if a sweep hit overlaps any detector-captured surface.

    A sweep hit is considered captured when its normalized text is a substring
    of some captured surface OR a captured surface is a substring of the hit.
    (Containment in either direction: the detector surface "lannoitelain
    (711/2022)" contains the sweep id-cite "(711/2022)"; the detector EU surface
    may be a sub-piece of a longer sweep name-head.)
    """
    h = _norm(hit.text)
    if not h:
        return False
    for c in captured_norm:
        if h in c or c in h:
            return True
    return False


@dataclass
class ClassStat:
    surface_class: str
    candidates: int = 0
    captured: int = 0

    @property
    def capture_rate(self) -> float:
        if self.candidates == 0:
            return 1.0
        return self.captured / self.candidates


@dataclass
class ProxyReport:
    statutes_scanned: int
    statutes_with_body: int
    per_class: dict[str, ClassStat]
    # uncaptured candidates: (statute_id, SweepHit)
    uncaptured: list[tuple[str, SweepHit]] = field(default_factory=list)
    errored: list[tuple[str, str]] = field(default_factory=list)

    def overall_rate(self) -> float:
        cand = sum(c.candidates for c in self.per_class.values())
        capt = sum(c.captured for c in self.per_class.values())
        return (capt / cand) if cand else 1.0


def run_proxy(statute_ids: Iterable[str], read_body, max_uncaptured: int = 200) -> ProxyReport:
    """Run the coverage proxy over the given statute ids.

    ``read_body(sid) -> bytes | None`` supplies the body XML (the corpus store
    reader). Errors per statute are recorded in ``errored`` (fail-loud), never
    silently dropped.
    """
    per_class = {c: ClassStat(surface_class=c) for c in ALL_CLASSES}
    report = ProxyReport(
        statutes_scanned=0,
        statutes_with_body=0,
        per_class=per_class,
    )
    for sid in statute_ids:
        report.statutes_scanned += 1
        try:
            xb = read_body(sid)
        except Exception as exc:  # noqa: BLE001 — fail-loud bucket
            report.errored.append((sid, f"read_body: {exc!r}"))
            continue
        if not xb:
            continue
        report.statutes_with_body += 1
        try:
            body = decode_body_text(xb)
            hits = sweep_candidates(body)
            captured = _captured_surface_set(xb, sid)
        except Exception as exc:  # noqa: BLE001 — fail-loud bucket
            report.errored.append((sid, f"scan: {exc!r}"))
            continue
        for hit in hits:
            st = per_class[hit.surface_class]
            st.candidates += 1
            if _is_captured(hit, captured):
                st.captured += 1
            elif len(report.uncaptured) < max_uncaptured:
                report.uncaptured.append((sid, hit))
    return report


# ---------------------------------------------------------------------------
# Hand-labeled gold set
# ---------------------------------------------------------------------------
#
# Each gold case is a short synthetic AKN-ish body whose EXACT reference set is
# known. We label each EXPECTED reference by a normalized substring that must be
# present in SOME detector mention's surface_text (detection = "found that this
# citation exists"). This is a detection check, not a resolution check: we only
# require the surface be detected, not that it maps to a target id.
#
# AKN <p> with the akn namespace so the extractor's <p>-walk finds the text.

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _body(paragraphs: list[str]) -> bytes:
    ps = "".join(f"<p>{p}</p>" for p in paragraphs)
    xml = f'<akomaNtoso xmlns="{_AKN}"><act><body>{ps}</body></act></akomaNtoso>'
    return xml.encode("utf-8")


@dataclass(frozen=True)
class GoldCase:
    name: str
    statute_id: str
    xml_bytes: bytes
    # Each expected reference: a normalized surface substring that MUST appear in
    # some detector mention surface_text for the reference to count as detected.
    expected_surfaces: tuple[str, ...]


def _gold_cases() -> list[GoldCase]:
    return [
        GoldCase(
            name="plain_id_cite",
            statute_id="100/2020",
            xml_bytes=_body([
                "T\xe4ss\xe4 laissa tarkoitetaan lannoitelain (711/2022) 7 "
                "\xa7:ss\xe4 m\xe4\xe4ritelty\xe4 tuotetta.",
            ]),
            # The plain-text lane should detect the lannoitelain (711/2022) cite.
            expected_surfaces=("711/2022",),
        ),
        GoldCase(
            name="coordinated_and_range_sections",
            statute_id="101/2020",
            xml_bytes=_body([
                "Mit\xe4 elintarvikelain (297/2021) 6 ja 8 \xa7:ss\xe4 "
                "s\xe4\xe4det\xe4\xe4n, sovelletaan my\xf6s 10–12 \xa7:n "
                "mukaisiin tilanteisiin.",
            ]),
            # The cross-statute id cite must be detected. Coordinated/range
            # section handling is exercised; we require the act-id detection.
            expected_surfaces=("297/2021",),
        ),
        GoldCase(
            name="by_name_no_id",
            statute_id="102/2020",
            xml_bytes=_body([
                "Hallintolaissa s\xe4\xe4det\xe4\xe4n menettelyst\xe4, jota "
                "noudatetaan t\xe4t\xe4 lakia sovellettaessa.",
            ]),
            # By-name reference with NO parenthetical id ("Hallintolaissa").
            expected_surfaces=("hallintolai",),
        ),
        GoldCase(
            name="eu_reference_number",
            statute_id="103/2020",
            xml_bytes=_body([
                "Sovelletaan Euroopan parlamentin ja neuvoston asetusta (EU) "
                "N:o 1169/2011 elintarviketietojen antamisesta kuluttajille.",
            ]),
            # EU reference-number form must be detected by the EU lane.
            expected_surfaces=("1169/2011",),
        ),
        GoldCase(
            name="treaty_and_vague",
            statute_id="104/2020",
            xml_bytes=_body([
                "Yleissopimus (SopS 19/1956) on voimassa. Lis\xe4ksi "
                "noudatetaan, mit\xe4 muussa laissa s\xe4\xe4det\xe4\xe4n.",
            ]),
            # Treaty-series cite + a vague "muussa laissa" marker.
            expected_surfaces=("sops 19/1956", "muussa laissa"),
        ),
    ]


@dataclass
class GoldStat:
    name: str
    expected: int
    detected: int
    missed: list[str]  # expected surfaces not found
    n_mentions: int  # total mentions the detector emitted (for precision view)


@dataclass
class GoldReport:
    cases: list[GoldStat]

    @property
    def total_expected(self) -> int:
        return sum(c.expected for c in self.cases)

    @property
    def total_detected(self) -> int:
        return sum(c.detected for c in self.cases)

    @property
    def recall(self) -> float:
        te = self.total_expected
        return (self.total_detected / te) if te else 1.0


def run_gold() -> GoldReport:
    """Run the detector against the hand-labeled gold set; exact recall."""
    cases: list[GoldStat] = []
    for gc in _gold_cases():
        result = extract_all_reference_mentions(gc.xml_bytes, gc.statute_id)
        surfaces = [s for s in _mention_detection_tokens(result.mentions) if s]
        detected = 0
        missed: list[str] = []
        for exp in gc.expected_surfaces:
            en = _norm(exp)
            if any(en in s or s in en for s in surfaces):
                detected += 1
            else:
                missed.append(exp)
        cases.append(
            GoldStat(
                name=gc.name,
                expected=len(gc.expected_surfaces),
                detected=detected,
                missed=missed,
                n_mentions=len(surfaces),
            )
        )
    return GoldReport(cases=cases)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _proxy_to_dict(report: ProxyReport, top_n: int) -> dict:
    return {
        "statutes_scanned": report.statutes_scanned,
        "statutes_with_body": report.statutes_with_body,
        "overall_capture_rate": round(report.overall_rate(), 4),
        "per_class": {
            cls: {
                "candidates": st.candidates,
                "captured": st.captured,
                "capture_rate": round(st.capture_rate, 4),
            }
            for cls, st in report.per_class.items()
        },
        "uncaptured_top": [
            {
                "statute_id": sid,
                "surface_class": hit.surface_class,
                "text": hit.text,
                "context": hit.context,
            }
            for sid, hit in report.uncaptured[:top_n]
        ],
        "uncaptured_class_histogram": dict(
            collections.Counter(hit.surface_class for _, hit in report.uncaptured)
        ),
        "errored": [{"statute_id": sid, "error": err} for sid, err in report.errored],
        "caveat": (
            "Capture rate LOWER-BOUNDS precision-adjusted detection-recall: the "
            "sweep OVERCOUNTS (false positives inflate the denominator), so true "
            "recall >= capture rate. uncaptured_top is 'candidate misses "
            "(includes sweep false positives), for inspection', NOT confirmed "
            "misses."
        ),
    }


def _gold_to_dict(report: GoldReport) -> dict:
    return {
        "total_expected": report.total_expected,
        "total_detected": report.total_detected,
        "recall": round(report.recall, 4),
        "cases": [
            {
                "name": c.name,
                "expected": c.expected,
                "detected": c.detected,
                "missed": c.missed,
                "n_mentions": c.n_mentions,
            }
            for c in report.cases
        ],
    }


def _print_gold(report: GoldReport) -> None:
    print("=== HAND-LABELED GOLD (exact detection recall) ===")
    for c in report.cases:
        flag = "" if not c.missed else f"  MISSED: {c.missed}"
        print(
            f"  {c.name:<32} detected {c.detected}/{c.expected}"
            f"  (mentions={c.n_mentions}){flag}"
        )
    print(
        f"  GOLD RECALL = {report.total_detected}/{report.total_expected} "
        f"= {report.recall:.1%}"
    )
    print()


def _print_proxy(report: ProxyReport, top_n: int) -> None:
    print("=== COVERAGE PROXY (corpus, upper-bound sweep) ===")
    print(
        f"  statutes scanned={report.statutes_scanned} "
        f"with_body={report.statutes_with_body} errored={len(report.errored)}"
    )
    print("  per-surface-class CAPTURE RATE (= detected / sweep-candidates):")
    for cls in ALL_CLASSES:
        st = report.per_class[cls]
        print(
            f"    {cls:<14} {st.captured:>7}/{st.candidates:<7} "
            f"= {st.capture_rate:.1%}"
        )
    print(f"  OVERALL capture rate = {report.overall_rate():.1%}")
    print()
    print(
        "  CAVEAT: capture rate LOWER-BOUNDS precision-adjusted recall "
        "(sweep overcounts; true recall >= this)."
    )
    hist = collections.Counter(hit.surface_class for _, hit in report.uncaptured)
    print(
        "  uncaptured candidate class histogram "
        f"(includes sweep false positives): {dict(hist)}"
    )
    print(
        f"  top {top_n} candidate misses (includes sweep false positives), "
        "for inspection:"
    )
    for sid, hit in report.uncaptured[:top_n]:
        print(f"    [{hit.surface_class}] {sid}: {hit.text!r}  …{hit.context}…")
    if report.errored:
        print("  errored statutes:")
        for sid, err in report.errored[:20]:
            print(f"    {sid}: {err}")
    print()


def _read_body_via_store():
    """Return a ``read_body(sid)`` closure backed by the corpus store.

    Imports the archive-guarded helpers from ``surface_lints`` lazily so the
    gold-only path needs no corpus. Returns None if the archive is absent.
    """
    from lawvm.tools.surface_lints import _get_store, _read_body

    try:
        store = _get_store()
    except Exception:
        return None, None

    def read_body(sid: str):
        return _read_body(store, sid)

    return store, read_body


def _statute_sample(store, limit: int) -> list[str]:
    ids = store.list_statute_ids()
    return ids[:limit] if limit else ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="recall-audit",
        description=(
            "Detection-recall audit for Finnish reference extraction: a corpus "
            "upper-bound coverage proxy + a hand-labeled gold recall number."
        ),
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=200,
        help="Number of statutes to sample for the corpus proxy (0 = all).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=40,
        help="How many uncaptured candidate misses to surface.",
    )
    parser.add_argument(
        "--max-uncaptured",
        type=int,
        default=400,
        help="Cap on retained uncaptured candidates (memory bound).",
    )
    parser.add_argument(
        "--gold-only",
        action="store_true",
        help="Run only the gold set (no corpus needed).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    gold = run_gold()

    proxy: ProxyReport | None = None
    proxy_skipped_reason: str | None = None
    if not args.gold_only:
        store, read_body = _read_body_via_store()
        if store is None or read_body is None:
            proxy_skipped_reason = "corpus archive absent (LAWVM_CANONICAL_DATA_ROOT)"
        else:
            try:
                ids = _statute_sample(store, args.sample)
            except Exception as exc:  # noqa: BLE001
                proxy_skipped_reason = f"could not list statute ids: {exc!r}"
                ids = []
            if proxy_skipped_reason is None:
                proxy = run_proxy(
                    ids, read_body, max_uncaptured=args.max_uncaptured
                )

    if args.json:
        out = {
            "gold": _gold_to_dict(gold),
            "proxy": _proxy_to_dict(proxy, args.top) if proxy else None,
            "proxy_skipped_reason": proxy_skipped_reason,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    _print_gold(gold)
    if proxy is not None:
        _print_proxy(proxy, args.top)
    elif not args.gold_only:
        print(f"=== COVERAGE PROXY SKIPPED: {proxy_skipped_reason} ===")
        print("  (run with LAWVM_CANONICAL_DATA_ROOT pointing at the corpus root)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
