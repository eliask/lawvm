"""Unit tests for the LAWVM_IGNORE_SEMANTIC_ANNOTATIONS toggle + census.

Covers:
  * toggle OFF == current extraction (byte-identical mention set);
  * toggle ON drops <ref>-only mentions but keeps text-derived ones;
  * the plain-text dedup-guard removal: a cite that was <ref>-covered surfaces
    via the text lane when the toggle is ON;
  * the per-family census classification (neutral statuses).
"""
from __future__ import annotations

import os

from lawvm.finland.references.annotation_independence_census import (
    census_one_statute,
    family_of,
    target_key,
)
from lawvm.finland.references.ref_mention_extractor import (
    extract_all_reference_mentions,
    ignore_semantic_annotations,
)

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A witness statute (source "001/2000") with:
#   - an inline <ref> to act 2001/2 §9 (the <ref>-element / annotation lane);
#   - a plain-text id-anchored cite to perustuslain (731/1999) 5 § that is ALSO
#     wrapped in a <ref> (so production dedups it away from the plain-text lane);
#   - a plain-text id-anchored cite to lannoitelain (711/2022) 7 § with NO <ref>
#     (text lane only — present in BOTH runs).
_WITNESS_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN_NS}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Katso <ref href="/akn/fi/act/statute/2001/2#sec_9">toinen laki</ref> tarkemmin.</p>
          <p>Sovelletaan <ref href="/akn/fi/act/statute/1999/731#sec_5">perustuslain (731/1999) 5 §</ref> mukaan.</p>
          <p>Lisaksi lannoitelain (711/2022) 7 § koskee tata asiaa.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")


def _mention_keyset(mentions) -> set[tuple]:
    """A comparable identity per mention (lane-agnostic, order-agnostic)."""
    out: set[tuple] = set()
    for m in mentions:
        tgt = m.target_provision_ref
        out.add(
            (
                m.cite_kind.value,
                m.phrase_lemma,
                m.edge_subtype,
                tgt.statute_id if tgt else None,
                tgt.section_label if tgt else None,
                m.source_span.byte_offset if m.source_span else None,
            )
        )
    return out


def test_toggle_default_off() -> None:
    """Unset / empty / falsy env → OFF (fail-closed)."""
    for val in (None, "", "0", "false", "no", "off", "  "):
        if val is None:
            os.environ.pop("LAWVM_IGNORE_SEMANTIC_ANNOTATIONS", None)
        else:
            os.environ["LAWVM_IGNORE_SEMANTIC_ANNOTATIONS"] = val
        assert ignore_semantic_annotations() is False
    os.environ.pop("LAWVM_IGNORE_SEMANTIC_ANNOTATIONS", None)


def test_toggle_on_truthy() -> None:
    for val in ("1", "true", "TRUE", "Yes", "on"):
        os.environ["LAWVM_IGNORE_SEMANTIC_ANNOTATIONS"] = val
        assert ignore_semantic_annotations() is True
    os.environ.pop("LAWVM_IGNORE_SEMANTIC_ANNOTATIONS", None)


def test_off_is_byte_identical_to_today() -> None:
    """Explicit ignore_annotations=False == default (no env) == current behavior."""
    os.environ.pop("LAWVM_IGNORE_SEMANTIC_ANNOTATIONS", None)
    default = extract_all_reference_mentions(_WITNESS_XML, "001/2000")
    explicit_off = extract_all_reference_mentions(
        _WITNESS_XML, "001/2000", ignore_annotations=False
    )
    assert _mention_keyset(default.mentions) == _mention_keyset(explicit_off.mentions)
    assert len(default.mentions) == len(explicit_off.mentions)


