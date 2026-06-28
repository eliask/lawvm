"""Tests for the §2.3 firewall de-leak of the Finnish ``tarkoitetaan`` idiom.

Pins the move of ``core/tree_ops._FI_DEFINITION_INTRO_PHRASES`` out of the
kernel into ``lawvm.finland.definition_introducer`` (rank 11 of
notes/REGEX_TO_GRAMMAR_MIGRATION.md). The kernel keeps the jurisdiction-
neutral suffix-colon (``:``) drafting check; the Finnish-language fragment is
supplied as a frontend predicate.

Coverage:
  - synthetic positive: FI ``tarkoitetaan`` intro without trailing ``:`` is
    skipped only when the FI predicate is wired in.
  - synthetic positive: kernel ``:`` ending still skips without a predicate.
  - negative: English ``means`` intro is not skipped by the FI predicate.
  - negative: kernel does not skip when neither ``:`` nor a predicate fires.
  - guard-liveness: the FI predicate is reachable through the FI replay-
    projection call site (``project_replay_warning_findings``).
  - end-to-end: ``build_flattened_sublist_findings`` forwards the predicate.
"""

from __future__ import annotations

from typing import List

from lawvm.core.ir import IRNode
from lawvm.core.invariant_surface_matrix import project_replay_warning_findings
from lawvm.core.phase_result import Finding
from lawvm.core.replay_lints import build_flattened_sublist_findings
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import find_flattened_sublist_warnings
from lawvm.finland.definition_introducer import (
    _FI_DEFINITION_INTRO_PHRASES,
    fi_definition_list_introducer_predicate,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _body_with_section_intro(section_label: str, intro_text: str) -> IRNode:
    """Build a section-19-style body with a mixed alpha+digit paragraph run.

    The ``a b c 1 2`` shape is the canonical flattened-sublist mixed-family
    signal: a real replay bug would emit ``flattened_sublist_mixed_family``.
    A parent that opens a definitions list (``Tässä luvussa tarkoitetaan:``)
    is a legitimate Finnish shape that must NOT be reported.
    """
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label=section_label,
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.INTRO, text=intro_text),
                            *(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label=label,
                                    text=f"{label}) def",
                                )
                                for label in ("a", "b", "c", "1", "2")
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Predicate unit tests
# ---------------------------------------------------------------------------


def test_fi_predicate_recognizes_tarkoitetaan_without_colon() -> None:
    """FI predicate fires for a ``tarkoitetaan`` intro without trailing ``:``.

    This is the regression for the moved idiom: the substring check that
    previously lived in the kernel must still produce a True verdict from the
    FI predicate when the intro text carries ``tarkoitetaan`` alone.
    """
    body = _body_with_section_intro("19", "Tässä luvussa tarkoitetaan")
    section = body.children[0]
    subsection = section.children[0]

    assert fi_definition_list_introducer_predicate(subsection) is True


def test_fi_predicate_recognizes_joilla_tarkoitetaan_variant() -> None:
    """FI predicate fires for the ``joilla tarkoitetaan`` phrase variant."""
    body = _body_with_section_intro("19", "Sivuotteilla joilla tarkoitetaan")
    subsection = body.children[0].children[0]

    assert fi_definition_list_introducer_predicate(subsection) is True


def test_fi_predicate_does_not_fire_for_english_means_intro() -> None:
    """FI predicate must NOT fire for an English ``means`` intro.

    This is the §2.3 firewall guarantee — the predicate is jurisdiction-scoped
    and does not act as a generic "looks like a definition" detector for
    non-FI text.
    """
    body = _body_with_section_intro("1", "In this Act, 'X' means")
    subsection = body.children[0].children[0]

    assert fi_definition_list_introducer_predicate(subsection) is False


def test_fi_predicate_does_not_fire_when_no_intro_present() -> None:
    """FI predicate must NOT fire when there is no intro child at all.

    A body without an intro must not match — the predicate walks intro
    children, and absence of intro means absence of signal.
    """
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.CONTENT, text="plain content"),
                            *(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label=label,
                                    text=label,
                                )
                                for label in ("a", "b", "c", "1", "2")
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    subsection = body.children[0].children[0]

    assert fi_definition_list_introducer_predicate(subsection) is False


def test_fi_definition_intro_phrases_constant_preserved() -> None:
    """The FI phrase tuple was moved, not renamed or mutated in flight.

    Behavional parity with the pre-move kernel requires the same three
    phrases. Triggers if someone trims or reorders the tuple without a
    witnessed rule.
    """
    assert _FI_DEFINITION_INTRO_PHRASES == (
        "tarkoitetaan",
        "joilla tarkoitetaan",
        "jolla tarkoitetaan",
    )


# ---------------------------------------------------------------------------
# Kernel + predicate integration tests
# ---------------------------------------------------------------------------


