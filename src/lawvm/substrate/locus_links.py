"""Implied links between LOCUS documents — citations + model-code harmonization.

This is the demo centerpiece (the substrate as a GRAPH, not islands). It is
purely ADDITIVE: it reads the rows the :mod:`lawvm.substrate.locus` adapter
already consumes, induces addresses with the SAME stack fold, and emits links
into the reserved ``edges/`` layer using the existing content-addressed
resolution machinery (:func:`lawvm.substrate.corpus.make_cross_work_resolution`).

Two flavors:

* **(2a) Citations.** Scan ``content`` + ``header`` for reference expressions
  (``Section X.Y.Z``, ``§ X.Y``, ``Chapter N``, ``N U.S.C. § M``, ``N C.F.R.``).
  An INTERNAL reference (a dotted/dashed section number that exists as a claimed
  leaf address in the SAME work) resolves to that address — a real
  ``reference_resolution`` overlay with a content-addressed ``resolution_id``.
  An EXTERNAL reference (US Code / CFR / another body of law) is kept as a TYPED
  unresolved target — never silently dropped, never coerced.

* **(2b) Harmonization.** US municipal codes are largely copied from model codes.
  :func:`measure_harmonization` measures cross-municipality overlap at corpus
  scale via duckdb. The SURPRISE the measurement surfaces: full-PROVISION text is
  rarely byte-identical (cities interpolate local names/dates/amounts), but the
  section-TITLE skeleton is massively shared — that is where the model-code
  fingerprint lives.

Honesty backstop (the recall-max discipline): every extracted reference is typed
(``internal_section`` / ``external_usc`` / ``narrative`` / …); an internal ref
that does NOT resolve to a known address is a typed ``unresolved_internal``
target, not a phantom edge. Heuristic harmonization granularity (title vs body)
is reported explicitly, never collapsed into one misleading number.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from lawvm.substrate.locus import (
    AddressInducer,
    LocusRow,
    _segment_kind,
)

# --------------------------------------------------------------------------- #
# Reference expression extraction                                              #
# --------------------------------------------------------------------------- #

# Internal section refs: a dotted (``5.30.090``) or dash-dotted (``38-1014``)
# number, optionally behind ``Section`` / ``Sec.`` / ``§``. Whitespace between the
# label and the number may include newlines (``Section\n\n2.36.010``).
_REF_SECTION = re.compile(
    r"(?:§+|\b[Ss]ec(?:tion|\.)?)\s*"  # label
    r"([0-9]+(?:[.\-][0-9]+)+)\b"  # the dotted/dashed number
)
# A bare dotted number cited mid-sentence (``as provided in 5.30.090``) — lower
# precision, so only counted when preceded by a citation verb (see _CITE_VERB).
_REF_BARE_DOTTED = re.compile(r"\b([0-9]+(?:[.\-][0-9]+){1,})\b")
_CITE_VERB = re.compile(
    r"(?:as (?:provided|set forth|defined|described|required|authorized) in|"
    r"subject to|pursuant to|in accordance with|under|see)\s*$",
    re.IGNORECASE,
)
# Word-container refs (``Chapter 5``, ``Article II``, ``Title 9``).
_REF_CONTAINER = re.compile(
    r"\b(chapter|article|title|part|division)\s+([0-9]+|[IVXLC]+)\b",
    re.IGNORECASE,
)
# External: US Code, CFR, state statutes / general statutes.
_REF_USC = re.compile(r"\b(\d+)\s+U\.?\s?S\.?\s?C\.?(?:\s*§+\s*([\w.\-]+))?", re.IGNORECASE)
_REF_CFR = re.compile(r"\b(\d+)\s+C\.?\s?F\.?\s?R\.?(?:\s*§+\s*([\w.\-]+))?", re.IGNORECASE)
_REF_STATE_LAW = re.compile(
    r"\b([A-Z][a-z]+\.?\s+(?:Rev\.?\s+)?(?:Gen\.?\s+)?Stat(?:utes|\.)?(?:\s+§+\s*[\w.\-]+)?)",
)


@dataclass(frozen=True, slots=True)
class ReferenceExpr:
    """One reference expression scanned from a row's text, with a typed target."""

    source_row_index: int
    expr_text: str
    target_kind: str  # internal_section | internal_container | external_usc | external_cfr | external_state_law
    target_token: str  # the normalized target (e.g. "5.30.090", "26 §501")

    @property
    def is_internal(self) -> bool:
        return self.target_kind.startswith("internal_")