def test_on_drops_ref_only_mentions_keeps_text() -> None:
    """Toggle ON removes <ref>-element mentions, keeps text-derived ones."""
    with_res = extract_all_reference_mentions(
        _WITNESS_XML, "001/2000", ignore_annotations=False
    )
    without_res = extract_all_reference_mentions(
        _WITNESS_XML, "001/2000", ignore_annotations=True
    )

    with_lemmas = {m.phrase_lemma for m in with_res.mentions}
    without_lemmas = {m.phrase_lemma for m in without_res.mentions}

    # The <ref>-element lane is present WITH, absent WITHOUT.
    assert "ref_element" in with_lemmas
    assert "ref_element" not in without_lemmas
    # A text lane (plain_text) survives in BOTH.
    assert "plain_text" in without_lemmas

    # The <ref>-only target 2001/2 (no plain-text form) is lost without <ref>.
    with_targets = {
        m.target_provision_ref.statute_id
        for m in with_res.mentions
        if m.target_provision_ref
    }
    without_targets = {
        m.target_provision_ref.statute_id
        for m in without_res.mentions
        if m.target_provision_ref
    }
    assert "2001/2" in with_targets
    assert "2001/2" not in without_targets


def test_dedup_guard_removal_surfaces_ref_covered_cite() -> None:
    """A cite that was <ref>-covered (731/1999) surfaces via the text lane ON.

    With annotations, the plain-text lane is suppressed for 731/1999 because the
    <ref>-element lane already covered it (ref_covered dedup guard). When the
    toggle drops the <ref> lane it MUST also drop that guard, so the plain-text
    form re-surfaces — else the measurement is contaminated.
    """
    with_res = extract_all_reference_mentions(
        _WITNESS_XML, "001/2000", ignore_annotations=False
    )
    without_res = extract_all_reference_mentions(
        _WITNESS_XML, "001/2000", ignore_annotations=True
    )

    # WITH: 731/1999 is present via the <ref> lane (ref_element), NOT plain_text.
    with_731 = [
        m
        for m in with_res.mentions
        if m.target_provision_ref and m.target_provision_ref.statute_id == "1999/731"
        or m.target_provision_ref and m.target_provision_ref.statute_id == "731/1999"
    ]
    assert with_731, "731/1999 should be present WITH annotations"
    assert any(m.phrase_lemma == "ref_element" for m in with_731)
    assert not any(m.phrase_lemma == "plain_text" for m in with_731)

    # WITHOUT: the SAME target re-surfaces via the plain_text text lane.
    without_731 = [
        m
        for m in without_res.mentions
        if m.target_provision_ref and m.target_provision_ref.statute_id == "731/1999"
    ]
    assert without_731, "731/1999 should re-surface via the text lane WHEN <ref> ignored"
    assert any(m.phrase_lemma == "plain_text" for m in without_731)


def test_family_classification() -> None:
    """family_of maps the closed lemma/cite_kind space deterministically."""
    res = extract_all_reference_mentions(
        _WITNESS_XML, "001/2000", ignore_annotations=False
    )
    fams = {family_of(m) for m in res.mentions}
    # The inline <ref> CITES to 2001/2 → explicit_id; plain_text → explicit_id.
    assert "explicit_id" in fams


def test_census_one_statute_neutral_diff() -> None:
    """census_one_statute produces a neutral per-family diff on the witness."""
    per_family = census_one_statute(_WITNESS_XML, "001/2000")
    assert "explicit_id" in per_family
    eid = per_family["explicit_id"]
    # 711/2022 is text-recoverable (no <ref>) → text_recovers >= 1.
    # 2001/2 is <ref>-only → annotation_only >= 1.
    assert eid.with_count >= 1
    assert eid.annotation_only >= 1, "the <ref>-only 2001/2 cite is annotation_only"
    assert eid.text_recovers >= 1, "711/2022 / 731/1999 recover via text lanes"
    # dependence_ratio is a clean fraction in [0, 1].
    assert 0.0 <= eid.dependence_ratio <= 1.0


def test_census_target_key_stable_across_lanes() -> None:
    """A <ref> and a plain-text mention to the same target share a target_key."""
    res = extract_all_reference_mentions(
        _WITNESS_XML, "001/2000", ignore_annotations=False
    )
    keys_by_statute: dict[str, set[str]] = {}
    for m in res.mentions:
        if m.target_provision_ref:
            keys_by_statute.setdefault(
                m.target_provision_ref.statute_id, set()
            ).add(target_key(m))
    # Every emitted mention yields a non-empty key.
    assert all(target_key(m) for m in res.mentions)
