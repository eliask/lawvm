"""UK presence-dimension comparison reconciliations (task #211).

Two comparison-only, monotone reconciliations on the UK compare-eId surface:

* whole-provision ``<Repeal RetainText="true">`` — an oracle provision whose
  ENTIRE live body wording is retained-repealed (only number/heading labels sit
  outside the wrapper) is repealed law kept visible for display, so its EID is
  presence-ambiguous: a repeal-applied replay (eId absent) and a
  repeal-not-applied replay (eId present) both match the oracle
  (``uk_oracle_retain_text_whole_provision_repeal_presence_optional``);

* prospective-only effect ambiguity — whether the current consolidation
  reflects a structural effect whose only feed in-force dates are PROSPECTIVE
  is point-in-time / editorial dependent (``prospective_effect_warrant``,
  verified mixed-sign), so eIds under such an op's target are excused on
  whichever side they are one-sided.

Both accept EITHER form and can only remove penalized keys — they never
manufacture a divergence; a live-provision drop still convicts.
"""
from __future__ import annotations

from types import SimpleNamespace

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.source_adjudication import (
    normalize_uk_replay_compare_eids,
    uk_prospective_only_presence_ambiguous_eids,
)
from lawvm.uk_legislation.uk_grafter import extract_eid_map_bytes

_NS = "http://www.legislation.gov.uk/namespaces/legislation"


def _oracle_xml(inner: str) -> bytes:
    return (
        f'<Legislation xmlns="{_NS}">\n'
        f"  <Body>\n{inner}\n  </Body>\n"
        f"</Legislation>\n"
    ).encode("utf-8")


_FULLY_REPEALED_SECTION = """
    <P1group>
      <Title>Protection of the individual</Title>
      <P1 id="section-2"><Pnumber>2</Pnumber><P1para>
        <Text><Repeal ChangeId="k-1" RetainText="true">Schedule 1 contains
        modifications to the law.</Repeal></Text>
      </P1para></P1>
    </P1group>
    <P1group>
      <Title>Live section</Title>
      <P1 id="section-3"><Pnumber>3</Pnumber><P1para>
        <Text>Still-live wording with a <Repeal ChangeId="k-2"
        RetainText="true">partially retained phrase</Repeal> inside.</Text>
      </P1para></P1>
    </P1group>
"""


def test_fully_retained_repealed_eid_detected_and_partial_not() -> None:
    data = extract_eid_map_bytes(_oracle_xml(_FULLY_REPEALED_SECTION))
    fully = set(data["retain_text_fully_repealed_eids"])
    # section-2: every body word is inside the RetainText repeal (only the
    # Pnumber label sits outside) -> presence-optional.
    assert "section-2" in fully
    # section-3 keeps live wording outside the wrapper -> NOT presence-optional.
    assert "section-3" not in fully
    rule_ids = {o.get("rule_id") for o in data["oracle_identity_observations"]}
    assert "uk_oracle_retain_text_whole_provision_repeal_presence_optional" in rule_ids


def test_no_detection_without_retain_text() -> None:
    xml = _oracle_xml(
        '<P1 id="section-9"><Pnumber>9</Pnumber><P1para>'
        "<Text>ordinary live provision</Text></P1para></P1>"
    )
    data = extract_eid_map_bytes(xml)
    assert not tuple(data["retain_text_fully_repealed_eids"])


# --- normalize_uk_replay_compare_eids: retained-repeal presence form ---------


def test_retained_repeal_oracle_eid_accepts_repeal_applied_replay() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-1"},
        {"section-1", "section-2"},
        oracle_retained_repeal_eids={"section-2"},
    )
    # Replay applied the repeal (section-2 absent): the retained-repealed
    # oracle eId is excused, no penalized key remains.
    assert oracle == {"section-1"}
    assert replayed == {"section-1"}


def test_retained_repeal_oracle_eid_accepts_repeal_not_applied_replay() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-1", "section-2"},
        {"section-1", "section-2"},
        oracle_retained_repeal_eids={"section-2"},
    )
    # Replay kept the provision: both sides keep it and it scores as a match.
    assert oracle == {"section-1", "section-2"}
    assert replayed == {"section-1", "section-2"}


def test_live_provision_drop_still_convicts() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-1"},
        {"section-1", "section-2"},
        oracle_retained_repeal_eids=(),
    )
    # Without the oracle's retained-repeal testimony the drop stays penalized.
    assert "section-2" in oracle
    assert "section-2" not in replayed


# --- normalize_uk_replay_compare_eids: oracle_suspect presence form (D3) ------


def test_oracle_suspect_eid_dropped_when_replay_applied_the_repeal() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-1"},
        {"section-1", "section-80-5", "section-80-6"},
        oracle_suspect_eids={"section-80-5", "section-80-6"},
    )
    # Feed-repealed (replay dropped them), oracle retained: the author-typed
    # oracle_suspect eIds are dropped from the oracle side, no penalized key.
    assert oracle == {"section-1"}
    assert replayed == {"section-1"}