def _normalize_dotted(token: str) -> str:
    """Normalize a cited section number to its dotted-address comparison form.

    Dash separators are mapped to dots (the adapter does the same so ``1-2-1``
    and ``1.2.1`` share an address shape), and surrounding whitespace removed.
    """
    return re.sub(r"\s+", "", token).replace("-", ".")


def _address_path_for(dotted: str) -> str:
    """Render a dotted number as the canonical ``title:1/chapter:05/...`` path."""
    parts = dotted.split(".")
    return "/".join(f"{_segment_kind(i)}:{p}" for i, p in enumerate(parts))


def extract_references(text: str | None, row_index: int) -> list[ReferenceExpr]:
    """Extract typed reference expressions from one row's text.

    Returns intra/inter-code references (internal section/container) and external
    references (US Code / CFR / state statutes). The caller resolves the internal
    ones against the work's claimed-leaf address set; external ones stay typed
    unresolved targets (never coerced into an internal address).
    """
    if not text:
        return []
    out: list[ReferenceExpr] = []
    seen: set[tuple[str, str]] = set()

    def add(expr: str, kind: str, token: str) -> None:
        key = (kind, token)
        if key in seen:
            return
        seen.add(key)
        out.append(ReferenceExpr(row_index, expr.strip(), kind, token))

    for m in _REF_SECTION.finditer(text):
        add(m.group(0), "internal_section", _normalize_dotted(m.group(1)))
    # Bare dotted numbers only when a citation verb immediately precedes them.
    for m in _REF_BARE_DOTTED.finditer(text):
        prefix = text[: m.start()]
        if _CITE_VERB.search(prefix[-40:]):
            add(m.group(0), "internal_section", _normalize_dotted(m.group(1)))
    for m in _REF_CONTAINER.finditer(text):
        kind = m.group(1).lower()
        add(m.group(0), "internal_container", f"{kind}:{m.group(2)}")
    for m in _REF_USC.finditer(text):
        add(m.group(0), "external_usc", f"{m.group(1)} USC §{m.group(2) or '?'}")
    for m in _REF_CFR.finditer(text):
        add(m.group(0), "external_cfr", f"{m.group(1)} CFR §{m.group(2) or '?'}")
    for m in _REF_STATE_LAW.finditer(text):
        add(m.group(0), "external_state_law", re.sub(r"\s+", " ", m.group(1)).strip())
    return out


# --------------------------------------------------------------------------- #
# Per-work citation resolution                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class WorkCitationResult:
    """The citation links found + resolved for one work."""

    work_id: str
    n_rows: int
    refs_total: int
    refs_by_kind: dict[str, int] = field(default_factory=dict)
    internal_refs: int = 0
    internal_resolved: int = 0
    external_refs: int = 0
    resolutions: list[dict[str, Any]] = field(default_factory=list)  # lawvm.overlay.v1 bodies
    unresolved_internal_tokens: Counter = field(default_factory=Counter)
    most_referenced: list[tuple[str, int]] = field(default_factory=list)

    @property
    def internal_resolve_rate(self) -> float:
        return self.internal_resolved / self.internal_refs if self.internal_refs else 0.0


def _claimed_leaf_addresses(rows: list[LocusRow]) -> dict[str, str]:
    """Run the SAME stack fold over a work's rows → {address_path: address_path}.

    Returns the set of leaf addresses the adapter would claim (first-wins on
    duplicates), so a citation's internal target can be resolved against exactly
    the addresses that exist in the emitted pack. Keyed by the canonical address
    path string (the resolution target identity).
    """
    inducer = AddressInducer()
    claimed: dict[str, str] = {}
    for row in rows:
        induced = inducer.induce(row.header)
        if induced is None:
            continue
        path = induced.address_path
        if path not in claimed:
            claimed[path] = path
    return claimed


