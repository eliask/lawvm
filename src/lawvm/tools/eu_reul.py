"""lawvm eu-reul -- inspect EU CELEX/EULI bridge identifiers and retained-law URIs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
import sys

if TYPE_CHECKING:
    import argparse

from lawvm.core.ir import IRNode
from lawvm.eu.reul_bridge import REULBridge
from lawvm.eu.grafter import parse_eu_regulation_ir


def _strip_uri_qualifier(token: str) -> str:
    return token.split("?", 1)[0].split("#", 1)[0].strip()


def _parse_retained_law_parts(uri: str) -> list[str]:
    uri = uri.strip()
    scheme, sep, rest = uri.partition("://")
    if not sep or scheme.lower() != "retained-law":
        return []
    if not rest:
        return []
    return [part for part in rest.split("/") if part.strip()]


def _parse_retained_law_uri(uri: str) -> str:
    parts = _parse_retained_law_parts(uri)
    if len(parts) < 2 or _strip_uri_qualifier(parts[0]).lower() != "celex":
        return ""
    return _strip_uri_qualifier(parts[1])


def _parse_retained_law_path(uri: str) -> list[str]:
    parts = _parse_retained_law_parts(uri)
    if len(parts) < 3 or _strip_uri_qualifier(parts[0]).lower() != "celex":
        return []
    return [_strip_uri_qualifier(part) for part in parts[2:] if part.strip()]


def _node_payload(node: IRNode) -> dict[str, object]:
    return {
        "kind": str(node.kind),
        "label": node.label,
        "text": (node.text or "").strip(),
        "children_count": len(node.children),
    }


def _run_map(args: "argparse.Namespace") -> dict[str, object]:
    bridge = REULBridge()
    celex = args.celex
    eu_path = args.eu_path
    return {
        "mode": "map",
        "celex": celex,
        "eu_path": eu_path,
        "uk_eid": bridge.map_celex_to_uk_eid(celex, eu_path),
    }


def _run_resolve(args: "argparse.Namespace") -> dict[str, object]:
    bridge = REULBridge()
    uri = args.uri
    statute_xml = Path(args.statute_xml)
    parsed_parts = _parse_retained_law_parts(uri)
    if (
        len(parsed_parts) < 2
        or _strip_uri_qualifier(parsed_parts[0]).lower() != "celex"
    ):
        raise ValueError("uri must start with retained-law://celex/<CELEX>/...")

    path_parts = _parse_retained_law_path(uri)
    if not path_parts or (len(path_parts) % 2) != 0:
        raise ValueError(
            "uri must match pattern retained-law://celex/<CELEX>/<kind>/<label>[...]/"
        )

    celex = _parse_retained_law_uri(uri)
    if not celex:
        raise ValueError("uri must start with retained-law://celex/<CELEX>/...")

    eu_statute = parse_eu_regulation_ir(statute_xml, celex=celex)
    diagnostics: list[dict[str, Any]] = []
    resolved = bridge.resolve_retained_law_uri(uri, eu_statute, diagnostics_out=diagnostics)
    payload: dict[str, object] = {
        "mode": "resolve",
        "uri": uri,
        "found": resolved is not None,
        "eu_statute_id": eu_statute.statute_id,
    }
    if diagnostics:
        payload["diagnostics"] = diagnostics
    if resolved is not None:
        payload["node"] = _node_payload(resolved)
    return payload


def _format_output(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        mode = payload.get("mode")
        if mode == "map":
            print(payload["uk_eid"])
            return

        if mode == "resolve":
            if payload["found"]:
                node = cast(dict[str, Any], payload["node"])
                print(f"{node['kind']}:{node['label']}")
                text = node.get("text", "")
                if text:
                    print((text or "").strip())
            else:
                print("not_found")
            return

        raise ValueError(f"unsupported mode: {mode!r}")


def main(args: "argparse.Namespace") -> None:
    if getattr(args, "eu_reul_command", "") not in {"map", "resolve"}:
        print("error: eu-reul requires a subcommand: map or resolve", file=sys.stderr)
        sys.exit(1)

    if args.eu_reul_command == "map":
        payload = _run_map(args)
    else:
        payload = _run_resolve(args)

    _format_output(payload, bool(getattr(args, "json", False)))
