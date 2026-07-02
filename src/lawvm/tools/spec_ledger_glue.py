"""First-class the two implicit spec-glue components: ≺ (precedence policy) and
≈ (comparison lens).

``notes_internal/FABLE_SPEC_RECONSTRUCTION.md`` §1 names two pieces of spec content
that are load-bearing yet, today, live only implicitly in code:

* **≺, the precedence / conflict policy.** Two identical rule sets with different
  span-overlap suppression or recognizer order produce *different law*. ≺ is spec, but
  today it is mostly implicit in code order (the UK nlp_parser precedence policy; FI
  johtolause rule ordering). A silent rewrite of that order would silently change the
  law the spec claims (§6.4(3)). Surfacing it as a *named, versioned* ledger entry makes
  the policy a citable object with a falsifier of its own.

* **≈, the comparison lens.** The editorial-normalization equivalence under which
  "replay matches oracle" is judged. The immunizing stratagem (§3.4(2)) is *lens
  inflation*: growing ≈ until divergences vanish. The guard is to fix and *version* the
  lens before comparison; an unversioned lens tuned against the oracle it judges is an
  immunizing stratagem by construction. Each elision class carries its own believed_spec
  and falsifier here.

This module is READ-ONLY / ADDITIVE by design (mirroring ``spec_ledger`` itself): it is
a data catalog of the glue components plus a small renderer. It imports nothing from the
replay path and changes no apply/replay behaviour. The entries are *versioned* so a lens
or precedence change is a visible ``version`` bump with a recorded ``changelog`` line,
never a silent edit (the anti-immunization discipline).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Tuple

GlueKind = Literal["precedence", "lens"]


@dataclass(frozen=True)
class GlueComponent:
    """One named, versioned spec-glue entry (≺ or ≈).

    ``believed_spec`` and ``falsifier`` mirror the per-rule schema (§2) so a glue
    component is falsifiable on the same terms as a rule. ``version`` + ``changelog``
    make every change to the policy/lens a visible event — the anti-immunization guard:
    an unversioned lens that drifts to hide divergences is the classic immunizing
    stratagem (§3.4(2)); a bumped version with a changelog line is not.
    """

    glue_id: str
    kind: GlueKind
    jurisdiction: str  # "*" = jurisdiction-neutral (a cross-cutting policy/lens)
    version: str
    believed_spec: str
    falsifier: str
    code_anchor: str  # where the policy/lens lives in code today (provenance)
    changelog: Tuple[str, ...] = field(default_factory=tuple)
    # #184 CTSF unification: when this lens is realized as a Canonical Text-State
    # Form editorial rule, this points at the rule_id whose four-part control-pair
    # admission gate (lawvm.core.ctsf_admission_gate) validates it.  Empty for a
    # lens not (yet) migrated into CTSF — default-empty so existing entries are
    # byte-identically unchanged.
    ctsf_rule_id: str = ""


# ---------------------------------------------------------------------------
# ≺ — precedence / conflict policies (currently implicit in code order)
# ---------------------------------------------------------------------------

_PRECEDENCE: Tuple[GlueComponent, ...] = (
    GlueComponent(
        glue_id="fi.precedence.johtolause_rule_order",
        kind="precedence",
        jurisdiction="fi",
        version="v1",
        believed_spec=(
            "When two FI johtolause recognizers match overlapping spans of the same "
            "instruction clause, the earlier-registered ParseRule (registry declaration "
            "order in johtolause/rule_registry.py) wins and suppresses the later match; "
            "coordinated targets under a shared verb inherit the first-registered arm's "
            "op shape."
        ),
        falsifier=(
            "An amendment where a later-registered recognizer's reading is the drafter's "
            "intent (oracle-witnessed) but registration order silently selected the "
            "earlier recognizer's op sequence — i.e. reordering the registry changes the "
            "compiled law for a corpus instance."
        ),
        code_anchor="finland/johtolause/rule_registry.py (ParseRule registration order)",
        changelog=("v1: initial extraction from code order (no behaviour change).",),
    ),
    GlueComponent(
        glue_id="uk.precedence.effect_recognizer_span_overlap",
        kind="precedence",
        jurisdiction="uk",
        version="v1",
        believed_spec=(
            "When two UK effect recognizers claim overlapping text spans of one amendment "
            "instruction, the nlp_parser precedence policy selects a single winner "
            "(more-specific / earlier-in-order recognizer) and suppresses the overlapped "
            "match, so exactly one witness_rule_id owns each lowered op."
        ),
        falsifier=(
            "An instruction whose oracle-correct lowering is the *suppressed* recognizer's "
            "reading, so the span-overlap suppression order — not the drafter's text — "
            "decided the compiled effect."
        ),
        code_anchor="uk_legislation/nlp_parser.py + uk_legislation/ordering.py",
        changelog=("v1: initial extraction from code order (no behaviour change).",),
    ),
    GlueComponent(
        glue_id="uk.precedence.same_moment_cross_act",
        kind="precedence",
        jurisdiction="uk",
        version="v1",
        believed_spec=(
            "Two acts that amend the same target at the same commencement moment with "
            "incompatible payloads are resolved by an owned same-moment-precedence claim "
            "naming which act prevails on a recognized basis (later-enacted / specific "
            "over general), not by silent application order."
        ),
        falsifier=(
            "A same-moment cross-act conflict whose oracle-correct resolution is the act "
            "the precedence basis did NOT name as winner (the named basis picks the wrong "
            "act)."
        ),
        code_anchor="uk_legislation/same_moment_precedence_claim.py",
        changelog=("v1: initial extraction (the one text-binding claim kind in prod).",),
    ),
)


# ---------------------------------------------------------------------------
# ≈ — comparison lenses (editorial-normalization equivalence), versioned
# ---------------------------------------------------------------------------

_LENS: Tuple[GlueComponent, ...] = (
    GlueComponent(
        glue_id="uk.lens.retain_text_repeal_elision",
        kind="lens",
        jurisdiction="uk",
        version="v1",
        believed_spec=(
            "An oracle <Repeal RetainText=\"true\"> retained phrase is a 1-D consolidation "
            "display artifact, not law (the analogue of Finlex's 'Aiempi sanamuoto "
            "kuuluu:' marker); the comparison lens accepts either the repeal-applied or "
            "repeal-not-applied oracle text form, never raising a spurious text_diff. "
            "Replay is untouched (presentation_cleanup)."
        ),
        falsifier=(
            "A RetainText span that is materially load-bearing legal content (its "
            "presence/absence changes the operative meaning), so eliding it hides a real "
            "divergence rather than a display artifact."
        ),
        code_anchor="uk_effect_oracle_retain_text_repeal_elided (comparison-only variant)",
        changelog=(
            "v1: RetainText elided into a comparison-only oracle text variant; replay "
            "untouched.",
            "v1: #184 realized as CTSF rule ctsf.occupancy.repeal_tombstone_elision "
            "(control-pair admission-gated).",
        ),
        ctsf_rule_id="ctsf.occupancy.repeal_tombstone_elision",
    ),
    GlueComponent(
        glue_id="fi.lens.aiempi_sanamuoto_elision",
        kind="lens",
        jurisdiction="fi",
        version="v1",
        believed_spec=(
            "A Finlex 'Aiempi sanamuoto kuuluu:' (prior-wording) block and RetainText-"
            "style editorial markers are orthogonal 1-D display hacks; the comparison lens "
            "elides them from the oracle text before scoring, and replay never consumes "
            "them as legal content."
        ),
        falsifier=(
            "A consolidation where the 'aiempi sanamuoto' block carries live operative "
            "content the current text depends on, so eliding it suppresses a real "
            "text divergence."
        ),
        code_anchor="tools/oracle_text.py + tools/divergence_core.py (editorial elision)",
        changelog=(
            "v1: initial extraction of the editorial-elision equivalence.",
            "v1: #184 realized as CTSF rule ctsf.text.aiempi_sanamuoto_elision "
            "(control-pair admission-gated).",
        ),
        ctsf_rule_id="ctsf.text.aiempi_sanamuoto_elision",
    ),
    GlueComponent(
        glue_id="fi.lens.grammar_text_normalization",
        kind="lens",
        jurisdiction="fi",
        version="v1",
        believed_spec=(
            "Whitespace runs, dot-leader alignment fill, § spacing, dash/quote "
            "variants and OCR word-fusion are non-normative because the FI amendment "
            "grammar's quoted-span matcher normalizes them when locating text; two "
            "wordings equal under that normalization are definitionally equal. The "
            "comparison lens compares grammar-normalized wording, never the raw form."
        ),
        falsifier=(
            "An amendment whose quoted-span match succeeds on one whitespace/dot-"
            "leader form but fails on the other — the normalization merges two "
            "wordings the grammar's own matcher distinguishes."
        ),
        code_anchor="core/ctsf.py::_normalize_wording_for_diff (grammar text normalization)",
        changelog=(
            "v1: #184 first-classed the grammar text-normalization equivalence as a "
            "CTSF rule ctsf.text.grammar_normalization (control-pair admission-gated).",
        ),
        ctsf_rule_id="ctsf.text.grammar_normalization",
    ),
    GlueComponent(
        glue_id="uk.lens.oracle_eid_alignment",
        kind="lens",
        jurisdiction="uk",
        version="v1",
        believed_spec=(
            "Before scoring divergences the comparison lens aligns LawVM and oracle eIDs "
            "(the oracle-comparison adapter), so a pure eID/addressing-scheme difference "
            "between the two renderings of the same provision is not counted as a text "
            "divergence."
        ),
        falsifier=(
            "Two provisions the eID alignment merges that are in fact distinct provisions "
            "(the alignment collapses a real structural difference)."
        ),
        code_anchor="uk_oracle_eid_alignment_adapter",
        changelog=("v1: initial extraction of the eID-alignment equivalence.",),
    ),
)


_GLUE: Tuple[GlueComponent, ...] = _PRECEDENCE + _LENS


def glue_components(
    *, kind: str = "", jurisdiction: str = ""
) -> List[GlueComponent]:
    """Return the catalogued glue components, optionally filtered by kind/jurisdiction."""
    out = list(_GLUE)
    if kind:
        out = [g for g in out if g.kind == kind]
    if jurisdiction:
        out = [g for g in out if g.jurisdiction == jurisdiction]
    return sorted(out, key=lambda g: (g.kind, g.jurisdiction, g.glue_id))


def glue_to_dict(g: GlueComponent) -> Dict[str, object]:
    return {
        "glue_id": g.glue_id,
        "kind": g.kind,
        "jurisdiction": g.jurisdiction,
        "version": g.version,
        "believed_spec": g.believed_spec,
        "falsifier": g.falsifier,
        "code_anchor": g.code_anchor,
        "changelog": list(g.changelog),
        **({"ctsf_rule_id": g.ctsf_rule_id} if g.ctsf_rule_id else {}),
    }


def render_glue_markdown() -> str:
    """Render ≺ (precedence) and ≈ (lens) as a diffable, versioned catalog."""
    lines: List[str] = [
        "# Spec glue: ≺ precedence policy + ≈ comparison lens (versioned)",
        "",
        "First-class, versioned entries for the two load-bearing spec components that "
        "otherwise live only implicitly in code order (≺) or as an unversioned lens (≈).",
        "",
        "## ≺ Precedence / conflict policy",
        "",
        "| glue_id | juris | version | code_anchor |",
        "|---------|-------|---------|-------------|",
    ]
    for g in glue_components(kind="precedence"):
        lines.append(
            f"| {g.glue_id} | {g.jurisdiction} | {g.version} | {g.code_anchor} |"
        )
    lines += [
        "",
        "## ≈ Comparison lens (editorial-equivalence)",
        "",
        "| glue_id | juris | version | code_anchor |",
        "|---------|-------|---------|-------------|",
    ]
    for g in glue_components(kind="lens"):
        lines.append(
            f"| {g.glue_id} | {g.jurisdiction} | {g.version} | {g.code_anchor} |"
        )
    return "\n".join(lines)
