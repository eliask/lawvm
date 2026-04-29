"""Build an Estonia divergence publication SQLite database."""
from __future__ import annotations

import csv
import hashlib
import re
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.estonia.compare import irnode_to_ee_comparison_text, normalize_ee_comparison_text
from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml, open_rt_archive
from lawvm.estonia.replay import replay_ee_to_pit
from lawvm.estonia.residual_reporting import (
    build_ee_residual_summary,
    is_ee_punctuation_whitespace_only_difference,
)
from lawvm.tools.ee_reporting import build_ee_benchmark_reporting_summary

if TYPE_CHECKING:
    import argparse

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _ROOT / "data" / "ee_riigiteataja.farchive"
_DEFAULT_CORPUS = _ROOT / "data" / "estonia" / "current_replayable_corpus.csv"
_DEFAULT_OUTPUT = _ROOT / "data" / "estonia" / "ee_divergences_publication.db"
_WORKER_ARCHIVE: Any = None
_INSTITUTIONAL_NAME_PROJECTIONS: tuple[tuple[str, str], ...] = (
    ("Siseministeerium", "Rahandusministeerium"),
    ("Siseministeeriumi", "Rahandusministeeriumi"),
    ("siseminister", "rahandusminister"),
    ("Veterinaar-ja Toiduamet", "Põllumajandus-ja Toiduamet"),
    ("Veterinaar-ja Toiduameti", "Põllumajandus-ja Toiduameti"),
    ("Põllumajandusamet", "Põllumajandus-ja Toiduamet"),
    ("Põllumajandusameti", "Põllumajandus-ja Toiduameti"),
    ("Maaeluministeerium", "Regionaal-ja Põllumajandusministeerium"),
    ("Maaeluministeeriumi", "Regionaal-ja Põllumajandusministeeriumi"),
    ("Maanteeamet", "Transpordiamet"),
    ("Maanteeameti", "Transpordiameti"),
    ("Põllumajandusministeerium", "Maaeluministeerium"),
    ("Põllumajandusministeeriumi", "Maaeluministeeriumi"),
    ("Maa-amet", "Maa-ja Ruumiamet"),
    ("Maa-ameti", "Maa-ja Ruumiameti"),
    ("Keskkonnateabe Keskus", "Keskkonnaagentuur"),
    ("Keskkonnateabe Keskuse", "Keskkonnaagentuuri"),
    ("Veeteede Amet", "Transpordiamet"),
    ("Veeteede Ameti", "Transpordiameti"),
)
_SOURCE_TYPO_PROJECTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "Tarbijakatise ja Tehnilise Järelevalve Amet",
        "Tarbijakaitse ja Tehnilise Järelevalve Amet",
        "ee_source_typo_126022019001_tarbijakaitse",
    ),
    (
        "Tarbijakatise ja Tehnilise Järelevalve Ametile",
        "Tarbijakaitse ja Tehnilise Järelevalve Ametile",
        "ee_source_typo_126022019001_tarbijakaitse",
    ),
)
_SYMBOL_PLACEHOLDER_TRANSLATION = str.maketrans(
    {
        "≤": "?",
        "β": "?",
        "α": "?",
        "σ": "?",
    }
)
_EE_OMITTED_TEXT_PLACEHOLDER_RE = re.compile(
    r"\[?\s*Käesolevast\s+tekstist\s+välja\s+jäetud\.?\s*\]?",
    re.IGNORECASE,
)
_OUTREACH_PRESENTATION_BUCKETS = frozenset(
    {
        "address_alignment_shadow",
        "appendix_display_pathology",
        "presentation_omitted_text_placeholder",
        "presentation_punctuation_whitespace",
    }
)
_OUTREACH_SOURCE_SURFACE_BUCKETS = frozenset(
    {
        "descendant_residual_mix",
        "oracle_correction_notice",
        "source_ambiguity",
        "source_oracle_drift",
    }
)


def _address_to_string(address: Any) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in getattr(address, "path", ()))


def _configure(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA temp_store=MEMORY")


def _create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pairs (
            pair_key TEXT PRIMARY KEY,
            grupi_id TEXT NOT NULL,
            base_id TEXT NOT NULL,
            oracle_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            schema TEXT NOT NULL DEFAULT '',
            n_amendments INTEGER NOT NULL DEFAULT 0,
            base_effective TEXT NOT NULL DEFAULT '',
            oracle_effective TEXT NOT NULL DEFAULT '',
            version_index INTEGER NOT NULL DEFAULT 0,
            version_count INTEGER NOT NULL DEFAULT 0,
            as_of TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            source_basis TEXT NOT NULL DEFAULT '',
            comparison_class TEXT NOT NULL DEFAULT '',
            benchmark_reporting_stratum TEXT NOT NULL DEFAULT '',
            benchmark_reporting_headline_eligible INTEGER NOT NULL DEFAULT 0,
            core_benchmark INTEGER NOT NULL DEFAULT 0,
            n_ops INTEGER NOT NULL DEFAULT 0,
            divergence_count INTEGER NOT NULL DEFAULT 0,
            mismatch_count INTEGER NOT NULL DEFAULT 0,
            ops_missing_count INTEGER NOT NULL DEFAULT 0,
            consolidated_missing_count INTEGER NOT NULL DEFAULT 0,
            open_current_divergence_count INTEGER NOT NULL DEFAULT 0,
            section_total_count INTEGER NOT NULL DEFAULT 0,
            section_identical_count INTEGER NOT NULL DEFAULT 0,
            section_divergent_count INTEGER NOT NULL DEFAULT 0,
            section_replay_only_count INTEGER NOT NULL DEFAULT 0,
            section_consolidated_only_count INTEGER NOT NULL DEFAULT 0,
            section_text_total_chars INTEGER NOT NULL DEFAULT 0,
            section_text_identical_chars INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS divergences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair_key TEXT NOT NULL,
            base_id TEXT NOT NULL,
            oracle_id TEXT NOT NULL,
            section_address TEXT NOT NULL,
            address TEXT NOT NULL,
            divergence_type TEXT NOT NULL,
            replay_text_hash TEXT,
            oracle_text_hash TEXT,
            residual_bucket TEXT,
            residual_evidence TEXT,
            alignment_peer_addresses TEXT,
            open_current INTEGER NOT NULL DEFAULT 1,
            outreach_bucket TEXT NOT NULL DEFAULT '',
            meaningful_candidate INTEGER NOT NULL DEFAULT 0,
            outreach_evidence TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(pair_key) REFERENCES pairs(pair_key)
        );

        CREATE TABLE IF NOT EXISTS text_blobs (
            text_hash TEXT PRIMARY KEY,
            text TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ee_pub_pairs_base_oracle
            ON pairs(base_id, oracle_id);
        CREATE INDEX IF NOT EXISTS idx_ee_pub_pairs_status
            ON pairs(status, comparison_class);
        CREATE INDEX IF NOT EXISTS idx_ee_pub_divergences_pair
            ON divergences(pair_key);
        CREATE INDEX IF NOT EXISTS idx_ee_pub_divergences_section
            ON divergences(pair_key, section_address);
        CREATE INDEX IF NOT EXISTS idx_ee_pub_divergences_bucket
            ON divergences(residual_bucket);
        CREATE INDEX IF NOT EXISTS idx_ee_pub_divergences_outreach
            ON divergences(meaningful_candidate, outreach_bucket);
        """
    )


def _int_value(raw: str | None) -> int:
    try:
        return int(raw or 0)
    except ValueError:
        return 0


def _pair_key(base_id: str, oracle_id: str) -> str:
    return f"{base_id}->{oracle_id}"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _section_address(address: str) -> str | None:
    pieces = []
    for part in address.split("/"):
        if not part:
            continue
        pieces.append(part)
        if part.startswith("section:"):
            return "/".join(pieces)
    return None


def _is_browser_detail_address(address: str) -> bool:
    section = _section_address(address)
    return section is not None and address == section


def _iter_section_texts(statute: IRStatute | None) -> dict[str, str]:
    if statute is None:
        return {}
    sections: dict[str, str] = {}

    def walk(node: IRNode, path: tuple[str, ...]) -> None:
        next_path = path
        if node.label is not None:
            next_path = (*path, f"{node.kind}:{node.label}")
        if node.kind == IRNodeKind.SECTION:
            text = normalize_ee_comparison_text(irnode_to_ee_comparison_text(node).strip())
            sections["/".join(next_path)] = text
        for child in node.children:
            walk(child, next_path)

    walk(statute.body, ())
    return sections


def _section_agreement_metrics(
    replayed: IRStatute | None,
    oracle: IRStatute | None,
) -> dict[str, int]:
    replay_sections = _iter_section_texts(replayed)
    oracle_sections = _iter_section_texts(oracle)
    addresses = set(replay_sections) | set(oracle_sections)

    metrics = {
        "section_total_count": len(addresses),
        "section_identical_count": 0,
        "section_divergent_count": 0,
        "section_replay_only_count": 0,
        "section_consolidated_only_count": 0,
        "section_text_total_chars": 0,
        "section_text_identical_chars": 0,
    }
    for address in addresses:
        replay_text = replay_sections.get(address)
        oracle_text = oracle_sections.get(address)
        replay_len = len(replay_text or "")
        oracle_len = len(oracle_text or "")
        metrics["section_text_total_chars"] += max(replay_len, oracle_len)
        if replay_text is None:
            metrics["section_consolidated_only_count"] += 1
        elif oracle_text is None:
            metrics["section_replay_only_count"] += 1
        elif replay_text == oracle_text:
            metrics["section_identical_count"] += 1
            metrics["section_text_identical_chars"] += max(replay_len, oracle_len)
        else:
            metrics["section_divergent_count"] += 1
    return metrics


def _address_alignment_peer_addresses(
    divergence: dict[str, Any],
    *,
    replay_hash_addresses: dict[str, set[str]],
    oracle_hash_addresses: dict[str, set[str]],
) -> tuple[str, ...]:
    """Return opposite-side addresses carrying the same full section text.

    This is publication/adjudication metadata only. It does not repair replay,
    rewrite addresses, or assert which address is legally correct.
    """
    address = str(divergence["address"])
    peers: set[str] = set()
    replay_text = str(divergence.get("replay_text") or "")
    oracle_text = str(divergence.get("oracle_text") or "")
    if len(oracle_text) >= 40:
        oracle_hash = _text_hash(oracle_text)
        peers.update(peer for peer in replay_hash_addresses.get(oracle_hash, set()) if peer != address)
    if len(replay_text) >= 40:
        replay_hash = _text_hash(replay_text)
        peers.update(peer for peer in oracle_hash_addresses.get(replay_hash, set()) if peer != address)
    return tuple(sorted(peers))


def _classify_address_alignment_shadows(divergences: list[dict[str, Any]]) -> None:
    """Mark exact cross-address text shadows as non-open publication rows.

    A section can be text-identical to the opposite side at a different address
    when a source/oracle surface shifts labels or omits an insertion that moves
    the visible address sequence. Those rows are useful for diagnosing address
    alignment, but they should not pollute the default current content-diff
    queue as independent substantive text divergences.
    """
    replay_hash_addresses: dict[str, set[str]] = {}
    oracle_hash_addresses: dict[str, set[str]] = {}
    for divergence in divergences:
        replay_text = str(divergence.get("replay_text") or "")
        oracle_text = str(divergence.get("oracle_text") or "")
        if replay_text:
            replay_hash_addresses.setdefault(_text_hash(replay_text), set()).add(str(divergence["address"]))
        if oracle_text:
            oracle_hash_addresses.setdefault(_text_hash(oracle_text), set()).add(str(divergence["address"]))

    for divergence in divergences:
        if divergence.get("residual_bucket"):
            divergence["alignment_peer_addresses"] = ""
            continue
        peers = _address_alignment_peer_addresses(
            divergence,
            replay_hash_addresses=replay_hash_addresses,
            oracle_hash_addresses=oracle_hash_addresses,
        )
        if not peers:
            divergence["alignment_peer_addresses"] = ""
            continue
        divergence["residual_bucket"] = "address_alignment_shadow"
        divergence["residual_evidence"] = (
            "The full section text appears verbatim on the opposite comparison "
            f"side at another address: {', '.join(peers)}. This classifies the "
            "row as an address-alignment shadow for publication triage; it does "
            "not decide whether replay or Riigi Teataja has the legally correct address."
        )
        divergence["alignment_peer_addresses"] = ", ".join(peers)
        divergence["open_current"] = 0


def _classify_replay_coverage_gaps(
    divergences: list[dict[str, Any]],
    *,
    amendments_failed: list[str],
    unsupported_action_sources: list[str],
    n_ops: int,
    comparison_class: str,
) -> None:
    """Mark rows whose amendment chain did not fully compile as non-candidates.

    Failed amendment refs mean replay did not have a complete executable source
    program for the pair. The resulting differences may still be useful for
    frontend coverage work, but they are not publication-side candidate
    divergences.
    """
    evidence = ""
    if amendments_failed:
        failed = ", ".join(amendments_failed)
        evidence = (
            "LawVM did not compile a complete amendment chain for this pair; "
            f"failed amendment refs: {failed}. Treat these rows as replay/source "
            "coverage debt, not as Riigi Teataja candidate divergences."
        )
    elif unsupported_action_sources:
        sources = ", ".join(sorted(set(unsupported_action_sources)))
        evidence = (
            "LawVM parsed at least one source instruction but replay skipped an "
            "unsupported action for this pair; unsupported source refs: "
            f"{sources}. Treat these rows as replay/action coverage debt, not "
            "as Riigi Teataja candidate divergences."
        )
    elif n_ops == 0 and comparison_class == "commensurable_delta" and divergences:
        evidence = (
            "LawVM compiled no executable amendment operations for this changed "
            "current-version pair. Treat these rows as amendment-extraction "
            "coverage debt until the source program is compiled, not as Riigi "
            "Teataja candidate divergences."
        )
    else:
        return
    for divergence in divergences:
        if divergence.get("residual_bucket"):
            continue
        divergence["residual_bucket"] = "replay_coverage_gap"
        divergence["residual_evidence"] = evidence
        divergence["open_current"] = 0


def _classify_noncommensurable_pair_surface(
    divergences: list[dict[str, Any]],
    *,
    comparison_class: str,
) -> None:
    """Close rows for pair classes that are not direct current deltas."""
    if comparison_class == "commensurable_delta":
        return
    evidence = (
        f"The pair is classified as {comparison_class}, not as a direct "
        "commensurable current-version delta. Keep these rows for source-surface "
        "diagnostics, but do not treat them as current Riigi Teataja candidate divergences."
    )
    for divergence in divergences:
        if divergence.get("residual_bucket"):
            continue
        divergence["residual_bucket"] = "pair_surface_classification"
        divergence["residual_evidence"] = evidence
        divergence["open_current"] = 0


def _institutional_name_projection(text: str) -> tuple[str, tuple[str, ...]]:
    projected = text
    fired: list[str] = []
    for old, new in _INSTITUTIONAL_NAME_PROJECTIONS:
        if old not in projected:
            continue
        projected = projected.replace(old, new)
        fired.append(f"{old} -> {new}")
    return projected, tuple(fired)


def _classify_institutional_name_projection(divergences: list[dict[str, Any]]) -> None:
    """Close exact institutional-name projection rows for publication triage.

    Estonia's consolidated surface sometimes carries later institutional
    successor names where the replay source still contains the older institution
    name. This classifier is deliberately exact: it does not normalize the
    comparison globally and it only closes a row when the explicit term map makes
    the full section texts equal.
    """
    for divergence in divergences:
        if divergence.get("residual_bucket"):
            continue
        replay_text = divergence.get("replay_text")
        oracle_text = divergence.get("oracle_text")
        if replay_text is None or oracle_text is None:
            continue
        projected, fired = _institutional_name_projection(str(replay_text))
        if not fired or projected != str(oracle_text):
            continue
        divergence["residual_bucket"] = "source_oracle_drift"
        divergence["residual_evidence"] = (
            "Exact bounded institutional-name projection makes replay and "
            f"Riigi Teataja text equal for this section. Projection(s): {'; '.join(fired)}. "
            "This is classified as source/oracle surface drift for publication triage; "
            "it does not mutate replay output or decide a legal successor rule."
        )
        divergence["open_current"] = 0


def _source_typo_projection(text: str) -> tuple[str, tuple[str, ...]]:
    projected = text
    fired: list[str] = []
    for old, new, rule_id in _SOURCE_TYPO_PROJECTIONS:
        if old not in projected:
            continue
        projected = projected.replace(old, new)
        fired.append(f"{rule_id}: {old} -> {new}")
    return projected, tuple(fired)


def _classify_source_typo_projection(divergences: list[dict[str, Any]]) -> None:
    """Close exact rows where replay preserves a known source typo.

    This is publication/adjudication metadata only. Replay remains faithful to
    the source amendment text; the row is hidden from the default RT-candidate
    queue only when a bounded source-typo projection makes the full section
    texts equal.
    """
    for divergence in divergences:
        if divergence.get("residual_bucket"):
            continue
        replay_text = divergence.get("replay_text")
        oracle_text = divergence.get("oracle_text")
        if replay_text is None or oracle_text is None:
            continue
        projected, fired = _source_typo_projection(str(replay_text))
        if not fired or projected != str(oracle_text):
            continue
        divergence["residual_bucket"] = "source_oracle_drift"
        divergence["residual_evidence"] = (
            "Exact bounded source-typo projection makes replay and Riigi Teataja "
            f"text equal for this section. Projection(s): {'; '.join(fired)}. "
            "This is classified as source/oracle surface drift for publication "
            "triage; replay output is not mutated."
        )
        divergence["open_current"] = 0


def _symbol_placeholder_projection(text: str) -> str:
    projected = text.translate(_SYMBOL_PLACEHOLDER_TRANSLATION)
    projected = " ".join(projected.split())
    return projected.replace(" ?", "?")


def _classify_symbol_placeholder_projection(divergences: list[dict[str, Any]]) -> None:
    """Close exact rows where RT carries placeholder question marks for symbols.

    Some RT comparison surfaces expose mathematical signs or Greek variables as
    literal ``?`` while the source/replay text carries the actual symbol. This is
    classification metadata only: replay remains source-faithful.
    """
    for divergence in divergences:
        if divergence.get("residual_bucket"):
            continue
        replay_text = divergence.get("replay_text")
        oracle_text = divergence.get("oracle_text")
        if replay_text is None or oracle_text is None:
            continue
        replay_projected = _symbol_placeholder_projection(str(replay_text))
        oracle_projected = _symbol_placeholder_projection(str(oracle_text))
        if replay_projected != oracle_projected:
            continue
        if str(replay_text) == str(oracle_text):
            continue
        divergence["residual_bucket"] = "source_oracle_drift"
        divergence["residual_evidence"] = (
            "Exact symbol-placeholder projection makes replay and Riigi Teataja "
            "text equal for this section. RT carries '?' where the source/replay "
            "surface carries mathematical or Greek symbols such as <=, beta, "
            "alpha, or sigma. This is classified as source/oracle surface drift "
            "for publication triage; replay output is not mutated."
        )
        divergence["open_current"] = 0


def _classify_punctuation_whitespace_only(divergences: list[dict[str, Any]]) -> None:
    """Close rows whose remaining difference is only punctuation/whitespace.

    This is publication triage only. It does not change replay/oracle text and
    it deliberately does not normalize letters, digits, symbols, or words.
    """
    for divergence in divergences:
        if divergence.get("residual_bucket"):
            continue
        replay_text = divergence.get("replay_text")
        oracle_text = divergence.get("oracle_text")
        if replay_text is None or oracle_text is None:
            continue
        replay_surface = str(replay_text)
        oracle_surface = str(oracle_text)
        if replay_surface == oracle_surface:
            continue
        if not is_ee_punctuation_whitespace_only_difference(replay_surface, oracle_surface):
            continue
        divergence["residual_bucket"] = "presentation_punctuation_whitespace"
        divergence["residual_evidence"] = (
            "Replay and Riigi Teataja text differ only by Unicode punctuation "
            "and whitespace after a publication-only projection. The row is "
            "kept for auditability but hidden from the default open candidate queue."
        )
        divergence["open_current"] = 0


def _without_omitted_text_placeholder(text: str) -> str:
    stripped = _EE_OMITTED_TEXT_PLACEHOLDER_RE.sub("", text)
    return " ".join(stripped.replace("[", " ").replace("]", " ").split()).strip(" .;:")


def _is_omitted_display_heading(text: str) -> bool:
    normalized = _without_omitted_text_placeholder(text)
    if not normalized:
        return True
    lowered = normalized.casefold()
    return normalized in {"Kehtetud"} or lowered in {
        "määruse kehtetuks tunnistamine",
        "seaduse kehtetuks tunnistamine",
    }


def _classify_omitted_text_placeholder_display(divergences: list[dict[str, Any]]) -> None:
    """Close exact RT omitted-text display placeholder rows for publication triage.

    Riigi Teataja sometimes projects non-current final/repeal provisions as a
    heading plus ``[Käesolevast tekstist välja jäetud]`` while the opposite
    surface has no active section row. This classifier is deliberately narrow:
    it only closes placeholder/heading equivalences and does not hide rows where
    one side has actual provision text such as a commencement sentence.
    """
    for divergence in divergences:
        if divergence.get("residual_bucket"):
            continue
        replay_text = divergence.get("replay_text")
        oracle_text = divergence.get("oracle_text")
        if replay_text is None or oracle_text is None:
            continue
        replay_surface = str(replay_text)
        oracle_surface = str(oracle_text)
        replay_has_placeholder = bool(_EE_OMITTED_TEXT_PLACEHOLDER_RE.search(replay_surface))
        oracle_has_placeholder = bool(_EE_OMITTED_TEXT_PLACEHOLDER_RE.search(oracle_surface))
        replay_heading = _is_omitted_display_heading(replay_surface)
        oracle_heading = _is_omitted_display_heading(oracle_surface)
        if not (
            replay_surface in {"", "[Kehtetud]", "Kehtetud"}
            or oracle_surface in {"", "[Kehtetud]", "Kehtetud"}
            or replay_has_placeholder
            or oracle_has_placeholder
        ):
            continue
        if replay_heading and oracle_heading:
            divergence["residual_bucket"] = "presentation_omitted_text_placeholder"
            divergence["residual_evidence"] = (
                "The row differs only by Riigi Teataja omitted-text/repealed-section "
                "display scaffolding such as '[Käesolevast tekstist välja jäetud]' "
                "or '[Kehtetud]'. This is publication triage metadata only; replay "
                "and oracle text are not mutated."
            )
            divergence["open_current"] = 0


def _classify_descendant_projection_residuals(
    divergences: list[dict[str, Any]],
    *,
    raw_divergences: list[dict[str, Any]],
) -> None:
    """Close section rows that are exactly explained by a child missing-row.

    The publication DB stores section-level browser rows, while raw consistency
    can also report child rows. If a parent mismatch is just
    ``heading + child_text`` on one side and ``heading`` on the other, the
    section row is not an independent RT outreach candidate. This classifier is
    deliberately equality-based and does not mutate replay or oracle text.
    """
    raw_by_section = {}
    for raw in raw_divergences:
        raw_address = str(raw.get("address") or "")
        section_address = _section_address(raw_address)
        if section_address is None or raw_address == section_address:
            continue
        raw_by_section.setdefault(section_address, []).append(raw)

    for divergence in divergences:
        if divergence.get("residual_bucket"):
            continue
        if divergence.get("divergence_type") != "MISMATCH":
            continue
        address = str(divergence.get("address") or "")
        section_children = raw_by_section.get(address, [])
        if not section_children:
            continue
        replay_text = normalize_ee_comparison_text(str(divergence.get("replay_text") or ""))
        oracle_text = normalize_ee_comparison_text(str(divergence.get("oracle_text") or ""))
        explaining_children: list[str] = []
        for child in section_children:
            child_type = str(child.get("divergence_type") or "")
            child_replay = normalize_ee_comparison_text(str(child.get("replay_text") or ""))
            child_oracle = normalize_ee_comparison_text(str(child.get("oracle_text") or ""))
            if child_type == "CONSOLIDATED_MISSING":
                projected = normalize_ee_comparison_text(f"{oracle_text} {child_replay}")
                if child_replay and projected == replay_text:
                    explaining_children.append(str(child.get("address") or ""))
            elif child_type == "OPS_MISSING":
                projected = normalize_ee_comparison_text(f"{replay_text} {child_oracle}")
                if child_oracle and projected == oracle_text:
                    explaining_children.append(str(child.get("address") or ""))
        if not explaining_children:
            continue
        divergence["residual_bucket"] = "comparison_descendant_projection"
        divergence["residual_evidence"] = (
            "The section-level mismatch is exactly explained by descendant "
            f"missing-row text at {', '.join(explaining_children)}. This is a "
            "publication comparison projection residual, not an independent "
            "Riigi Teataja outreach candidate; replay and oracle text are not "
            "mutated."
        )
        divergence["alignment_peer_addresses"] = ",".join(explaining_children)
        divergence["open_current"] = 0


def _classify_table_fragment_replay_gaps(
    divergences: list[dict[str, Any]],
    *,
    raw_divergences: list[dict[str, Any]],
) -> None:
    """Close browser rows where replay exposed a table fragment as a section.

    EE amendment prose can target ``paragrahvi N tabeliosa``. Until table-part
    identity is modeled, those operations can replay a local table fragment as
    if it were the entire section while the oracle still has the full section
    body under a child row. This is replay coverage work, not RT outreach.
    """
    raw_by_section = {}
    for raw in raw_divergences:
        raw_address = str(raw.get("address") or "")
        section_address = _section_address(raw_address)
        if section_address is None or raw_address == section_address:
            continue
        raw_by_section.setdefault(section_address, []).append(raw)

    for divergence in divergences:
        if divergence.get("residual_bucket"):
            continue
        if divergence.get("divergence_type") != "MISMATCH":
            continue
        address = str(divergence.get("address") or "")
        replay_text = normalize_ee_comparison_text(str(divergence.get("replay_text") or ""))
        oracle_text = normalize_ee_comparison_text(str(divergence.get("oracle_text") or ""))
        if len(replay_text) < 40 or len(oracle_text) <= len(replay_text):
            continue
        explaining_children: list[str] = []
        for child in raw_by_section.get(address, []):
            if str(child.get("divergence_type") or "") != "OPS_MISSING":
                continue
            child_oracle = normalize_ee_comparison_text(str(child.get("oracle_text") or ""))
            if replay_text and replay_text in child_oracle:
                explaining_children.append(str(child.get("address") or ""))
        if not explaining_children:
            continue
        divergence["residual_bucket"] = "table_fragment_replay_gap"
        divergence["residual_evidence"] = (
            "The replay-side section text is a strict fragment of oracle "
            f"descendant text at {', '.join(explaining_children)}. This matches "
            "known EE table-part amendment coverage gaps where a targeted "
            "tabeliosa operation is not yet modeled as table-part identity. "
            "Replay and oracle text are not mutated."
        )
        divergence["alignment_peer_addresses"] = ",".join(explaining_children)
        divergence["open_current"] = 0


def _assign_publication_outreach_triage(divergences: list[dict[str, Any]]) -> None:
    """Project residual classifications into outreach-safe publication buckets.

    This is a reporting projection only. It does not change replay, oracle text,
    residual classifications, or open-current counts. The separate
    ``meaningful_candidate`` flag exists so publication/outreach queries can
    omit punctuation/whitespace-only rows without reimplementing bucket logic.
    """
    for divergence in divergences:
        residual_bucket = str(divergence.get("residual_bucket") or "")
        open_current_raw = divergence.get("open_current")
        open_current = open_current_raw is True or open_current_raw == 1 or open_current_raw == "1"
        if open_current and not residual_bucket:
            divergence["outreach_bucket"] = "publication_candidate"
            divergence["meaningful_candidate"] = 1
            divergence["outreach_evidence"] = (
                "Open current divergence with no residual classifier. This row "
                "is a candidate for human review before Riigi Teataja outreach."
            )
            continue
        if residual_bucket in _OUTREACH_PRESENTATION_BUCKETS:
            divergence["outreach_bucket"] = "excluded_presentation"
            divergence["meaningful_candidate"] = 0
            divergence["outreach_evidence"] = (
                f"Excluded from outreach candidate set by residual bucket "
                f"{residual_bucket}; this is presentation or alignment triage."
            )
            continue
        if residual_bucket == "replay_coverage_gap":
            divergence["outreach_bucket"] = "excluded_replay_coverage"
            divergence["meaningful_candidate"] = 0
            divergence["outreach_evidence"] = (
                "Excluded from outreach candidate set because LawVM does not yet "
                "fully replay the relevant amendment chain."
            )
            continue
        if residual_bucket == "table_fragment_replay_gap":
            divergence["outreach_bucket"] = "excluded_replay_coverage"
            divergence["meaningful_candidate"] = 0
            divergence["outreach_evidence"] = (
                "Excluded from outreach candidate set because LawVM does not yet "
                "model the targeted EE table-part operation as table identity."
            )
            continue
        if residual_bucket == "pair_surface_classification":
            divergence["outreach_bucket"] = "excluded_pair_surface"
            divergence["meaningful_candidate"] = 0
            divergence["outreach_evidence"] = (
                "Excluded from outreach candidate set because the pair is not a "
                "core commensurable replay/oracle comparison surface."
            )
            continue
        if residual_bucket == "comparison_descendant_projection":
            divergence["outreach_bucket"] = "excluded_comparison_projection"
            divergence["meaningful_candidate"] = 0
            divergence["outreach_evidence"] = (
                "Excluded from outreach candidate set because the section-level "
                "row is exactly explained by a raw descendant missing-row in "
                "the comparison output."
            )
            continue
        if residual_bucket in _OUTREACH_SOURCE_SURFACE_BUCKETS:
            divergence["outreach_bucket"] = "excluded_source_surface"
            divergence["meaningful_candidate"] = 0
            divergence["outreach_evidence"] = (
                f"Excluded from outreach candidate set by residual bucket "
                f"{residual_bucket}; the row is already classified as a source, "
                "oracle, or correction-surface issue."
            )
            continue
        if residual_bucket:
            divergence["outreach_bucket"] = "excluded_adjudicated_other"
            divergence["meaningful_candidate"] = 0
            divergence["outreach_evidence"] = (
                f"Excluded from outreach candidate set by residual bucket "
                f"{residual_bucket}."
            )
            continue
        divergence["outreach_bucket"] = "excluded_closed_unclassified"
        divergence["meaningful_candidate"] = 0
        divergence["outreach_evidence"] = (
            "Excluded from outreach candidate set because the row is not open "
            "current, even though no residual bucket is present."
        )


def _score_publication_pair(row: dict[str, str], archive: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base_id = row["base_id"].strip()
    oracle_id = row["oracle_id"].strip()
    pair_key = _pair_key(base_id, oracle_id)

    try:
        oracle_xml = fetch_rt_xml(oracle_id, archive=archive)
        as_of = extract_effective_date(oracle_xml) or row.get("oracle_effective", "") or "9999-12-31"
        result = replay_ee_to_pit(
            base_id=base_id,
            as_of=as_of,
            archive=archive,
            verbose=False,
            oracle_id=oracle_id,
        )
    except Exception as exc:
        return (
            {
                "pair_key": pair_key,
                "as_of": row.get("oracle_effective", ""),
                "status": f"EXC:{str(exc)[:120]}",
                "source_basis": "",
                "comparison_class": "exception",
                "benchmark_reporting_stratum": "EE_NONCORE_SOURCE_GAP",
                "benchmark_reporting_headline_eligible": 0,
                "core_benchmark": 0,
                "n_ops": 0,
                "divergence_count": 0,
                "mismatch_count": 0,
                "ops_missing_count": 0,
                "consolidated_missing_count": 0,
                "open_current_divergence_count": 0,
                "browser_divergence_count": 0,
                "browser_open_current_divergence_count": 0,
                "browser_meaningful_candidate_count": 0,
                **_section_agreement_metrics(None, None),
            },
            [],
        )

    raw_divergence_addresses = tuple(_address_to_string(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id=base_id,
        oracle_id=oracle_id,
        divergence_addresses=raw_divergence_addresses,
    )
    reporting_summary = build_ee_benchmark_reporting_summary(
        getattr(result, "source_basis", ""),
        result.comparison_class,
    )
    matched_current = (
        residual_summary.matched_current_divergence_count
        if residual_summary is not None
        else 0
    )
    pair = {
        "pair_key": pair_key,
        "as_of": getattr(result, "as_of", ""),
        "status": "OK" if not result.error else f"ERR:{result.error[:120]}",
        "source_basis": getattr(result, "source_basis", ""),
        "comparison_class": result.comparison_class,
        "benchmark_reporting_stratum": reporting_summary["benchmark_reporting_stratum"],
        "benchmark_reporting_headline_eligible": int(reporting_summary["benchmark_reporting_headline_eligible"]),
        "core_benchmark": int(result.source_adjudication is not None and not result.source_adjudication.oracle_suspect),
        "n_ops": result.n_ops,
        "divergence_count": len(result.divergences),
        "mismatch_count": result.n_mismatch,
        "ops_missing_count": result.n_ops_missing,
        "consolidated_missing_count": result.n_con_missing,
        "open_current_divergence_count": max(0, len(result.divergences) - matched_current),
        **_section_agreement_metrics(result.replayed, result.oracle),
    }
    divergences: list[dict[str, Any]] = []
    raw_divergences: list[dict[str, Any]] = []
    for divergence, address in zip(result.divergences, raw_divergence_addresses):
        raw_divergences.append(
            {
                "address": address,
                "divergence_type": divergence.divergence_type,
                "replay_text": divergence.ops_text,
                "oracle_text": divergence.consolidated_text,
            }
        )
        if not _is_browser_detail_address(address):
            continue
        replay_text = divergence.ops_text
        oracle_text = divergence.consolidated_text
        if replay_text == oracle_text:
            continue
        residual_record = (
            residual_summary.record_by_address.get(address)
            if residual_summary is not None
            else None
        )
        section_address = _section_address(address)
        if section_address is None:
            continue
        divergences.append(
            {
                "pair_key": pair_key,
                "base_id": base_id,
                "oracle_id": oracle_id,
                "section_address": section_address,
                "address": address,
                "divergence_type": divergence.divergence_type,
                "replay_text": replay_text,
                "oracle_text": oracle_text,
                "residual_bucket": residual_record.bucket if residual_record else None,
                "residual_evidence": residual_record.evidence if residual_record else None,
                "alignment_peer_addresses": "",
                "open_current": 0 if residual_record else 1,
            }
        )
    _classify_replay_coverage_gaps(
        divergences,
        amendments_failed=list(getattr(result, "amendments_failed", ())),
        unsupported_action_sources=[
            str(adjudication.source_statute)
            for adjudication in getattr(result, "adjudications", ())
            if getattr(adjudication, "kind", "") == "ee_replay_unsupported_action"
        ],
        n_ops=result.n_ops,
        comparison_class=result.comparison_class,
    )
    _classify_noncommensurable_pair_surface(
        divergences,
        comparison_class=result.comparison_class,
    )
    _classify_institutional_name_projection(divergences)
    _classify_source_typo_projection(divergences)
    _classify_symbol_placeholder_projection(divergences)
    _classify_punctuation_whitespace_only(divergences)
    _classify_omitted_text_placeholder_display(divergences)
    _classify_address_alignment_shadows(divergences)
    _classify_descendant_projection_residuals(
        divergences,
        raw_divergences=raw_divergences,
    )
    _classify_table_fragment_replay_gaps(
        divergences,
        raw_divergences=raw_divergences,
    )
    _assign_publication_outreach_triage(divergences)
    pair["browser_divergence_count"] = len(divergences)
    pair["browser_open_current_divergence_count"] = sum(1 for divergence in divergences if divergence["open_current"])
    pair["browser_meaningful_candidate_count"] = sum(
        1 for divergence in divergences if divergence["meaningful_candidate"]
    )
    return pair, divergences


def _init_worker(archive_path: str) -> None:
    global _WORKER_ARCHIVE
    _WORKER_ARCHIVE = open_rt_archive(Path(archive_path), readonly=True)


def _score_publication_pair_worker(row: dict[str, str]) -> tuple[dict[str, str], dict[str, Any], list[dict[str, Any]]]:
    if _WORKER_ARCHIVE is None:
        raise RuntimeError("EE publication worker archive was not initialized")
    pair, divergences = _score_publication_pair(row, _WORKER_ARCHIVE)
    return row, pair, divergences


def _iter_scored_pairs(
    rows: list[dict[str, str]],
    *,
    archive_path: Path,
    workers: int,
):
    if workers <= 1:
        archive = open_rt_archive(archive_path, readonly=True)
        try:
            for row in rows:
                pair, divergences = _score_publication_pair(row, archive)
                yield row, pair, divergences
        finally:
            archive.close()
        return

    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(str(archive_path),),
    ) as pool:
        futures = [pool.submit(_score_publication_pair_worker, row) for row in rows]
        for future in as_completed(futures):
            yield future.result()


def build_ee_publication_db(
    *,
    corpus_path: Path = _DEFAULT_CORPUS,
    output_path: Path = _DEFAULT_OUTPUT,
    archive_path: Path = _DEFAULT_DB,
    limit: int | None = None,
    workers: int = 1,
) -> dict[str, int]:
    rows = list(csv.DictReader(corpus_path.open(encoding="utf-8")))
    if limit is not None:
        rows = rows[:limit]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    con = sqlite3.connect(str(output_path))
    _configure(con)
    _create_schema(con)
    con.execute("INSERT INTO meta(key, value) VALUES (?, ?)", ("built_at", time.strftime("%Y-%m-%d %H:%M:%S")))
    con.execute("INSERT INTO meta(key, value) VALUES (?, ?)", ("corpus_path", str(corpus_path)))
    con.execute("INSERT INTO meta(key, value) VALUES (?, ?)", ("archive_path", str(archive_path)))

    stats = {
        "pairs": 0,
        "errors": 0,
        "divergences": 0,
        "open_divergences": 0,
        "meaningful_candidates": 0,
    }
    try:
        for idx, (row, pair, divergences) in enumerate(
            _iter_scored_pairs(rows, archive_path=archive_path, workers=workers),
            start=1,
        ):
            if not pair["status"].startswith("OK"):
                stats["errors"] += 1
            stats["pairs"] += 1
            stats["divergences"] += pair["browser_divergence_count"]
            stats["open_divergences"] += pair["browser_open_current_divergence_count"]
            stats["meaningful_candidates"] += pair["browser_meaningful_candidate_count"]

            con.execute(
                """
                INSERT INTO pairs(
                    pair_key, grupi_id, base_id, oracle_id, title, schema,
                    n_amendments, base_effective, oracle_effective, version_index,
                    version_count, as_of, status, source_basis, comparison_class,
                    benchmark_reporting_stratum, benchmark_reporting_headline_eligible,
                    core_benchmark, n_ops, divergence_count, mismatch_count,
                    ops_missing_count, consolidated_missing_count,
                    open_current_divergence_count, section_total_count,
                    section_identical_count, section_divergent_count,
                    section_replay_only_count, section_consolidated_only_count,
                    section_text_total_chars, section_text_identical_chars
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pair["pair_key"],
                    row.get("grupi_id", ""),
                    row.get("base_id", ""),
                    row.get("oracle_id", ""),
                    row.get("title", ""),
                    row.get("schema", ""),
                    _int_value(row.get("n_amendments")),
                    row.get("base_effective", ""),
                    row.get("oracle_effective", ""),
                    _int_value(row.get("version_index") or row.get("pair_index")),
                    _int_value(row.get("version_count") or row.get("redaction_count")),
                    pair["as_of"],
                    pair["status"],
                    pair["source_basis"],
                    pair["comparison_class"],
                    pair["benchmark_reporting_stratum"],
                    pair["benchmark_reporting_headline_eligible"],
                    pair["core_benchmark"],
                    pair["n_ops"],
                    pair["browser_divergence_count"],
                    pair["mismatch_count"],
                    pair["ops_missing_count"],
                    pair["consolidated_missing_count"],
                    pair["browser_open_current_divergence_count"],
                    pair["section_total_count"],
                    pair["section_identical_count"],
                    pair["section_divergent_count"],
                    pair["section_replay_only_count"],
                    pair["section_consolidated_only_count"],
                    pair["section_text_total_chars"],
                    pair["section_text_identical_chars"],
                ),
            )
            text_rows = {}
            divergence_rows = []
            for divergence in divergences:
                replay_text = divergence["replay_text"]
                oracle_text = divergence["oracle_text"]
                replay_hash = _text_hash(replay_text) if replay_text is not None else None
                oracle_hash = _text_hash(oracle_text) if oracle_text is not None else None
                if replay_hash is not None:
                    text_rows[replay_hash] = replay_text
                if oracle_hash is not None:
                    text_rows[oracle_hash] = oracle_text
                divergence_rows.append(
                    (
                        divergence["pair_key"],
                        divergence["base_id"],
                        divergence["oracle_id"],
                        divergence["section_address"],
                        divergence["address"],
                        divergence["divergence_type"],
                        replay_hash,
                        oracle_hash,
                        divergence["residual_bucket"],
                        divergence["residual_evidence"],
                        divergence["alignment_peer_addresses"],
                        divergence["open_current"],
                        divergence["outreach_bucket"],
                        divergence["meaningful_candidate"],
                        divergence["outreach_evidence"],
                    )
                )
            con.executemany(
                "INSERT OR IGNORE INTO text_blobs(text_hash, text) VALUES (?, ?)",
                sorted(text_rows.items()),
            )
            con.executemany(
                """
                INSERT INTO divergences(
                    pair_key, base_id, oracle_id, section_address, address, divergence_type,
                    replay_text_hash, oracle_text_hash, residual_bucket, residual_evidence,
                    alignment_peer_addresses,
                    open_current, outreach_bucket, meaningful_candidate, outreach_evidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                divergence_rows,
            )
            if idx % 100 == 0:
                con.commit()
                print(f"  [{idx}/{len(rows)}] pairs, divergences={stats['divergences']}")
        con.commit()
    finally:
        con.close()
    return stats


def main(args: "argparse.Namespace") -> None:
    stats = build_ee_publication_db(
        corpus_path=Path(args.corpus),
        output_path=Path(args.output),
        archive_path=Path(args.db),
        limit=getattr(args, "limit", None),
        workers=getattr(args, "workers", 1),
    )
    print()
    print("=== EE Publication DB ===")
    print(f"  output          : {args.output}")
    print(f"  pairs           : {stats['pairs']}")
    print(f"  errors          : {stats['errors']}")
    print(f"  divergences     : {stats['divergences']}")
    print(f"  open divergences: {stats['open_divergences']}")
    print(f"  candidates      : {stats['meaningful_candidates']}")


__all__ = ["build_ee_publication_db", "main"]
