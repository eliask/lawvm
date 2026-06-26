"""``eu-ref-report`` — corpus-wide EU-directive / CELEX reference SURFACE report (fi).

WHAT THIS IS. A read-only corpus scan that surfaces, as a category, the Finnish
statutes whose body text REFERENCES an EU instrument (directive / regulation /
decision …) — counts, witnesses, and, where determinable, the transposition
relationship (the FI act DECLARES it transposes a named directive) bound to a
CELEX. It consumes the EXISTING deterministic EU extractors — it adds no new
recognition authority:

* TRANSPOSITION DECLARATIONS — :func:`recognize_transposition_claims`
  (``lawvm.finland.references.eu_transposition``) finds the act's own verbal claim
  to transpose a named directive ("… direktiivin täytäntöönpanemiseksi") and binds
  the directive to a CELEX via the deterministic ``eu_nickname`` registry. A bound
  claim is an evidenced FI→EU ``transposes`` relation; an unbound named directive
  is still surfaced (tag-don't-guess: ``statute_only`` / ``ambiguous``), never
  dropped, never fabricated.

* GENERAL EU-INSTRUMENT CITATIONS — :func:`recognize_eu_acts` /
  :func:`recognize_celex` (``lawvm.finland.references.eu_reference``, cross-ref
  dialect) find every cited EU act span ((EY/EU) N:o NNNN/YYYY, year-first, and
  bare CELEX forms). This is the broader "this FI act points at an EU instrument"
  surface — a regulation cited as applicable law, a directive named in a
  recital-style cross-reference, etc.

HONESTY BOUNDARY (load-bearing — feeds an external demo):

* Every count is a SURFACE fact about the published FI statute text: "the body of
  act X contains a recognised citation to EU instrument Y". It is NOT a claim that
  the FI act correctly / completely transposes or complies with the EU instrument
  — conformance is legal interpretation, outside the oracle.
* A transposition edge means "the FI act SAYS it transposes this directive", not a
  verified conformance. CELEX binding is via the curated ``eu_nickname`` registry;
  an unbound directive is reported with its surface and an honest ``unbound``
  status, never a guessed CELEX.
* The general EU-citation recognizer is a textual matcher over the consolidated
  body; an embedded-repeal provenance span (an EU act named only as the object of
  a repeal) is tagged ``repealed_embedded`` by the underlying recognizer and is
  reported separately from primary citations so it is never miscounted as an
  operative reference.
* This is a recognition SURFACE, not a complete EU-relationship ledger: a FI act
  may reference an EU instrument in a form the deterministic recognizers do not
  cover; such a reference is simply absent here, not denied. The report states the
  scanned slice so a slice is never mistaken for the whole corpus.
"""
from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from lawvm.finland.references.eu_reference import (
    DIALECT_CROSS_REF,
    recognize_celex,
    recognize_eu_acts,
)
from lawvm.finland.references.eu_transposition import (
    TranspositionStatus,
    recognize_transposition_claims,
    transposition_deadline,
)
from lawvm.substrate.canonical_json import JsonValue, nfc

_SCHEMA_EU_REF_REPORT = "lawvm.eu_reference_report.v1"


class _StoreLike(Protocol):
    def list_statute_ids(self) -> list[str]: ...
    def read_oracle(self, sid: str, /) -> Optional[bytes]: ...
    def read_source(self, sid: str, /) -> Optional[bytes]: ...
    def read_amendment(self, sid: str, /) -> Optional[bytes]: ...


def _read_body(store: _StoreLike, sid: str) -> Optional[bytes]:
    """Best-available body for a statute (oracle > source > amendment).

    Mirrors the corpus surface build's body preference so the EU scan reads the
    same text the rest of the FI layer reasons over.
    """
    for reader in (store.read_oracle, store.read_source, store.read_amendment):
        try:
            xb = reader(sid)
        except Exception:
            xb = None
        if xb:
            return xb
    return None


@dataclass(frozen=True, slots=True)
class TranspositionWitness:
    """One FI act DECLARING it transposes a named directive (CELEX where bound)."""

    citing_statute_id: str
    directive_celex: Optional[str]
    directive_surface: str
    binding_status: str
    transposition_deadline: Optional[str]
    claim_surface: str

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "citing_statute_id": nfc(self.citing_statute_id),
            "directive_celex": self.directive_celex,
            "directive_surface": nfc(self.directive_surface),
            "binding_status": self.binding_status,
            "transposition_deadline": self.transposition_deadline,
            "claim_surface": nfc(self.claim_surface),
        }


@dataclass(frozen=True, slots=True)
class EuCitationWitness:
    """One FI act citing an EU instrument in its body (general cross-reference)."""

    citing_statute_id: str
    celex: Optional[str]
    eu_form: Optional[str]
    eu_number: str
    eu_year: str
    raw: str

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "citing_statute_id": nfc(self.citing_statute_id),
            "celex": self.celex,
            "eu_form": self.eu_form,
            "eu_number": nfc(self.eu_number),
            "eu_year": nfc(self.eu_year),
            "raw": nfc(self.raw),
        }


@dataclass(frozen=True, slots=True)
class EuReferenceReport:
    """``lawvm.eu_reference_report.v1`` — the corpus-wide EU-reference surface.

    Fields:

    * ``statutes_scanned`` — FI acts whose body was read.
    * ``transposition_acts`` / ``transposition_claims`` — distinct FI acts that
      declare a transposition / total declared-transposition claims.
    * ``transposition_bound`` / ``transposition_unbound`` — claims whose directive
      bound to exactly one CELEX vs named-but-unbound (ambiguous / statute_only).
    * ``transposition_by_status`` — the binding-status breakdown.
    * ``eu_citation_acts`` / ``eu_citation_spans`` — distinct FI acts citing an EU
      instrument / total recognised primary citation spans (embedded-repeal
      provenance excluded from the operative count, reported separately).
    * ``eu_citation_embedded_repeal_spans`` — EU-act spans named only as the object
      of a repeal (provenance), kept distinct so they are never miscounted.
    * ``celex_spans`` — recognised bare-CELEX citation spans.
    * ``transposition_witnesses`` / ``eu_citation_witnesses`` — capped witness
      lists (full counts always in the scalar fields).
    """

    statutes_scanned: int
    transposition_acts: int
    transposition_claims: int
    transposition_bound: int
    transposition_unbound: int
    transposition_by_status: dict[str, int]
    eu_citation_acts: int
    eu_citation_spans: int
    eu_citation_embedded_repeal_spans: int
    celex_spans: int
    transposition_witnesses: tuple[TranspositionWitness, ...] = field(default_factory=tuple)
    eu_citation_witnesses: tuple[EuCitationWitness, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.transposition_bound + self.transposition_unbound != self.transposition_claims:
            raise ValueError(
                "EuReferenceReport totality violated: bound+unbound="
                f"{self.transposition_bound + self.transposition_unbound} != "
                f"transposition_claims={self.transposition_claims}"
            )

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": _SCHEMA_EU_REF_REPORT,
            "statutes_scanned": self.statutes_scanned,
            "transposition_acts": self.transposition_acts,
            "transposition_claims": self.transposition_claims,
            "transposition_bound": self.transposition_bound,
            "transposition_unbound": self.transposition_unbound,
            "transposition_by_status": dict(sorted(self.transposition_by_status.items())),
            "eu_citation_acts": self.eu_citation_acts,
            "eu_citation_spans": self.eu_citation_spans,
            "eu_citation_embedded_repeal_spans": self.eu_citation_embedded_repeal_spans,
            "celex_spans": self.celex_spans,
            "transposition_witnesses": [w.to_canonical_dict() for w in self.transposition_witnesses],
            "eu_citation_witnesses": [w.to_canonical_dict() for w in self.eu_citation_witnesses],
        }


