"""Tests for the reconcile verb: replay-L1 vs oracle-L1 + divergence classes."""

from __future__ import annotations

import datetime
from typing import Any

from lxml import etree

from lawvm.tools import reconcile as rec

_FIN_NS = "http://data.finlex.fi/schema/finlex"

# A section mirroring 2011/805 §3:1: a version-pinned IN_FORCE subsection, an
# editorial note ("tulee voimaan 1.6.2026. Aiempi sanamuoto kuuluu:"), the
# superseded prior-wording subsection, a note that ADDS a momentti, and a plain
# in-force subsection.
_SECTION_XML = (
    """<section xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" """
    f'''xmlns:finlex="{_FIN_NS}" eId="chp_3__sec_1">
      <num>1 §</num>
      <subsection finlex:originalVersion="@20260269" finlex:originalVersionLabel="17.4.2026/269">
        <content><p>AMENDED MOM1 IN FORCE</p></content>
      </subsection>
      <hcontainer finlex:outline="huomautus" name="noteAuthorial">
        <content><p>L:lla 269/2026 muutettu 1 momentti tulee voimaan 1.6.2026. Aiempi sanamuoto kuuluu:</p></content>
      </hcontainer>
      <subsection>
        <content><p>OLD MOM1 SUPERSEDED</p></content>
      </subsection>
      <hcontainer finlex:outline="huomautus" name="noteAuthorial">
        <content><p>L:lla 269/2026 lisätty 2 momentti tulee voimaan 1.6.2026.</p></content>
      </hcontainer>
      <subsection finlex:originalVersion="@20260269" finlex:originalVersionLabel="17.4.2026/269">
        <content><p>ADDED MOM2 IN FORCE</p></content>
      </subsection>
    </section>'''
).encode("utf-8")


def _patch_oracle(monkeypatch, section_xml: bytes = _SECTION_XML, **meta: Any) -> None:
    el = etree.fromstring(section_xml)

    def fake_load(statute_id, section_filter, at_amendment="", lang="fin"):
        return {
            "statute_id": statute_id,
            "locator": "finlex://sd-cons/x/main.xml",
            "oracle_cutoff_date": meta.get("cutoff", "2026-04-17"),
            "oracle_version_amendment_id": meta.get("amendment", "2026/269"),
            "found": True,
            "section_el": el,
            "error": None,
        }

    monkeypatch.setattr("lawvm.tools.oracle_text.load_oracle_section", fake_load)


class TestBuildOracleL1:
    def test_keeps_in_force_drops_superseded_and_note(self, monkeypatch):
        _patch_oracle(monkeypatch)
        # as-of AFTER the 1.6.2026 commencement → 269/2026 is in force.
        l1 = rec.build_oracle_l1("2011/805", "chapter:3/section:1", "2026-06-09")
        assert l1.basis == "structural"
        assert "AMENDED MOM1 IN FORCE" in l1.text
        assert "ADDED MOM2 IN FORCE" in l1.text
        assert "SUPERSEDED" not in l1.text  # prior wording dropped
        assert "tulee voimaan" not in l1.text  # editorial note dropped
        assert "17.4.2026/269" in l1.version_markers
        # The notes whose commencement (1.6.2026) has passed are straddling.
        assert len(l1.straddling_notes) == 2

    def test_no_straddle_before_commencement(self, monkeypatch):
        _patch_oracle(monkeypatch)
        # as-of BEFORE the 1.6.2026 commencement → notes do not straddle.
        l1 = rec.build_oracle_l1("2011/805", "chapter:3/section:1", "2026-05-01")
        assert l1.straddling_notes == []


