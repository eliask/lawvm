from __future__ import annotations

import re


_REPEAL_CITATION_RE = (
    r'(?:[LAP](?:\:ll[äa])?\s+(?:\d{1,2}\.\d{1,2}\.\d{4}/\d+|\d+/\d{4})(?:\s+v\.\s+\d{4})?)'
)


_AIEMPI_SANAMUOTO_SUFFIX = r'(?:\s*Aiempi\s+sanamuoto\s+kuuluu\s*:?\s*)?'

_EDITORIAL_RE = re.compile(
    rf'\d+\s*[a-zäöå]?\s*§\s+on\s+kumottu\s+(?:\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s+)?{_REPEAL_CITATION_RE}'
    rf'(?:\s*,\s*joka\s+tul(?:ee|i)\s+voimaan\s+\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s*[,.\s]*|\s*[,.]+\s*){_AIEMPI_SANAMUOTO_SUFFIX}|'
    rf'\d+\s*[a-zäöå]?\s*(?:[–\-—]\s*\d+\s*[a-zäöå]?\s*)?(?:luku|mome?ntti|momentit|mom\.?|kohta|kohdat|§)\s+(?:on|ovat)\s+kumottu\s+(?:\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s+)?{_REPEAL_CITATION_RE}'
    rf'(?:\s*,\s+joka\s+tul(?:ee|i)\s+voimaan\s+\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s*[,.\s]*|\s*[,.]+\s*){_AIEMPI_SANAMUOTO_SUFFIX}|'
    r'[LAP]:ll[äa]\s+\d+/\d{4}\s+(?:muutettu|lisätty|kumottu|siirretty)\s+[^.]*?'
    r'(?:tul(?:ee|i)\s+voimaan\s+\d{1,2}\.\d{1,2}\.\d{4}\.?\s*|\.\s*)|'
    r'\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)',
    re.DOTALL,
)


_KUMOTTU_ATTRIBUTION_RE = re.compile(
    rf'on\s+kumottu\s+(?:\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s+)?{_REPEAL_CITATION_RE}'
    r'(?:\s*,\s*joka\s+tul(?:ee|i)\s+voimaan\s+\d{1,2}\.\d{1,2}\.\d{4})?\s*\.?',
    re.DOTALL,
)

# Matches full kumottu-stub sentences for removal from oracle text before comparison.
# Structural prefixes cover: sections (N §, N a §), chapters (N luku/LUKU), single
# momentti/kohta, and plural ranges (N–M momentit/kohdat).
#
# Attribution alternatives (after "on kumottu"):
#   1. Standard _REPEAL_CITATION_RE: L:lla/A:lla/P:llä + date/number — properly
#      handles dotted dates like "L:lla 1.4.2022/261" without fragmentation.
#      Optionally followed by "joka tuli voimaan DD.MM.YYYY" commencement clause.
#   2. Historical compound forms (rare, old law era): JakoL:lla, TyöaikaL:lla,
#      EtuoikeusA:lla, etc. — matched by \w+:ll[äa]; no date after, so [^.]*
#      is safe here.
#   3. Bare "lailla" or "-lailla" suffixed forms (Rakennuslailla etc.).
_KUMOTTU_STUBS_RE = re.compile(
    # Optional <num>-element residual prefix: when oracle XML has both <num>7 §</num>
    # and <content>7 § on kumottu...</content>, etree text-serialization produces
    # "7 §  7 § on kumottu..." — the optional prefix consumes the first "7 §".
    rf'(?:'
    rf'\d+\s*[a-zäöå]?\s*(?:[–\-—]\s*\d+\s*[a-zäöå]?\s*)?§'       # N § / N–M § (prefix)
    rf'|\d+\s*[a-zäöå]?\s*(?:[–\-—]\s*\d+\s*[a-zäöå]?\s*)?luku'    # N luku (prefix)
    rf'|\d+\s+(?:mome?ntti|momentin|kohta|kohdan)'                    # N momentti (prefix)
    rf'|\d+[–\-—]\d+\s+(?:momentit|kohdat|momenttia|kohtaa)'         # N–M momentit (prefix)
    rf')?\s*'
    rf'(?:'
    rf'\d+\s*[a-zäöå]?\s*(?:[–\-—]\s*\d+\s*[a-zäöå]?\s*)?§'       # N § / N–M §
    rf'|\d+\s*[a-zäöå]?\s*(?:[–\-—]\s*\d+\s*[a-zäöå]?\s*)?luku'    # N luku / N–M luku (case-insensitive below)
    rf'|\d+\s+(?:mome?ntti|momentin|kohta|kohdan)'                    # N momentti / N kohta
    rf'|\d+[–\-—]\d+\s+(?:momentit|kohdat|momenttia|kohtaa)'         # N–M momentit/kohdat
    rf')'
    rf'\s+(?:on|ovat)\s+kumottu'
    rf'(?:\s+\d{{1,2}}\.\d{{1,2}}\.\d{{4}})?'                        # optional DD.MM.YYYY prefix
    rf'\s+(?:{_REPEAL_CITATION_RE}'                                    # standard: L/A/P + statute ref
    rf'(?:\s*,\s*joka\s+tul(?:ee|i)\s+voimaan\s+\d{{1,2}}\.\d{{1,2}}\.\d{{4}})?'  # optional commencement
    rf'|\w+:ll[äa]'                                                    # historical: FooL:lla, BarA:llä
    rf'|[a-zäöåA-ZÄÖÅ\-]*lailla'                                       # lailla / Rakennuslailla
    rf')[^.]*\.?',
    re.DOTALL | re.IGNORECASE,
)

