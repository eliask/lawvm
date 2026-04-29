from __future__ import annotations

from lawvm.estonia.fetch import AmendmentRef
from lawvm.estonia.pair_planning import plan_ee_oracle_pair


def _ref(akt_viide: str, joustumine: str) -> AmendmentRef:
    return AmendmentRef(
        aktViide=akt_viide,
        passed=joustumine,
        joustumine=joustumine,
    )


def test_plan_ee_oracle_pair_classifies_same_chain_editorial_drift(monkeypatch) -> None:
    base_xml = b"<base/>"
    oracle_xml = b"<oracle/>"
    shared_refs = [_ref("101", "2025-01-01"), _ref("102", "2025-02-01")]

    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_grupi_id", lambda xml: "gid-1")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: "oracle",
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "terviktekst")
    monkeypatch.setattr("lawvm.estonia.pair_planning.fetch_rt_xml", lambda akt_viide, archive: oracle_xml)
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_amendment_refs",
        lambda xml: shared_refs if xml == base_xml else list(shared_refs),
    )

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2026-03-24",
        base_xml=base_xml,
        archive=None,
    )

    assert planned.plan.oracle_id == "oracle"
    assert planned.plan.source_basis.value == "earliest_available_terviktekst"
    assert planned.plan.comparison_class == "same_chain_editorial_drift"
    assert planned.plan.amendments_to_apply == ()
    assert planned.plan.source_adjudication.oracle_suspect == "same_chain_editorial_drift"


def test_plan_ee_oracle_pair_selects_only_effective_new_amendments(monkeypatch) -> None:
    base_xml = b"<base/>"
    oracle_xml = b"<oracle/>"
    base_refs = [_ref("101", "2025-01-01"), _ref("102", "2025-02-01")]
    oracle_refs = [
        _ref("101", "2025-01-01"),
        _ref("102", "2025-02-01"),
        _ref("103", "2026-03-01"),
        _ref("104", "2026-04-01"),
    ]

    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_grupi_id", lambda xml: "gid-2")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: "oracle",
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "terviktekst")
    monkeypatch.setattr("lawvm.estonia.pair_planning.fetch_rt_xml", lambda akt_viide, archive: oracle_xml)
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_amendment_refs",
        lambda xml: base_refs if xml == base_xml else oracle_refs,
    )

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2026-03-24",
        base_xml=base_xml,
        archive=None,
    )

    assert planned.plan.source_basis.value == "pairwise_terviktekst_delta"
    assert planned.plan.comparison_class == "forward_looking_oracle"
    assert [ref.aktViide for ref in planned.plan.amendments_to_apply] == ["103"]
    assert planned.plan.effective_new_amendments == ("103",)
    assert planned.plan.future_new_amendments == ("104",)
    assert planned.plan.source_adjudication.oracle_suspect == "forward_looking_oracle"


def test_plan_ee_oracle_pair_repairs_impossible_muutmismarge_publication_year(monkeypatch) -> None:
    base_xml = b"<base/>"
    oracle_xml = b"<oracle/>"
    bad_ref = AmendmentRef(
        aktViide="103122012009",
        passed="2013-11-29",
        joustumine="2013-12-06",
    )

    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_grupi_id", lambda xml: "gid-2")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: "oracle",
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "terviktekst")
    monkeypatch.setattr("lawvm.estonia.pair_planning.fetch_rt_xml", lambda akt_viide, archive: oracle_xml)
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_amendment_refs",
        lambda xml: [] if xml == base_xml else [bad_ref],
    )

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2013-12-06",
        base_xml=base_xml,
        archive=None,
    )

    assert [ref.aktViide for ref in planned.plan.amendments_to_apply] == ["103122013009"]
    assert planned.plan.effective_new_amendments == ("103122013009",)
    assert any(
        item.get("rule") == "ee_muutmismarge_aktviide_publication_year_repair"
        and item.get("original_aktViide") == "103122012009"
        and item.get("repaired_aktViide") == "103122013009"
        for item in planned.plan.source_adjudication.lineage
    )


