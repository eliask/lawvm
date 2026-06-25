"""Enclosing-provision anaphor surface lens — ``Tätä pykälää ei sovelleta …``.

Mints one :class:`~lawvm.core.legal_surface_graph.SurfaceNode`
``enclosing_anaphor_cue`` node per enclosing-provision anaphor in the body text:
the closed determiner+noun shapes (``Tätä pykälää`` / ``Tätä momenttia`` /
``Tämän pykälän`` / ``Tätä lakia`` …) followed by an applicability MATRIX
(``ei [kuitenkaan] sovelleta`` / ``estämättä`` / ``sovelletaan``). The anaphor's
referent is the SECTION / SUBSECTION / WHOLE-LAW it itself sits in — a structural
identity the flattened body decode drops — so the node records the *named scope*
(``section`` / ``subsection`` / ``whole_law``) and the matrix span, and a SEPARATE
edge pass (:class:`~lawvm.finland.legal_surface.norm_composition.EnclosingAnaphoraPass`)
resolves it against the unit's ``provision_index`` and joins it to the cores of
its own provision.

DISTINCT from the H6 ``exception_condition_cue`` lens: a NEW node kind
(``enclosing_anaphor_cue``) and a distinct ``lens_id``, so it never pollutes the
H6 cue census (the H6 recognizer does NOT key on the ``ei sovelleta`` /
``sovelletaan`` applicability matrix). It runs ALONGSIDE the H6 lens, additively.

SAFETY BOUNDARY (mirrors the recognizers): SURFACE FACTS ONLY. A node records the
*form* of the anaphor (its determiner+noun surface, its named scope, the matrix
that confirms the applicability reading), NEVER a legal conclusion that the
provision is conditioned/excepted as a matter of law. The qualifier KIND
(``condition`` / ``exception``) is the surface split of the matrix verb
(``sovelletaan`` → condition; ``ei sovelleta`` / ``estämättä`` → exception), not a
legal classification.

A bare determiner+noun WITHOUT an applicability matrix is NOT minted (it is an
ordinary mention, owned by the reference / discourse-anaphora lens). A
determiner+noun followed by an explicit provision number (``Tämän lain 7 §:n 3
momentin …``) is an ordinary CROSS-reference, also NOT minted here.
"""
from __future__ import annotations

import hashlib
import re
from lawvm.core.regex_safety import compile_classifier_regex

from lawvm.core.legal_surface_graph import SourceSpanRef
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
    SurfaceLensResult,
    SurfaceNodeSeed,
)

_LENS_ID = "fi.enclosing_anaphora.v0"
SCHEMA_VERSION = "v0"

ENCLOSING_ANAPHOR_CUE_KIND = "enclosing_anaphor_cue"
_RULE_ID = "fi.enclosing_anaphora.v0.enclosing_anaphor_cue"

# Surface qualifier kinds (mirror the condition/exception split — the matrix verb
# decides: an applicability statement is a CONDITION, a derogation/negation is an
# EXCEPTION). Kept as bare strings here to avoid a lens→composition import.
KIND_CONDITION = "condition"
KIND_EXCEPTION = "exception"

# Named anaphor SCOPES (the referent level the determiner+noun names).
SCOPE_SECTION = "section"
SCOPE_SUBSECTION = "subsection"
SCOPE_WHOLE_LAW = "whole_law"

#: The closed enclosing-provision anaphor determiner+noun family → its scope.
#: Casefolded; matched word-bounded with collapsed internal whitespace.
#:   Tätä pykälää / Tämän pykälän → this SECTION
#:   Tätä momenttia / Tämän momentin → this SUBSECTION (momentti)
#:   Tätä lakia / Tämän lain → this WHOLE LAW
_DET_NOUN_SCOPE: dict[str, str] = {
    "tätä pykälää": SCOPE_SECTION,
    "tämän pykälän": SCOPE_SECTION,
    "tätä momenttia": SCOPE_SUBSECTION,
    "tämän momentin": SCOPE_SUBSECTION,
    "tätä lakia": SCOPE_WHOLE_LAW,
    "tämän lain": SCOPE_WHOLE_LAW,
}

#: Chars after the noun within which the applicability MATRIX must sit for the
#: determiner+noun to read as an enclosing-anaphor cue (else it is a plain
#: mention).
_MATRIX_GAP = 48

#: The determiner+noun alternation (word-bounded, internal whitespace flexible).
_CUE_RE = re.compile(
    r"\b(?:"
    + "|".join(
        r"\s+".join(re.escape(w) for w in cue.split(" "))
        for cue in sorted(_DET_NOUN_SCOPE, key=len, reverse=True)
    )
    + r")\b",
    re.IGNORECASE,
)

#: A determiner+noun directly followed by ``N §`` / ``N momentin`` … → ordinary
#: cross-reference, not an enclosing anaphor (skip; left to the reference lens).
_EXPLICIT_REF_AFTER_NOUN = re.compile(
    r"^\s*\d+\s*(§|luvun|momentin|momenttia|kohdan|kohtaa)", re.IGNORECASE
)

