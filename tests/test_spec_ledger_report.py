"""Tests for the spec-ledger report / persistence / frontier-queue layer.

All fixtures are **synthetic** — a small ``SpecLedger`` is built in-code via the
public ``build_ledger`` core; no corpus is run.  We assert:

* persistence is byte-deterministic across two calls (the diffability contract);
* the blind-spot frontier ranks by contradicted / real-bug count;
* ``diff_catalog_coverage`` flags a decataloged rule and a new uncataloged rule;
* the grounding column renders when a fake grounding dict is injected and is
  absent when not.
"""
from __future__ import annotations

import json

from lawvm.tools.spec_ledger import (
    DivergenceRow,
    StatuteLedgerInput,
    build_ledger,
)
from lawvm.tools.spec_ledger_report import (
    diff_catalog_coverage,
    persist_ledger,
    render_blind_spot_frontier,
    render_report_markdown,
)


def _div(sid, section, diagnosis, disposition, rule_id, blame=""):
    return DivergenceRow(
        sid=sid,
        section_key=section,
        diagnosis=diagnosis,
        disposition=disposition,
        rule_id=rule_id,
        blame_source=blame,
    )


def _synthetic_ledger():
    """A small ledger with: a heavily-contradicted rule, a clean rule, an
    uncataloged rule, and two unattributed blind-spot divergences."""
    inputs = [
        StatuteLedgerInput(
            "a/1",
            {"r.alpha": 5, "r.beta": 2, "r.gamma": 1},
            [
                # r.alpha: two falsifying (lawvm_wrong + structural) => contradicted=2
                _div("a/1", "section:5", "REPLAY_EXTRA", "lawvm_wrong", "r.alpha"),
                _div("a/1", "section:6", "MISSING", "structural", "r.alpha"),
                # an oracle-suspect divergence is not falsifying
                _div("a/1", "section:7", "ORACLE_STALE", "oracle_suspect", "r.beta"),
                # unattributed falsifying divergence => blind spot
                _div("a/1", "section:9", "REPLAY_MISSING", "lawvm_wrong", None),
            ],
        ),
        StatuteLedgerInput(
            "b/2",
            {"r.alpha": 3},
            [
                # r.alpha picks up one more falsifying div in a second statute
                _div("b/2", "section:1", "REPLAY_EXTRA", "lawvm_wrong", "r.alpha"),
                # another unattributed blind spot, same diagnosis (groups together)
                _div("b/2", "section:2", "REPLAY_MISSING", "lawvm_wrong", None),
                _div("b/2", "section:3", "MISSING", "structural", None),
            ],
        ),
    ]
    return build_ledger(
        inputs,
        jurisdiction="uk",
        mode="official_consolidation",
        catalog={
            "r.alpha": "alpha believed spec",
            "r.beta": "beta believed spec",
            # r.gamma intentionally uncataloged
        },
    )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_persist_is_byte_deterministic(tmp_path):
    ledger = _synthetic_ledger()
    d1 = tmp_path / "run1"
    d2 = tmp_path / "run2"
    json1 = persist_ledger(ledger, d1)
    json2 = persist_ledger(ledger, d2)

    assert json1.name == "spec_ledger.json"
    assert (d1 / "spec_ledger.md").exists()
    # JSON and MD are both byte-identical across runs.
    assert json1.read_bytes() == json2.read_bytes()
    assert (d1 / "spec_ledger.md").read_bytes() == (d2 / "spec_ledger.md").read_bytes()


def test_persist_double_run_into_same_dir_is_stable(tmp_path):
    ledger = _synthetic_ledger()
    json_path = persist_ledger(ledger, tmp_path)
    first = json_path.read_bytes()
    md_first = (tmp_path / "spec_ledger.md").read_bytes()
    # Re-persist into the same directory; overwrite must be byte-identical.
    persist_ledger(ledger, tmp_path)
    assert json_path.read_bytes() == first
    assert (tmp_path / "spec_ledger.md").read_bytes() == md_first