def test_plan_ee_oracle_pair_repairs_unfetchable_publication_number_ref(monkeypatch) -> None:
    base_xml = b"<base/>"
    oracle_xml = b"""
    <akt>
      <muutmismarge>
        <aktikuupaev>2011-06-06</aktikuupaev>
        <avaldamismarge>
          <avaldamismargeTekst>RT I 2011-06-09 1</avaldamismargeTekst>
          <aktViide>109062011005</aktViide>
        </avaldamismarge>
        <joustumine>2011-06-12</joustumine>
      </muutmismarge>
    </akt>
    """
    bad_ref = AmendmentRef(
        aktViide="109062011005",
        passed="2011-06-06",
        joustumine="2011-06-12",
    )

    def fake_fetch(akt_viide: str, archive: object = None) -> bytes:
        if akt_viide == "oracle":
            return oracle_xml
        if akt_viide == "109062011001":
            return b"<amendment/>"
        raise RuntimeError("missing")

    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_grupi_id", lambda xml: "gid-2")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: "oracle",
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "terviktekst")
    monkeypatch.setattr("lawvm.estonia.pair_planning.fetch_rt_xml", fake_fetch)
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_amendment_refs",
        lambda xml: [] if xml == base_xml else [bad_ref],
    )

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2011-06-12",
        base_xml=base_xml,
        archive=None,
    )

    assert [ref.aktViide for ref in planned.plan.amendments_to_apply] == ["109062011001"]
    assert planned.plan.effective_new_amendments == ("109062011001",)
    assert any(
        item.get("rule") == "ee_muutmismarge_aktviide_publication_number_repair"
        and item.get("original_aktViide") == "109062011005"
        and item.get("repaired_aktViide") == "109062011001"
        for item in planned.plan.source_adjudication.lineage
    )


def test_plan_ee_oracle_pair_blocks_cross_statute_oracle_group(monkeypatch) -> None:
    base_xml = b"<base/>"
    oracle_xml = b"<oracle/>"
    base_refs = [_ref("101", "2025-01-01")]
    oracle_refs = [_ref("999", "2026-03-01")]

    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_grupi_id",
        lambda xml: "gid-base" if xml == base_xml else "gid-oracle",
    )
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: "oracle",
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "terviktekst")
    monkeypatch.setattr("lawvm.estonia.pair_planning.fetch_rt_xml", lambda akt_viide, archive: oracle_xml)
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_amendment_refs",
        lambda xml: base_refs if xml == base_xml else oracle_refs,
    )

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2026-03-24",
        base_xml=base_xml,
        archive=None,
    )

    assert planned.plan.source_basis.value == "noncommensurable"
    assert planned.plan.comparison_class == "cross_statute_oracle_mismatch"
    assert planned.plan.oracle_grupi_id == "gid-oracle"
    assert planned.plan.oracle_refs == ()
    assert planned.plan.amendments_to_apply == ()
    assert planned.plan.effective_new_amendments == ()
    assert planned.plan.source_adjudication.oracle_suspect == "cross_statute_oracle_mismatch"
    assert planned.plan.source_adjudication.lineage[-1]["rule"] == "ee_oracle_group_mismatch"


def test_plan_ee_oracle_pair_treats_later_slice_of_same_act_as_new_delta(monkeypatch) -> None:
    base_xml = b"<base/>"
    oracle_xml = b"<oracle/>"
    base_refs = [_ref("101", "2025-01-01")]
    oracle_refs = [
        _ref("101", "2025-01-01"),
        _ref("101", "2026-03-01"),
    ]

    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_grupi_id", lambda xml: "gid-slice")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: "oracle",
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "terviktekst")
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_effective_date", lambda xml: "2025-01-01")
    monkeypatch.setattr("lawvm.estonia.pair_planning.fetch_rt_xml", lambda akt_viide, archive: oracle_xml)
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_amendment_refs",
        lambda xml: base_refs if xml == base_xml else oracle_refs,
    )

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2026-03-24",
        base_xml=base_xml,
        archive=None,
    )

    assert planned.plan.source_basis.value == "pairwise_terviktekst_delta"
    assert planned.plan.comparison_class == "commensurable_delta"
    assert planned.plan.effective_new_amendments == ("101",)
    assert planned.plan.future_new_amendments == ()
    assert [(ref.aktViide, ref.joustumine) for ref in planned.plan.amendments_to_apply] == [
        ("101", "2026-03-01"),
    ]