class TestReconcileClassification:
    def _patch_replay(self, monkeypatch, rendered: str, available: bool = True,
                      effective: str = "2026-04-14", src: str = "2026/222"):
        def fake_resolve(**kwargs):
            return {
                "statute_id": kwargs["statute_id"],
                "status": "selected" if available else "absent",
                "query": {"provision": kwargs["provision"], "as_of": kwargs["as_of"],
                          "query_type": kwargs["query_type"]},
                "version": {"effective": effective, "content_state": "live"},
                "source": {"statute_id": src},
                "text": {"rendered": rendered, "available": available},
            }
        monkeypatch.setattr("lawvm.provision_state.resolve_provision_state", fake_resolve)

    def test_temporal_divergence(self, monkeypatch):
        # Replay returns the OLD wording; oracle-L1 returns the NEW in-force text
        # with straddling notes → DISAGREE (temporal). This is the live case.
        _patch_oracle(monkeypatch)
        self._patch_replay(monkeypatch, rendered="OLD MOM1 SUPERSEDED text only")
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§3:1", as_of="2026-06-09",
        )
        assert r.verdict == "DISAGREE"
        assert r.divergence_class == "temporal"
        assert r.oracle.straddling_notes

    def test_agree(self, monkeypatch):
        _patch_oracle(monkeypatch)
        # Replay returns exactly the concatenated in-force oracle text.
        self._patch_replay(
            monkeypatch,
            rendered="AMENDED MOM1 IN FORCE ADDED MOM2 IN FORCE",
        )
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§3:1", as_of="2026-06-09",
        )
        assert r.verdict == "AGREE"
        assert r.divergence_class is None
        assert r.agree_ratio >= 0.995

    def test_agree_when_replay_carries_section_heading(self, monkeypatch):
        # Replay-L1 renders "<num> § <heading> <body...>"; oracle-L1 is body-only
        # (build_temporal_spans skips num/heading). The leading heading must be
        # normalized away so the agreeing bodies are NOT reported as a divergence.
        # This is the live 2011/805 §3:1 case after the amendment-ingestion fix.
        _patch_oracle(monkeypatch)
        self._patch_replay(
            monkeypatch,
            rendered=(
                "1 § Rikoksesta tehdyn ilmoituksen kirjaaminen "
                "AMENDED MOM1 IN FORCE ADDED MOM2 IN FORCE"
            ),
            effective="2026-06-01",
            src="2026/269",
        )
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§3:1", as_of="2026-06-09",
        )
        assert r.verdict == "AGREE"
        assert r.divergence_class is None
        assert r.agree_ratio >= 0.995

    def test_editorial_divergence_no_straddle(self, monkeypatch):
        # A section with a single plain (no-marker) subsection: oracle-L1 is the
        # raw prose (basis=inline_unsegmented), no notes straddle. A text diff
        # then classifies as editorial, not temporal.
        plain_xml = (
            """<section xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" """
            f'''xmlns:finlex="{_FIN_NS}" eId="chp_1__sec_1">
              <num>1 §</num>
              <subsection><content><p>STABLE CURRENT TEXT</p></content></subsection>
            </section>'''
        ).encode("utf-8")
        _patch_oracle(monkeypatch, section_xml=plain_xml)
        self._patch_replay(monkeypatch, rendered="COMPLETELY DIFFERENT TEXT")
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§1:1", as_of="2026-05-01",
        )
        assert r.verdict == "DISAGREE"
        assert r.divergence_class == "editorial"
        assert r.oracle.straddling_notes == []

    def test_presence_divergence(self, monkeypatch):
        _patch_oracle(monkeypatch)
        self._patch_replay(monkeypatch, rendered="", available=False)
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§3:1", as_of="2026-06-09",
        )
        assert r.verdict == "DISAGREE"
        assert r.divergence_class == "presence"

    def test_straddle_but_replay_resembles_in_force_is_editorial(self, monkeypatch):
        # A straddling note exists, but replay matches the IN-FORCE text (with a
        # small typo), NOT the superseded prior wording → the diff is editorial
        # drift, not an un-applied amendment. Must NOT over-claim temporal.
        _patch_oracle(monkeypatch)
        self._patch_replay(
            monkeypatch,
            # in-force text is "AMENDED MOM1 IN FORCE ADDED MOM2 IN FORCE";
            # here replay has it with a tiny edit, far from "OLD MOM1 SUPERSEDED".
            rendered="AMENDED MOM1 IN FORCE ADDED MOM2 IN FORCEX extra",
        )
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§3:1", as_of="2026-06-09",
        )
        assert r.verdict == "DISAGREE"
        assert r.divergence_class == "editorial"


