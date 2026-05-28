"""REUL Bridge for mapping EU CELEX/ELI to UK Retained EU Law identifiers."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Optional

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir import IRStatute, IRNode
from lawvm.core import tree_ops


@dataclass(frozen=True)
class _CelexParsed:
    year: str
    number: str


_EU_PATH_KIND_ALIAS: Dict[str, str] = {
    "art": "article",
    "article": "article",
    "sec": "section",
    "section": "section",
    "para": "paragraph",
    "paragraph": "paragraph",
    "par": "paragraph",
    "point": "item",
    "subpara": "subparagraph",
    "subparagraph": "subparagraph",
    "annex": "annex",
    "recital": "recital",
    "chapter": "chapter",
    "division": "division",
}

_EU_IR_KIND_ALIAS: Dict[str, str] = {
    "article": "section",
    "section": "section",
    "sec": "section",
    "art": "section",
    "paragraph": "paragraph",
    "para": "paragraph",
    "par": "paragraph",
    "point": "item",
    "item": "item",
    "itm": "item",
    "subparagraph": "subparagraph",
    "subpara": "subparagraph",
    "annex": "annex",
    "recital": "recital",
    "chapter": "chapter",
    "division": "division",
}

def _parse_celex(celex: str) -> Optional[_CelexParsed]:
    celex = celex.strip()
    m = re.match(
        r"^\d(?P<year>\d{4})(?P<kind>[A-Za-z])(?P<number>\d+)$",
        celex,
    )
    if not m:
        return None
    celex_kind = m.group("kind").upper()
    if celex_kind not in {"R", "D", "L"}:
        return None
    number = m.group("number").lstrip("0") or "0"
    return _CelexParsed(year=m.group("year"), number=number)


class REULBridge:
    def __init__(self):
        self.celex_to_reul: Dict[str, str] = {}

    def map_celex_to_uk_eid(self, celex: str, eu_path: str) -> str:
        """Map CELEX and EU path into a UK REUL-compatible stable identifier.

        Format:
            eur_<year>_<number>_<kind>_<number>_...
        """
        parsed = _parse_celex(celex)
        if parsed is None:
            return f"eur_unknown_unknown_{celex}"
        prefix = f"eur_{parsed.year}_{parsed.number}"

        # Normalize "art/1/para/2" or "article/1/paragraph/2".
        path_parts = [
            part.strip().lower()
            for part in re.split(r"[./_]", eu_path.strip().strip("/"))
            if part.strip()
        ]
        if not path_parts:
            return prefix
        path_suffix_parts = []
        for part in path_parts:
            normalized = _EU_PATH_KIND_ALIAS.get(part.lower())
            path_suffix_parts.append(normalized or part)
        path_suffix = "_".join(path_suffix_parts)
        return f"{prefix}_{path_suffix}"

    def _record_uri_resolution_diagnostic(
        self,
        diagnostics_out: list[dict[str, Any]] | None,
        *,
        uri: str,
        eu_statute: IRStatute,
        reason_code: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if diagnostics_out is None:
            return
        payload = diagnostic_detail(
            rule_id="eu_reul_uri_resolution_failed",
            family="target_resolution_recovery",
            phase="lowering",
            reason="EU REUL bridge could not resolve retained-law URI against the EU statute tree",
            blocking=True,
            kind="eu_reul_uri_resolution_failed",
            uri=uri,
            statute_id=eu_statute.statute_id,
        )
        payload["detail"] = {"reason_code": reason_code, **(detail or {})}
        diagnostics_out.append(payload)

    def resolve_retained_law_uri(
        self,
        uri: str,
        eu_statute: IRStatute,
        diagnostics_out: list[dict[str, Any]] | None = None,
    ) -> Optional[IRNode]:
        """
        Resolve a retained-law:// URI against an EU IRStatute.
        e.g. retained-law://celex/32016R0679/article/1
        """
        uri = uri.strip()
        scheme, sep, rest = uri.partition("://")
        if not sep or scheme.lower() != "retained-law":
            self._record_uri_resolution_diagnostic(
                diagnostics_out,
                uri=uri,
                eu_statute=eu_statute,
                reason_code="invalid_scheme",
                detail={"scheme": scheme},
            )
            return None

        parts = [part for part in rest.split("/") if part]
        # parts: ['celex', '32016R0679', 'article', '1']
        if len(parts) < 4:
            self._record_uri_resolution_diagnostic(
                diagnostics_out,
                uri=uri,
                eu_statute=eu_statute,
                reason_code="too_few_parts",
                detail={"parts": parts},
            )
            return None

        if parts[0].lower() != "celex" or len(parts) < 3:
            self._record_uri_resolution_diagnostic(
                diagnostics_out,
                uri=uri,
                eu_statute=eu_statute,
                reason_code="invalid_authority",
                detail={"authority": parts[0] if parts else ""},
            )
            return None

        celex = _strip_uri_suffix(parts[1], "?", "#")
        if celex.lower() != eu_statute.statute_id.lower():
            self._record_uri_resolution_diagnostic(
                diagnostics_out,
                uri=uri,
                eu_statute=eu_statute,
                reason_code="celex_mismatch",
                detail={"celex": celex},
            )
            return None

        if (len(parts) - 2) % 2 != 0:
            self._record_uri_resolution_diagnostic(
                diagnostics_out,
                uri=uri,
                eu_statute=eu_statute,
                reason_code="malformed_path_arity",
                detail={"path_parts": parts[2:]},
            )
            return None

        # Build a LegalAddress path from URI segments and resolve it against parsed IR.
        # Accepted examples:
        #   retained-law://celex/32016R0679/article/1
        #   retained-law://celex/32016R0679/article/1/point/2
        path = []
        for i in range(2, len(parts), 2):
            kind = _EU_IR_KIND_ALIAS.get(parts[i].strip().lower(), parts[i].strip().lower())
            if i + 1 >= len(parts):
                self._record_uri_resolution_diagnostic(
                    diagnostics_out,
                    uri=uri,
                    eu_statute=eu_statute,
                    reason_code="malformed_path_arity",
                    detail={"path_parts": parts[2:]},
                )
                return None
            label = _strip_uri_suffix(parts[i + 1], "?", "#")
            if not kind or not label:
                self._record_uri_resolution_diagnostic(
                    diagnostics_out,
                    uri=uri,
                    eu_statute=eu_statute,
                    reason_code="empty_kind_or_label",
                    detail={"kind": kind, "label": label, "path_parts": parts[2:]},
                )
                return None
            path.append((kind, label))

        resolved = tree_ops.resolve(eu_statute.body, path)
        if resolved is None:
            self._record_uri_resolution_diagnostic(
                diagnostics_out,
                uri=uri,
                eu_statute=eu_statute,
                reason_code="target_unresolved",
                detail={"path": path},
            )
        return resolved


def _strip_uri_suffix(value: str, prefix_sep: str, suffix_sep: str) -> str:
    for sep in (prefix_sep, suffix_sep):
        value = value.split(sep, 1)[0]
    return value.strip()
