"""Serve-CLI wire-contract test — HERMETIC: runs without torch/transformers.

The ``wire.py`` emission half is pinned by ``test_wire_contract.py``. THIS file
pins the ``serve.py`` CLI dispatch — the actual process boundary the main-package
client talks across: ``probe`` / ``parse`` routing, the ``READY``/``NOT-READY``
lines, and EVERY documented exit code (0 ok · 3 bad input · 4 unavailable · 5
inference). The heavy ``model`` module is FAKED via ``sys.modules`` (serve
imports it lazily inside each command), so no GPU / torch / weights are touched.

Stdout is the wire; diagnostics go to stderr only — both are asserted.

Run: uv run --project subprojects/nemotron_parse pytest subprojects/nemotron_parse/tests -p no:cacheprovider
"""
from __future__ import annotations

import io
import sys
import types
from pathlib import Path
from typing import Sequence, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nemotron_parse import serve, wire  # noqa: E402


# --------------------------------------------------------------------------- #
# Fake heavy ``model`` module — installed into sys.modules so serve's lazy     #
# ``from nemotron_parse import model`` binds to it (no torch, no weights).     #
# --------------------------------------------------------------------------- #


def _install_fake_model(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_id: str = "nvidia/NVIDIA-Nemotron-Parse-v1.2",
    probe_raises: bool = False,
    parse_regions: Sequence[Tuple[str, str]] | None = None,
    parse_raises: str | None = None,  # "" | "unavailable" | "inference"
) -> types.ModuleType:
    fake = types.ModuleType("nemotron_parse.model")

    class ModelUnavailable(Exception):
        pass

    class InferenceError(Exception):
        pass

    fake.ModelUnavailable = ModelUnavailable  # type: ignore[attr-defined]
    fake.InferenceError = InferenceError  # type: ignore[attr-defined]

    def probe_ready() -> str:
        if probe_raises:
            raise ModelUnavailable("heavy deps not importable: no module named 'torch'")
        return model_id

    def parse_page_png(png_bytes: bytes) -> Tuple[Tuple[str, str], ...]:
        if parse_raises == "unavailable":
            raise ModelUnavailable("weights not loadable")
        if parse_raises == "inference":
            raise InferenceError("CUDA OOM")
        return tuple(parse_regions or ())

    fake.probe_ready = probe_ready  # type: ignore[attr-defined]
    fake.parse_page_png = parse_page_png  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nemotron_parse.model", fake)
    return fake