def test_persisted_json_is_sorted_and_parseable(tmp_path):
    ledger = _synthetic_ledger()
    json_path = persist_ledger(ledger, tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    rule_ids = [r["rule_id"] for r in payload["rules"]]
    # Most-contradicted first: r.alpha (contradicted=3) leads.
    assert rule_ids[0] == "r.alpha"
    # top_statutes ranked by real-bug count desc.
    statutes = [row["statute"] for row in payload["top_statutes"]]
    counts = [row["real_bugs"] for row in payload["top_statutes"]]
    assert counts == sorted(counts, reverse=True)
    assert set(statutes) == {"a/1", "b/2"}


# ---------------------------------------------------------------------------
# Blind-spot frontier ranking
# ---------------------------------------------------------------------------

def test_frontier_ranks_statutes_by_real_bug_count():
    ledger = _synthetic_ledger()
    out = render_blind_spot_frontier(ledger)
    # a/1 has 3 falsifying divergences, b/2 has 2 => a/1 listed before b/2.
    assert out.index("| a/1 |") < out.index("| b/2 |")


def test_frontier_lists_unattributed_blind_spots_grouped_by_diagnosis():
    ledger = _synthetic_ledger()
    out = render_blind_spot_frontier(ledger)
    # REPLAY_MISSING appears twice (a/1, b/2) => count 2; MISSING once.
    assert "REPLAY_MISSING" in out
    assert "MISSING" in out
    # The bigger group (count 2) sorts above the singleton (count 1).
    assert out.index("REPLAY_MISSING") < out.index("| MISSING |")


def test_frontier_handles_empty_ledger():
    ledger = build_ledger(
        [], jurisdiction="uk", mode="official_consolidation", catalog={}
    )
    out = render_blind_spot_frontier(ledger)
    assert "Blind-spot frontier" in out
    assert "(none)" in out


# ---------------------------------------------------------------------------
# Grounding column
# ---------------------------------------------------------------------------

def test_grounding_column_absent_by_default():
    ledger = _synthetic_ledger()
    md = render_report_markdown(ledger)
    # The header row must not contain a grounding column.
    header = next(line for line in md.splitlines() if line.startswith("| rule_id"))
    assert "grounding" not in header


def test_grounding_column_renders_when_injected():
    ledger = _synthetic_ledger()
    fake_grounding = {
        "r.alpha": ("statute_text", "HAVE"),
        "r.beta": {"authority_tier": "explanatory_note", "status": "GAP"},
        # r.gamma omitted => should render as fallback GAP.
    }
    md = render_report_markdown(ledger, grounding=fake_grounding)
    header = next(line for line in md.splitlines() if line.startswith("| rule_id"))
    assert "grounding" in header
    assert "statute_text/HAVE" in md
    assert "explanatory_note/GAP" in md
    # A rule with no grounding entry degrades visibly, not silently.
    assert "—/GAP" in md


def test_grounding_unknown_status_degrades_to_gap():
    ledger = _synthetic_ledger()
    fake_grounding = {"r.alpha": ("statute_text", "NONSENSE")}
    md = render_report_markdown(ledger, grounding=fake_grounding)
    assert "statute_text/GAP" in md
    assert "NONSENSE" not in md


def test_grounding_renders_from_stream_c_authority_grounding_rows():
    """The real Stream C shape — ``dict[str, AuthorityGrounding]`` — must
    normalize via the frozen row's fields, not degrade through the str(value)
    fallback (which would render the dataclass repr and force GAP)."""
    from lawvm.tools.spec_authority import AuthorityGrounding

    ledger = _synthetic_ledger()
    grounding = {
        "r.alpha": AuthorityGrounding(
            rule_id="r.alpha",
            authority_tier=1,
            source_ref="Interpretation Act 1978 s.5",
            authority_status="HAVE",
        ),
        "r.beta": AuthorityGrounding(
            rule_id="r.beta",
            authority_tier="1/2",
            source_ref="OPC drafting guidance",
            authority_status="SPEC",
        ),
    }
    md = render_report_markdown(ledger, grounding=grounding)
    header = next(line for line in md.splitlines() if line.startswith("| rule_id"))
    assert "grounding" in header
    # Tier + status read straight off the frozen row; no degraded GAP for these.
    assert "1/HAVE" in md
    assert "1/2/SPEC" in md
    # The dataclass repr must never leak into the rendered column.
    assert "AuthorityGrounding(" not in md


def test_persisted_uk_report_loads_real_grounding_and_gap_fallback(tmp_path):
    from lawvm.tools.spec_authority import load_uk_authority_grounding

    grounding = load_uk_authority_grounding()
    known_rule_id, known = next(iter(grounding.items()))
    ungrounded_rule_id = "uk.synthetic.ungrounded_rule"
    ledger = build_ledger(
        [
            StatuteLedgerInput(
                "ukpga/2000/1",
                {known_rule_id: 1, ungrounded_rule_id: 1},
                [],
            )
        ],
        jurisdiction="uk",
        mode="official_consolidation",
        catalog={
            known_rule_id: "grounded spec",
            ungrounded_rule_id: "ungrounded spec",
        },
    )

    persist_ledger(ledger, tmp_path)

    md = (tmp_path / "spec_ledger.md").read_text(encoding="utf-8")
    header = next(line for line in md.splitlines() if line.startswith("| rule_id"))
    assert "grounding" in header
    assert f"{known.authority_tier}/{known.authority_status}" in md
    assert f"| {ungrounded_rule_id} | Y | —/GAP |" in md


# ---------------------------------------------------------------------------
# Catalog-coverage regression guard
# ---------------------------------------------------------------------------

def _payload(rules):
    return {"rules": rules}


def test_diff_flags_decataloged_rule():
    prev = _payload([
        {"rule_id": "r.alpha", "believed_spec": "spec", "cataloged": True},
    ])
    cur = _payload([
        {"rule_id": "r.alpha", "believed_spec": "", "cataloged": False},
    ])
    msgs = diff_catalog_coverage(prev, cur)
    assert any("DECATALOGED RULE: r.alpha" in m for m in msgs)


def test_diff_flags_new_uncataloged_rule():
    prev = _payload([
        {"rule_id": "r.alpha", "believed_spec": "spec", "cataloged": True},
    ])
    cur = _payload([
        {"rule_id": "r.alpha", "believed_spec": "spec", "cataloged": True},
        {"rule_id": "r.new", "believed_spec": "", "cataloged": False},
    ])
    msgs = diff_catalog_coverage(prev, cur)
    assert any("NEW UNCATALOGED RULE: r.new" in m for m in msgs)


def test_diff_silent_when_no_drift():
    rules = [
        {"rule_id": "r.alpha", "believed_spec": "spec", "cataloged": True},
        {"rule_id": "r.beta", "believed_spec": "spec2", "cataloged": True},
    ]
    assert diff_catalog_coverage(_payload(rules), _payload(rules)) == []


def test_diff_new_cataloged_rule_is_not_drift():
    # A new rule that arrives *with* a believed_spec is healthy, not drift.
    prev = _payload([
        {"rule_id": "r.alpha", "believed_spec": "spec", "cataloged": True},
    ])
    cur = _payload([
        {"rule_id": "r.alpha", "believed_spec": "spec", "cataloged": True},
        {"rule_id": "r.new", "believed_spec": "fresh spec", "cataloged": True},
    ])
    assert diff_catalog_coverage(prev, cur) == []


def test_diff_against_real_persisted_payload(tmp_path):
    # End-to-end: persist a ledger, mutate the cataloged flag, confirm drift.
    ledger = _synthetic_ledger()
    json_path = persist_ledger(ledger, tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    # No drift against itself.
    assert diff_catalog_coverage(payload, payload) == []
    # Decatalog r.alpha in a copy.
    mutated = json.loads(json.dumps(payload))
    for row in mutated["rules"]:
        if row["rule_id"] == "r.alpha":
            row["cataloged"] = False
            row["believed_spec"] = ""
    msgs = diff_catalog_coverage(payload, mutated)
    assert any("DECATALOGED RULE: r.alpha" in m for m in msgs)