def resolve_work_citations(
    work_id: str,
    rows: list[LocusRow],
    corpus_version: str,
) -> WorkCitationResult:
    """Scan one work for citations, resolve the internal ones to its addresses.

    An internal section ref whose dotted number matches a claimed leaf address in
    THIS work resolves to that address (a real cross-/intra-work
    ``reference_resolution`` overlay). Container refs resolve when the container
    path exists. External refs (US Code / CFR / state law) and internal refs that
    do not match any known address are TYPED-but-unresolved (counted, embedded in
    the result), never coerced into a phantom edge.
    """
    from lawvm.substrate.corpus import make_cross_work_resolution
    from lawvm.substrate.exporter import _struct_node_id

    claimed = _claimed_leaf_addresses(rows)
    # Address-path → struct_node_id (for the resolution target selector).
    # We need the structural_kind; recompute leaf kind from the path's last seg.
    res = WorkCitationResult(work_id=work_id, n_rows=len(rows), refs_total=0)
    by_kind: Counter = Counter()
    target_hits: Counter = Counter()

    # Source address for each row (the citing provision), for the resolution anchor.
    inducer = AddressInducer()
    row_addr: dict[int, str] = {}
    for row in rows:
        induced = inducer.induce(row.header)
        if induced is not None:
            row_addr[row.row_index] = induced.address_path

    for row in rows:
        refs = extract_references(row.content, row.row_index)
        refs += extract_references(row.header, row.row_index)
        for ref in refs:
            res.refs_total += 1
            by_kind[ref.target_kind] += 1
            if ref.is_internal:
                res.internal_refs += 1
                target_path: str | None = None
                if ref.target_kind == "internal_section":
                    target_path = _address_path_for(ref.target_token)
                elif ref.target_kind == "internal_container":
                    kind, _, val = ref.target_token.partition(":")
                    target_path = f"{kind}:{val}"
                if target_path is not None and target_path in claimed:
                    res.internal_resolved += 1
                    target_hits[target_path] += 1
                    src_path = row_addr.get(row.row_index, "")
                    src_kind = src_path.rsplit("/", 1)[-1].split(":", 1)[0] if src_path else "section"
                    tgt_kind = target_path.rsplit("/", 1)[-1].split(":", 1)[0]
                    from lawvm.substrate.corpus import WorkAnchor

                    body = make_cross_work_resolution(
                        source=WorkAnchor(
                            work_id=work_id,
                            struct_node_id=(
                                _struct_node_id(work_id, src_path, src_kind) if src_path else ""
                            ),
                            address=src_path,
                        ),
                        target=WorkAnchor(
                            work_id=work_id,
                            struct_node_id=_struct_node_id(work_id, target_path, tgt_kind),
                            address=target_path,
                        ),
                        surface_expr_text=ref.expr_text,
                        corpus_version=corpus_version,
                    )
                    res.resolutions.append(body)
                else:
                    res.unresolved_internal_tokens[ref.target_token] += 1
            else:
                res.external_refs += 1

    res.refs_by_kind = dict(by_kind)
    res.most_referenced = target_hits.most_common(10)
    return res


# --------------------------------------------------------------------------- #
# (2b) Harmonization / model-code structure measurement                        #
# --------------------------------------------------------------------------- #


@dataclass
class HarmonizationReport:
    """Cross-municipality overlap measurement (the model-code fingerprint)."""

    n_works: int
    # full-body provision text
    provisions: int
    distinct_provisions: int
    provision_dedup: float
    shared_provisions: int
    shared_provision_pct: float
    # section-title skeleton (where the model-code structure actually lives)
    titles: int
    distinct_titles: int
    title_dedup: float
    shared_titles: int
    shared_title_pct: float
    top_title_clusters: list[tuple[str, int, int]] = field(default_factory=list)
    top_provision_clusters: list[tuple[str, int, int]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"works={self.n_works} | "
            f"PROVISION-text: {self.provisions} → {self.distinct_provisions} distinct "
            f"({self.provision_dedup:.2f}x), shared={self.shared_provision_pct:.1f}% | "
            f"SECTION-TITLE: {self.titles} → {self.distinct_titles} distinct "
            f"({self.title_dedup:.2f}x), shared={self.shared_title_pct:.1f}%"
        )