# Byte patterns for heuristic kumottu-fraction counting in raw oracle XML bytes.
_KUMOTTU_BYTE_PATTERNS = (b"kumottu L:lla", b"kumottu A:lla")

_TEMPORARY_RESIDUE_RE = re.compile(
    rf'(?:\d+\s*[a-zäöå]?\s*§|\d+\s+(?:mome?ntti|mom\.?|kohta))\s+(?:oli|on\s+ollut)\s+(?:väliaikaisesti\s+)?voimassa\s+'
    r'\d{1,2}\.\d{1,2}\.\d{4}\s*[–—\-]\s*\d{1,2}\.\d{1,2}\.\d{4}'
    rf'(?:\s+{_REPEAL_CITATION_RE})?\.{{0,3}}\s*',
    re.DOTALL | re.IGNORECASE,
)


def strip_aiempi_sanamuoto_blocks(text: str) -> str:
    marker = "Aiempi sanamuoto kuuluu:"
    while marker in text:
        start = text.find(marker)
        after = text[start + len(marker):]
        cut = len(after)
        cur_item = re.match(r'\s*(\d+[a-zäöå]?)\)', after, re.I)
        if cur_item:
            cur_label = cur_item.group(1).lower()
            for m in re.finditer(r'\s+(\d+[a-zäöå]?)\)', after, re.I):
                if m.group(1).lower() != cur_label:
                    cut = m.start()
                    break
        text = text[:start] + after[cut:]
    return text


def strip_temporary_residue_annotations(text: str) -> str:
    return _TEMPORARY_RESIDUE_RE.sub('', text)


def strip_editorial_annotations(text: str) -> str:
    return strip_aiempi_sanamuoto_blocks(
        strip_temporary_residue_annotations(_EDITORIAL_RE.sub('', text))
    )


def strip_kumottu_attribution(text: str) -> str:
    return _KUMOTTU_ATTRIBUTION_RE.sub('on kumottu.', text)


def normalize_kumottu_stubs(text: str) -> str:
    """Remove kumottu-stub sentences from oracle text before comparison.

    Strips sentences of the form "N § on kumottu L:lla/A:lla/P:llä YYYY/NNN."
    (and analogues for luku, momentti, kohta, and plural ranges) that appear in
    Finlex consolidated oracle text but not in LawVM replay output.

    This is the canonical Finland oracle-normalization function.  All scoring
    and comparison paths should use this instead of ad-hoc per-file regex subs.
    """
    return _KUMOTTU_STUBS_RE.sub('', text)


def count_kumottu_bytes(data: bytes) -> int:
    """Count kumottu-attribution occurrences in raw oracle XML bytes.

    Used by bench/classify pipelines as a heuristic fraction of repealed
    sections.  Counts both ``kumottu L:lla`` (Lailla) and ``kumottu A:lla``
    (Asetuksella) since both attribution forms appear in the corpus.
    """
    return sum(data.count(p) for p in _KUMOTTU_BYTE_PATTERNS)