def _run(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], stdin: bytes = b""
) -> Tuple[int, str, str]:
    """Drive ``serve.main(argv)`` with faked stdin; capture (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(stdin)))
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = serve.main(argv)
    return rc, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- #
# Exit-code table pinned by name to the documented contract (README).         #
# --------------------------------------------------------------------------- #


def test_exit_code_constants_match_contract() -> None:
    assert (serve.EXIT_OK, serve.EXIT_BAD_INPUT, serve.EXIT_UNAVAILABLE, serve.EXIT_INFERENCE) == (
        0,
        3,
        4,
        5,
    )


# --------------------------------------------------------------------------- #
# probe                                                                        #
# --------------------------------------------------------------------------- #


def test_probe_ready_prints_ready_model_id_exit_0(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_model(monkeypatch, model_id="nvidia/NVIDIA-Nemotron-Parse-v1.2")
    rc, out, err = _run(monkeypatch, ["probe"])
    assert rc == serve.EXIT_OK
    assert out == "READY nvidia/NVIDIA-Nemotron-Parse-v1.2\n"
    assert err == ""  # stdout carries the wire; stderr is silent on success


def test_probe_unavailable_prints_not_ready_to_stderr_exit_4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_model(monkeypatch, probe_raises=True)
    rc, out, err = _run(monkeypatch, ["probe"])
    assert rc == serve.EXIT_UNAVAILABLE
    assert out == ""  # NOTHING on stdout when not ready — the client keys on this
    assert err.startswith("NOT-READY ")


# --------------------------------------------------------------------------- #
# parse — happy path                                                           #
# --------------------------------------------------------------------------- #


def test_parse_emits_governed_blocks_to_stdout_exit_0(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_model(
        monkeypatch,
        parse_regions=(
            ("Page-header", "HE 45/2026 vp"),  # dropped: unmapped
            ("Title", "4 §"),
            ("Text", "Sen lisäksi, mitä 1 momentissa säädetään."),
            ("Footnote", "1) Sovelletaan verovuodesta 2025."),
        ),
    )
    rc, out, err = _run(
        monkeypatch,
        ["parse", "--page-num", "10", "--artifact-digest", "b" * 64],
        stdin=b"\x89PNG-not-really-decoded-by-the-fake",
    )
    assert rc == serve.EXIT_OK
    assert out == (
        "HEADING: 4 §\n"
        "PARA: Sen lisäksi, mitä 1 momentissa säädetään.\n"
        "FOOTNOTE: 1) Sovelletaan verovuodesta 2025.\n"
    )
    assert err == ""
    # The stdout must pass the wire-clean guard the CLI itself applies.
    wire.assert_wire_clean(out)


def test_parse_empty_page_emits_empty_stdout_exit_0(monkeypatch: pytest.MonkeyPatch) -> None:
    # A page the model reads as no governed regions is a legitimate empty parse
    # (exit 0, empty stdout) — NOT an error. The bad-input/unavailable/inference
    # exits are the error channel; an empty governed set is not one of them.
    _install_fake_model(monkeypatch, parse_regions=())
    rc, out, err = _run(
        monkeypatch, ["parse", "--page-num", "1", "--artifact-digest", "a" * 64], stdin=b"png"
    )
    assert rc == serve.EXIT_OK
    assert out == ""


# --------------------------------------------------------------------------- #
# parse — exit 3 bad input (empty stdin / bad args), never touches the model   #
# --------------------------------------------------------------------------- #


def test_parse_empty_stdin_is_bad_input_exit_3(monkeypatch: pytest.MonkeyPatch) -> None:
    # Model must NOT be consulted for a structurally bad request; install a fake
    # that would EXPLODE if parse_page_png were called, proving the short-circuit.
    def _explode(_png: bytes) -> Tuple[Tuple[str, str], ...]:
        raise AssertionError("model must not be loaded for empty stdin")

    fake = _install_fake_model(monkeypatch, parse_regions=())
    fake.parse_page_png = _explode  # type: ignore[attr-defined]
    rc, out, err = _run(
        monkeypatch, ["parse", "--page-num", "1", "--artifact-digest", "a" * 64], stdin=b""
    )
    assert rc == serve.EXIT_BAD_INPUT
    assert out == ""
    assert "empty stdin" in err


def test_parse_bad_page_num_is_bad_input_exit_3(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_model(monkeypatch, parse_regions=())
    rc, out, err = _run(
        monkeypatch, ["parse", "--page-num", "0", "--artifact-digest", "a" * 64], stdin=b"png"
    )
    assert rc == serve.EXIT_BAD_INPUT
    assert "--page-num must be >= 1" in err


def test_parse_empty_digest_is_bad_input_exit_3(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_model(monkeypatch, parse_regions=())
    rc, out, err = _run(
        monkeypatch, ["parse", "--page-num", "3", "--artifact-digest", ""], stdin=b"png"
    )
    assert rc == serve.EXIT_BAD_INPUT
    assert "artifact-digest non-empty" in err


# --------------------------------------------------------------------------- #
# parse — exit 4 unavailable / exit 5 inference (typed, never silent)          #
# --------------------------------------------------------------------------- #


def test_parse_model_unavailable_is_exit_4(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_model(monkeypatch, parse_raises="unavailable")
    rc, out, err = _run(
        monkeypatch, ["parse", "--page-num", "2", "--artifact-digest", "c" * 64], stdin=b"png"
    )
    assert rc == serve.EXIT_UNAVAILABLE
    assert out == ""
    assert "model unavailable" in err


def test_parse_inference_error_is_exit_5(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_model(monkeypatch, parse_raises="inference")
    rc, out, err = _run(
        monkeypatch, ["parse", "--page-num", "7", "--artifact-digest", "d" * 64], stdin=b"png"
    )
    assert rc == serve.EXIT_INFERENCE
    assert out == ""
    assert "inference failed on page 7" in err


# --------------------------------------------------------------------------- #
# argparse contract — a missing subcommand / required arg is a usage error     #
# --------------------------------------------------------------------------- #


def test_no_subcommand_is_argparse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    with pytest.raises(SystemExit) as exc:
        serve.main([])
    assert exc.value.code == 2  # argparse's own usage-error code


def test_parse_missing_required_args_is_argparse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    with pytest.raises(SystemExit) as exc:
        serve.main(["parse"])  # no --page-num / --artifact-digest
    assert exc.value.code == 2


# --------------------------------------------------------------------------- #
# The CLI's last-line stdout guard: an ungoverned head can NEVER reach stdout  #
# even if the mapping/model produced one (defense in depth on the boundary).   #
# --------------------------------------------------------------------------- #


def test_parse_never_emits_ungoverned_head_even_if_wire_would(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # emit_kind_blocks already drops unmapped classes, so a clean run can never
    # produce an ungoverned head; this pins that the CLI applies assert_wire_clean
    # as a guard (a class OUTSIDE the mapping is dropped, stdout stays governed).
    _install_fake_model(
        monkeypatch,
        parse_regions=(("Picture", "a chart"), ("Formula", "E=mc^2"), ("Text", "real")),
    )
    rc, out, _err = _run(
        monkeypatch, ["parse", "--page-num", "1", "--artifact-digest", "e" * 64], stdin=b"png"
    )
    assert rc == serve.EXIT_OK
    assert out == "PARA: real\n"
    for line in out.splitlines():
        head, sep, _ = line.partition(":")
        assert not sep or head in wire.GOVERNED_WIRE_KINDS
