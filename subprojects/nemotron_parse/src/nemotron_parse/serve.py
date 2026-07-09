"""Service CLI — the process boundary the main package talks across.

    python -m nemotron_parse.serve probe
    python -m nemotron_parse.serve parse --page-num N --artifact-digest D < page.png

Wire contract (frozen; see README):
- ``probe``: stdout ``READY <model-id>`` + exit 0 iff the heavy stack imports.
- ``parse``: PNG bytes on stdin -> governed ``KIND: text`` blocks on stdout.
- exit codes: 0 ok, 3 bad input, 4 model/deps unavailable, 5 inference error.
  Diagnostics go to stderr only; stdout carries NOTHING but the wire format.

Argument parsing and the wire emission are light imports; torch loads only
inside ``parse`` (lazily via ``model``), so ``probe``'s failure mode and all
argparse errors work in an environment without the heavy deps installed.
"""
from __future__ import annotations

import argparse
import sys

from nemotron_parse import wire

EXIT_OK = 0
EXIT_BAD_INPUT = 3
EXIT_UNAVAILABLE = 4
EXIT_INFERENCE = 5


def _cmd_probe() -> int:
    from nemotron_parse import model

    try:
        model_id = model.probe_ready()
    except model.ModelUnavailable as exc:
        print(f"NOT-READY {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    print(f"READY {model_id}")
    return EXIT_OK


def _cmd_parse(page_num: int, artifact_digest: str) -> int:
    png_bytes = sys.stdin.buffer.read()
    if not png_bytes:
        print("empty stdin: expected page PNG bytes", file=sys.stderr)
        return EXIT_BAD_INPUT
    if page_num < 1 or not artifact_digest:
        print("--page-num must be >= 1 and --artifact-digest non-empty", file=sys.stderr)
        return EXIT_BAD_INPUT

    from nemotron_parse import model

    try:
        regions = model.parse_page_png(png_bytes)
    except model.ModelUnavailable as exc:
        print(f"model unavailable: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    except model.InferenceError as exc:
        print(f"inference failed on page {page_num}: {exc}", file=sys.stderr)
        return EXIT_INFERENCE

    blocks = wire.emit_kind_blocks(regions)
    wire.assert_wire_clean(blocks)  # stdout NEVER carries an ungoverned head
    sys.stdout.write(blocks)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nemotron_parse.serve", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", help="exit 0 + 'READY <model-id>' iff the heavy stack imports")
    p_parse = sub.add_parser("parse", help="PNG on stdin -> governed KIND: blocks on stdout")
    p_parse.add_argument("--page-num", type=int, required=True, help="1-indexed page number (provenance echo)")
    p_parse.add_argument("--artifact-digest", required=True, help="SHA-256 of the source artifact (provenance echo)")
    args = parser.parse_args(argv)

    if args.command == "probe":
        return _cmd_probe()
    return _cmd_parse(args.page_num, args.artifact_digest)


if __name__ == "__main__":
    raise SystemExit(main())
