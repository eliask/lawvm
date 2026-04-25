"""Section-level bisect support for evidence bundles.

Extracted from evidence.py to reduce file size.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from lawvm.tools._evidence_helpers import (
    _REPLAY_BUG_DIAGNOSES,
    _chapter_label_from_key,
    _normalize_observation_streams,
    _payload_materially_prefers_replay,
    _run_quietly,
    _same_chapter_alternative_replay_matches,
    _same_section_unmatched_oracle_subsections,
    _section_label_from_key,
    _section_similarity,
)


def _section_bisect_support(
    statute_id: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    section_results: Iterable[Dict],
    *,
    oracle_root: Optional[Any] = None,
    corpus: Optional[Any] = None,
) -> List[Dict]:
    from lxml import etree

    from lawvm.finland.corpus import get_corpus as _get_corpus
    from lawvm.tools.bisect_section import build_bisect_bundle, build_bisect_bundles_batch
    from lawvm.tools._section_debug import render_node_text
    from lawvm.tools.section_keys import extract_ir_sections, extract_oracle_sections
    from lawvm.tools.trace_section import build_trace_bundle
    from lawvm.finland.grafter import (
        _resolve_applicable_amendment_records,
        get_ground_truth_tree,
        replay_xml,
        process_muutoslaki,
    )
    from lawvm.finland.apply_events import ApplyMutationInvariantReport
    from lawvm.finland.helpers import _fi_label_postprocessor
    from lawvm.finland.statute import ReplayState, StatuteContext
    from lawvm.tools._section_debug import score_text_pair

    replay_sections = [
        {
            "section": str(item.get("section") or ""),
            "blame_source": str(item.get("blame_source") or ""),
            "replay_text": str(item.get("replay_text") or ""),
            "oracle_text": str(item.get("oracle_text") or ""),
        }
        for item in section_results
        if str(item.get("diagnosis") or "") in _REPLAY_BUG_DIAGNOSES
    ]
    if corpus is None:
        try:
            corpus = _get_corpus()
        except Exception:
            corpus = None
    amendment_support_cache: Dict[str, Dict[str, Any]] = {}
    # Unified baseline cache: stop_before -> (section_texts, section_ir_nodes)
    _baseline_cache: Dict[str, Tuple[Dict[str, str], Dict[str, Any]]] = {}
    if oracle_root is None:
        try:
            oracle_root = _run_quietly(get_ground_truth_tree, statute_id)
        except (OSError, RuntimeError):
            oracle_root = None
    oracle_sections = extract_oracle_sections(oracle_root) if oracle_root is not None else {}

    # ── Batch blame snapshots ──────────────────────────────────────────
    # One incremental replay pass snapshots (state, ctx) at each blame
    # point.  _amendment_support, _baseline_replay, and trace all reuse
    # these snapshots instead of calling replay_xml(stop_before=...).
    # Saves O(B) full/partial replays → O(1 pass through amendment chain).
    unique_blame_sources = sorted(
        {item["blame_source"] for item in replay_sections if item["blame_source"]}
    )
    # {blame_source: (state_before, state_after, ctx)}
    _blame_snapshots: Dict[str, Tuple[Any, Any, Any]] = {}
    if unique_blame_sources and corpus is not None:
        try:
            xml_bytes = corpus.read_source(statute_id)
            if xml_bytes:
                _snap_ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
                # Baseline IR is immutable; this snapshot seed is a pure replay
                # starting point, so a deep copy would only add overhead here.
                _snap_state = ReplayState(ir=_snap_ctx.base_ir)
                _snap_records, _, _ = _resolve_applicable_amendment_records(statute_id, mode)
                _snap_wanted = set(unique_blame_sources)
                for _snap_rec in _snap_records:
                    _snap_mid = str(_snap_rec["statute_id"])
                    if _snap_mid in _snap_wanted:
                        # Snapshot BEFORE applying this amendment
                        _state_before = _snap_state
                        _pm_res = _run_quietly(
                            process_muutoslaki,
                            _snap_mid, _snap_state, _snap_ctx,
                            replay_mode=mode, parent_id=statute_id, corpus=corpus,
                        )
                        _state_after = _pm_res.output if _pm_res is not None else _snap_state
                        _blame_snapshots[_snap_mid] = (_state_before, _state_after, _snap_ctx)
                        _snap_wanted.discard(_snap_mid)
                        if not _snap_wanted:
                            break
                    _pm_res2 = _run_quietly(
                        process_muutoslaki,
                        _snap_mid, _snap_state, _snap_ctx,
                        replay_mode=mode, parent_id=statute_id, corpus=corpus,
                    )
                    _snap_state = _pm_res2.output if _pm_res2 is not None else _snap_state
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            _blame_snapshots = {}  # fall back to per-blame replay

    def _normalize_section_num_label(raw: str) -> str:
        norm = " ".join(str(raw or "").replace("\xa0", " ").split()).strip()
        norm = norm.replace(" §", "").replace("§", "").strip()
        return norm.replace(" ", "")

    def _amendment_support(blame_source: str) -> Dict[str, Any]:
        cached = amendment_support_cache.get(blame_source)
        if cached is not None:
            return cached

        if corpus is None:
            cached = {
                "body_section_labels": set(),
                "body_section_texts": {},
                "compiled_ops_out": [],
                "elaboration_observations": [],
                "sparse_slot_bindings": [],
                "sparse_leftovers": [],
                "apply_mutation_events": [],
                "apply_mutation_invariant_reports": [],
            }
            amendment_support_cache[blame_source] = cached
            return cached

        xml_bytes = corpus.read_source(blame_source)
        if not xml_bytes:
            cached = {
                "body_section_labels": set(),
                "body_section_texts": {},
                "compiled_ops_out": [],
                "elaboration_observations": [],
                "sparse_slot_bindings": [],
                "sparse_leftovers": [],
                "apply_mutation_events": [],
                "apply_mutation_invariant_reports": [],
            }
            amendment_support_cache[blame_source] = cached
            return cached

        root = etree.fromstring(xml_bytes)
        body_section_texts = {
            _normalize_section_num_label(str(num_el.text or "")): " ".join(
                etree.tostring(section_el, encoding="unicode", method="text").split()
            )
            for section_el in root.findall(".//{*}section")
            for num_el in [section_el.find("{*}num")]
            if num_el is not None and str(num_el.text or "").strip()
        }
        body_labels = {
            _normalize_section_num_label(str(num_el.text or ""))
            for num_el in root.findall(".//{*}section/{*}num")
            if str(num_el.text or "").strip()
        }

        def _compile_rows() -> List[Dict[str, Any]]:
            # Use pre-computed snapshot if available, otherwise fall back to replay
            snap = _blame_snapshots.get(blame_source)
            if snap is not None:
                snap_state_before, _, snap_ctx = snap
            else:
                rr = replay_xml(
                    statute_id,
                    mode=mode,
                    stop_before=blame_source,
                    corpus=corpus,
                    quiet=True,
                )
                snap_state_before = rr.state
                snap_ctx = rr.ctx
            compiled_rows: List[Dict[str, Any]] = []
            elaboration_observations: List[Dict[str, Any]] = []
            sparse_slot_bindings: List[Dict[str, Any]] = []
            sparse_leftovers: List[Dict[str, Any]] = []
            apply_mutation_events: List[Any] = []
            apply_mutation_invariant_reports: List[ApplyMutationInvariantReport] = []
            process_muutoslaki(
                blame_source,
                snap_state_before,
                snap_ctx,
                replay_mode=mode,
                compiled_ops_out=compiled_rows,
                parent_id=statute_id,
                corpus=corpus,
                elaboration_observations_out=elaboration_observations,
                sparse_slot_bindings_out=sparse_slot_bindings,
                sparse_leftovers_out=sparse_leftovers,
                mutation_events_out=apply_mutation_events,
                mutation_invariant_reports_out=apply_mutation_invariant_reports,
            )
            amendment_support_cache[blame_source] = {
                "body_section_labels": body_labels,
                "body_section_texts": body_section_texts,
                "compiled_ops_out": compiled_rows,
                "elaboration_observations": elaboration_observations,
                "sparse_slot_bindings": sparse_slot_bindings,
                "sparse_leftovers": sparse_leftovers,
                "apply_mutation_events": apply_mutation_events,
                "apply_mutation_invariant_reports": apply_mutation_invariant_reports,
                "normalized_compiler_observations": _normalize_observation_streams(
                    elaboration_observations=elaboration_observations,
                    sparse_slot_bindings=sparse_slot_bindings,
                    sparse_leftovers=sparse_leftovers,
                    apply_mutation_events=apply_mutation_events,
                    apply_mutation_invariant_reports=apply_mutation_invariant_reports,
                ),
            }
            return compiled_rows

        compiled_rows = _run_quietly(_compile_rows) or []
        cached = amendment_support_cache.get(blame_source) or {
            "body_section_labels": body_labels,
            "body_section_texts": body_section_texts,
            "compiled_ops_out": compiled_rows,
            "elaboration_observations": [],
            "sparse_slot_bindings": [],
            "sparse_leftovers": [],
            "apply_mutation_events": [],
            "apply_mutation_invariant_reports": [],
        }
        amendment_support_cache[blame_source] = cached
        return cached

    def _baseline_replay(stop_before: str) -> Tuple[Dict[str, str], Dict[str, Any]]:
        """Return (section_texts, section_ir_nodes) from snapshot or replay."""
        cached = _baseline_cache.get(stop_before)
        if cached is not None:
            return cached
        # Use pre-computed snapshot if available
        snap = _blame_snapshots.get(stop_before)
        if snap is not None:
            snap_state_before, _, _ = snap
            ir = snap_state_before.ir
        else:
            rr = _run_quietly(
                replay_xml,
                statute_id,
                mode=mode,
                stop_before=stop_before,
                corpus=corpus,
                quiet=True,
            )
            ir = None
            if rr is not None:
                materialized_state = getattr(rr, "materialized_state", None)
                if materialized_state is not None:
                    ir = getattr(materialized_state, "ir", None)
                if ir is None:
                    state_obj = getattr(rr, "state", None)
                    ir = getattr(state_obj, "ir", None)
        sections = extract_ir_sections(ir) if ir is not None else {}
        texts = {
            key: render_node_text(node)
            for key, node in sections.items()
        }
        result = (texts, sections)
        _baseline_cache[stop_before] = result
        return result

    def _matching_section_observations(
        observations: List[Dict[str, Any]],
        source_statute: str,
        section_key: str,
    ) -> List[Dict[str, Any]]:
        exact = [
            obs for obs in observations
            if (
                not str(obs.get("source_statute") or "")
                or str(obs.get("source_statute") or "") == source_statute
            )
            and str(obs.get("section") or "") == section_key
        ]
        if exact:
            return exact
        section_label = _section_label_from_key(section_key)
        if not section_label:
            return []
        by_label = [
            obs for obs in observations
            if (
                not str(obs.get("source_statute") or "")
                or str(obs.get("source_statute") or "") == source_statute
            )
            and _section_label_from_key(str(obs.get("section") or "")) == section_label
        ]
        unique_chapter_sections = {
            str(obs.get("section") or "")
            for obs in by_label
            if str(obs.get("section") or "").startswith("chapter:")
        }
        if len(unique_chapter_sections) <= 1:
            return by_label
        return []

    def _matching_frontend_records(
        records: List[Dict[str, Any]],
        source_statute: str,
        section_key: str,
    ) -> List[Dict[str, Any]]:
        section_label = _section_label_from_key(section_key)
        chapter_label = _chapter_label_from_key(section_key)
        if not section_label:
            return []
        matched: List[Dict[str, Any]] = []
        for record in records:
            if (
                str(record.get("source_statute") or "")
                and str(record.get("source_statute") or "") != source_statute
            ):
                continue
            target_unit_kind = str(record.get("target_unit_kind") or "").strip()
            target_kind = str(record.get("target_kind") or "").strip()
            if not target_unit_kind and target_kind == "P":
                target_unit_kind = "section"
            if target_unit_kind != "section":
                continue
            if str(record.get("target_norm") or "").strip() != section_label:
                continue
            record_chapter = str(record.get("target_chapter") or "").strip()
            if record_chapter and record_chapter != chapter_label:
                continue
            matched.append(record)
        return matched

    # Batch bisect: replay once, score all sections at each step — O(A) not O(S×A)
    all_section_keys = [item["section"] for item in replay_sections]
    try:
        batch_bundles = _run_quietly(
            build_bisect_bundles_batch,
            statute_id, all_section_keys, mode, 1.0, 5,
            oracle_root=oracle_root, corpus=corpus,
        ) or {}
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        batch_bundles = {}

    # ── Second snapshot pass for first_drop_sources ───────────────────
    # batch_bundles now reveals first_drop_source values that may not be in
    # _blame_snapshots (they weren't in unique_blame_sources from section_results).
    # Build snapshots for those too so _amendment_support can reuse them instead
    # of falling back to O(A) full replay_xml(stop_before=...) calls.
    _first_drop_missing = sorted(
        {
            str(b.get("first_drop_source") or "")
            for b in batch_bundles.values()
            if str(b.get("first_drop_source") or "")
            and str(b.get("first_drop_source") or "") not in _blame_snapshots
        }
    )
    if _first_drop_missing and corpus is not None:
        try:
            xml_bytes_fd = corpus.read_source(statute_id)
            if xml_bytes_fd:
                _fd_ctx = StatuteContext.from_xml(xml_bytes_fd, _fi_label_postprocessor)
                _fd_state = ReplayState(ir=_fd_ctx.base_ir)
                _fd_records, _, _ = _resolve_applicable_amendment_records(statute_id, mode)
                _fd_wanted = set(_first_drop_missing)
                for _fd_rec in _fd_records:
                    _fd_mid = str(_fd_rec["statute_id"])
                    if _fd_mid in _fd_wanted:
                        _fd_before = _fd_state
                        _fd_pm = _run_quietly(
                            process_muutoslaki,
                            _fd_mid, _fd_state, _fd_ctx,
                            replay_mode=mode, parent_id=statute_id, corpus=corpus,
                        )
                        _fd_after = _fd_pm.output if _fd_pm is not None else _fd_state
                        _blame_snapshots[_fd_mid] = (_fd_before, _fd_after, _fd_ctx)
                        _fd_wanted.discard(_fd_mid)
                        if not _fd_wanted:
                            break
                    _fd_pm2 = _run_quietly(
                        process_muutoslaki,
                        _fd_mid, _fd_state, _fd_ctx,
                        replay_mode=mode, parent_id=statute_id, corpus=corpus,
                    )
                    _fd_state = _fd_pm2.output if _fd_pm2 is not None else _fd_state
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass  # fall back to per-blame replay for the remaining missing sources

    support: List[Dict] = []
    for item in replay_sections:
        section = item["section"]
        blame_source = item["blame_source"]
        section_label = _section_label_from_key(section)
        try:
            bundle = batch_bundles.get(section)
            if bundle is None:
                # Fallback to single-section bisect if batch missed it
                bundle = _run_quietly(
                    build_bisect_bundle,
                    statute_id,
                    section,
                    mode,
                    1.0,
                    5,
                    oracle_root=oracle_root,
                    corpus=corpus,
                )
        except ValueError as exc:
            support.append(
                {
                    "section": section,
                    "baseline_score": 0.0,
                    "first_bad_source": "",
                    "first_drop_source": "",
                    "worst_drops": [],
                    "preexisting_before_any_drop": False,
                    "bisect_available": False,
                    "bisect_error": str(exc),
                    "blame_source": blame_source,
                    "blame_trace_available": False,
                    "blame_body_has_section_payload": False,
                    "blame_compiled_actions_for_section": [],
                    "blame_only_repeal_without_payload": False,
                    "blame_payload_vs_replay_score": None,
                    "blame_payload_vs_oracle_score": None,
                    "blame_payload_prefers_replay": False,
                    "blame_elaboration_kinds": [],
                    "blame_sparse_elaboration": False,
                    "blame_sparse_slot_binding_count": 0,
                    "blame_sparse_slot_binding_labels": [],
                    "blame_apply_helpers_for_section": [],
                }
            )
            continue
        # Inline trace: use blame snapshots to get before/after section text
        # without calling build_trace_bundle (which does 2 replay_xml calls).
        before_score: Optional[float] = None
        after_score: Optional[float] = None
        if blame_source:
            snap = _blame_snapshots.get(blame_source)
            # Use snapshot only if state_after differs from state_before
            # (identity check catches monkeypatched no-op process_muutoslaki)
            if snap is not None and snap[0] is not snap[1]:
                snap_before, snap_after, _ = snap
                before_secs = extract_ir_sections(snap_before.ir) if snap_before else {}
                after_secs = extract_ir_sections(snap_after.ir) if snap_after else {}
                oracle_node = oracle_sections.get(section)
                oracle_text_for_trace = (
                    render_node_text(oracle_node) if oracle_node is not None else ""
                )
                if oracle_text_for_trace:
                    before_node = before_secs.get(section)
                    after_node = after_secs.get(section)
                    before_text_t = render_node_text(before_node)
                    after_text_t = render_node_text(after_node)
                    before_score = score_text_pair(before_text_t, oracle_text_for_trace)
                    after_score = score_text_pair(after_text_t, oracle_text_for_trace)
            else:
                trace_bundle = _run_quietly(
                    build_trace_bundle,
                    statute_id,
                    blame_source,
                    section,
                    mode,
                    oracle_root=oracle_root,
                )
                before_score = (
                    float(trace_bundle.get("before_vs_oracle"))
                    if trace_bundle and trace_bundle.get("before_vs_oracle") is not None
                    else None
                )
                after_score = (
                    float(trace_bundle.get("after_vs_oracle"))
                    if trace_bundle and trace_bundle.get("after_vs_oracle") is not None
                    else None
                )
        blame_source_improved_or_equal = (
            before_score is not None
            and after_score is not None
            and after_score >= (before_score - 1e-9)
        )
        replay_text = item["replay_text"]
        oracle_text = item["oracle_text"]
        blame_body_has_section_payload = False
        blame_compiled_actions_for_section: List[str] = []
        blame_only_repeal_without_payload = False
        blame_payload_vs_replay_score: Optional[float] = None
        blame_payload_vs_oracle_score: Optional[float] = None
        blame_payload_prefers_replay = False
        blame_elaboration_kinds: List[str] = []
        blame_sparse_elaboration = False
        blame_sparse_slot_binding_count = 0
        blame_sparse_slot_binding_labels: List[str] = []
        blame_sparse_leftover_count = 0
        blame_apply_helpers_for_section: List[str] = []
        blame_apply_invariant_kinds: List[str] = []
        first_drop_elaboration_kinds: List[str] = []
        first_drop_sparse_elaboration = False
        first_drop_sparse_slot_binding_count = 0
        first_drop_sparse_slot_binding_labels: List[str] = []
        first_drop_sparse_leftover_count = 0
        first_drop_apply_helpers_for_section: List[str] = []
        baseline_alternative_replay_match: Dict[str, Any] = {}
        baseline_unmatched_oracle_subsections: Dict[str, Any] = {}
        if blame_source:
            amend_support = _amendment_support(blame_source)
            body_labels = amend_support.get("body_section_labels") or set()
            body_section_texts = amend_support.get("body_section_texts") or {}
            blame_body_has_section_payload = section_label in body_labels
            compiled_rows = amend_support.get("compiled_ops_out") or []
            actions = [
                str(row.get("action") or "")
                for row in compiled_rows
                if str(((row.get("target") or {}).get("section") or "")) == section_label
            ]
            blame_compiled_actions_for_section = sorted({action for action in actions if action})
            blame_only_repeal_without_payload = (
                not blame_body_has_section_payload
                and blame_compiled_actions_for_section == ["repeal"]
            )
            observations = amend_support.get("normalized_compiler_observations") or []
            matching_observations = _matching_section_observations(
                observations,
                blame_source,
                section,
            )
            matching_frontend_observations = _matching_frontend_records(
                list(amend_support.get("elaboration_observations") or []),
                blame_source,
                section,
            )
            matching_sparse_slot_bindings = _matching_frontend_records(
                list(amend_support.get("sparse_slot_bindings") or []),
                blame_source,
                section,
            )
            matching_sparse_leftovers = _matching_frontend_records(
                list(amend_support.get("sparse_leftovers") or []),
                blame_source,
                section,
            )
            blame_elaboration_kinds = sorted(
                {
                    str(obs.get("kind") or "")
                    for obs in matching_frontend_observations
                    if str(obs.get("kind") or "")
                }
            )
            blame_sparse_leftover_count = sum(
                len(obs.get("unassigned_slots") or [])
                for obs in matching_sparse_leftovers
            )
            blame_sparse_slot_binding_count = sum(
                1
                for _ in matching_sparse_slot_bindings
            )
            blame_sparse_slot_binding_labels = sorted(
                {
                    str(obs.get("payload_slot_label") or "")
                    for obs in matching_sparse_slot_bindings
                    if str(obs.get("payload_slot_label") or "")
                }
            )
            blame_sparse_elaboration = any(
                kind in {
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
                }
                for kind in blame_elaboration_kinds
            ) or blame_sparse_leftover_count > 0
            blame_apply_helpers_for_section = sorted(
                {
                    str(obs.get("helper") or "")
                    for obs in matching_observations
                    if str(obs.get("family") or "") in {"apply_mutation", "apply_mutation_invariant"}
                    and str(obs.get("helper") or "")
                }
            )
            blame_apply_invariant_kinds = sorted(
                {
                    str(code)
                    for obs in matching_observations
                    if str(obs.get("family") or "") == "apply_mutation_invariant"
                    for code in (obs.get("result_codes") or [])
                    if str(code)
                }
            )
            payload_text = str(body_section_texts.get(section_label) or "")
            if blame_body_has_section_payload and payload_text:
                blame_payload_vs_replay_score = _section_similarity(payload_text, replay_text)
                blame_payload_vs_oracle_score = _section_similarity(payload_text, oracle_text)
                explicit_payload_witness = bool(
                    matching_frontend_observations
                    or matching_sparse_slot_bindings
                    or matching_sparse_leftovers
                    or any(
                        str(obs.get("family") or "") == "apply_mutation_invariant"
                        for obs in matching_observations
                    )
                )
                blame_payload_prefers_replay = (
                    explicit_payload_witness
                    and _payload_materially_prefers_replay(
                        blame_payload_vs_replay_score,
                        blame_payload_vs_oracle_score,
                    )
                )
            _, baseline_sections = _baseline_replay(blame_source)
            baseline_unmatched_oracle_subsections = _same_section_unmatched_oracle_subsections(
                baseline_sections.get(section),
                oracle_sections.get(section),
            )
        first_bad_source = str(bundle.get("first_bad_source") or "")
        first_drop_source = str(bundle.get("first_drop_source") or "")
        if first_drop_source:
            first_drop_support = _amendment_support(first_drop_source)
            first_drop_observations = first_drop_support.get("normalized_compiler_observations") or []
            matching_first_drop_observations = _matching_section_observations(
                first_drop_observations,
                first_drop_source,
                section,
            )
            matching_first_drop_elaboration_observations = _matching_frontend_records(
                list(first_drop_support.get("elaboration_observations") or []),
                first_drop_source,
                section,
            )
            matching_first_drop_sparse_slot_bindings = _matching_frontend_records(
                list(first_drop_support.get("sparse_slot_bindings") or []),
                first_drop_source,
                section,
            )
            matching_first_drop_sparse_leftovers = _matching_frontend_records(
                list(first_drop_support.get("sparse_leftovers") or []),
                first_drop_source,
                section,
            )
            first_drop_elaboration_kinds = sorted(
                {
                    str(obs.get("kind") or "")
                    for obs in matching_first_drop_elaboration_observations
                    if str(obs.get("kind") or "")
                }
            )
            first_drop_sparse_leftover_count = sum(
                len(obs.get("unassigned_slots") or [])
                for obs in matching_first_drop_sparse_leftovers
            )
            first_drop_sparse_slot_binding_count = sum(
                1
                for _ in matching_first_drop_sparse_slot_bindings
            )
            first_drop_sparse_slot_binding_labels = sorted(
                {
                    str(obs.get("payload_slot_label") or "")
                    for obs in matching_first_drop_sparse_slot_bindings
                    if str(obs.get("payload_slot_label") or "")
                }
            )
            first_drop_sparse_elaboration = any(
                kind in {
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
                    "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH",
                }
                for kind in first_drop_elaboration_kinds
            ) or first_drop_sparse_leftover_count > 0
            first_drop_apply_helpers_for_section = sorted(
                {
                    str(obs.get("helper") or "")
                    for obs in matching_first_drop_observations
                    if str(obs.get("family") or "") in {"apply_mutation", "apply_mutation_invariant"}
                    and str(obs.get("helper") or "")
                }
            )
        if first_bad_source and oracle_text:
            baseline_texts, _ = _baseline_replay(first_bad_source)
            baseline_match = _same_chapter_alternative_replay_matches(
                [
                    {
                        "section": section,
                        "replay_text": str(baseline_texts.get(section) or ""),
                        "oracle_text": oracle_text,
                    }
                ],
                baseline_texts,
            )
            baseline_alternative_replay_match = baseline_match.get(section) or {}
        support.append(
            {
                "section": section,
                "baseline_score": float(bundle.get("baseline_score") or 0.0),
                "first_bad_source": str(bundle.get("first_bad_source") or ""),
                "first_drop_source": first_drop_source,
                "worst_drops": list(bundle.get("worst_drops") or []),
                "preexisting_before_any_drop": (
                    float(bundle.get("baseline_score") or 0.0) < 0.9999
                    and not str(bundle.get("first_drop_source") or "")
                ),
                "bisect_available": True,
                "blame_source": blame_source,
                "blame_trace_available": before_score is not None or after_score is not None,
                "blame_before_score": before_score,
                "blame_after_score": after_score,
                "blame_source_improved_or_equal": blame_source_improved_or_equal,
                "blame_body_has_section_payload": blame_body_has_section_payload,
                "blame_compiled_actions_for_section": blame_compiled_actions_for_section,
                "blame_only_repeal_without_payload": blame_only_repeal_without_payload,
                "blame_payload_vs_replay_score": blame_payload_vs_replay_score,
                "blame_payload_vs_oracle_score": blame_payload_vs_oracle_score,
                "blame_payload_prefers_replay": blame_payload_prefers_replay,
                "blame_elaboration_kinds": blame_elaboration_kinds,
                "blame_sparse_elaboration": blame_sparse_elaboration,
                "blame_sparse_slot_binding_count": blame_sparse_slot_binding_count,
                "blame_sparse_slot_binding_labels": blame_sparse_slot_binding_labels,
                "blame_sparse_leftover_count": blame_sparse_leftover_count,
                "blame_apply_helpers_for_section": blame_apply_helpers_for_section,
                "blame_apply_invariant_kinds": blame_apply_invariant_kinds,
                "first_drop_elaboration_kinds": first_drop_elaboration_kinds,
                "first_drop_sparse_elaboration": first_drop_sparse_elaboration,
                "first_drop_sparse_slot_binding_count": (
                    first_drop_sparse_slot_binding_count
                ),
                "first_drop_sparse_slot_binding_labels": (
                    first_drop_sparse_slot_binding_labels
                ),
                "first_drop_sparse_leftover_count": first_drop_sparse_leftover_count,
                "first_drop_apply_helpers_for_section": first_drop_apply_helpers_for_section,
                "baseline_alternative_replay_match": baseline_alternative_replay_match or None,
                "baseline_unmatched_oracle_subsections": (
                    baseline_unmatched_oracle_subsections or None
                ),
            }
        )
    return support