def test_plan_ee_oracle_pair_keeps_multiple_slices_of_same_act_when_base_lacks_it(monkeypatch) -> None:
    base_xml = b"<base/>"
    oracle_xml = b"<oracle/>"
    base_refs = [_ref("100", "2020-05-07")]
    oracle_refs = [
        _ref("100", "2020-05-07"),
        _ref("101", "2020-07-01"),
        _ref("101", "2021-01-01"),
    ]

    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_grupi_id", lambda xml: "gid-multi-slice")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: "oracle",
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "terviktekst")
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_effective_date", lambda xml: "2020-05-07")
    monkeypatch.setattr("lawvm.estonia.pair_planning.fetch_rt_xml", lambda akt_viide, archive: oracle_xml)
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_amendment_refs",
        lambda xml: base_refs if xml == base_xml else oracle_refs,
    )

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2021-01-01",
        base_xml=base_xml,
        archive=None,
    )

    assert [(ref.aktViide, ref.joustumine) for ref in planned.plan.amendments_to_apply] == [
        ("101", "2020-07-01"),
        ("101", "2021-01-01"),
    ]


def test_plan_ee_oracle_pair_replays_future_effective_refs_already_in_base(monkeypatch) -> None:
    base_xml = b"<base/>"
    oracle_xml = b"<oracle/>"
    base_refs = [_ref("101", "2025-01-01"), _ref("102", "2026-03-01")]

    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_grupi_id", lambda xml: "gid-3")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: "oracle",
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "terviktekst")
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_effective_date", lambda xml: "2025-02-01")
    monkeypatch.setattr("lawvm.estonia.pair_planning.fetch_rt_xml", lambda akt_viide, archive: oracle_xml)
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_amendment_refs",
        lambda xml: base_refs if xml == base_xml else list(base_refs),
    )

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2026-03-24",
        base_xml=base_xml,
        archive=None,
    )

    assert planned.plan.source_basis.value == "earliest_available_terviktekst"
    assert [ref.aktViide for ref in planned.plan.amendments_to_apply] == ["102"]
    assert planned.plan.effective_new_amendments == ()
    assert planned.plan.future_new_amendments == ()


def test_plan_ee_oracle_pair_classifies_base_is_oracle_source_basis(monkeypatch) -> None:
    base_xml = b"<base/>"

    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_grupi_id", lambda xml: "gid-5")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: "base",
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "terviktekst")
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_amendment_refs", lambda xml: [])

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2026-03-24",
        base_xml=base_xml,
        archive=None,
    )

    assert planned.plan.source_basis.value == "base_is_oracle"
    assert planned.plan.comparison_class == "base_is_oracle"


def test_plan_ee_oracle_pair_classifies_algtekst_source_basis(monkeypatch) -> None:
    base_xml = b"<base/>"

    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_grupi_id", lambda xml: "gid-6")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: None,
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "algtekst")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_amendment_refs",
        lambda xml: [_ref("101", "2025-01-01")],
    )

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2026-03-24",
        base_xml=base_xml,
        archive=None,
    )

    assert planned.plan.source_basis.value == "algtekst_source"


def test_plan_ee_oracle_pair_orders_same_effective_refs_by_passed_then_id(monkeypatch) -> None:
    base_xml = b"<base/>"
    oracle_xml = b"<oracle/>"
    base_refs = [
        AmendmentRef(aktViide="101072025001", passed="2025-06-18", joustumine="2025-09-01"),
        AmendmentRef(aktViide="109012025001", passed="2024-12-11", joustumine="2025-09-01"),
        AmendmentRef(aktViide="123122024001", passed="2024-12-04", joustumine="2025-09-01"),
    ]

    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_grupi_id", lambda xml: "gid-4")
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.get_oracle_aktviide_for_pit",
        lambda grupi_id, as_of, archive: "oracle",
    )
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_tekstiliik", lambda xml: "terviktekst")
    monkeypatch.setattr("lawvm.estonia.pair_planning.extract_effective_date", lambda xml: "2025-01-01")
    monkeypatch.setattr("lawvm.estonia.pair_planning.fetch_rt_xml", lambda akt_viide, archive: oracle_xml)
    monkeypatch.setattr(
        "lawvm.estonia.pair_planning.extract_amendment_refs",
        lambda xml: list(base_refs),
    )

    planned = plan_ee_oracle_pair(
        base_id="base",
        as_of="2025-09-01",
        base_xml=base_xml,
        archive=None,
    )

    assert [ref.aktViide for ref in planned.plan.amendments_to_apply] == [
        "123122024001",
        "109012025001",
        "101072025001",
    ]