def test_oracle_suspect_never_forces_replay_to_re_add() -> None:
    # When replay kept the eId (not the D3 shape), the oracle_suspect drop is a
    # no-op on that key: it only removes an oracle-only residual.
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-1", "section-80-5"},
        {"section-1", "section-80-5"},
        oracle_suspect_eids={"section-80-5"},
    )
    assert oracle == {"section-1", "section-80-5"}
    assert replayed == {"section-1", "section-80-5"}


def test_oracle_suspect_absent_is_noop() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-1"},
        {"section-1", "section-80-5"},
        oracle_suspect_eids=(),
    )
    # Without the author's oracle_suspect testimony the drop stays penalized.
    assert "section-80-5" in oracle
    assert "section-80-5" not in replayed


# --- normalize_uk_replay_compare_eids: prospective presence ambiguity --------


def test_presence_ambiguous_eids_excused_on_both_sides() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-1", "section-10-1a"},
        {"section-1", "section-61"},
        presence_ambiguous_eids={"section-61", "section-10-1a"},
    )
    # section-61: replay applied an uncommenced repeal the editors did not.
    # section-10-1a: replay applied an uncommenced insert. Both excused.
    assert oracle == {"section-1"}
    assert replayed == {"section-1"}


def test_presence_ambiguous_eid_present_on_both_sides_is_kept() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-1", "section-61"},
        {"section-1", "section-61"},
        presence_ambiguous_eids={"section-61"},
    )
    assert oracle == {"section-1", "section-61"}
    assert replayed == {"section-1", "section-61"}


# --- uk_prospective_only_presence_ambiguous_eids ------------------------------


def _effect(effect_id: str, *, in_force_dates: list[dict[str, str]]) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type="repealed",
        applied=True,
        requires_applied=True,
        modified="2020-01-01",
        affected_uri="",
        affected_class="",
        affected_year="1949",
        affected_number="97",
        affected_provisions="s. 61",
        affecting_uri="http://www.legislation.gov.uk/id/ukpga/2000/37",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2000",
        affecting_number="37",
        affecting_provisions="Sch. 16 Pt. 1",
        affecting_title="",
        in_force_dates=in_force_dates,
    )


def _node(kind: str, label: str, eid: str, children=()) -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind, label=label, attrs={"eId": eid}, children=tuple(children)
    )


def _statute() -> SimpleNamespace:
    s61 = _node(
        "section",
        "61",
        "section-61",
        [_node("subsection", "1", "section-61-1")],
    )
    body = SimpleNamespace(
        kind="body", label=None, attrs={}, children=(s61,)
    )
    sched = _node(
        "schedule",
        "SCHEDULE 6",
        "schedule-6",
        [_node("paragraph", "12", "schedule-6-paragraph-12")],
    )
    return SimpleNamespace(body=body, supplements=(sched,))


def test_prospective_only_op_targets_project_to_subtree_eids() -> None:
    prospective = _effect(
        "key-pro",
        in_force_dates=[{"date": "", "applied": "true", "prospective": "true"}],
    )
    commenced = _effect(
        "key-real",
        in_force_dates=[{"date": "2015-04-01", "applied": "true", "prospective": "false"}],
    )
    ops = [
        SimpleNamespace(op_id="key-pro", target=SimpleNamespace(path=(("section", "61"),))),
        SimpleNamespace(
            op_id="key-pro_1",
            target=SimpleNamespace(path=(("schedule", "6"), ("paragraph", "12"))),
        ),
        SimpleNamespace(op_id="key-real", target=SimpleNamespace(path=(("section", "61"),))),
    ]
    out = uk_prospective_only_presence_ambiguous_eids(
        [prospective, commenced],
        ops,
        enacted_statute=_statute(),
    )
    # The prospective op's subtree eIds are ambiguous — including the schedule
    # target resolved through the "SCHEDULE 6" supplements-root label form.
    assert out == {"section-61", "section-61-1", "schedule-6-paragraph-12"}


def test_commenced_effect_contributes_no_ambiguity() -> None:
    commenced = _effect(
        "key-real",
        in_force_dates=[{"date": "2015-04-01", "applied": "true", "prospective": "false"}],
    )
    ops = [
        SimpleNamespace(op_id="key-real", target=SimpleNamespace(path=(("section", "61"),))),
    ]
    assert (
        uk_prospective_only_presence_ambiguous_eids(
            [commenced], ops, enacted_statute=_statute()
        )
        == set()
    )


def test_whole_act_prospective_op_never_widens_ambiguity() -> None:
    prospective = _effect(
        "key-pro",
        in_force_dates=[{"date": "", "applied": "true", "prospective": "true"}],
    )
    ops = [SimpleNamespace(op_id="key-pro", target=SimpleNamespace(path=()))]
    assert (
        uk_prospective_only_presence_ambiguous_eids(
            [prospective], ops, enacted_statute=_statute()
        )
        == set()
    )