def measure_harmonization(
    parquet_glob: str,
    *,
    threads: int = 8,
    top_n: int = 12,
) -> HarmonizationReport:
    """Measure cross-municipality content overlap at corpus scale (2b), via duckdb.

    Two granularities, because the model-code fingerprint lives at one and not the
    other (the surprise this measurement surfaces):

    * **provision-text**: ``md5(content)`` over substantive non-trivial rows —
      how many WHOLE provisions are byte-identical across >=2 works;
    * **section-title**: the header with its leading number/label stripped and
      upper-cased — how many section TITLES recur across >=2 works.

    Returns the dedup ratio + shared-% at both granularities and the biggest
    shared clusters (a title/provision and how many distinct municipalities run
    it). All computed in duckdb (corpus-scale; nothing held in Python memory).
    """
    import duckdb

    con = duckdb.connect()
    con.execute("SET enable_progress_bar=false")
    con.execute(f"PRAGMA threads={int(threads)}")
    g = f"read_parquet('{parquet_glob}')"
    wk = "(state||'|'||source_jurisdiction_type||'|'||coalesce(city,'')||'|'||coalesce(county,''))"

    n_works = con.execute(f"SELECT count(DISTINCT {wk}) FROM {g}").fetchone()[0]

    # -- provision-text granularity ----------------------------------------- #
    con.execute(
        f"""CREATE TEMP TABLE _prov AS
        SELECT md5(content) AS h, content, {wk} AS wk
        FROM {g}
        WHERE is_substantive AND content IS NOT NULL AND length(trim(content)) > 30"""
    )
    provisions = con.execute("SELECT count(*) FROM _prov").fetchone()[0]
    con.execute(
        """CREATE TEMP TABLE _provc AS
        SELECT h, any_value(content) AS sample, count(*) nr, count(DISTINCT wk) nw
        FROM _prov GROUP BY h"""
    )
    distinct_provisions = con.execute("SELECT count(*) FROM _provc").fetchone()[0]
    shared_provisions = con.execute(
        "SELECT coalesce(sum(nr),0) FROM _provc WHERE nw>=2"
    ).fetchone()[0]
    top_prov = con.execute(
        f"SELECT sample, nw, nr FROM _provc WHERE nw>=2 ORDER BY nw DESC, nr DESC LIMIT {int(top_n)}"
    ).fetchall()

    # -- section-title granularity ------------------------------------------- #
    title_expr = (
        r"upper(trim(regexp_replace(regexp_replace(header,'^#+\s*',''),"
        r"'^(§+|[Ss]ec(tion|\.)?)?\s*[0-9][0-9.\-]*\.?\s*','')))"
    )
    con.execute(
        f"""CREATE TEMP TABLE _ttl AS
        SELECT md5({title_expr}) AS h, {title_expr} AS title, {wk} AS wk
        FROM {g}
        WHERE is_substantive AND header IS NOT NULL AND length(trim(header)) > 3"""
    )
    titles = con.execute("SELECT count(*) FROM _ttl").fetchone()[0]
    con.execute(
        """CREATE TEMP TABLE _ttlc AS
        SELECT h, any_value(title) AS sample, count(*) nr, count(DISTINCT wk) nw
        FROM _ttl GROUP BY h"""
    )
    distinct_titles = con.execute("SELECT count(*) FROM _ttlc").fetchone()[0]
    shared_titles = con.execute(
        "SELECT coalesce(sum(nr),0) FROM _ttlc WHERE nw>=2"
    ).fetchone()[0]
    top_ttl = con.execute(
        f"SELECT sample, nw, nr FROM _ttlc WHERE nw>=2 AND length(sample)>2 "
        f"ORDER BY nw DESC, nr DESC LIMIT {int(top_n)}"
    ).fetchall()
    con.close()

    return HarmonizationReport(
        n_works=n_works,
        provisions=provisions,
        distinct_provisions=distinct_provisions,
        provision_dedup=provisions / distinct_provisions if distinct_provisions else 0.0,
        shared_provisions=shared_provisions,
        shared_provision_pct=100.0 * shared_provisions / provisions if provisions else 0.0,
        titles=titles,
        distinct_titles=distinct_titles,
        title_dedup=titles / distinct_titles if distinct_titles else 0.0,
        shared_titles=shared_titles,
        shared_title_pct=100.0 * shared_titles / titles if titles else 0.0,
        top_title_clusters=[(str(s or ""), int(nw), int(nr)) for s, nw, nr in top_ttl],
        top_provision_clusters=[(str(s or "")[:120], int(nw), int(nr)) for s, nw, nr in top_prov],
    )