class TestSubprovisionScope:
    def test_momentti_selector_compares_at_section_scope(self, monkeypatch):
        # §3:1.2 must NOT yield a false 'presence' from the oracle being unable to
        # address a momentti; reconcile drops to section scope and flags it.
        _patch_oracle(monkeypatch)
        captured = {}

        def fake_resolve(**kwargs):
            captured.update(kwargs)
            return {
                "statute_id": kwargs["statute_id"], "status": "selected",
                "query": {"provision": kwargs["provision"], "as_of": kwargs["as_of"],
                          "query_type": kwargs["query_type"]},
                "version": {"effective": "2026-04-14", "content_state": "live"},
                "source": {"statute_id": "2026/222"},
                "text": {"rendered": "AMENDED MOM1 IN FORCE ADDED MOM2 IN FORCE",
                         "available": True},
            }

        monkeypatch.setattr(
            "lawvm.provision_state.resolve_provision_state", fake_resolve
        )
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§3:1.2", as_of="2026-06-09",
        )
        # replay was resolved at SECTION scope, not the momentti
        assert captured["provision"] == "chapter:3/section:1"
        assert r.scope == "section_for_subprovision"
        assert r.scope_note is not None
        assert r.verdict != "DISAGREE" or r.divergence_class != "presence"


class TestRenderAndJson:
    def test_jsonable_shape(self, monkeypatch):
        _patch_oracle(monkeypatch)
        TestReconcileClassification()._patch_replay(
            monkeypatch, rendered="OLD MOM1 SUPERSEDED text only"
        )
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§3:1", as_of="2026-06-09",
        )
        j = rec._result_to_jsonable(r)
        assert set(j) >= {
            "selector", "locator", "verdict", "divergence_class", "agree_ratio",
            "replay", "oracle",
        }
        assert j["locator"] == "chapter:3/section:1"
        assert j["replay"]["source_amendment"] == "2026/222"
        assert j["oracle"]["basis"] == "structural"

    def test_human_disagree_render(self, monkeypatch):
        _patch_oracle(monkeypatch)
        TestReconcileClassification()._patch_replay(
            monkeypatch, rendered="OLD MOM1 SUPERSEDED text only"
        )
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§3:1", as_of="2026-06-09",
        )
        out = rec._render_human(r)
        assert "⚠ DISAGREE (temporal)" in out
        assert "replay-L1" in out
        assert "oracle-L1" in out
        assert "DIVERGENCE is the signal" in out
        # Temporal: the "replay did NOT apply" note + cutoff context are valid.
        assert "replay did NOT apply" in out
        assert "snapshot cutoff" in out

    def test_human_agree_render_omits_stale_temporal_narratives(self, monkeypatch):
        # AGREE (replay applied the amendment): the "replay did NOT apply" note
        # and the snapshot-cutoff context line are FALSE and must not render,
        # even though straddling notes exist on the oracle side.
        _patch_oracle(monkeypatch)
        TestReconcileClassification()._patch_replay(
            monkeypatch,
            rendered="AMENDED MOM1 IN FORCE ADDED MOM2 IN FORCE",
            effective="2026-06-01",
            src="2026/269",
        )
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§3:1", as_of="2026-06-09",
        )
        assert r.verdict == "AGREE"
        assert r.oracle.straddling_notes  # straddle present on oracle side
        out = rec._render_human(r)
        assert "✔ replay and oracle agree" in out
        assert "replay did NOT apply" not in out
        assert "snapshot cutoff" not in out

    def test_human_editorial_render_omits_did_not_apply_note(self, monkeypatch):
        # DISAGREE(editorial) with a straddling note present: replay DID apply
        # the dated change (text resembles in-force, not superseded), so the
        # "replay did NOT apply" / cutoff narratives must be suppressed.
        _patch_oracle(monkeypatch)
        TestReconcileClassification()._patch_replay(
            monkeypatch,
            rendered="AMENDED MOM1 IN FORCE ADDED MOM2 IN FORCEX extra drift",
            effective="2026-06-01",
            src="2026/269",
        )
        r = rec.reconcile_provision(
            statute_id="2011/805", selector="§3:1", as_of="2026-06-09",
        )
        assert r.verdict == "DISAGREE"
        assert r.divergence_class == "editorial"
        out = rec._render_human(r)
        assert "⚠ DISAGREE (editorial)" in out
        assert "replay did NOT apply" not in out
        assert "snapshot cutoff" not in out