def build_eu_reference_report(
    store: _StoreLike,
    statute_ids: list[str],
    *,
    max_transposition_witnesses: int = 200,
    max_citation_witnesses: int = 200,
) -> EuReferenceReport:
    """Scan ``statute_ids`` for EU-directive transpositions + EU-instrument citations.

    Deterministic: statutes in input order, claims/spans in recognizer order.
    Witness lists are capped for display; the scalar counts are exact over the
    whole slice (no silent truncation of the counts).
    """
    statutes_scanned = 0
    transposition_acts: set[str] = set()
    transposition_claims = 0
    transposition_bound = 0
    transposition_unbound = 0
    status_counts: Counter[str] = Counter()
    eu_citation_acts: set[str] = set()
    eu_citation_spans = 0
    eu_embedded_spans = 0
    celex_spans = 0
    trans_witnesses: list[TranspositionWitness] = []
    cite_witnesses: list[EuCitationWitness] = []

    for sid in statute_ids:
        xb = _read_body(store, sid)
        if not xb:
            continue
        statutes_scanned += 1
        text = xb.decode("utf-8", errors="replace")

        # --- transposition declarations (CELEX-bound where the registry binds) ---
        for claim in recognize_transposition_claims(text, citing_engine_id=sid):
            transposition_claims += 1
            transposition_acts.add(sid)
            status_counts[claim.transposition_status.value] += 1
            if claim.transposition_status is TranspositionStatus.RESOLVED and claim.directive_celex:
                transposition_bound += 1
            else:
                transposition_unbound += 1
            if len(trans_witnesses) < max_transposition_witnesses:
                deadline = (
                    transposition_deadline(claim.directive_celex)
                    if claim.directive_celex
                    else None
                )
                trans_witnesses.append(
                    TranspositionWitness(
                        citing_statute_id=sid,
                        directive_celex=claim.directive_celex,
                        directive_surface=claim.directive_surface,
                        binding_status=claim.transposition_status.value,
                        transposition_deadline=deadline,
                        claim_surface=claim.claim_surface,
                    )
                )

        # --- general EU-instrument citations (cross-ref dialect) ---
        cited = False
        for ref in recognize_eu_acts(text, dialect=DIALECT_CROSS_REF):
            if ref.role == "repealed_embedded":
                eu_embedded_spans += 1
                continue
            eu_citation_spans += 1
            cited = True
            if len(cite_witnesses) < max_citation_witnesses:
                cite_witnesses.append(
                    EuCitationWitness(
                        citing_statute_id=sid,
                        celex=ref.celex,
                        eu_form=ref.form,
                        eu_number=ref.number,
                        eu_year=ref.year,
                        raw=ref.raw,
                    )
                )
        for cref in recognize_celex(text, dialect=DIALECT_CROSS_REF):
            celex_spans += 1
            cited = True
            if len(cite_witnesses) < max_citation_witnesses:
                cite_witnesses.append(
                    EuCitationWitness(
                        citing_statute_id=sid,
                        celex=cref.celex,
                        eu_form=cref.form,
                        eu_number=cref.number,
                        eu_year=cref.year,
                        raw=cref.raw,
                    )
                )
        if cited:
            eu_citation_acts.add(sid)

    return EuReferenceReport(
        statutes_scanned=statutes_scanned,
        transposition_acts=len(transposition_acts),
        transposition_claims=transposition_claims,
        transposition_bound=transposition_bound,
        transposition_unbound=transposition_unbound,
        transposition_by_status=dict(status_counts),
        eu_citation_acts=len(eu_citation_acts),
        eu_citation_spans=eu_citation_spans,
        eu_citation_embedded_repeal_spans=eu_embedded_spans,
        celex_spans=celex_spans,
        transposition_witnesses=tuple(trans_witnesses),
        eu_citation_witnesses=tuple(cite_witnesses),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_text(report: EuReferenceReport, top: int) -> str:
    lines: list[str] = []
    lines.append("\n=== eu-ref-report (FI statutes referencing EU instruments) ===")
    lines.append(
        "  (surface facts: a recognised citation in the published FI body. A "
        "transposition\n   edge = the FI act DECLARES it transposes a directive — "
        "NOT a verified conformance.)"
    )
    lines.append(f"  FI statutes scanned                 : {report.statutes_scanned}")
    lines.append("\n  EU-directive TRANSPOSITION declarations:")
    lines.append(f"    FI acts declaring a transposition : {report.transposition_acts}")
    lines.append(f"    transposition claims (total)      : {report.transposition_claims}")
    lines.append(f"      CELEX-bound (single directive)  : {report.transposition_bound}")
    lines.append(f"      named but unbound (tag-no-guess): {report.transposition_unbound}")
    if report.transposition_by_status:
        lines.append("    by binding status:")
        for st, n in sorted(report.transposition_by_status.items()):
            lines.append(f"      {n:6}  {st}")
    lines.append("\n  General EU-instrument CITATIONS (cross-ref dialect):")
    lines.append(f"    FI acts citing an EU instrument   : {report.eu_citation_acts}")
    lines.append(f"    primary EU-act citation spans     : {report.eu_citation_spans}")
    lines.append(f"    bare-CELEX citation spans         : {report.celex_spans}")
    lines.append(
        f"    embedded-repeal provenance spans  : "
        f"{report.eu_citation_embedded_repeal_spans} (excluded from operative count)"
    )
    lines.append(f"\n  Transposition witnesses (showing up to {top}):")
    if report.transposition_witnesses:
        for w in report.transposition_witnesses[:top]:
            celex = w.directive_celex or f"(unbound: {w.directive_surface})"
            dl = f", deadline {w.transposition_deadline}" if w.transposition_deadline else ""
            lines.append(
                f"    {w.citing_statute_id} -> {celex}  [{w.binding_status}{dl}]"
            )
    else:
        lines.append("    (none)")
    lines.append(f"\n  EU-citation witnesses (showing up to {top}):")
    if report.eu_citation_witnesses:
        for w in report.eu_citation_witnesses[:top]:
            ident = w.celex or f"({w.eu_form} N:o {w.eu_number}/{w.eu_year})"
            lines.append(f"    {w.citing_statute_id} -> {ident}")
    else:
        lines.append("    (none)")
    return "\n".join(lines)


def _resolve_store() -> Any:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    return TransparentCorpusStore(Farchive(_archive_path(), readonly=True))


def main(args: Any) -> None:
    import json
    import os

    out_path: Optional[str] = getattr(args, "out", None)
    as_json: bool = bool(getattr(args, "json", False))
    top: int = int(getattr(args, "top", 20) or 20)
    limit: Optional[int] = getattr(args, "limit", None)

    store = _resolve_store()
    ids = store.list_statute_ids()
    if limit is not None:
        ids = ids[: int(limit)]
    print(
        f"eu-ref-report: scanning {len(ids)} FI statutes for EU-instrument "
        "references...",
        file=sys.stderr,
    )
    report = build_eu_reference_report(store, ids)

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(report.to_canonical_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(f"eu-ref-report: wrote report -> {out_path}", file=sys.stderr)

    if as_json:
        json.dump(report.to_canonical_dict(), sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return
    print(_render_text(report, top))


__all__ = [
    "EuCitationWitness",
    "EuReferenceReport",
    "TranspositionWitness",
    "build_eu_reference_report",
    "main",
]