def test_kernel_skips_mixed_family_when_fi_predicate_fires() -> None:
    """Kernel skips the mixed-family lint when the FI predicate fires.

    The intro text ``"Tässä luvussa tarkoitetaan"`` (no trailing ``:``) is
    NOT caught by the kernel's universal suffix-colon check; the skip here
    relies entirely on the FI predicate being wired in.
    """
    body = _body_with_section_intro("19", "Tässä luvussa tarkoitetaan")

    # Default (no predicate): the kernel does NOT skip — the lint would fire.
    warnings_without_predicate = find_flattened_sublist_warnings(body)
    assert any(
        warning["kind"] == "flattened_sublist_mixed_family"
        for warning in warnings_without_predicate
    ), (
        "Default kernel (no frontend predicate) must NOT skip — over-retention "
        "of lint signal is the safe wrong when the frontend has not opted in."
    )

    # With the FI predicate wired in: the skip fires as before.
    warnings_with_predicate = find_flattened_sublist_warnings(
        body,
        definition_introducer_predicate=fi_definition_list_introducer_predicate,
    )
    assert warnings_with_predicate == [], (
        "FI predicate must suppress the flattened_sublist_mixed_family warning "
        "for the ``Tässä luvussa tarkoitetaan`` intro shape."
    )


def test_kernel_colon_ending_still_skips_without_predicate() -> None:
    """Kernel's universal ``:`` suffix check survives without a predicate.

    The suffix-colon is a jurisdiction-neutral drafting convention and stays
    in the kernel — it fires for the FI ``Tässä luvussa tarkoitetaan:``
    signature WITHOUT needing the FI predicate wired in.
    """
    body = _body_with_section_intro("19", "Tässä luvussa tarkoitetaan:")

    warnings = find_flattened_sublist_warnings(body)
    assert warnings == [], (
        "The trailing-``:`` kernel check must continue to suppress the lint "
        "for this universal drafting shape even without a frontend predicate."
    )


def test_kernel_does_not_skip_for_plain_english_intro_without_predicate() -> None:
    """Kernel does NOT skip for an English ``means`` intro.

    Without a frontend predicate (and without a ``:`` suffix) the kernel
    conservatively reports the suspicious shape — over-retention of lint
    signal is the safe wrong.
    """
    body = _body_with_section_intro("1", "In this Act, 'X' means")

    warnings = find_flattened_sublist_warnings(body)
    assert any(
        warning["kind"] == "flattened_sublist_mixed_family"
        for warning in warnings
    ), (
        "Kernel must NOT skip when neither ``:`` nor a FI predicate fires — "
        "the lint fires so the suspicious shape stays visible."
    )


def test_build_flattened_sublist_findings_forwards_predicate() -> None:
    """``build_flattened_sublist_findings`` forwards the predicate.

    Guard-liveness check that the predicate is plumbed through the finding-
    builder, not just the lower-level ``find_flattened_sublist_warnings``:
    a tarkoitetaan-without-colon intro must yield zero findings when the FI
    predicate is wired in, and produce a finding when it is not.
    """
    body = _body_with_section_intro("19", "Tässä luvussa tarkoitetaan")

    findings_with_predicate: List[Finding] = build_flattened_sublist_findings(
        body,
        phase="replay_fold",
        source_statute="1995/398",
        definition_introducer_predicate=fi_definition_list_introducer_predicate,
    )
    assert findings_with_predicate == [], (
        "Predicate-forwarded builder must suppress the flattened_sublist_family "
        "warning for a tarkoitetaan-without-colon intro."
    )

    findings_without_predicate = build_flattened_sublist_findings(
        body,
        phase="replay_fold",
        source_statute="1995/398",
    )
    assert any(
        finding.kind == "flattened_sublist_family_warning"
        and finding.detail["kind"] == "flattened_sublist_mixed_family"
        for finding in findings_without_predicate
    ), "Without predicate the builder must emit the finding (over-retention safe)."


def test_project_replay_warning_findings_forwards_fi_predicate() -> None:
    """``project_replay_warning_findings`` forwards the predicate to findings.

    Drives a FI-shaped parent IRNode through the production projection path
    that FI replay-fold/replay-product use (guard-liveness — the predicate
    must be reachable through the production lane, not just an unused parameter).
    """
    body = _body_with_section_intro("19", "Tässä luvussa tarkoitetaan")
    findings: list[Finding] = []
    project_replay_warning_findings(
        tree=body,
        phase="replay_fold",
        source_statute="1995/398",
        warnings=("flattened_sublist_family",),
        replay_findings=findings,
        replay_meta_out={},
        replay_print=lambda _message: None,
        definition_introducer_predicate=fi_definition_list_introducer_predicate,
    )
    assert not any(
        finding.kind == "flattened_sublist_family_warning" for finding in findings
    ), "Guard-liveness: FI predicate wired at the projection call site must suppress."


def test_project_replay_warning_findings_default_does_not_skip() -> None:
    """Without the FI predicate the projection path emits the finding.

    The default ``definition_introducer_predicate=None`` keeps the kernel at
    suffix-colon-only behavior — the tarkoitetaan-without-colon intro is
    NOT suppressed, so the finding fires (safe over-retention).
    """
    body = _body_with_section_intro("19", "Tässä luvussa tarkoitetaan")
    findings: list[Finding] = []
    project_replay_warning_findings(
        tree=body,
        phase="replay_fold",
        source_statute="1995/398",
        warnings=("flattened_sublist_family",),
        replay_findings=findings,
        replay_meta_out={},
        replay_print=lambda _message: None,
    )
    assert any(
        finding.kind == "flattened_sublist_family_warning"
        and finding.detail["kind"] == "flattened_sublist_mixed_family"
        for finding in findings
    ), "Default predicate (None) must NOT suppress — over-retention is safe."
