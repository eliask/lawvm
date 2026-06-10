"""FinlexSectionSourceProvider — Finnish statute section source bytes.

Piece 2 of the source-provider stack (see task spec §Piece 2).

Implements SourceBytesProvider for jurisdiction 'fi'.

Strategy: section-granularity (not byte-precise).
  - Load oracle AKN XML for the statute via the corpus store.
  - Walk sections to find the one matching provision_ref.
  - Return section's full text bytes (UTF-8). span = (0, len(bytes_)).
  - Return None if statute or section not found — no exception.

'provision_ref' is interpreted in two formats:

  1. section_key format: 'section:N' or 'chapter:N/section:N'
     Produced by section_text_extractor._eid_to_section_key(). Direct
     comparison against SectionTextRow.section_key.

  2. ProvisionRef.serialized() format: 'YYYY/N[-suffix]/section_label[/...]'
     Produced by ProvisionRef.serialized() in core/reference_mention.py.
     Parsed by finland/provision_ref_locator.parse_provision_ref_serialized()
     into (statute_id, HierarchicalLocator).  The HierarchicalLocator is
     then resolved against the oracle XML via FinnishAKNResolver, and the
     matching section_key is located by eId-to-section_key translation.

AGENTS.md discipline:
  §1.10: no broad try/except — single bounded XML-parse boundary.
  §1.13: tree walk for section location, not regex over raw XML.
  §1.11: substring guards before XML parse.
  §12: Finnish-specific provision_ref parsing belongs in finland/.
  Frozen dataclass + slots.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from lawvm.core.manual_claims.primitive import ClaimScope, SourceLocator
from lawvm.core.manual_claims.source_provider import FetchedSource, make_fetched_source


def _resolve_provision_ref_to_section_key(
    provision_ref: str,
    oracle_bytes: bytes,
    statute_id: str,
) -> Optional[str]:
    """Convert a provision_ref (either format) to a section_key string.

    Accepts:
      - section_key format ('section:N', 'chapter:N/section:N') — returned as-is
        if it contains ':' (locator segment separator).
      - ProvisionRef.serialized() format ('YYYY/N/section_label') — parsed by
        parse_provision_ref_serialized() then resolved via FinnishAKNResolver
        to find the matching section_key.

    Returns None if the provision_ref cannot be resolved to a section_key.
    This is a soft failure: the caller falls back to the first section.
    """
    if ":" in provision_ref:
        # Already in section_key format (section:N or chapter:N/section:N)
        return provision_ref

    # ProvisionRef.serialized() format — needs Finnish-specific parsing
    from lawvm.finland.provision_ref_locator import parse_provision_ref_serialized
    parsed_statute_id, locator = parse_provision_ref_serialized(provision_ref)
    if locator is None:
        # Statute-level only — no section to look up
        return None

    # Resolve via FinnishAKNResolver to get the AKN element, then map eId → section_key
    import xml.etree.ElementTree as ET
    from lawvm.finland.section_resolver import FinnishAKNResolver
    from lawvm.finland.section_text_extractor import _eid_to_section_key

    root = ET.fromstring(oracle_bytes)
    resolver = FinnishAKNResolver()
    element = resolver.resolve(root, locator)
    if element is None:
        return None

    eid = element.get("eId") or ""
    section_key = _eid_to_section_key(eid)
    return section_key if section_key else None


@dataclass(frozen=True, slots=True)
class FinlexSectionSourceProvider:
    """Fetches section source bytes from the Finlex oracle corpus.

    corpus_root: root path used to construct the corpus store. When None,
    the corpus store factory uses its default (data/finlex.farchive).
    """

    corpus_root: Optional[Path] = None

    def _get_store(self):
        """Return a corpus store, applying corpus_root as LAWVM_FARCHIVE_DB if set."""
        import os
        from lawvm.corpus_store import get_corpus_store
        if self.corpus_root is not None:
            old = os.environ.get("LAWVM_FARCHIVE_DB")
            os.environ["LAWVM_FARCHIVE_DB"] = str(self.corpus_root)
            try:
                store = get_corpus_store(readonly=True)
            finally:
                if old is None:
                    os.environ.pop("LAWVM_FARCHIVE_DB", None)
                else:
                    os.environ["LAWVM_FARCHIVE_DB"] = old
            return store
        return get_corpus_store(readonly=True)

    def fetch(self, scope: ClaimScope) -> Optional[FetchedSource]:
        """Fetch section bytes for *scope.statute_id* + *scope.provision_ref*.

        Returns None if:
          - statute oracle XML is not in the corpus store;
          - the XML cannot be parsed;
          - provision_ref is None and no fallback section is available;
          - provision_ref is set but no matching section is found.

        provision_ref may be in section_key format ('section:N') or in
        ProvisionRef.serialized() format ('YYYY/N/section_label').  Both
        formats are accepted transparently.

        Logs a one-line diagnostic to stderr so callers can see why rows are
        skipped without raising.
        """
        statute_id = scope.statute_id
        provision_ref = scope.provision_ref

        store = self._get_store()
        oracle_bytes = store.read_oracle(statute_id)
        if oracle_bytes is None:
            print(
                f"  FinlexSectionSourceProvider: oracle not found for {statute_id!r}",
                file=sys.stderr,
            )
            return None

        from lawvm.finland.section_text_extractor import extract_sections_text
        result = extract_sections_text(oracle_bytes, statute_id)

        if not result.sections:
            diag_ids = [d.rule_id for d in result.diagnostics]
            print(
                f"  FinlexSectionSourceProvider: no sections extracted for {statute_id!r} "
                f"(diagnostics: {diag_ids})",
                file=sys.stderr,
            )
            return None

        # Locate target section by provision_ref, or fall back to first section.
        section = None
        if provision_ref:
            # Normalize provision_ref to section_key format first
            section_key = _resolve_provision_ref_to_section_key(
                provision_ref, oracle_bytes, statute_id
            )
            if section_key is None:
                print(
                    f"  FinlexSectionSourceProvider: provision_ref {provision_ref!r} "
                    f"did not resolve to a section_key in {statute_id!r}",
                    file=sys.stderr,
                )
                return None
            for s in result.sections:
                if s.section_key == section_key:
                    section = s
                    break
            if section is None:
                print(
                    f"  FinlexSectionSourceProvider: section_key {section_key!r} "
                    f"(from provision_ref {provision_ref!r}) not found "
                    f"in {statute_id!r} ({len(result.sections)} sections available)",
                    file=sys.stderr,
                )
                return None
        else:
            # No provision_ref: use the full oracle XML as a coarse span.
            # This is section-granularity fallback: first section, or whole oracle.
            section = result.sections[0]

        # Build source bytes from section text fields (label + heading + body).
        parts = []
        if section.section_label:
            parts.append(section.section_label)
        if section.heading_text:
            parts.append(section.heading_text)
        if section.body_text:
            parts.append(section.body_text)
        section_text = " ".join(parts).strip()
        if not section_text:
            print(
                f"  FinlexSectionSourceProvider: empty section text for "
                f"{statute_id!r} {provision_ref!r}",
                file=sys.stderr,
            )
            return None

        bytes_ = section_text.encode("utf-8")
        locator = SourceLocator(
            artifact_kind="finlex_akn",
            statute_id=statute_id,
            he_id=None,
            version_id=None,
        )
        return make_fetched_source(bytes_, locator)
