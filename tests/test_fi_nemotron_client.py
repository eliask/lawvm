"""Nemotron-Parse thin client — process-boundary wire contract + isolation.

Hermetic: every test drives the ``_run_service`` transport seam with a fake
(no subprocess, no heavy deps, no network). The one real-service test is
env-gated on ``LAWVM_NEMOTRON_PARSE_CMD`` + a sample PDF.

Also the ISOLATION RATCHET: pins that the heavy VLM stack never leaks into
the main pyproject and that ``subprojects/`` stays outside main CI collection.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Tuple

import pytest

from lawvm.core.source_document import ExtractionAssertion, SourceManifestation
from lawvm.finland.llm_backends.nemotron_client import (
    SERVICE_CMD_ENV,
    NemotronParseClient,
    NemotronParseFailure,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SUBPROJECT = _REPO_ROOT / "subprojects" / "nemotron_parse"
_WIRE_GOLDEN = _SUBPROJECT / "tests" / "data" / "wire_contract_golden.txt"


def _manifestation(bytes_: bytes = b"%PDF-1.4") -> SourceManifestation:
    return SourceManifestation(
        artifact_digest="b" * 64,
        source_bytes=bytes_,
        locator="doc.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 1, 1),
        media_type="application/pdf",
    )


class _FakeService(NemotronParseClient):
    """Fake transport: canned (rc, stdout, stderr) per subcommand."""

    def __init__(
        self,
        *,
        probe: Tuple[int, str, str] = (0, "READY test-nemotron-v1.2\n", ""),
        parse: Tuple[int, str, str] = (0, "", ""),
    ) -> None:
        super().__init__(service_cmd="fake-nemotron-service")
        self._probe = probe
        self._parse = parse
        self.calls: list[Tuple[Tuple[str, ...], int]] = []

    def _render_page_png(self, pdf_bytes: bytes, page_num: int) -> bytes:  # type: ignore[override]
        return b"\x89PNG-fake"

    def _run_service(
        self, args: Tuple[str, ...], stdin_bytes: bytes, timeout: float
    ) -> Tuple[int, str, str]:  # type: ignore[override]
        self.calls.append((args, len(stdin_bytes)))
        return self._probe if args[0] == "probe" else self._parse


# --------------------------------------------------------------------------- #
# Availability: the determinism firewall                                       #
# --------------------------------------------------------------------------- #


def test_unconfigured_client_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # No env, no explicit command -> inert client, pipeline falls back.
    monkeypatch.delenv(SERVICE_CMD_ENV, raising=False)
    assert NemotronParseClient().is_available() is False


def test_unreachable_service_is_unavailable() -> None:
    # A configured but nonexistent command must be False, never a raise.
    client = NemotronParseClient(
        service_cmd="/nonexistent/nemotron-parse-service definitely-not-here",
        probe_timeout=5.0,
    )
    assert client.is_available() is False


def test_probe_failure_exit_is_unavailable() -> None:
    client = _FakeService(probe=(4, "", "NOT-READY heavy deps not importable"))
    assert client.is_available() is False


def test_probe_ready_makes_available_and_records_model_id() -> None:
    client = _FakeService()
    assert client.is_available() is True
    client._parse = (0, "PARA: x", "")
    (a,) = client.propose_page(_manifestation(), 3)
    assert a.run_id.startswith("nemotron_parse@test-nemotron-v1.2:")


# --------------------------------------------------------------------------- #
# Wire contract: KIND: blocks -> ExtractionAssertions                          #
# --------------------------------------------------------------------------- #


def test_propose_page_parses_shared_wire_golden() -> None:
    # THE cross-boundary pin: the exact golden the subproject's hermetic
    # emission test freezes must parse into the expected assertions here.
    golden = _WIRE_GOLDEN.read_text(encoding="utf-8")
    client = _FakeService(parse=(0, golden, ""))
    assertions = client.propose_page(_manifestation(), 10)
    assert all(isinstance(a, ExtractionAssertion) for a in assertions)
    assert [a.fragment_kind for a in assertions] == [
        "heading",
        "paragraph",
        "item",
        "table",
        "footnote",
    ]
    assert assertions[0].text == "4 §"
    # the wrapped continuation line is joined into the same PARA block
    assert "valmisteveroa 4 senttiä litralta." in assertions[1].text
    a = assertions[0]
    assert a.anchor.page_num == 10
    assert a.anchor.locator == "nemotron_parse:page=10"
    assert a.anchor.artifact_digest == "b" * 64
    assert a.run_id.startswith("nemotron_parse@")
    # the service was invoked with the provenance-echo args + the PNG on stdin
    args, stdin_len = client.calls[-1]
    assert args == ("parse", "--page-num", "10", "--artifact-digest", "b" * 64)
    assert stdin_len > 0


def test_ungoverned_kind_is_dropped_never_relabeled() -> None:
    client = _FakeService(parse=(0, "BOGUS: nonsense\nPARA: real text", ""))
    assertions = client.propose_page(_manifestation(), 1)
    assert [(a.fragment_kind, a.text) for a in assertions] == [("paragraph", "real text")]


def test_service_failure_exit_raises_typed_never_silent() -> None:
    client = _FakeService(parse=(5, "", "inference failed on page 2: boom"))
    with pytest.raises(NemotronParseFailure) as exc_info:
        client.propose_page(_manifestation(), 2)
    assert exc_info.value.page_num == 2
    assert exc_info.value.reason_code == "nemotron_service_exit_5"
    assert "boom" in exc_info.value.detail


# --------------------------------------------------------------------------- #
# Isolation ratchet: the heavy stack must NEVER leak into the main package     #
# --------------------------------------------------------------------------- #

_HEAVY_DEP_MARKERS = ("torch", "transformers", "vllm", "accelerate", "timm", "einops")


def test_main_pyproject_never_names_the_heavy_stack() -> None:
    # The whole point of subprojects/nemotron_parse: heavy deps are declared
    # there and ONLY there. This is the frozen guard for that boundary.
    text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    leaked = [dep for dep in _HEAVY_DEP_MARKERS if f'"{dep}' in text]
    assert leaked == [], (
        f"heavy VLM deps {leaked} leaked into the MAIN pyproject.toml — they "
        "belong ONLY in subprojects/nemotron_parse/pyproject.toml (process-"
        "isolated service); the main package talks to them across a "
        "subprocess boundary, never an import."
    )


def test_subproject_declares_heavy_stack_in_its_own_pyproject() -> None:
    text = (_SUBPROJECT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"torch' in text and '"transformers' in text


def test_subprojects_dir_is_excluded_from_main_pytest_collection() -> None:
    # ci.sh runs shards over tests/ only, but a bare `uv run pytest` collects
    # from rootdir — norecursedirs must fence subprojects/ off so main CI can
    # never trip over a directory whose deps it does not install.
    text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "norecursedirs" in text and "subprojects" in text


def test_client_module_imports_nothing_heavy() -> None:
    import ast

    src = (_REPO_ROOT / "src/lawvm/finland/llm_backends/nemotron_client.py").read_text(
        encoding="utf-8"
    )
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    heavy = imported & {*_HEAVY_DEP_MARKERS, "nemotron_parse"}
    assert not heavy, f"thin client imports the heavy/isolated side: {sorted(heavy)}"


# --------------------------------------------------------------------------- #
# Live (env-gated): real subprocess round-trip against the isolated service    #
# --------------------------------------------------------------------------- #

_HE_PDF = Path(os.environ.get("LAWVM_HE_SAMPLE_PDF") or "/nonexistent/no-he-sample.pdf")


@pytest.mark.network
@pytest.mark.skipif(
    not os.environ.get(SERVICE_CMD_ENV), reason=f"set {SERVICE_CMD_ENV} to the service command"
)
@pytest.mark.skipif(not _HE_PDF.exists(), reason="set LAWVM_HE_SAMPLE_PDF to a draft-HE PDF")
def test_live_nemotron_parses_the_bill_page() -> None:
    import hashlib

    pytest.importorskip("pypdfium2")
    client = NemotronParseClient()
    if not client.is_available():
        pytest.skip("nemotron-parse service probe not READY")
    b = _HE_PDF.read_bytes()
    m = SourceManifestation(
        artifact_digest=hashlib.sha256(b).hexdigest(),
        source_bytes=b,
        locator="vm045/he_luonnos.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 5, 20),
        media_type="application/pdf",
    )
    assertions = client.propose_page(m, 10)
    assert len(assertions) >= 1
    joined = " ".join(a.text for a in assertions).lower()
    assert "laki" in joined or "§" in joined
