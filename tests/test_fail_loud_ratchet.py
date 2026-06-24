"""Monotone fail-loud ratchet gate (audit registry EV-08).

Enforces "no NEW non-test broad `except Exception` / bare `except:` that silently
swallows the error without re-raising or emitting a named typed diagnostic/
finding" over ``src/lawvm/{core,finland}`` — the least-operationalized arm of the
§1.10 fail-loud invariant. Mirrors the proven ``tests/test_regex_ratchet.py``
shape: a committed per-file baseline (``tests/data/fail_loud_ratchet_baseline.json``)
that may only FALL; a NEW un-waived silent-swallow trips the gate.

Classification (see ``scripts/inventory_control_flow_smells.py``):
  - A handler is *broad* if it is ``except:`` (bare) or catches ``Exception`` /
    ``BaseException`` (a narrowly-typed handler is never counted).
  - A broad handler is *owned* (NOT a silent swallow) if its body re-raises (any
    ``raise``) OR constructs/calls a named typed diagnostic/finding/failure/
    residual/pathology/… — i.e. the error becomes a typed, production-visible
    fact, not a swallowed return/continue/pass.
  - An inline ``# lawvm-failloud: <reason>`` waiver (on the ``except`` line or the
    line directly above) clears a deliberately-silent site.

This is a FREEZE-and-ratchet, not a drive-to-zero: the current 65 baselined
broad-swallow sites are fenced, not fixed. Lowering them is future work; this gate
only forbids ADDING a new one.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_control_flow_smells.py"


def _load_inventory_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_control_flow_smells", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.FAIL_LOUD_BASELINE_PATH
    assert path.exists(), (
        f"Missing fail-loud ratchet baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_control_flow_smells.py "
        "--ratchet failloud --update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestFailLoudRatchet:
    def test_no_new_silent_broad_except(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_fail_loud_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["unwaived_counts"]
        current_counts: dict[str, int] = state["unwaived_counts"]

        increases: list[str] = []
        for rel, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(rel, 0)
            if count > allowed:
                increases.append(
                    f"  {rel}: {count} un-waived silent broad-except handlers "
                    f"(baseline {allowed}, +{count - allowed})"
                )

        if increases:
            pytest.fail(
                "\n[FAIL-LOUD RATCHET] NEW un-waived broad `except Exception` / "
                "bare `except:` that silently swallows the error:\n"
                + "\n".join(increases)
                + "\n\nA broad handler must either:\n"
                "  (1) re-raise (the error is a programming bug / not owned here), "
                "or\n"
                "  (2) emit a NAMED typed diagnostic/finding/failure/residual on "
                "the handler (the error becomes a production-visible typed fact), "
                "or\n"
                "  (3) narrow the caught type (`except ValueError`), or\n"
                "  (4) if the silent swallow is genuinely intentional, mark it "
                "`# lawvm-failloud: <reason>` on the `except` line.\n"
                "See notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md row EV-08."
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_fail_loud_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["unwaived_counts"]
        current_counts: dict[str, int] = state["unwaived_counts"]

        decreases: list[str] = []
        for rel, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(
                    f"  {rel}: now {count} un-waived (baseline {allowed}, "
                    f"-{allowed - count})"
                )

        if decreases:
            pytest.fail(
                "\n[FAIL-LOUD RATCHET] The un-waived silent broad-except count "
                "DROPPED — good work, but the baseline must be lowered to lock the "
                "gain in:\n"
                + "\n".join(decreases)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python scripts/inventory_control_flow_smells.py "
                "--ratchet failloud --update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever fall)."
            )

    def test_total_unwaived_matches_baseline_invariant(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_fail_loud_ratchet(_REPO_ROOT)
        assert baseline["total_unwaived"] == sum(
            baseline["unwaived_counts"].values()
        ), "Baseline total_unwaived is inconsistent with its per-file counts."
        assert state["total_unwaived"] <= baseline["total_unwaived"], (
            f"Total un-waived silent broad-except {state['total_unwaived']} "
            f"exceeds baseline {baseline['total_unwaived']}."
        )


# ---------------------------------------------------------------------------
# Guard-liveness: the scan must catch a NEW silent swallow, honor ownership /
# re-raise / waiver, and NOT flag a narrow handler. Each fixture drives the
# REAL production scan (scan_file_fail_loud_sites) — AGENTS.md §2.9.
# ---------------------------------------------------------------------------


class TestFailLoudGuardLiveness:
    _F = "src/lawvm/core/x.py"

    def _scan(self, text: str) -> list[Any]:
        return _INV.scan_file_fail_loud_sites(self._F, text)

    def test_silent_swallow_is_counted(self) -> None:
        text = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception:\n"
            "        return None\n"
        )
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["owned"] is False
        assert recs[0]["waived"] is False
        assert recs[0]["counts"] is True

    def test_bare_except_silent_swallow_is_counted(self) -> None:
        text = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except:\n"
            "        pass\n"
        )
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["bare"] is True
        assert recs[0]["counts"] is True

    def test_reraise_is_owned_not_counted(self) -> None:
        text = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception:\n"
            "        raise\n"
        )
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["owned"] is True
        assert recs[0]["counts"] is False

    def test_named_diagnostic_emit_is_owned_not_counted(self) -> None:
        text = (
            "def f(out):\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        out.append(HEAcquisitionFailure(exc))\n"
            "        return None\n"
        )
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["owned"] is True
        assert recs[0]["counts"] is False

    def test_record_call_is_owned_not_counted(self) -> None:
        text = (
            "def f(self):\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        self.record_diagnostic(exc)\n"
            "        return None\n"
        )
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["owned"] is True

    def test_waiver_on_except_line_clears_site(self) -> None:
        text = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception:  # lawvm-failloud: best-effort telemetry only\n"
            "        return None\n"
        )
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["waived"] is True
        assert recs[0]["counts"] is False

    def test_waiver_on_line_above_clears_site(self) -> None:
        text = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    # lawvm-failloud: parsing a malformed optional cache is benign\n"
            "    except Exception:\n"
            "        return None\n"
        )
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["waived"] is True

    def test_narrow_handler_is_not_a_hit(self) -> None:
        text = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError:\n"
            "        return None\n"
        )
        assert self._scan(text) == []

    def test_narrow_tuple_handler_is_not_a_hit(self) -> None:
        text = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except (KeyError, OSError):\n"
            "        return None\n"
        )
        assert self._scan(text) == []

    def test_unparseable_text_yields_no_hits(self) -> None:
        text = "def f(  # broken\n    except Exception:\n        pass\n"
        assert self._scan(text) == []
