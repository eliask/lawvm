"""Registered CTSF editorial rules — migrated from the neutralizer blacklist.

Each rule migrated here from ``_section_diff_is_bench_neutralized`` /
``semantic/diff.py`` ships ALL FOUR control-pair obligations (§1.4) and a
falsifier sentence, and is registered in the #181 spec-ledger glue catalog.
The admission gate (``ctsf_admission_gate``) proves each passes; the shard test
fails if any registered rule lacks its control pairs.

Three rules are migrated in v0 (the safest, per the task):

1. ``ctsf.text.grammar_normalization`` — whitespace / dot-leader / §-spacing
   normalization (from ``_is_wording_whitespace_only_diff`` + the dot-leader
   normalizer).  Glue: ``fi.lens.grammar_text_normalization`` (new).
2. ``ctsf.occupancy.repeal_tombstone_elision`` — a repealed unit's residual
   tombstone/RetainText wording is editorial (from the editorial_only /
   repeal-indicator neutralization).  Glue: ``uk.lens.retain_text_repeal_elision``.
3. ``ctsf.text.aiempi_sanamuoto_elision`` — Finlex "Aiempi sanamuoto kuuluu:"
   former-wording banner elision.  Glue: ``fi.lens.aiempi_sanamuoto_elision``.
"""

from __future__ import annotations

from dataclasses import replace

from lawvm.core.ctsf import CTSFNode
from lawvm.core.ctsf_admission_gate import (
    CTSFEditorialRule,
    CongruenceCase,
    ControlPair,
    WitnessCase,
)
from lawvm.semantic.model import SemanticStructureFacet, SemanticStructureNode


def _wf(text: str) -> tuple[SemanticStructureFacet, ...]:
    return (SemanticStructureFacet(kind="wording", text=text),)


def _sec(label: str, *, text: str = "", basis: str = "explicit") -> SemanticStructureNode:
    return SemanticStructureNode(
        kind="section",
        label=label,
        label_basis=basis,
        facets=_wf(text) if text else (),
    )


# ---------------------------------------------------------------------------
# Rule 1 — grammar text normalization (whitespace / dot-leader / § spacing)
# ---------------------------------------------------------------------------

# Amendment substituting new wording into 5 § (quoted payload = source truth).
def _apply_substitute_5(node: SemanticStructureNode) -> SemanticStructureNode:
    return _sec("5", text="uusi maksu 30")


def _apply_substitute_5_ctsf(node: CTSFNode) -> CTSFNode:
    # Same effect on CTSF: replace the addressable wording with the normalized
    # payload; child order/occupancy/label unchanged.
    return replace(node, normalized_text="uusi maksu 30", elisions=())


_RULE_GRAMMAR_NORMALIZATION = CTSFEditorialRule(
    rule_id="ctsf.text.grammar_normalization",
    jurisdiction="fi",
    believed_spec=(
        "Whitespace runs, dot-leader alignment fill, § spacing, dash/quote "
        "variants and OCR word-fusion are non-normative: the FI amendment "
        "grammar's quoted-span matcher normalizes them when locating text, so "
        "two wordings equal under that normalization are definitionally equal."
    ),
    falsifier=(
        "An amendment whose quoted-span match SUCCEEDS on one whitespace/dot-"
        "leader form but FAILS on the other — i.e. the normalization merges two "
        "wordings the grammar's own matcher distinguishes."
    ),
    ledger_glue_id="fi.lens.grammar_text_normalization",
    unamended_control_pairs=(
        # (a) untouched unit: source-as-enacted 'maksu 20' vs oracle dot-leader
        # rendering must project equal (source is truth; oracle deviates only in
        # presentation on a unit no amendment touched).
        ControlPair(
            label="unamended dot-leader vs clean",
            left=_sec("5", text="maksu 20"),
            right=_sec("5", text="maksu.......... 20"),
        ),
        ControlPair(
            label="unamended § spacing",
            left=_sec("40", text="40 §:n mukaan"),
            right=_sec("40", text="40 §: n mukaan"),
        ),
    ),
    quoted_payload_control_pairs=(
        # (b) freshly-substituted unit: amendment quoted payload vs oracle's
        # padded rendering of the same result.
        ControlPair(
            label="quoted payload vs padded oracle",
            left=_sec("5", text="uusi maksu 30"),
            right=_sec("5", text="uusi maksu.......... 30"),
        ),
    ),
    congruence_cases=(
        # (c) project-then-apply == apply-then-project for the addressable part.
        CongruenceCase(
            label="substitute 5 § wording",
            pre=_sec("5", text="maksu 20"),
            apply_fn=_apply_substitute_5,
            apply_ctsf=_apply_substitute_5_ctsf,
        ),
    ),
    witness_cases=(
        # (d) an elided fragment emits a witness.
        WitnessCase(label="dot-leader elision witness", node=_sec("5", text="maksu.......... 20")),
    ),
)


# ---------------------------------------------------------------------------
# Rule 2 — repeal tombstone / RetainText elision
# ---------------------------------------------------------------------------

