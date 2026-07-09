"""Frozen Level-1 node metadata codec — the shared metadata contract (Decision 9).

FROZEN at the end of Track A (§5.5 of ``notes/SOURCE_DOCUMENT_TWO_LEVEL_PIPELINE.md``).

The richer Level 1 annotates each simulacrum node DETERMINISTICALLY (geometry,
continuation cues, recurrence, content hints, provenance) so Level 2 can
adjudicate DROP / DEDUP / REJOIN in context. Metadata rides IN-BAND on
``SourceDocumentNode.attrs`` (a closed ``Mapping[str, str]``) under a CLOSED,
namespaced key vocabulary — no sidecar (``SourceDocumentNode`` has no identity
field), per-node bbox lives in the anchor.

``NodeMetadata`` is the typed view; ``encode_metadata`` / ``decode_metadata``
round-trip it through ``attrs``. An affordance, never authority: a furniture
*hint* (``hint.furniture``, NOT ``role=`` — that key is taken by images) is
confirmed by the model across pages, not obeyed.

Pass-1 (Decision 7): geometry + continuation cues + recurrence + content hints +
provenance + text-derivable caps. DEFERRED to ``meta.v2`` (keys RESERVED now so
Level 2 treats them optional): ``typo.font`` / ``typo.size_class`` / ``typo.bold``
/ ``typo.italic``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

META_VERSION = 1

# ---------------------------------------------------------------------------
# CLOSED key vocabulary (Decision 9). Any attrs key under a metadata namespace
# that is NOT in this set is a fail-loud codec error on decode — the vocab is
# frozen, not open. Non-metadata attrs (e.g. image_locator, assurance_tier,
# rowspan) are left untouched by the codec: only the namespaced keys below are
# owned by ``NodeMetadata``.
# ---------------------------------------------------------------------------
_META_KEY = "meta.v"

# Namespaces the codec OWNS. A key that starts with one of these prefixes must be
# a recognized member of ``_KNOWN_META_KEYS`` (or a reserved v2 key), else decode
# rejects it — closed vocabulary enforcement.
_OWNED_NAMESPACES = ("meta.", "geom.", "typo.", "cue.", "rec.", "hint.", "freeform.", "prov.")

# The v1 closed vocabulary (Decision 9 / §5.5).
_KNOWN_META_KEYS = frozenset(
    {
        _META_KEY,  # meta.v=1
        # geometry
        "geom.band",  # top|body|bottom
        "geom.col",  # column index
        "geom.indent",  # indent depth
        "geom.y_order",  # y-order within page
        # typography (text-derivable in v1)
        "typo.caps",  # 1
        # continuation cues
        "cue.ends_terminal",  # 1
        "cue.starts_lower",  # 1
        "cue.hyphen_tail",  # 1
        "cue.list_marker",  # <marker>
        "cue.section_number",  # <label>
        # recurrence
        "rec.band_count",  # <int>
        # content hints
        "hint.numeric",  # 1
        "hint.section_ref",  # 1
        "hint.furniture",  # 1  (NOT role= — that key is taken by images)
        # freeform escape hatch
        "freeform.reason",  # <closed vocab>
        # provenance
        "prov.producer",  # producer id
        "prov.converged",  # 1
    }
)

# RESERVED for meta.v2 — recognized (never rejected) but carried opaquely; Level 2
# treats them optional. Not emitted by the v1 encoder.
_RESERVED_V2_KEYS = frozenset(
    {
        "typo.font",
        "typo.size_class",
        "typo.bold",
        "typo.italic",
    }
)

_GEOM_BANDS = frozenset({"top", "body", "bottom"})
_FREEFORM_REASONS = frozenset(
    {
        "marginalia",
        "complex_layout",
        "image_baked",
        "garbled_source",
        "ambiguous",
        "rotated",
        "handwritten",
    }
)


class MetadataVocabError(ValueError):
    """A namespaced ``attrs`` key is outside the frozen v1/v2 metadata vocabulary."""


@dataclass(frozen=True, slots=True)
class NodeMetadata:
    """Typed view of the deterministic Level-1 node metadata (Decision 9).

    All fields are optional — a clean body line may carry almost none. The codec
    round-trips this ↔ the closed namespaced ``attrs`` keys; non-metadata attrs
    are preserved untouched.
    """

    # geometry
    band: Optional[str] = None  # top|body|bottom
    col: Optional[int] = None
    indent: Optional[int] = None
    y_order: Optional[int] = None
    # typography (v1: text-derivable caps only)
    caps: bool = False
    # continuation cues
    ends_terminal: bool = False
    starts_lower: bool = False
    hyphen_tail: bool = False
    list_marker: Optional[str] = None
    section_number: Optional[str] = None
    # recurrence
    band_count: Optional[int] = None
    # content hints
    numeric: bool = False
    section_ref: bool = False
    furniture: bool = False
    # freeform escape hatch
    freeform_reason: Optional[str] = None
    # provenance
    producer: Optional[str] = None
    converged: bool = False

    def __post_init__(self) -> None:
        if self.band is not None and self.band not in _GEOM_BANDS:
            raise MetadataVocabError(
                f"geom.band must be one of {sorted(_GEOM_BANDS)}; got {self.band!r}"
            )
        if self.freeform_reason is not None and self.freeform_reason not in _FREEFORM_REASONS:
            raise MetadataVocabError(
                f"freeform.reason must be one of {sorted(_FREEFORM_REASONS)}; "
                f"got {self.freeform_reason!r}"
            )


def encode_metadata(meta: NodeMetadata) -> dict[str, str]:
    """Encode ``NodeMetadata`` into closed-vocabulary ``attrs`` (str→str).

    Emits ``meta.v=1`` plus only the SET fields — clean lines stay attrs-sparse.
    Boolean flags are emitted as ``"1"`` only when true (absence == false).
    """
    out: dict[str, str] = {_META_KEY: str(META_VERSION)}
    if meta.band is not None:
        out["geom.band"] = meta.band
    if meta.col is not None:
        out["geom.col"] = str(meta.col)
    if meta.indent is not None:
        out["geom.indent"] = str(meta.indent)
    if meta.y_order is not None:
        out["geom.y_order"] = str(meta.y_order)
    if meta.caps:
        out["typo.caps"] = "1"
    if meta.ends_terminal:
        out["cue.ends_terminal"] = "1"
    if meta.starts_lower:
        out["cue.starts_lower"] = "1"
    if meta.hyphen_tail:
        out["cue.hyphen_tail"] = "1"
    if meta.list_marker is not None:
        out["cue.list_marker"] = meta.list_marker
    if meta.section_number is not None:
        out["cue.section_number"] = meta.section_number
    if meta.band_count is not None:
        out["rec.band_count"] = str(meta.band_count)
    if meta.numeric:
        out["hint.numeric"] = "1"
    if meta.section_ref:
        out["hint.section_ref"] = "1"
    if meta.furniture:
        out["hint.furniture"] = "1"
    if meta.freeform_reason is not None:
        out["freeform.reason"] = meta.freeform_reason
    if meta.producer is not None:
        out["prov.producer"] = meta.producer
    if meta.converged:
        out["prov.converged"] = "1"
    return out


def decode_metadata(attrs: Mapping[str, str]) -> NodeMetadata:
    """Decode the closed-vocabulary metadata keys from ``attrs``.

    Non-metadata keys (outside the owned namespaces) are IGNORED. A key that IS in
    an owned namespace but is neither a known v1 key nor a reserved v2 key is a
    fail-loud ``MetadataVocabError`` (the vocabulary is closed). Reserved v2 keys
    are tolerated (forward-compatible) but do not populate a v1 field.
    """
    for key in attrs:
        if not key.startswith(_OWNED_NAMESPACES):
            continue
        if key in _KNOWN_META_KEYS or key in _RESERVED_V2_KEYS:
            continue
        raise MetadataVocabError(
            f"attrs key {key!r} is under an owned metadata namespace but is not "
            f"in the closed v1 vocabulary (nor a reserved v2 key)"
        )

    def _int(key: str) -> Optional[int]:
        v = attrs.get(key)
        return int(v) if v is not None else None

    return NodeMetadata(
        band=attrs.get("geom.band"),
        col=_int("geom.col"),
        indent=_int("geom.indent"),
        y_order=_int("geom.y_order"),
        caps=attrs.get("typo.caps") == "1",
        ends_terminal=attrs.get("cue.ends_terminal") == "1",
        starts_lower=attrs.get("cue.starts_lower") == "1",
        hyphen_tail=attrs.get("cue.hyphen_tail") == "1",
        list_marker=attrs.get("cue.list_marker"),
        section_number=attrs.get("cue.section_number"),
        band_count=_int("rec.band_count"),
        numeric=attrs.get("hint.numeric") == "1",
        section_ref=attrs.get("hint.section_ref") == "1",
        furniture=attrs.get("hint.furniture") == "1",
        freeform_reason=attrs.get("freeform.reason"),
        producer=attrs.get("prov.producer"),
        converged=attrs.get("prov.converged") == "1",
    )