#: Applicability matrices. EXCEPTION (``ei [kuitenkaan] sovelleta`` / ``estämättä``)
#: vs CONDITION (``sovelletaan``).
_MATRIX_EXCEPTION_RE = compile_classifier_regex(r"\b(ei\s{1,8}kuitenkaan\s{1,8}sovelleta|ei\s{1,8}sovelleta|estämättä)\b", re.IGNORECASE, classifier_id="fi.legal_surface.lenses.enclosing_anaphora.matrix_exception_re")
_MATRIX_CONDITION_RE = compile_classifier_regex(r"\bsovelletaan\b", re.IGNORECASE, classifier_id="fi.legal_surface.lenses.enclosing_anaphora.matrix_condition_re")


def _matrix_after(text: str, noun_end: int) -> tuple[str, int, int] | None:
    """Classify + locate the applicability matrix after an anaphor noun.

    Returns ``(kind, matrix_abs_start, matrix_abs_end)`` or ``None`` when no matrix
    sits within :data:`_MATRIX_GAP` chars after the noun. EXCEPTION wins ties (the
    deterministic tie-break).
    """
    window = text[noun_end : noun_end + _MATRIX_GAP]
    exc = _MATRIX_EXCEPTION_RE.search(window)  # lawvm-regex: owning_parser closed applicability-matrix EXCEPTION pattern over a bounded (_MATRIX_GAP=48) slice of this lens's own anaphor-noun window; surface-fact classifier, mints no state
    con = _MATRIX_CONDITION_RE.search(window)  # lawvm-regex: owning_parser closed applicability-matrix CONDITION pattern (\bsovelletaan\b) over the same bounded own-surface window; surface-fact classifier
    if exc is not None and (con is None or exc.start() <= con.start()):
        return (KIND_EXCEPTION, noun_end + exc.start(), noun_end + exc.end())
    if con is not None:
        return (KIND_CONDITION, noun_end + con.start(), noun_end + con.end())
    return None


def _span_ref(unit: SourceSurfaceUnit, start: int, end: int) -> SourceSpanRef:
    surface = unit.raw_text[start:end]
    return SourceSpanRef(
        source_unit_id=unit.source_unit_id,
        source_hash=unit.source_hash,
        work_id=unit.work_id,
        address=unit.address,
        char_start=start,
        char_end=end,
        text_hash=hashlib.sha256(surface.encode("utf-8")).hexdigest(),
    )


class EnclosingAnaphoraLens:
    """SurfaceLens minting ``enclosing_anaphor_cue`` nodes. Mints NO edges.

    Runs ALONGSIDE the H6 ExceptionConditionLens (additive). One node per
    determiner+noun+matrix enclosing-anaphor cue; the node span covers the whole
    determiner..matrix run (so the edge pass can locate it by overlap and read its
    named scope from the payload).
    """

    lens_id: str = _LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = SCHEMA_VERSION
    produces_node_kinds: tuple[str, ...] = (ENCLOSING_ANAPHOR_CUE_KIND,)
    produces_edge_kinds: tuple[str, ...] = ()
    required_views: tuple[str, ...] = ()

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult:
        node_seeds: list[SurfaceNodeSeed] = []
        units_scanned = 0
        for unit in bundle.units:
            units_scanned += 1
            text = unit.raw_text
            for m in _CUE_RE.finditer(text):  # lawvm-regex: owning_parser closed determiner+noun anaphor-cue alternation (_DET_NOUN_SCOPE) over this lens's OWN SourceSurfaceUnit.raw_text (the §D4 lens substrate it owns, not another plane); mints only enclosing_anaphor_cue SurfaceNode seeds
                det_noun = re.sub(r"\s+", " ", m.group(0).casefold())
                scope = _DET_NOUN_SCOPE.get(det_noun)
                if scope is None:
                    continue
                if _EXPLICIT_REF_AFTER_NOUN.match(text, m.end()):
                    continue
                matrix = _matrix_after(text, m.end())
                if matrix is None:
                    # bare mention, no applicability matrix → not an anaphor cue.
                    continue
                kind, matrix_start, matrix_end = matrix
                # node span: determiner start .. matrix end (the whole cue run).
                ref = _span_ref(unit, m.start(), matrix_end)
                node_seeds.append(
                    SurfaceNodeSeed(
                        node_kind=ENCLOSING_ANAPHOR_CUE_KIND,
                        source_ref=ref,
                        local_discriminator=(
                            f"{kind}|{det_noun}|{scope}|{m.start()}"
                        ),
                        rule_id=_RULE_ID,
                        status="asserted",
                        payload={
                            "qualifier_kind": kind,
                            "cue": det_noun,
                            "anaphor_scope": scope,
                            "det_noun_span": [m.start(), m.end()],
                            "matrix_span": [matrix_start, matrix_end],
                            "source": "construction_enclosing_anaphor_cue",
                            "experimental": True,
                        },
                        authority_role="surface_fact",
                    )
                )

        return SurfaceLensResult(
            lens_id=self.lens_id,
            node_seeds=tuple(node_seeds),
            edge_seeds=(),
            residuals=(),
            diagnostics=(),
            coverage={
                "units_scanned": units_scanned,
                "enclosing_anaphor_cues": len(node_seeds),
            },
        )
