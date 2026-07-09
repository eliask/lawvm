"""Nemotron-Parse thin client — a PROCESS-ISOLATED vision producer.

One more INDEPENDENT vision witness (NVIDIA Nemotron-Parse, a purpose-built
doc-parse VLM) feeding the SAME producer-neutral adjudication as pdfplumber /
reading-order / the llama.cpp vision producer. Never trusted alone; it only
emits region-anchored ``ExtractionAssertion`` candidates.

ISOLATION CONTRACT (the reason this module exists): the heavy torch/VLM stack
lives in ``subprojects/nemotron_parse/`` with its OWN pyproject; this module
NEVER imports it. The only coupling is a subprocess wire contract — the
service CLI takes a page PNG on stdin and returns the SAME compact ``KIND:``
block format ``vision_producer._parse_blocks`` already understands (contract
frozen in ``subprojects/nemotron_parse/README.md`` + golden, and pinned from
both sides by ``tests/test_fi_nemotron_client.py`` here and the subproject's
hermetic ``test_wire_contract.py``).

Determinism firewall: the client is INERT unless ``LAWVM_NEMOTRON_PARSE_CMD``
names the service command (e.g. ``uv run --project subprojects/nemotron_parse
python -m nemotron_parse.serve``). Unset -> ``is_available() == False`` -> the
ingest pipeline falls back to reading-order extraction. No default command:
a default of ``uv run --project ...`` could trigger a multi-gigabyte heavy-dep
install as a SIDE EFFECT of an availability probe, which is exactly the
explosion this boundary forbids.

Discipline (AGENTS.md §1.9, §1.10): typed carriers; a service failure is a
typed raise, never a silent empty page; the subprocess call is a seam
(``_run_service``) so parsing is testable without the service.
"""
from __future__ import annotations

import io
import os
import shlex
import subprocess
from typing import Optional, Tuple

from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.extraction import (
    ExtractionAssertion,
    SourceManifestation,
)
from lawvm.finland.llm_backends.vision_producer import _parse_blocks

#: Env var naming the service command (shlex-split). Unset => unavailable.
SERVICE_CMD_ENV = "LAWVM_NEMOTRON_PARSE_CMD"


class NemotronParseFailure(Exception):
    """Service spawn / exit-code / render failure (typed, never silent)."""

    def __init__(self, *, page_num: int, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.page_num = page_num
        self.reason_code = reason_code
        self.detail = detail


class NemotronParseClient:
    """``_VisionProducer`` over the isolated nemotron-parse service CLI."""

    def __init__(
        self,
        *,
        service_cmd: Optional[str] = None,
        scale: float = 2.0,
        probe_timeout: float = 30.0,
        parse_timeout: float = 300.0,
    ) -> None:
        self._service_cmd = service_cmd if service_cmd is not None else os.environ.get(SERVICE_CMD_ENV)
        self._scale = scale
        self._probe_timeout = probe_timeout
        self._parse_timeout = parse_timeout
        self._model_id: Optional[str] = None

    @property
    def producer_id(self) -> str:
        return "nemotron_parse"

    # ------------------------------------------------------------------ #
    # Transport seam — the ONLY place a subprocess is spawned. Tests fake #
    # this; an HTTP mode would replace only this method.                  #
    # ------------------------------------------------------------------ #
    def _run_service(
        self, args: Tuple[str, ...], stdin_bytes: bytes, timeout: float
    ) -> Tuple[int, str, str]:
        """Run ``<service_cmd> <args>`` -> (returncode, stdout, stderr)."""
        if not self._service_cmd:
            raise FileNotFoundError(f"{SERVICE_CMD_ENV} is not set")
        cmd = tuple(shlex.split(self._service_cmd)) + args
        proc = subprocess.run(  # noqa: S603 — operator-configured command
            cmd,
            input=stdin_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return (
            proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
        )

    def is_available(self) -> bool:
        """True iff the service probe answers ``READY`` (exit 0). Absent -> False."""
        if not self._service_cmd:
            return False
        try:
            rc, out, _err = self._run_service(("probe",), b"", self._probe_timeout)
        except (OSError, subprocess.SubprocessError):
            return False
        first = out.strip().splitlines()[0] if out.strip() else ""
        if rc != 0 or not first.startswith("READY"):
            return False
        # "READY <model-id>" — record the model id for run_id provenance.
        parts = first.split(None, 1)
        if len(parts) == 2:
            self._model_id = parts[1].strip()
        return True

    def _render_page_png(self, pdf_bytes: bytes, page_num: int) -> bytes:
        import importlib

        try:
            pdfium = importlib.import_module("pypdfium2")
        except ImportError as exc:
            raise NemotronParseFailure(
                page_num=page_num,
                reason_code="nemotron_render_backend_missing",
                detail=f"pypdfium2 not importable: {exc}",
            ) from exc
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            if page_num < 1 or page_num > len(doc):
                raise NemotronParseFailure(
                    page_num=page_num,
                    reason_code="nemotron_page_out_of_range",
                    detail=f"page {page_num} out of range (1..{len(doc)})",
                )
            pil = doc[page_num - 1].render(scale=self._scale).to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            return buf.getvalue()
        finally:
            doc.close()

    def propose_page(
        self, manifestation: SourceManifestation, page_num: int
    ) -> Tuple[ExtractionAssertion, ...]:
        """Render 1-indexed ``page_num``, parse it across the process boundary.

        Raises ``NemotronParseFailure`` (never a silent empty tuple) so the
        caller emits a typed residual or retries.
        """
        png = self._render_page_png(manifestation.source_bytes, page_num)
        args = (
            "parse",
            "--page-num",
            str(page_num),
            "--artifact-digest",
            manifestation.artifact_digest,
        )
        try:
            rc, out, err = self._run_service(args, png, self._parse_timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise NemotronParseFailure(
                page_num=page_num,
                reason_code="nemotron_service_unreachable",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        if rc != 0:
            raise NemotronParseFailure(
                page_num=page_num,
                reason_code=f"nemotron_service_exit_{rc}",
                detail=err.strip()[:300] or f"service exited {rc} with no stderr",
            )
        model = self._model_id or "unresolved-nemotron-model"
        run_id = f"nemotron_parse@{model}:{manifestation.artifact_digest[:12]}:page={page_num}"
        anchor = SourceAnchor(
            artifact_digest=manifestation.artifact_digest,
            locator=f"nemotron_parse:page={page_num}",
            page_num=page_num,
        )
        return tuple(
            ExtractionAssertion(run_id=run_id, fragment_kind=kind, text=text, anchor=anchor)
            for kind, text in _parse_blocks(out)
        )
