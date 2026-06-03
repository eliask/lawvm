"""lawvm fi-proposal-bundle HE_ID — per-HE typed bundle aggregator.

Composes already-projected Parquet tables (features #1-#5) into a single
JSON bundle per HE.  No new extraction; pure composition over typed
primitives.

Usage:
    lawvm fi-proposal-bundle --he "HE 98/1996 vp"
    lawvm fi-proposal-bundle --he "HE 98/1996 vp" --all
    lawvm fi-proposal-bundle --he "HE 98/1996 vp" --include-atoms --include-signatures
    lawvm fi-proposal-bundle --he "HE/2024/184" --include-law-refs --include-telos

Per AGENTS.md §1.8: if a requested --include-* flag has no data (e.g.
--include-actors on a PDF_WRAPPER HE), return an empty list with a warnings
entry explaining why — not silently omit the key.

Per AGENTS.md §1.9: bundle assembly uses typed dataclass composition, then
serialization.  No hand-built dicts in the core path.

Phase: Emit evidence (AGENTS.md §6 phase 11).
Composed from: #1 fi_he_law_refs, #2 fi_actors, #3 fi_pools, #4 fi_he_corpus
  + fi_he_atoms + fi_he_signatures, #5 sections.parquet telos flag.

Data-dir layout:
    data/fi/v1/
        fi_he_corpus.parquet        <- #4 corpus row
        fi_he_atoms.parquet         <- #4 body atoms
        fi_he_law_refs.parquet      <- #4 law refs (via #1 extractor)
        fi_he_signatures.parquet    <- #4 signatures
    .tmp/projections/
        fi_actors.parquet           <- #2 actor mentions (over enacted statutes)
        fi_pools.parquet            <- #3 pool mentions (over enacted statutes)
        sections.parquet            <- #5 telos flag + section rows
        statutes.parquet            <- replay status per statute
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from lawvm.tools._cli_duckdb import (
    find_source_file,
    require_duckdb,
    source_expr_for_path,
)
from lawvm.tools._cli_output import json_safe


# ---------------------------------------------------------------------------
# HE-ID normalisation
# ---------------------------------------------------------------------------

# Corpus he_id strings use the form "HE 98/1996 vp" (from docNumber).
# CLI users may supply "HE/2024/184" or "HE-184/2024" or "HE 184/2024".
# We normalise the CLI value to match the corpus he_id column via SQL LIKE
# filter (case-insensitive) so the lookup is robust.


def _parse_he_id_variants(raw: str) -> List[str]:
    """Return candidate he_id strings from a user-supplied identifier.

    Supports:
        "HE 98/1996 vp"   -> ["HE 98/1996 vp", "HE 98/1996"]
        "HE/2024/184"     -> ["HE 184/2024 vp", "HE 184/2024"]
        "HE-184/2024"     -> ["HE 184/2024 vp", "HE 184/2024"]
        "HE 184/2024"     -> ["HE 184/2024 vp", "HE 184/2024"]
        "184/2024"        -> ["HE 184/2024 vp", "HE 184/2024"]

    Returns a list of candidates in priority order.  The caller issues a
    SQL OR across all candidates.  Longer/more-specific first.
    """
    s = raw.strip()

    # Detect slash-separated "HE/YEAR/NUMBER" form (e.g. "HE/2024/184")
    if s.upper().startswith("HE/"):
        parts = s[3:].split("/")
        if len(parts) == 2:
            year, num = parts[0].strip(), parts[1].strip()
            return [
                f"HE {num}/{year} vp",
                f"HE {num}/{year}",
            ]

    # Detect "HE-NUMBER/YEAR" form (e.g. "HE-184/2024")
    if s.upper().startswith("HE-"):
        remainder = s[3:]
        if "/" in remainder:
            num, year = remainder.split("/", 1)
            return [
                f"HE {num.strip()}/{year.strip()} vp",
                f"HE {num.strip()}/{year.strip()}",
            ]

    # Already normalised or close ("HE NUMBER/YEAR ..." or "NUMBER/YEAR")
    if "/" in s:
        # Ensure "vp" suffix variant is included
        s_lower = s.lower()
        if s_lower.endswith(" vp"):
            return [s, s[:-3].strip()]
        return [s + " vp", s]

    return [s]


def _build_corpus_lookup_query(corpus_expr: str, candidates: List[str]) -> str:
    """Build a SQL query to find one corpus row matching any candidate he_id."""
    clauses = []
    for c in candidates:
        safe = c.replace("'", "''")
        clauses.append(f"lower(he_id) = lower('{safe}')")
    where = " OR ".join(clauses)
    return (
        f"SELECT * FROM {corpus_expr} "
        f"WHERE {where} "
        f"ORDER BY he_year DESC, he_number DESC "
        f"LIMIT 1"
    )


# ---------------------------------------------------------------------------
# Typed bundle dataclasses (AGENTS.md §1.9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MinistryRef:
    canonical_id: str
    show_as: str


@dataclass(frozen=True, slots=True)
class AtomRow:
    atom_id: str
    parent_atom_id: Optional[str]
    atom_type: str
    seq: int
    num: Optional[str]
    heading: Optional[str]
    char_count: int
    source_span_file: Optional[str]


@dataclass(frozen=True, slots=True)
class LawRefRow:
    source_provision_ref_str: Optional[str]
    target_statute_id: Optional[str]
    target_provision_ref_str: Optional[str]
    cite_kind: Optional[str]
    cite_confidence: Optional[str]
    phrase_lemma: Optional[str]


@dataclass(frozen=True, slots=True)
class ActorMentionRow:
    source_statute_id: Optional[str]
    source_provision_ref_str: Optional[str]
    actor_canonical_id: Optional[str]
    actor_canonical_show_as: Optional[str]
    actor_phrase: Optional[str]
    modal_kind: Optional[str]
    resolution_confidence: Optional[str]


@dataclass(frozen=True, slots=True)
class PoolMentionRow:
    source_statute_id: Optional[str]
    source_provision_ref_str: Optional[str]
    pool_canonical_id: Optional[str]
    quantity_phrase: Optional[str]
    quantity_kind: Optional[str]
    resolution_confidence: Optional[str]
    numeric_value: Optional[float]
    unit: Optional[str]


@dataclass(frozen=True, slots=True)
class TelosSectionRow:
    statute_id: str
    section_key: str
    purpose_text_snippet: Optional[str]


@dataclass(frozen=True, slots=True)
class SignatureRow:
    role: Optional[str]
    person: Optional[str]
    signature_order: int


@dataclass
class ProposalBundle:
    """Typed bundle for one Finnish government proposal.

    Per brief: all include-* sections always present as lists (never omitted).
    Absent data emits warnings entries explaining why a list is empty.
    """

    he_id: str
    he_uri: str
    title: str
    ministry: MinistryRef
    structural_tier: str
    is_structured: bool
    date_issued: Optional[str]
    finlex_state: Optional[str]
    atoms: List[AtomRow] = field(default_factory=list)
    law_refs: List[LawRefRow] = field(default_factory=list)
    actor_mentions: List[ActorMentionRow] = field(default_factory=list)
    pool_mentions: List[PoolMentionRow] = field(default_factory=list)
    telos_sections: List[TelosSectionRow] = field(default_factory=list)
    signatures: List[SignatureRow] = field(default_factory=list)
    replay_status: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _row_to_dict(obj: Any) -> Any:
    """Recursively convert a dataclass / list / dict to JSON-serialisable dicts.

    asdict() on an outer dataclass already converts nested dataclasses to plain
    dicts, so we must handle both dataclasses and dicts here.
    """
    if hasattr(obj, "__dataclass_fields__"):
        # Call asdict once at the root; the values are already recursively
        # converted to plain Python types by asdict.
        return {k: _row_to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _row_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_row_to_dict(i) for i in obj]
    return json_safe(obj)


def _bundle_to_json(bundle: ProposalBundle) -> str:
    """Serialise a ProposalBundle to a deterministic JSON string."""
    d = _row_to_dict(bundle)
    return json.dumps(d, indent=2, ensure_ascii=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Scalar helpers
# ---------------------------------------------------------------------------


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _extract_target_statute_ids(law_refs: List[LawRefRow]) -> List[str]:
    """Deduplicate target statute IDs from law_refs, preserving insertion order."""
    seen: set[str] = set()
    out: List[str] = []
    for ref in law_refs:
        sid = ref.target_statute_id
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


# ---------------------------------------------------------------------------
# DuckDB query builders — each table is queried independently
# ---------------------------------------------------------------------------


def _query_corpus(
    con: Any, corpus_expr: str, candidates: List[str]
) -> Optional[Dict[str, Any]]:
    """Fetch the corpus row for this HE.  Returns None if not found."""
    q = _build_corpus_lookup_query(corpus_expr, candidates)
    result = con.execute(q)
    cols = [d[0] for d in result.description]
    rows = result.fetchall()
    if not rows:
        return None
    return dict(zip(cols, rows[0], strict=True))


def _query_atoms(
    con: Any,
    atoms_expr: str,
    he_id: str,
    limit: Optional[int],
) -> List[AtomRow]:
    safe = he_id.replace("'", "''")
    limit_clause = f" LIMIT {limit}" if limit else ""
    q = (
        f"SELECT atom_id, parent_atom_id, atom_type, seq, num, heading, "
        f"char_count, source_span_file "
        f"FROM {atoms_expr} "
        f"WHERE he_id = '{safe}' "
        f"ORDER BY seq"
        f"{limit_clause}"
    )
    result = con.execute(q)
    return [
        AtomRow(
            atom_id=str(r[0]) if r[0] is not None else "",
            parent_atom_id=_str_or_none(r[1]),
            atom_type=str(r[2]) if r[2] is not None else "",
            seq=int(r[3]) if r[3] is not None else 0,
            num=_str_or_none(r[4]),
            heading=_str_or_none(r[5]),
            char_count=int(r[6]) if r[6] is not None else 0,
            source_span_file=_str_or_none(r[7]),
        )
        for r in result.fetchall()
    ]


def _query_law_refs(
    con: Any,
    refs_expr: str,
    he_id: str,
    limit: Optional[int],
) -> List[LawRefRow]:
    safe = he_id.replace("'", "''")
    limit_clause = f" LIMIT {limit}" if limit else ""
    q = (
        f"SELECT source_provision_ref_str, target_statute_id, "
        f"target_provision_ref_str, cite_kind, cite_confidence, phrase_lemma "
        f"FROM {refs_expr} "
        f"WHERE he_id = '{safe}' "
        f"ORDER BY source_provision_ref_str"
        f"{limit_clause}"
    )
    result = con.execute(q)
    return [
        LawRefRow(
            source_provision_ref_str=_str_or_none(r[0]),
            target_statute_id=_str_or_none(r[1]),
            target_provision_ref_str=_str_or_none(r[2]),
            cite_kind=_str_or_none(r[3]),
            cite_confidence=_str_or_none(r[4]),
            phrase_lemma=_str_or_none(r[5]),
        )
        for r in result.fetchall()
    ]


def _query_signatures(
    con: Any,
    sigs_expr: str,
    he_id: str,
) -> List[SignatureRow]:
    safe = he_id.replace("'", "''")
    q = (
        f"SELECT role, person, signature_order "
        f"FROM {sigs_expr} "
        f"WHERE he_id = '{safe}' "
        f"ORDER BY signature_order"
    )
    result = con.execute(q)
    return [
        SignatureRow(
            role=_str_or_none(r[0]),
            person=_str_or_none(r[1]),
            signature_order=int(r[2]) if r[2] is not None else 0,
        )
        for r in result.fetchall()
    ]


def _query_actors(
    con: Any,
    actors_expr: str,
    target_statute_ids: List[str],
    limit: Optional[int],
) -> List[ActorMentionRow]:
    """Query actor mentions for the target statutes referenced by the HE."""
    if not target_statute_ids:
        return []
    id_list = ", ".join(f"'{s.replace(chr(39), chr(39)*2)}'" for s in target_statute_ids)
    limit_clause = f" LIMIT {limit}" if limit else ""
    q = (
        f"SELECT source_statute_id, source_provision_ref_str, actor_canonical_id, "
        f"actor_canonical_show_as, actor_phrase, modal_kind, resolution_confidence "
        f"FROM {actors_expr} "
        f"WHERE source_statute_id IN ({id_list}) "
        f"ORDER BY source_statute_id, source_provision_ref_str, actor_canonical_id"
        f"{limit_clause}"
    )
    result = con.execute(q)
    return [
        ActorMentionRow(
            source_statute_id=_str_or_none(r[0]),
            source_provision_ref_str=_str_or_none(r[1]),
            actor_canonical_id=_str_or_none(r[2]),
            actor_canonical_show_as=_str_or_none(r[3]),
            actor_phrase=_str_or_none(r[4]),
            modal_kind=_str_or_none(r[5]),
            resolution_confidence=_str_or_none(r[6]),
        )
        for r in result.fetchall()
    ]


def _query_pools(
    con: Any,
    pools_expr: str,
    target_statute_ids: List[str],
    limit: Optional[int],
) -> List[PoolMentionRow]:
    """Query pool mentions for the target statutes referenced by the HE."""
    if not target_statute_ids:
        return []
    id_list = ", ".join(f"'{s.replace(chr(39), chr(39)*2)}'" for s in target_statute_ids)
    limit_clause = f" LIMIT {limit}" if limit else ""
    q = (
        f"SELECT source_statute_id, source_provision_ref_str, pool_canonical_id, "
        f"quantity_phrase, quantity_kind, resolution_confidence, numeric_value, unit "
        f"FROM {pools_expr} "
        f"WHERE source_statute_id IN ({id_list}) "
        f"ORDER BY source_statute_id, source_provision_ref_str, quantity_kind"
        f"{limit_clause}"
    )
    result = con.execute(q)
    return [
        PoolMentionRow(
            source_statute_id=_str_or_none(r[0]),
            source_provision_ref_str=_str_or_none(r[1]),
            pool_canonical_id=_str_or_none(r[2]),
            quantity_phrase=_str_or_none(r[3]),
            quantity_kind=_str_or_none(r[4]),
            resolution_confidence=_str_or_none(r[5]),
            numeric_value=float(r[6]) if r[6] is not None else None,
            unit=_str_or_none(r[7]),
        )
        for r in result.fetchall()
    ]


def _query_telos(
    con: Any,
    sections_expr: str,
    target_statute_ids: List[str],
    limit: Optional[int],
) -> Optional[List[TelosSectionRow]]:
    """Query telos-flagged sections for the target statutes.

    Returns None if the is_purpose_section column is absent from the
    projection (feature #5 not yet applied).  Returns [] when no rows match.
    """
    if not target_statute_ids:
        return []

    # Graceful check: does is_purpose_section column exist?
    col_check = con.execute(f"SELECT * FROM {sections_expr} LIMIT 0")
    col_names = [d[0] for d in col_check.description]
    if "is_purpose_section" not in col_names:
        return None  # Signal: feature #5 not applied

    id_list = ", ".join(f"'{s.replace(chr(39), chr(39)*2)}'" for s in target_statute_ids)
    limit_clause = f" LIMIT {limit}" if limit else ""
    q = (
        f"SELECT statute_id, section_key, "
        f"LEFT(purpose_text_snippet, 300) AS purpose_text_snippet "
        f"FROM {sections_expr} "
        f"WHERE is_purpose_section = true AND statute_id IN ({id_list}) "
        f"ORDER BY statute_id, section_key"
        f"{limit_clause}"
    )
    result = con.execute(q)
    return [
        TelosSectionRow(
            statute_id=str(r[0]),
            section_key=str(r[1]),
            purpose_text_snippet=_str_or_none(r[2]),
        )
        for r in result.fetchall()
    ]


def _query_replay_status(
    con: Any,
    statutes_expr: str,
    target_statute_ids: List[str],
) -> Optional[List[tuple]]:
    """Query replay status for the target statutes from statutes.parquet.

    Returns None on query error.  Returns [] if no rows match.
    Returns a list of (statute_id, status, score_or_None) tuples.
    """
    if not target_statute_ids:
        return []

    # Detect available score column
    col_check = con.execute(f"SELECT * FROM {statutes_expr} LIMIT 0")
    col_names = [d[0] for d in col_check.description]
    if "score" in col_names:
        score_col: Optional[str] = "score"
    elif "similarity" in col_names:
        score_col = "similarity"
    else:
        score_col = None

    id_list = ", ".join(f"'{s.replace(chr(39), chr(39)*2)}'" for s in target_statute_ids)
    select = "statute_id, status" + (f", {score_col}" if score_col else "")
    q = (
        f"SELECT {select} FROM {statutes_expr} "
        f"WHERE statute_id IN ({id_list}) "
        f"ORDER BY statute_id"
    )
    result = con.execute(q)
    rows = result.fetchall()
    out = []
    for r in rows:
        statute_id = str(r[0])
        status = str(r[1]) if r[1] is not None else "unknown"
        score = float(r[2]) if len(r) > 2 and r[2] is not None else None
        out.append((statute_id, status, score))
    return out


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------

_NOT_STRUCTURED_TEMPLATE = (
    "HE {he_id!r} is a PDF_WRAPPER (structural_tier={tier!r}); "
    "no body {section} are available from HE body extraction."
)


def assemble_bundle(
    *,
    he_id: str,
    he_id_candidates: List[str],
    include_atoms: bool,
    include_law_refs: bool,
    include_actors: bool,
    include_pools: bool,
    include_telos: bool,
    include_replay_status: bool,
    include_text: str,
    include_signatures: bool,
    limit: Optional[int],
    he_data_dir: str,
    projections_data_dir: str,
) -> ProposalBundle:
    """Assemble a ProposalBundle from projected Parquet tables.

    Parameters
    ----------
    he_id:
        User-supplied HE identifier (used for display only after lookup).
    he_id_candidates:
        Normalised candidate strings for corpus lookup.
    include_*:
        Per AGENTS.md §1.8: even when False, the key is present as [].
        When True but data is missing, returns [] + warning entry.
    he_data_dir:
        Directory containing fi_he_*.parquet (default: data/fi/v1).
    projections_data_dir:
        Directory containing fi_actors.parquet, fi_pools.parquet,
        sections.parquet, statutes.parquet (default: .tmp/projections).
    """
    duckdb = require_duckdb()

    # --- Locate corpus file (always required) ---
    corpus_path = find_source_file(he_data_dir, "fi_he_corpus")
    if corpus_path is None:
        print(
            f"error: fi_he_corpus not found in {he_data_dir}/\n"
            "Run 'lawvm sync-fi-proposals' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    con = duckdb.connect(":memory:")

    # --- Corpus row ---
    corpus_expr = source_expr_for_path(corpus_path)
    corpus_row = _query_corpus(con, corpus_expr, he_id_candidates)
    if corpus_row is None:
        con.close()
        print(
            f"error: no HE found for {he_id!r} in {he_data_dir}/fi_he_corpus\n"
            "Check the HE identifier; expected form: 'HE 98/1996 vp'",
            file=sys.stderr,
        )
        sys.exit(1)

    canonical_he_id: str = str(corpus_row.get("he_id") or he_id)
    is_structured: bool = bool(corpus_row.get("is_structured", False))
    structural_tier: str = str(corpus_row.get("structural_tier") or "unknown")

    ministry = MinistryRef(
        canonical_id=str(corpus_row.get("ministry_canonical_id") or ""),
        show_as=str(corpus_row.get("ministry_show_as") or ""),
    )
    bundle = ProposalBundle(
        he_id=canonical_he_id,
        he_uri=str(corpus_row.get("he_uri") or ""),
        title=str(corpus_row.get("title") or ""),
        ministry=ministry,
        structural_tier=structural_tier,
        is_structured=is_structured,
        date_issued=_str_or_none(corpus_row.get("date_issued")),
        finlex_state=_str_or_none(corpus_row.get("finlex_state")),
    )

    def _not_structured_warn(section: str) -> str:
        return _NOT_STRUCTURED_TEMPLATE.format(
            he_id=canonical_he_id, tier=structural_tier, section=section
        )

    # --- Atoms ---
    if include_atoms:
        if not is_structured:
            bundle.warnings.append(_not_structured_warn("atoms"))
        else:
            atoms_path = find_source_file(he_data_dir, "fi_he_atoms")
            if atoms_path is None:
                bundle.warnings.append(
                    f"atoms: fi_he_atoms not found in {he_data_dir}/; "
                    "run 'lawvm sync-fi-proposals' to rebuild"
                )
            else:
                atoms_expr = source_expr_for_path(atoms_path)
                bundle.atoms = _query_atoms(con, atoms_expr, canonical_he_id, limit)

    # --- Law refs (also fetched when needed for downstream target_statute_ids) ---
    need_law_refs_for_downstream = include_actors or include_pools or include_telos or include_replay_status

    raw_law_refs: List[LawRefRow] = []
    if include_law_refs or need_law_refs_for_downstream:
        if not is_structured:
            if include_law_refs:
                bundle.warnings.append(_not_structured_warn("law_refs"))
        else:
            refs_path = find_source_file(he_data_dir, "fi_he_law_refs")
            if refs_path is None:
                if include_law_refs:
                    bundle.warnings.append(
                        f"law_refs: fi_he_law_refs not found in {he_data_dir}/; "
                        "run 'lawvm sync-fi-proposals' to rebuild"
                    )
            else:
                refs_expr = source_expr_for_path(refs_path)
                raw_law_refs = _query_law_refs(con, refs_expr, canonical_he_id, limit)

    if include_law_refs:
        bundle.law_refs = raw_law_refs

    # Derive target statute IDs for downstream queries
    target_statute_ids = _extract_target_statute_ids(raw_law_refs)

    # --- Actors ---
    if include_actors:
        actors_path = find_source_file(projections_data_dir, "fi_actors")
        if actors_path is None:
            bundle.warnings.append(
                f"actor_mentions: fi_actors not found in {projections_data_dir}/; "
                "run 'lawvm export-projections --include-actors' to build"
            )
        elif not target_statute_ids:
            bundle.warnings.append(
                "actor_mentions: no target statute IDs resolved from law_refs; "
                "cannot query actor mentions (no cross-statute references found)"
            )
        else:
            actors_expr = source_expr_for_path(actors_path)
            bundle.actor_mentions = _query_actors(con, actors_expr, target_statute_ids, limit)

    # --- Pools ---
    if include_pools:
        pools_path = find_source_file(projections_data_dir, "fi_pools")
        if pools_path is None:
            bundle.warnings.append(
                f"pool_mentions: fi_pools not found in {projections_data_dir}/; "
                "run 'lawvm export-projections --include-pools' to build"
            )
        elif not target_statute_ids:
            bundle.warnings.append(
                "pool_mentions: no target statute IDs resolved from law_refs; "
                "cannot query pool mentions"
            )
        else:
            pools_expr = source_expr_for_path(pools_path)
            bundle.pool_mentions = _query_pools(con, pools_expr, target_statute_ids, limit)

    # --- Telos sections ---
    if include_telos:
        sections_path = find_source_file(projections_data_dir, "sections")
        if sections_path is None:
            bundle.warnings.append(
                f"telos_sections: sections.parquet not found in {projections_data_dir}/; "
                "run 'lawvm export-projections' to build"
            )
        elif not target_statute_ids:
            bundle.warnings.append(
                "telos_sections: no target statute IDs resolved from law_refs; "
                "cannot query telos sections"
            )
        else:
            sections_expr = source_expr_for_path(sections_path)
            telos = _query_telos(con, sections_expr, target_statute_ids, limit)
            if telos is None:
                bundle.warnings.append(
                    "telos_sections: 'is_purpose_section' column absent from sections.parquet; "
                    "telos-section flag (feature #5) not applied to this export — "
                    "run 'lawvm export-projections' after updating to a version with telos support"
                )
            else:
                bundle.telos_sections = telos

    # --- Replay status ---
    if include_replay_status:
        statutes_path = find_source_file(projections_data_dir, "statutes")
        if statutes_path is None:
            bundle.warnings.append(
                f"replay_status: statutes.parquet not found in {projections_data_dir}/; "
                "run 'lawvm export-projections' to build"
            )
        elif not target_statute_ids:
            bundle.warnings.append(
                "replay_status: no target statute IDs resolved from law_refs; "
                "cannot determine replay status"
            )
            bundle.replay_status = "no_targets"
        else:
            statutes_expr = source_expr_for_path(statutes_path)
            rs_rows = _query_replay_status(con, statutes_expr, target_statute_ids)
            if rs_rows is None:
                bundle.warnings.append(
                    "replay_status: query failed (statutes.parquet present but unreadable)"
                )
            elif not rs_rows:
                bundle.replay_status = "unknown"
                bundle.warnings.append(
                    "replay_status: target statutes not found in statutes.parquet "
                    "(statutes may not be in bench corpus)"
                )
            else:
                # Aggregate: if all OK → clean; some OK → partial; none OK → diverged
                statuses = [r[1] for r in rs_rows]
                if all(s == "OK" for s in statuses):
                    bundle.replay_status = "clean"
                elif any(s == "OK" for s in statuses):
                    bundle.replay_status = "partial"
                else:
                    bundle.replay_status = "diverged"

    # --- Signatures ---
    if include_signatures:
        if not is_structured:
            bundle.warnings.append(_not_structured_warn("signatures"))
        else:
            sigs_path = find_source_file(he_data_dir, "fi_he_signatures")
            if sigs_path is None:
                bundle.warnings.append(
                    f"signatures: fi_he_signatures not found in {he_data_dir}/; "
                    "run 'lawvm sync-fi-proposals' to rebuild"
                )
            else:
                sigs_expr = source_expr_for_path(sigs_path)
                bundle.signatures = _query_signatures(con, sigs_expr, canonical_he_id)

    # --- include_text: noted but not yet implemented in projection-based path ---
    if include_text != "none":
        bundle.warnings.append(
            f"include_text={include_text!r}: full text rehydration ('affected'/"
            "'before-after') requires live statute replay and is not yet implemented "
            "in the projection-based bundle; set --include-text none to suppress this warning"
        )

    con.close()
    return bundle


# ---------------------------------------------------------------------------
# Main entry point (called from cli.py)
# ---------------------------------------------------------------------------


def run_fi_proposal_bundle(
    *,
    he_id: Optional[str],
    branch_id: Optional[str],
    include_atoms: bool,
    include_law_refs: bool,
    include_actors: bool,
    include_pools: bool,
    include_telos: bool,
    include_replay_status: bool,
    include_text: str,
    include_signatures: bool,
    include_all: bool,
    limit: Optional[int],
    he_data_dir: str,
    projections_data_dir: str,
    output_format: str,
    jurisdiction: str,
) -> None:
    """Run the fi-proposal-bundle command."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm fi-proposal-bundle' only supports 'fi'; got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    raw_id = he_id or branch_id
    if not raw_id:
        print(
            "error: one of --he HE_ID or --branch BRANCH_ID is required",
            file=sys.stderr,
        )
        sys.exit(1)

    if include_all:
        include_atoms = True
        include_law_refs = True
        include_actors = True
        include_pools = True
        include_telos = True
        include_replay_status = True
        include_signatures = True

    candidates = _parse_he_id_variants(raw_id)

    bundle = assemble_bundle(
        he_id=raw_id,
        he_id_candidates=candidates,
        include_atoms=include_atoms,
        include_law_refs=include_law_refs,
        include_actors=include_actors,
        include_pools=include_pools,
        include_telos=include_telos,
        include_replay_status=include_replay_status,
        include_text=include_text,
        include_signatures=include_signatures,
        limit=limit,
        he_data_dir=he_data_dir,
        projections_data_dir=projections_data_dir,
    )

    if output_format in ("json", "jsonl"):
        print(_bundle_to_json(bundle))
    else:
        # table: brief text summary
        print(f"\nHE Bundle: {bundle.he_id}")
        print(f"  URI             : {bundle.he_uri}")
        print(f"  Title           : {bundle.title[:80]}")
        print(f"  Ministry        : {bundle.ministry.show_as or bundle.ministry.canonical_id}")
        print(f"  Structural tier : {bundle.structural_tier} (structured={bundle.is_structured})")
        print(f"  Date issued     : {bundle.date_issued or ''}")
        print(f"  Finlex state    : {bundle.finlex_state or ''}")
        print(f"  Atoms           : {len(bundle.atoms)} rows")
        print(f"  Law refs        : {len(bundle.law_refs)} rows")
        print(f"  Actor mentions  : {len(bundle.actor_mentions)} rows")
        print(f"  Pool mentions   : {len(bundle.pool_mentions)} rows")
        print(f"  Telos sections  : {len(bundle.telos_sections)} rows")
        print(f"  Signatures      : {len(bundle.signatures)} rows")
        print(f"  Replay status   : {bundle.replay_status or '(not requested)'}")
        if bundle.warnings:
            print(f"\n  Warnings ({len(bundle.warnings)}):")
            for w in bundle.warnings:
                print(f"    - {w}")


def main(args: Any) -> None:
    """CLI entry point for lawvm fi-proposal-bundle."""
    run_fi_proposal_bundle(
        he_id=getattr(args, "he_id", None),
        branch_id=getattr(args, "branch_id", None),
        include_atoms=getattr(args, "include_atoms", False),
        include_law_refs=getattr(args, "include_law_refs", False),
        include_actors=getattr(args, "include_actors", False),
        include_pools=getattr(args, "include_pools", False),
        include_telos=getattr(args, "include_telos", False),
        include_replay_status=getattr(args, "include_replay_status", False),
        include_text=getattr(args, "include_text", "none"),
        include_signatures=getattr(args, "include_signatures", False),
        include_all=getattr(args, "include_all", False),
        limit=getattr(args, "limit", None),
        he_data_dir=getattr(args, "he_data_dir", "data/fi/v1"),
        projections_data_dir=getattr(args, "projections_data_dir", ".tmp/projections"),
        output_format=getattr(args, "output_format", "json"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )
