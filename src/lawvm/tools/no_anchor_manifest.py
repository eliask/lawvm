"""no_anchor_manifest.py — Norway's frozen content-addressed anchor + touch-relation
attribution engine (#183/#205, FOURTH jurisdiction).

This is the Norway analogue of :mod:`lawvm.tools.uk_anchor_manifest` (United
Kingdom), :mod:`lawvm.tools.ee_anchor_manifest` (Estonia) and
:mod:`lawvm.tools.fi_anchor_manifest` (Finland), extending the drift-robust #183
metric (``notes_internal/FABLE_CORRECTNESS_METRIC.md`` §3 / §5.4) to a fourth
jurisdiction. It is ADDITIVE: it never mutates the NO corpus, replay, or the
existing ``no-bench`` scoring; the default Norway bench stays byte-identical.

WHAT A NO ANCHOR IS (the jurisdiction adaptation — DOCUMENTED, and why it preserves
the same-dimension-touch principle). Finland enumerates the published
*consolidation snapshots* of one statute over its life (``plan_snapshots``); Estonia
enumerates the Riigi Teataja *terviktekst* chain per ``grupi_id`` (each carrying a
``kehtivuseAlgus`` effective date). Norway is like the UK — the archive carries ONE
oracle rendering, not an effective-date-addressed chain:

    Norway replays each ``lov`` from its original ``lovtidend``-published base text
    (``load_no_original_lti_bytes`` → :func:`parse_no_statute`) forward to a single
    point in time (``as_of``) and scores the replayed body against the CURRENT
    Lovdata consolidated text (``load_no_current_statute``). There is no crawled
    chain of dated consolidations to mirror EE's terviktekst chain — the oracle is
    the one live consolidated law. So NO, like UK, is a 2-node replay chain per act:

      * anchor[0] = the BASE (``no/lov/...`` original lovtidend text, before any
        amendment op). Its ``replay_text`` is the per-section normalized text of the
        parsed base IR (replay@base = the enacted statute).
      * anchor[1] = the CURRENT ``as_of``. Its ``replay_text`` is the per-section
        normalized text of the REPLAYED IR (the base IR after Norway's amendment
        replay applies every op forward to ``as_of``). It is SCORED against the
        current Lovdata consolidated oracle.

    The touch relation ``touch_set(base, current)`` is then, faithfully, the set of
    section keys whose replay text MOVED (or that appeared / disappeared) applying the
    amendment chain — replay's own notion of "what the intervening amendments
    touched", derived without op-extraction plumbing, exactly as FI derives it from
    adjacent snapshots and UK from enacted→current. This makes the SAME-DIMENSION-
    TOUCH principle bind on NO: a divergence at ``current`` over a section replay
    TOUCHED (its base→replayed text moved) that stays diverged from the oracle is a
    candidate REPLAY BUG (billable); a divergence over a section replay NEVER touched
    is a standing untouched divergence → oracle-side (non-billable). The convicting
    touch is in the same (wording) dimension as the divergence — the engine's
    invariant.

WHAT REPLAY IS SCORED AGAINST. For each act we run Norway's consistency-verification
pipeline :func:`lawvm.norway.verify.verify_no_against_current` (which itself runs
``replay_no_to_pit`` from the base forward to ``as_of``, then compares the replayed
body to the current Lovdata consolidated text via
:func:`lawvm.core.timeline_consistency.verify_consistency`). Norway's bench headline
(``no-bench``) scores exactly this per-section divergence partition
(``NOVerifyResult.divergences`` — the PRIMARY, post-``_partition_primary_divergences``
set), so a penalized section is commensurable with the ``no-bench`` structural axis.

STRUCTURE-SIGNATURE ADAPTATION (documented, per task — and WHY NO differs). EE's
section comparison is byte-exact; FI's is structure-aware over
``extract_ir_sections``. Norway is like EE — its verify path already emits a byte-
exact per-section verdict (``verify_consistency`` over ``irnode_to_no_comparison_text``
+ ``normalize_no_comparison_text`` — NO's editorial quotient). A section is PENALIZED
iff Norway's verify emitted a PRIMARY :class:`ConsistencyDivergence` at it (MISMATCH /
OPS_MISSING / CONSOLIDATED_MISSING). That is precisely the surface ``no-bench``'s
``structural_err`` counts, so ``penalized_keys`` is commensurable with the NO headline.

    We do NOT populate ``structural_only_penalized_keys`` — NO's divergence surface is
    wording (there is no separate byte-exact structure-only sub-verdict on the verify
    path), so the wording-level touch relation governs. This PRESERVES the same-
    dimension-touch principle: the convicting touch (a section whose replay text moved)
    is in the same (wording) dimension as the divergence (a section whose replay text
    differs from the oracle).

ORACLE-SUSPECT DISCIPLINE (reused, first-class — the NO adaptation). Norway's bench
already types oracle-side / acquisition-ceiling defects out of the SCORED lane: a
replay whose ``replay_status`` is a documented data ceiling
(``NO_BENCH_SOURCE_UNAVAILABLE_STATUSES``: contingent commencement, missing base
source, unknown effective status) or whose ``source_signal`` is
``sparse_indexed_history`` (≥50 primary divergences against ≤2 indexed amendments —
functionally an acquisition ceiling, not a replay surprise) is NOT a replay failure
per ``notes/NORWAY_LAWVM_STATUS.md``. That is Norway's analogue of Finland's
``get_consolidated_oracle_suspect`` / Estonia's ``source_adjudication.oracle_suspect``
/ the UK ``comparison_class`` witness. The attribution calculus consumes it the same
way: an oracle-suspect anchor's divergences type to
``temporal_mismatch_commensurability``, never a replay bug.

REUSED NEUTRAL CORE. The touch relation itself is jurisdiction-neutral: this module
imports :class:`fi_anchor_manifest.AnchorObservation`, :func:`touch_set`,
:class:`TouchObservation`, :func:`attribute_divergences`, and the
``_VERDICT_TO_FAMILY`` / ``_VERDICT_TO_STATUS`` maps unchanged (they take a ``sid``
string and operate on generic replay text maps — nothing Finland-specific). The
shared taxonomy (:class:`~lawvm.core.agreement_residual.AgreementResidual`) and the
shared CTSF gate primitives (``residual_set_diff_gate`` / ``FAIL_FAMILIES`` /
``GATE_VERSION``) are reused as-is. Only the anchor *enumeration* (base+current per
act), *scoring* (NO verify), and the content-addressing are NO-specific.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from lawvm.core.agreement_residual import AgreementResidual
from lawvm.core.ctsf_corpus_cache import memoize_default_corpus

# The touch relation is jurisdiction-neutral: reuse Finland's engine wholesale.
# These operate on a ``sid`` string + generic replay text maps; none of them import
# ``lawvm.finland``. Re-exported here so NO callers have a single surface.
from lawvm.tools.fi_anchor_manifest import (
    _VERDICT_TO_FAMILY,
    _VERDICT_TO_STATUS,
    AnchorObservation,
    TouchObservation,
    attribute_divergences,
)


MANIFEST_SCHEMA = "lawvm.no_anchor_manifest.v1"

# The two anchor version tags of the NO 2-node replay chain per act.
_BASE_TAG = "base"
_CURRENT_TAG = "current"


def _default_db() -> Path:
    """The default Norway source archive (the symlinked/canonical farchive)."""
    from lawvm.norway.sources import resolve_no_source_path

    return resolve_no_source_path(None)


def _repo_root() -> Path:
    # src/lawvm/tools/no_anchor_manifest.py → parents[3] == repo root.
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Section text projection (NO's commensurable per-section wording surface)
# ---------------------------------------------------------------------------


def _no_section_text_map(body: Any) -> dict[str, str]:
    """Per-section NO comparison text of an IR body (base or replayed).

    Keys are Norway's section keys (``section_key_from_path`` over the section
    address — the SAME key space ``no-bench`` derives divergence addresses in);
    values are the NO editorial-quotient comparison text
    (``irnode_to_no_comparison_text`` + ``normalize_no_comparison_text`` —
    definitionally NO's editorial quotient, the exact normalization Norway's
    ``verify_consistency`` scores over). This is the wording surface the touch
    relation reads.
    """
    from lawvm.norway.verify import (
        irnode_to_no_comparison_text,
        normalize_no_comparison_text,
    )
    from lawvm.tools.section_keys import extract_ir_sections

    if body is None:
        return {}
    out: dict[str, str] = {}
    for key, node in extract_ir_sections(body).items():
        try:
            out[str(key)] = normalize_no_comparison_text(
                irnode_to_no_comparison_text(node)
            )
        except Exception:  # noqa: BLE001 — a bad node must not sink the map
            out[str(key)] = ""
    return out


# ---------------------------------------------------------------------------
# Content-addressed anchor + manifest (mirrors uk/ee_anchor_manifest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    """One content-addressed NO rendering (base or current).

    ``artifact_hash`` pins the RAW published Lovdata/lovtidend bytes (immutability
    check — any re-edit changes it). ``cnf_hash`` pins the normative-projected
    per-section text map, so an editorial-only refresh moves ``artifact_hash`` but
    leaves ``cnf_hash`` stable.
    """

    base_id: str
    version_tag: str
    as_of: Optional[str]
    artifact_hash: str
    cnf_hash: str
    n_sections: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_tag": self.version_tag,
            "as_of": self.as_of,
            "artifact_hash": self.artifact_hash,
            "cnf_hash": self.cnf_hash,
            "n_sections": self.n_sections,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StatuteManifest:
    base_id: str
    anchors: tuple[Anchor, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_id": self.base_id,
            "anchors": [a.to_dict() for a in self.anchors],
        }


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cnf_hash_of_map(cnf_map: dict[str, str]) -> str:
    canonical = json.dumps(cnf_map, sort_keys=True, ensure_ascii=False)
    return _sha256_hex(canonical.encode("utf-8"))


def build_statute_manifest(
    base_id: str, as_of: str, *, data_dir: Path | None = None
) -> StatuteManifest:
    """Content-address the base + current anchors of *base_id* at *as_of*.

    Never raises for per-anchor problems — an unreadable/unparseable anchor yields
    an empty-hash anchor carrying a ``reason``.
    """
    from lawvm.norway.grafter import parse_no_statute
    from lawvm.norway.sources import (
        load_no_current_bytes,
        load_no_original_lti_bytes,
        resolve_no_source_path,
    )

    data_dir = resolve_no_source_path(data_dir)
    anchors: list[Anchor] = []

    # Base anchor.
    base_reason = ""
    base_art = ""
    base_cnf = ""
    base_n = 0
    try:
        raw = load_no_original_lti_bytes(base_id, data_dir)
        if raw:
            base_art = _sha256_hex(raw)
            statute = parse_no_statute(raw, base_id)
            cnf_map = _no_section_text_map(statute.body if statute else None)
            base_cnf = _cnf_hash_of_map(cnf_map)
            base_n = len(cnf_map)
        else:
            base_reason = "base-artifact-absent"
    except Exception as exc:  # noqa: BLE001
        base_reason = f"error:{exc}"
    anchors.append(
        Anchor(
            base_id=base_id,
            version_tag=_BASE_TAG,
            as_of=None,
            artifact_hash=base_art,
            cnf_hash=base_cnf,
            n_sections=base_n,
            reason=base_reason,
        )
    )

    # Current (oracle) anchor — content-address the raw current bytes only (the
    # replayed cnf map is a scoring product, hashed in the manifest via the
    # base-side projection; the oracle's own text is the immutability witness).
    cur_reason = ""
    cur_art = ""
    try:
        cur_raw = load_no_current_bytes(base_id, data_dir)
        if cur_raw:
            cur_art = _sha256_hex(cur_raw)
        else:
            cur_reason = "current-artifact-absent"
    except Exception as exc:  # noqa: BLE001
        cur_reason = f"error:{exc}"
    anchors.append(
        Anchor(
            base_id=base_id,
            version_tag=_CURRENT_TAG,
            as_of=as_of,
            artifact_hash=cur_art,
            cnf_hash="",
            n_sections=0,
            reason=cur_reason,
        )
    )
    return StatuteManifest(base_id=base_id, anchors=tuple(anchors))


def build_manifest(
    corpus: Iterable[tuple[str, str]], *, data_dir: Path | None = None
) -> dict[str, Any]:
    """Build a deterministic, diffable manifest over a ``(base_id, as_of)`` corpus."""
    statutes = {
        base_id: build_statute_manifest(base_id, as_of, data_dir=data_dir).to_dict()
        for base_id, as_of in sorted(corpus)
    }
    return {
        "schema": MANIFEST_SCHEMA,
        "jurisdiction": "norway",
        "statute_count": len(statutes),
        "statutes": statutes,
    }


# ---------------------------------------------------------------------------
# Per-act scoring (NO verify → AnchorObservation chain)
# ---------------------------------------------------------------------------


# Replay statuses / signals that Norway's OWN bench types out of the SCORED lane as
# documented data ceilings (see no_bench.py + notes/NORWAY_LAWVM_STATUS.md). An
# anchor scored on such a replay is commensurability-suspect: its divergences type
# to temporal_mismatch, never a replay bug (the oracle-suspect discipline).
def _no_oracle_suspect(result: Any) -> Optional[str]:
    """NO's per-anchor commensurability witness (None when the anchor is core).

    Non-empty iff Norway's own bench would NOT score this replay as SCORED: a
    data-ceiling ``replay_status`` (``NO_BENCH_SOURCE_UNAVAILABLE_STATUSES``) or the
    ``sparse_indexed_history`` acquisition-ceiling source signal. This is the exact
    partition ``no_bench.no_bench_unit_result`` uses to route SOURCE_UNAVAILABLE.
    """
    from lawvm.norway.sources import (
        NO_BENCH_SOURCE_UNAVAILABLE_STATUSES as _CEILING_STATUSES,
    )

    status = getattr(result, "replay_status", "") or ""
    if status in _CEILING_STATUSES:
        return f"replay_status:{status}"
    if getattr(result, "source_signal", None) == "sparse_indexed_history":
        return "source_signal:sparse_indexed_history"
    return None


# ---------------------------------------------------------------------------
# Confirmed oracle-side editorial corrections (the per-section oracle_suspect rail)
# ---------------------------------------------------------------------------
#
# Norway's Lovdata consolidation is law-IN-FORCE but not necessarily consolidation-
# CORRECT (``notes/NORWAY_LAWVM_STATUS.md``; project discipline
# ``reference_authoritative_oracle_not_correct``): the keeper sometimes SILENTLY
# corrects a typo in the enacted lovtidend text without any amending act. Replay
# faithfully preserves the ENACTED wording, so at such a section replay diverges from
# the oracle even though no op is missing — the divergence is oracle-side, not a replay
# bug. Because the touch relation keys on the whole section, an incidental replay touch
# ELSEWHERE in the section (e.g. a genuinely-applied amendment to another clause) would
# otherwise mis-convict the untouched, oracle-corrected clause as a replay bug.
#
# This registry types those divergences out of the billable lane — the per-SECTION
# analogue of the per-anchor ``oracle_suspect`` witness (``_no_oracle_suspect``) and of
# Finland's ``get_consolidated_oracle_suspect`` / Estonia's
# ``source_adjudication.oracle_suspect``. It is DELIBERATELY narrow: each entry is
# EXACT-TEXT-PINNED (the enacted fragment replay carries + the corrected fragment the
# oracle carries), and it only fires when substituting the enacted fragment for the
# corrected one in the replayed section text reproduces the oracle section text BYTE-
# FOR-BYTE. A confirmation therefore cannot mask a real replay defect: if replay drops
# or mangles ANYTHING else at the section, the byte-exact reconciliation fails and the
# section stays penalized/billable. Never a blanket "trust the oracle" — a single
# audited editorial fix, keyed to the exact bytes.
#
# Each key is ``(base_id, section_key)``; each value is a tuple of
# ``(enacted_fragment, corrected_fragment, note)`` corrections that jointly reconcile
# the section (applied in listed order).
_NO_ORACLE_EDITORIAL_CORRECTIONS: dict[
    tuple[str, str], tuple[tuple[str, str, str], ...]
] = {
    # Tilskuddsordning aug 2020 (Tilskudd ved koronautbruddet), §5 second-condition
    # item. The enacted lovtidend text cross-references "skatteloven § 23 første ledd
    # bokstav b" — a Lovtidend typo: skatteloven (no/lov/1999-03-26-14) has NO § 23; the
    # provision on non-resident tax liability actually cited is § 2-3 ("Person som ikke
    # er bosatt og selskap m.v. som ikke er hjemmehørende i riket"). Lovdata's live
    # consolidation SILENTLY corrected the missing hyphen ("§ 23" → "§ 2-3", and
    # hyperlinked it to lov/1999-03-26-14/§2-3) with no amending act. Replay preserves
    # the enacted "§ 23"; the divergence is the keeper's editorial correction. Not a
    # missed/mangled op — see the §5 amendments (2021-06-18-112, 2022-01-28-3), neither
    # of which touches this clause.
    (
        "no/lov/2020-12-18-156",
        "section:5",
    ): (
        (
            "skatteloven § 23 første ledd bokstav b",
            "skatteloven § 2-3 første ledd bokstav b",
            "Lovtidend typo § 23 for skatteloven § 2-3 (non-resident tax liability); "
            "silently corrected by the Lovdata consolidation, no amending act.",
        ),
    ),
}


def _oracle_editorial_reconciles(
    base_id: str,
    section_key: str,
    replay_text: str,
    oracle_text: str,
) -> Optional[str]:
    """Confirmed oracle-editorial-correction witness for a penalized section (else None).

    Returns a non-empty witness string iff ``(base_id, section_key)`` carries a curated
    correction AND applying every listed ``enacted→corrected`` substitution to the
    replayed section text reproduces the oracle section text BYTE-FOR-BYTE. The byte-
    exact gate is what makes this safe: it fires only when the WHOLE section-level
    divergence is exactly the confirmed editorial fix(es) and nothing else — any other
    replay drift at the section leaves ``patched != oracle`` and the section stays
    billable. ``None`` when there is no entry, a fragment is absent, or the substitution
    does not reconcile (a divergence that is NOT purely the confirmed correction).
    """
    corrections = _NO_ORACLE_EDITORIAL_CORRECTIONS.get((base_id, section_key))
    if not corrections:
        return None
    patched = replay_text
    notes: list[str] = []
    for enacted, corrected, note in corrections:
        if enacted not in patched:
            return None
        patched = patched.replace(enacted, corrected)
        notes.append(note)
    if patched != oracle_text:
        return None
    return "; ".join(notes)


def _oracle_editorial_note(base_id: str, section_key: str) -> str:
    """Human-readable note(s) for a confirmed oracle-editorial key (for evidence)."""
    corrections = _NO_ORACLE_EDITORIAL_CORRECTIONS.get((base_id, section_key), ())
    return "; ".join(note for _enacted, _corrected, note in corrections)


@dataclass(frozen=True)
class _NOReplayScore:
    """The raw materials of one NO base→current replay comparison."""

    base_text: dict[str, str]
    replayed_text: dict[str, str]
    penalized_keys: frozenset[str]
    # Penalized keys whose ENTIRE section-level divergence is a confirmed oracle-side
    # editorial correction (``_NO_ORACLE_EDITORIAL_CORRECTIONS``). These are NOT scored
    # as replay bugs: the attribution retypes them to ``oracle_suspect_standing_untouched``
    # (family ``oracle_editorial_pathology`` — the non-billable WARN lane).
    oracle_editorial_keys: frozenset[str]
    n_oracle_sections: int
    oracle_suspect: Optional[str]
    status: str


def _score_no_replay(
    base_id: str, as_of: str, *, data_dir: Path | None = None
) -> _NOReplayScore:
    """Replay *base_id* to *as_of* and gather the per-section wording surfaces.

    Reuses Norway's verify pipeline exactly (``verify_no_against_current``), so the
    penalized set is bench-identical (the PRIMARY divergence partition). Returns the
    base + replayed section-text maps, the penalized section keys, the oracle section
    count, and NO's oracle-suspect witness.
    """
    from lawvm.norway.grafter import parse_no_statute
    from lawvm.norway.sources import (
        load_no_original_lti_bytes,
        resolve_no_source_path,
    )
    from lawvm.norway.verify import load_no_current_statute, verify_no_against_current
    from lawvm.tools.section_keys import section_key_from_path

    dd = resolve_no_source_path(data_dir)

    # Base section text (seeds the touch relation) — loaded directly from the
    # original lovtidend text, independent of replay (the touch relation needs the
    # BEFORE-amendments wording).
    base_raw = load_no_original_lti_bytes(base_id, dd)
    if not base_raw:
        return _NOReplayScore({}, {}, frozenset(), frozenset(), 0, None, "BASE_ABSENT")
    base_statute = parse_no_statute(base_raw, base_id)
    base_text = _no_section_text_map(base_statute.body if base_statute else None)

    result = verify_no_against_current(base_id, as_of=as_of, data_dir=dd)
    if result.error:
        return _NOReplayScore(
            base_text, {}, frozenset(), frozenset(), 0, None, f"ERROR:{result.error}"
        )

    replay = result.replay
    replayed_body = (
        replay.replayed.body if replay is not None and replay.replayed is not None else None
    )
    replayed_text = _no_section_text_map(replayed_body)

    # Penalized keys = the PRIMARY divergences Norway's verify emitted (the SAME set
    # ``no-bench``'s structural_err counts). A divergence's section key is the
    # ``section_key_from_path`` of its address — the exact key space
    # ``extract_ir_sections`` (and thus ``replayed_text``) uses, so the penalized set
    # is commensurable with both the replay-text touch surface and the NO headline.
    penalized: set[str] = set()
    for div in result.divergences or []:
        key = section_key_from_path(div.address.path)
        if key:
            penalized.add(key)

    # Per-SECTION oracle-suspect rail: of the penalized keys, which are a CONFIRMED
    # oracle-side editorial correction (``_NO_ORACLE_EDITORIAL_CORRECTIONS``)? A key
    # qualifies only when substituting the curated enacted→corrected fragment(s) into
    # the replayed section text reproduces the ORACLE section text byte-for-byte — so we
    # need the oracle's own per-section wording surface. This is byte-exact-gated: any
    # other replay drift at the section keeps it billable (see the registry docstring).
    oracle_editorial: set[str] = set()
    if any((base_id, key) in _NO_ORACLE_EDITORIAL_CORRECTIONS for key in penalized):
        try:
            oracle_statute = load_no_current_statute(base_id, dd)
            oracle_text = _no_section_text_map(oracle_statute.body)
        except Exception:  # noqa: BLE001 — a missing oracle just leaves keys billable
            oracle_text = {}
        for key in penalized:
            witness = _oracle_editorial_reconciles(
                base_id,
                key,
                replayed_text.get(key, ""),
                oracle_text.get(key, ""),
            )
            if witness:
                oracle_editorial.add(key)

    # The oracle section denominator is (replay sections ∪ penalized keys): a
    # penalized key not present in the replayed body (CONSOLIDATED_MISSING /
    # OPS_MISSING) still counts a scored oracle unit.
    n_oracle = len(set(replayed_text) | penalized)

    return _NOReplayScore(
        base_text=base_text,
        replayed_text=replayed_text,
        penalized_keys=frozenset(penalized),
        oracle_editorial_keys=frozenset(oracle_editorial),
        n_oracle_sections=n_oracle,
        oracle_suspect=_no_oracle_suspect(result),
        status="OK",
    )


def score_no_anchors(
    base_id: str, as_of: str, *, data_dir: Path | None = None
) -> list[AnchorObservation]:
    """Score the base→current 2-node replay chain of *base_id* at *as_of*.

    Returns two :class:`AnchorObservation`s (base first): the base (its
    ``replay_text`` = base section-text map, seeding the touch relation) and the
    current anchor (``replay_text`` = replayed section-text map, ``penalized_keys`` =
    the sections Norway's verify flagged divergent against the current Lovdata oracle).
    ``touch_set(base, current)`` is thus the sections whose replay text MOVED applying
    the amendment chain — replay's own notion of what the amendments touched, in the
    same (wording) dimension as the divergence.
    """
    return _anchors_from_score(
        as_of, _score_no_replay(base_id, as_of, data_dir=data_dir)
    )


def _anchors_from_score(as_of: str, score: _NOReplayScore) -> list[AnchorObservation]:
    """Build the base→current anchor pair from an already-computed replay score.

    Split out of :func:`score_no_anchors` so :func:`attribute_statute` can score the
    (heavy) NO replay ONCE and reuse the same ``_NOReplayScore`` for both the anchor
    chain and the per-section oracle-editorial retyping (``oracle_editorial_keys``).
    """
    if score.status != "OK":
        return [
            AnchorObservation(
                version_tag=_CURRENT_TAG,
                amendment_id=_CURRENT_TAG,
                as_of=as_of,
                struct_sim=-1.0,
                n_sections=0,
                n_penalized=0,
                penalized_keys=frozenset(),
                replay_text={},
                oracle_suspect=None,
                status=score.status,
            )
        ]

    # anchor[0] — the BASE. It seeds the touch relation (its replay_text is the base
    # section-text map). It is NOT scored against the oracle in the chain (base is
    # replay's source, exactly like UK/EE/FI skip re-scoring the base); struct_sim =
    # 1.0 so it participates as a scored chain node.
    base = AnchorObservation(
        version_tag=_BASE_TAG,
        amendment_id=_BASE_TAG,
        as_of=_BASE_TAG,
        struct_sim=1.0,
        n_sections=len(score.base_text),
        n_penalized=0,
        penalized_keys=frozenset(),
        replay_text=dict(score.base_text),
        oracle_suspect=None,
        status="BASE",
    )

    n_sections = score.n_oracle_sections
    struct_sim = 1.0 if not n_sections else 1.0 - len(score.penalized_keys) / n_sections

    current = AnchorObservation(
        version_tag=_CURRENT_TAG,
        amendment_id=_CURRENT_TAG,
        as_of=as_of,
        struct_sim=struct_sim,
        n_sections=n_sections,
        n_penalized=len(score.penalized_keys),
        penalized_keys=frozenset(score.penalized_keys),
        replay_text=dict(score.replayed_text),
        oracle_suspect=score.oracle_suspect,
        status="OK",
        # NO's per-key surface is section wording (there is no byte-exact structure-
        # only sub-verdict on the verify path): every NO penalized section is a
        # wording divergence, so the wording-level touch relation governs (mirrors
        # EE's documented choice for its byte-exact model).
        structural_only_penalized_keys=frozenset(),
    )
    return [base, current]


# ---------------------------------------------------------------------------
# Statute-level driver: score anchors + attribute + gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatuteAttribution:
    base_id: str
    as_of: str
    anchors: tuple[AnchorObservation, ...]
    observations: tuple[TouchObservation, ...]
    status: str = "OK"

    @property
    def scored(self) -> list[AnchorObservation]:
        return [a for a in self.anchors if a.struct_sim >= 0.0]

    @property
    def min_over_life(self) -> Optional[float]:
        return min((a.struct_sim for a in self.scored), default=None)

    @property
    def latest_scored(self) -> Optional[float]:
        for a in reversed(self.anchors):
            if a.struct_sim >= 0.0:
                return a.struct_sim
        return None

    @property
    def candidate_bug_observations(self) -> tuple[TouchObservation, ...]:
        return tuple(
            o
            for o in self.observations
            if o.verdict == "candidate_replay_bug_persistent_post_touch"
        )

    @property
    def untyped_observations(self) -> tuple[TouchObservation, ...]:
        return tuple(o for o in self.observations if o.verdict == "untyped")

    @property
    def is_gated_clean(self) -> bool:
        return not self.candidate_bug_observations and not self.untyped_observations


def attribute_statute(
    base_id: str, as_of: str, *, data_dir: Path | None = None
) -> StatuteAttribution:
    """Score the base→current chain of *base_id* at *as_of*, then attribute + gate.

    The attribution calculus (Finland's neutral ``attribute_divergences``) runs over
    the chronological scored anchor list ``[base, current]``. A ``current`` divergence
    over a section replay TOUCHED (its base→replayed text moved) that stays diverged
    is a candidate replay bug; a divergence over an untouched section is oracle-side.

    PER-SECTION ORACLE-SUSPECT RETYPING. The neutral touch relation keys on the whole
    section, so a genuinely-applied amendment to ONE clause of a section makes the whole
    section "touched" — which would mis-convict a CONFIRMED oracle-side editorial
    correction at a DIFFERENT, untouched clause of the same section as a replay bug (the
    touch is not in the same textual locus as the divergence). After attribution we
    retype any observation over a byte-exact-confirmed oracle-editorial key
    (``_score_no_replay``'s ``oracle_editorial_keys``) to
    ``oracle_suspect_standing_untouched`` (family ``oracle_editorial_pathology``, the
    non-billable WARN lane). This never masks a real defect: the key only qualifies when
    the WHOLE section divergence reconciles to the curated correction byte-for-byte.
    """
    score = _score_no_replay(base_id, as_of, data_dir=data_dir)
    anchors = _anchors_from_score(as_of, score)
    scored = [a for a in anchors if a.struct_sim >= 0.0]
    if len(scored) < 2:
        return StatuteAttribution(
            base_id=base_id,
            as_of=as_of,
            anchors=tuple(anchors),
            observations=(),
            status="ERROR:fewer-than-2-scorable-anchors",
        )
    observations = list(attribute_divergences(base_id, anchors))
    if score.oracle_editorial_keys:
        retyped: list[TouchObservation] = []
        for obs in observations:
            if obs.section_key in score.oracle_editorial_keys:
                note = _oracle_editorial_note(base_id, obs.section_key)
                retyped.append(
                    TouchObservation(
                        sid=obs.sid,
                        section_key=obs.section_key,
                        verdict="oracle_suspect_standing_untouched",
                        window=obs.window,
                        touching_amendments=(),
                        evidence=(
                            "confirmed oracle-side editorial correction (byte-exact "
                            f"reconciliation): {note}"
                        ),
                    )
                )
            else:
                retyped.append(obs)
        observations = retyped
    return StatuteAttribution(
        base_id=base_id,
        as_of=as_of,
        anchors=tuple(anchors),
        observations=tuple(observations),
    )


# ---------------------------------------------------------------------------
# The REAL Norway anchor corpus + the CTSF residual-set scorer / baseline
# ---------------------------------------------------------------------------


REAL_ANCHOR_NO_JURISDICTION = "norway"

# The frozen, content-pinned NO touch-relation corpus: real ``lov`` acts with genuine
# amendment chains that are 0-BILLABLE (no ``replay_bug``/``unknown`` residual). Most
# REPLAY CLEAN (base→current reproduces every replay-touched oracle section); a member
# MAY carry a typed NON-billable residual (``oracle_editorial_pathology`` — the WARN
# lane, e.g. a confirmed oracle-side editorial correction), exactly as the FI/UK/EE
# corpora do. Acts whose replay surfaces genuine BILLABLE residuals (a replay-touched
# section the oracle carries that replay drops/mismatches) are DELIBERATELY EXCLUDED and
# reported as found bugs, never frozen green. Each entry is ``(base_id, as_of)``. Sorted,
# unique.
REAL_ANCHOR_NO_CORPUS: tuple[tuple[str, str], ...] = (
    ("no/lov/2004-05-14-25", "2026-03-29"),   # Voldgiftsloven
    ("no/lov/2006-08-18-61", "2026-03-29"),   # Beredskapslagringsloven
    ("no/lov/2017-06-16-60", "2026-03-29"),   # Klimaloven (3 amendments applied)
    ("no/lov/2019-06-21-70", "2026-03-29"),   # Havne- og farvannsloven
    ("no/lov/2020-05-07-38", "2026-03-29"),   # Rekonstruksjonsloven (§10-64 sunset date replays clean)
    ("no/lov/2020-12-18-156", "2026-03-29"),  # Tilskuddsordning aug 2020 (§5 oracle-editorial: § 23→§ 2-3 WARN lane)
    ("no/lov/2021-05-21-42", "2026-03-29"),   # Språklova
    ("no/lov/2022-05-12-28", "2026-03-29"),   # Advokatloven
    ("no/lov/2023-06-16-62", "2026-03-29"),   # Valgloven
    ("no/lov/2024-01-12-1", "2026-03-29"),    # Suppleringsskatteloven (3 amendments)
    ("no/lov/2025-04-25-12", "2026-03-29"),   # Innkrevingsloven
)

REAL_ANCHOR_NO_CORPUS_SIDS: tuple[str, ...] = tuple(
    sorted(base_id for base_id, _as_of in REAL_ANCHOR_NO_CORPUS)
)

GATE_NO_BASELINE_PATH = Path("tests/data/ctsf_gate_no_residual_baseline.json")


def no_anchor_corpus_available() -> bool:
    """True iff the Norway source archive is present (the corpus can be scored)."""
    try:
        return _default_db().exists()
    except Exception:  # noqa: BLE001
        return False


@memoize_default_corpus
def score_no_real_corpus(
    corpus: Iterable[tuple[str, str]] | None = None,
    *,
    data_dir: Path | None = None,
) -> dict[str, dict[str, int]]:
    """Score the NO #205 touch-relation corpus into its typed-residual set.

    For each act, run the ``no_anchor_manifest`` attribution engine over its
    base→current replay window and project each ``TouchObservation`` into its CTSF
    residual family (the shared ``_VERDICT_TO_FAMILY``). Returns the same diffable
    ``{sid: {family: count}}`` shape as the FI/EE/UK real-corpus scorers, only non-zero
    families retained, a clean-but-scored act present with an empty family map.
    Deterministic in sid order.

    Reads the Norway Farchive (per-act NO replay). Deterministic given the frozen
    corpus bytes.
    """
    rows = tuple(corpus) if corpus is not None else REAL_ANCHOR_NO_CORPUS
    out: dict[str, dict[str, int]] = {}
    for base_id, as_of in rows:
        attr = attribute_statute(base_id, as_of, data_dir=data_dir)
        families: dict[str, int] = {}
        for obs in attr.observations:
            family = _VERDICT_TO_FAMILY[obs.verdict]
            families[family] = families.get(family, 0) + 1
        out[base_id] = {fam: n for fam, n in sorted(families.items()) if n}
    return dict(sorted(out.items()))


def _no_baseline_payload(residuals: dict[str, dict[str, int]]) -> dict[str, Any]:
    from lawvm.core.ctsf_gate import FAIL_FAMILIES, GATE_VERSION
    from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES

    total = sum(
        count for families in residuals.values() for count in families.values()
    )
    return {
        "_doc": (
            "CTSF residual-set-diff gate baseline — NORWAY (#183/#205). Frozen typed-"
            "residual set of the REAL NO touch-relation anchor corpus "
            "(REAL_ANCHOR_NO_CORPUS), keyed {sid: {family: count}} with only non-zero "
            "families retained. A NO anchor is the base→current replay window (Norway "
            "replays the lovtidend base forward to as_of and scores against the single "
            "live Lovdata consolidated oracle — no crawled dated-PIT chain). The gate "
            "FAILs iff a NEW replay_bug/unknown residual appears vs this set; WARNs on a "
            "typed oracle/editorial/temporal move. This corpus is curated 0-BILLABLE "
            "(the honest steady state); NO acts whose base→current replay surfaces "
            "genuine billable residuals are deliberately excluded (they are defects to "
            "fix, not to freeze — see the module's found-bug report). Regenerate with "
            "`uv run python -m lawvm.tools.no_anchor_manifest --update-baseline` (needs "
            "the Norway Farchive) after a legitimate, reviewed corpus/projection change "
            "— a preregistered predict-then-compare event, never a silent baseline move."
        ),
        "gate_version": GATE_VERSION,
        "jurisdiction": REAL_ANCHOR_NO_JURISDICTION,
        "corpus_sids": list(REAL_ANCHOR_NO_CORPUS_SIDS),
        "families": list(RESIDUAL_VERDICT_FAMILIES),
        "fail_families": list(FAIL_FAMILIES),
        "total_residuals": total,
        "residuals": residuals,
    }


def load_no_baseline(path: Path | None = None) -> dict[str, dict[str, int]]:
    """Load the frozen NO typed-residual baseline ({sid: {family: count}})."""
    p = path if path is not None else _repo_root() / GATE_NO_BASELINE_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    residuals = data.get("residuals", {})
    return {
        sid: {fam: int(cnt) for fam, cnt in families.items()}
        for sid, families in sorted(residuals.items())
    }


def write_no_baseline(
    residuals: dict[str, dict[str, int]] | None = None, path: Path | None = None
) -> Path:
    """Write the frozen NO typed-residual baseline. Regeneration entrypoint.

    Defaults to snapshotting the REAL NO corpus (``score_no_real_corpus()``). Reads
    the Norway Farchive; pass ``residuals`` to write a precomputed set (corpus-free).
    """
    p = path if path is not None else _repo_root() / GATE_NO_BASELINE_PATH
    payload = _no_baseline_payload(
        residuals if residuals is not None else score_no_real_corpus()
    )
    p.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p


def run_no_gate_report(baseline_path: Path | None = None) -> Any:
    """Score the REAL NO corpus and diff it against the frozen NO baseline.

    Reads the Norway Farchive. Returns the shared :class:`GateResult`.
    """
    from lawvm.core.ctsf_gate import residual_set_diff_gate

    current = score_no_real_corpus()
    baseline = load_no_baseline(baseline_path)
    return residual_set_diff_gate(current, baseline)


# ---------------------------------------------------------------------------
# AgreementResidual projection (reuse the shared taxonomy + FI verdict maps)
# ---------------------------------------------------------------------------


def observation_to_residual(obs: TouchObservation) -> AgreementResidual:
    """Project one NO touch observation into the shared AgreementResidual taxonomy.

    Reuses Finland's ``_VERDICT_TO_FAMILY`` / ``_VERDICT_TO_STATUS`` (the verdict→
    family mapping is jurisdiction-neutral), stamped with the NO jurisdiction.
    """
    family = _VERDICT_TO_FAMILY[obs.verdict]
    status = _VERDICT_TO_STATUS[obs.verdict]
    return AgreementResidual(
        residual_id=f"no:anchor-touch:{obs.sid}:{obs.section_key}:{obs.window}",
        jurisdiction="norway",
        agreement_surface="no_anchor_touch",
        family=family,
        agreement_residual_status=status,
        owner_phase="no_bench.anchor.touch_relation",
        rule_id=obs.verdict,
        source_artifact_id=obs.sid,
        safe_default="classify_without_rewriting_replay_or_oracle",
        forbidden_shortcuts=(
            "touch_observation_as_replay_authorization",
            "oracle_conviction_as_source_truth",
        ),
        detail={
            "section_key": obs.section_key,
            "window": obs.window,
            "touching_amendments": list(obs.touching_amendments),
            "evidence": obs.evidence,
        },
    )


# ---------------------------------------------------------------------------
# CLI: attribute NO acts / regenerate the baseline
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="lawvm.tools.no_anchor_manifest",
        description="Run the NO touch-relation attribution engine over one or more "
        "Norway lov acts (base_id[:as_of]), the #183/#205 metric.",
    )
    parser.add_argument(
        "acts",
        nargs="*",
        help="base_id or base_id:as_of pairs (default: the frozen REAL_ANCHOR_NO_CORPUS)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="regenerate tests/data/ctsf_gate_no_residual_baseline.json from the real corpus",
    )
    args = parser.parse_args(argv)

    if args.update_baseline:
        p = write_no_baseline()
        print(f"wrote NO baseline: {p}")
        return 0

    if args.acts:
        rows: list[tuple[str, str]] = []
        for spec in args.acts:
            if ":" in spec and spec.count(":") == 1 and not spec.startswith("no/"):
                base_id, as_of = spec.split(":", 1)
            elif "@" in spec:
                base_id, as_of = spec.split("@", 1)
            else:
                base_id, as_of = spec, "2026-03-29"
            rows.append((base_id, as_of))
    else:
        rows = list(REAL_ANCHOR_NO_CORPUS)

    rc = 0
    for base_id, as_of in rows:
        attr = attribute_statute(base_id, as_of)
        if attr.status != "OK":
            print(f"\n=== {base_id} @ {as_of} === {attr.status}", file=sys.stderr)
            rc = 1
            continue
        gate = "GATED-CLEAN" if attr.is_gated_clean else "CANDIDATE-BUG"
        print(
            f"\n=== {base_id} @ {as_of}  ({len(attr.scored)} scored anchors)  [{gate}] ==="
        )
        print(
            f"  min-over-life={100 * (attr.min_over_life or 0):.2f}%  "
            f"latest={100 * (attr.latest_scored or 0):.2f}%"
        )
        for o in attr.observations:
            print(f"  {o.section_key:<24} {o.verdict}  window={o.window}")
        if not attr.is_gated_clean:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