def _apply_repeal_7(node: SemanticStructureNode) -> SemanticStructureNode:
    # Repeal 7 §: replay renders a clean placeholder.
    return _sec("7", basis="repeal_placeholder")


def _apply_repeal_7_ctsf(node: CTSFNode) -> CTSFNode:
    from dataclasses import replace as _r

    return _r(node, occupancy_state="repealed", normalized_text="", elisions=())


_RULE_REPEAL_TOMBSTONE = CTSFEditorialRule(
    rule_id="ctsf.occupancy.repeal_tombstone_elision",
    jurisdiction="fi",
    believed_spec=(
        "For a unit whose occupancy is repealed, the residual wording is a "
        "consolidation tombstone banner ('N § on kumottu…') or a RetainText "
        "retained phrase — a 1-D display artifact, not law.  occupancy=repealed "
        "is the whole normative content; the tombstone wording is elided."
    ),
    falsifier=(
        "A repealed unit whose retained/tombstone wording carries operative "
        "content the current law depends on, so eliding it hides a real "
        "divergence rather than a display artifact."
    ),
    ledger_glue_id="uk.lens.retain_text_repeal_elision",
    unamended_control_pairs=(
        # (a) both sides denote occupancy=repealed; presentation differs.
        ControlPair(
            label="clean placeholder vs tombstone banner",
            left=_sec("7", basis="repeal_placeholder"),
            right=_sec("7", text="(7 § on kumottu lailla 2020/123)", basis="editorial_repeal_notice"),
        ),
    ),
    # No quoted-payload analogue: a repeal amendment carries no result wording.
    quoted_payload_not_applicable=True,
    congruence_cases=(
        CongruenceCase(
            label="repeal 7 §",
            pre=_sec("7", text="alkuperäinen teksti"),
            apply_fn=_apply_repeal_7,
            apply_ctsf=_apply_repeal_7_ctsf,
        ),
    ),
    witness_cases=(
        WitnessCase(
            label="tombstone elision witness",
            node=_sec("7", text="(7 § on kumottu lailla 2020/123)", basis="editorial_repeal_notice"),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Rule 3 — "Aiempi sanamuoto kuuluu:" former-wording banner elision
# ---------------------------------------------------------------------------

def _apply_substitute_3(node: SemanticStructureNode) -> SemanticStructureNode:
    # Amendment substitutes new wording into 3 §; oracle later appends the
    # former-wording banner editorially.
    return _sec("3", text="Uusi teksti tässä.")


def _apply_substitute_3_ctsf(node: CTSFNode) -> CTSFNode:
    from dataclasses import replace as _r

    return _r(node, normalized_text="Uusi teksti tässä", elisions=())


_RULE_AIEMPI_SANAMUOTO = CTSFEditorialRule(
    rule_id="ctsf.text.aiempi_sanamuoto_elision",
    jurisdiction="fi",
    believed_spec=(
        "A Finlex 'Aiempi sanamuoto kuuluu:' block is the superseded prior "
        "wording — an editorial escape hatch a 1-D consolidation uses to show "
        "what the text used to say.  No amendment addresses it; the block from "
        "the marker to end of the wording facet is elided before normalization."
    ),
    falsifier=(
        "A consolidation where the 'aiempi sanamuoto' block carries live "
        "operative content the current text depends on, so eliding it "
        "suppresses a real text divergence."
    ),
    ledger_glue_id="fi.lens.aiempi_sanamuoto_elision",
    unamended_control_pairs=(
        ControlPair(
            label="clean current text vs banner-appended",
            left=_sec("3", text="Uusi teksti tässä."),
            right=_sec(
                "3",
                text="Uusi teksti tässä. Aiempi sanamuoto kuuluu: Vanha teksti oli tämä.",
            ),
        ),
    ),
    quoted_payload_control_pairs=(
        ControlPair(
            label="quoted payload vs banner-appended oracle",
            left=_sec("3", text="Uusi teksti tässä."),
            right=_sec(
                "3",
                text="Uusi teksti tässä. Aiempi sanamuoto kuuluu: aiempi.",
            ),
        ),
    ),
    congruence_cases=(
        CongruenceCase(
            label="substitute 3 § then banner appears",
            pre=_sec("3", text="vanha teksti"),
            apply_fn=_apply_substitute_3,
            apply_ctsf=_apply_substitute_3_ctsf,
        ),
    ),
    witness_cases=(
        WitnessCase(
            label="aiempi sanamuoto elision witness",
            node=_sec(
                "3",
                text="Uusi teksti tässä. Aiempi sanamuoto kuuluu: Vanha teksti oli tämä.",
            ),
        ),
    ),
)


_REGISTERED: tuple[CTSFEditorialRule, ...] = (
    _RULE_GRAMMAR_NORMALIZATION,
    _RULE_REPEAL_TOMBSTONE,
    _RULE_AIEMPI_SANAMUOTO,
)


def registered_ctsf_rules() -> tuple[CTSFEditorialRule, ...]:
    """Return the CTSF editorial rules admitted into the whitelist."""
    return _REGISTERED
