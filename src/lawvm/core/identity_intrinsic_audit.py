"""Identity-intrinsic + synthetic-label leak sweeps over stored legal surfaces.

This module implements the offline audit half of registry rows **LS-12**
(``APPLY.POSITIONAL_ID_LEAK``) and **LS-13** (``APPLY.SYNTHETIC_LABEL_LEAK``),
the structural enforcement of AGENTS.md §2.8 / §2.9 test-6:

  §2.8  Provision/node identity is **intrinsic and versioned, never positional**:
        a tuple index, row ordinal, HTML ordinal, ``lxml`` object identity, or
        ``expr#N`` counter is not an identity and must not survive into a stored
        address, edge, or projection key.

  §2.9  A no-leak test: synthetic markers never reach user output, persisted
        artifacts, :class:`LegalAddress`, or :class:`ProvisionTimeline`. The
        single sanctioned home for a synthesized rule identifier is
        ``attrs.source_rule_id``.

Both sweeps walk a *materialized dossier's stored surfaces* — :class:`IRStatute`
/ :class:`IRNode` trees (labels, attrs), :class:`LegalAddress` path tuples,
:class:`ProvisionTimeline` keys and version content, edge payloads, and
projection-row keys — extract the string values that sit in identity-bearing
positions, and match them against a positional-id / synthetic-marker vocabulary.

The sweeps are deterministic and read-only: they never mutate the dossier and
never touch ``observation_registry`` (they are implemented as test-gate-facing
APIs so they stay conflict-free with the central registries). The walker is
generic over arbitrary nested Python structures so a frontend can hand it a
``LegalAddress``, an ``IRNode`` tree, a list of edges, a projection row, or any
mix — every leaf string in an identity-bearing slot is checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


# ── Allowed exception ──────────────────────────────────────────────────────────
#
# The ONLY attribute key under which a synthesized rule identifier may live. A
# synthetic marker appearing under ``attrs.source_rule_id`` is sanctioned (§2.9);
# anywhere else it is a leak. Positional ids are never sanctioned, not even here.
SOURCE_RULE_ID_KEY = "source_rule_id"


# ── Positional-id vocabulary (LS-12 / §2.8) ─────────────────────────────────────
#
# A value is a positional-id leak when it matches any of these. The patterns
# encode the §2.8 enumeration: ``expr#N`` counters, ``tuple_index=`` / row /
# ordinal keys, ``lxml`` object pointers, and raw HTML/list ordinals used as an
# identity discriminator.
_POSITIONAL_ID_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # expr#42 / expr#0 — a bare emit-order counter masquerading as identity.
    ("expr_counter", re.compile(r"\bexpr#\d+")),
    # tuple_index=3 / tuple_index:3 — Python tuple position as identity.
    ("tuple_index", re.compile(r"\btuple_index\s*[=:]\s*\d+")),
    # row_ordinal=7 / row_index=7 / row#7 — a stored row position as identity.
    ("row_ordinal", re.compile(r"\brow_(?:ordinal|index)\s*[=:]\s*\d+|\brow#\d+")),
    # lxml_ptr=0x7f... / 0x-prefixed object pointer — lxml object identity.
    ("lxml_ptr", re.compile(r"\blxml_ptr\s*[=:]\s*0x[0-9a-fA-F]+|\b0x[0-9a-fA-F]{6,}\b")),
    # html_ordinal=4 / list_ordinal=4 / child_ordinal=4 — render/list position.
    ("html_ordinal", re.compile(r"\b(?:html|list|child|node)_ordinal\s*[=:]\s*\d+")),
)


# ── Synthetic-marker vocabulary (LS-13 / §2.9 test-6) ───────────────────────────
#
# Synthesized labels minted for internal stitching that must never reach a
# stored address / timeline / projection. Two families, matching the codebase's
# own synthetic-marker idioms:
#   * AKN-style synthesized ordinals: a ``__n{N}`` segment in an eId path, or a
#     bare ``n{N}`` synthesized peer suffix (an *unnumbered* peer the normalizer
#     stitched in, which carries no real Finnish label).
#   * The ``__test__`` synthetic-statute prefix (cf. the conformance corpus
#     ``no_leak_synthetic_marker`` fixtures: ``__test__/9999/synthetic``).
_SYNTHETIC_MARKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # __n3 inside an eId-style path, or a standalone synthesized "n{N}" label.
    ("synthetic_n_ordinal", re.compile(r"__n\d+\b|(?<![\w])n\d+__|^n\d+$")),
    # __test__/9999/synthetic — the synthetic statute-id marker family.
    ("synthetic_test_marker", re.compile(r"__test__|/synthetic(?:_\w+)?\b")),
)


# ── Findings ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IdentityLeakFinding:
    """One stored-surface value that violates the identity-intrinsic rule.

    ``finding_kind`` is the registry code (``APPLY.POSITIONAL_ID_LEAK`` or
    ``APPLY.SYNTHETIC_LABEL_LEAK``); ``vocab`` names which pattern fired;
    ``location`` is a human-readable path into the walked structure; ``value``
    is the offending string (self-evidencing — the offending text is the
    finding, not an opaque code).
    """

    finding_kind: str
    vocab: str
    location: str
    value: str


@dataclass(frozen=True, slots=True)
class IdentityAuditReport:
    """The result of a sweep over a dossier's stored surfaces."""

    findings: tuple[IdentityLeakFinding, ...] = field(default_factory=tuple)

    @property
    def clean(self) -> bool:
        return not self.findings

    def raise_if_dirty(self) -> None:
        """Fail loud when any leak is present (strict-mode entry point)."""
        if self.findings:
            preview = "; ".join(
                f"{f.finding_kind}[{f.vocab}] at {f.location}: {f.value!r}" for f in self.findings[:8]
            )
            raise IdentityLeakError(
                f"{len(self.findings)} identity-intrinsic leak(s) in stored surfaces: {preview}"
            )


class IdentityLeakError(AssertionError):
    """Raised by :meth:`IdentityAuditReport.raise_if_dirty` on any leak."""


# ── Generic stored-surface walker ───────────────────────────────────────────────
#
# We deliberately walk by structure rather than by type so the same sweep covers
# LegalAddress paths, IRNode trees, ProvisionTimeline version lists, edge
# payloads, and projection rows without per-type plumbing. The walker visits
# every string leaf, tagged with a location path; the ``attrs.source_rule_id``
# exemption is applied by inspecting the location's trailing mapping key.

_PRIMITIVE = (str, bytes, int, float, bool, type(None))


def _is_source_rule_id_slot(location: str) -> bool:
    """True when ``location`` ends at the sanctioned ``attrs.source_rule_id`` slot.

    A value reached via an ``attrs`` (or any mapping) key named ``source_rule_id``
    is the one sanctioned home for a synthesized rule id (§2.9). Positional-id
    matching ignores this exemption (a positional id is never an identity, §2.8).
    """
    return location.endswith(f".{SOURCE_RULE_ID_KEY}") or location.endswith(f"[{SOURCE_RULE_ID_KEY!r}]")


def _iter_string_leaves(value: Any, location: str) -> Iterable[tuple[str, str]]:
    """Yield ``(location, string_value)`` for every string leaf reachable from ``value``.

    Dataclasses, mappings, and sequences are walked recursively; mapping keys
    are themselves treated as identity-bearing surfaces (a synthetic key leaks
    just as a synthetic value does). ``bytes`` are decoded best-effort so a
    persisted byte field is still scanned.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        yield location, value
        return
    if isinstance(value, bytes):
        yield location, value.decode("utf-8", "replace")
        return
    if isinstance(value, Mapping):
        for key, sub in value.items():
            key_str = str(key)
            # The key itself is a stored surface (a projection-row key, an
            # edge-payload key) — scan it, but never under a sanctioned slot.
            yield f"{location}.<key>", key_str
            yield from _iter_string_leaves(sub, f"{location}.{key_str}")
        return
    # Dataclass instance: walk its fields.
    fields = getattr(value, "__dataclass_fields__", None)
    if fields is not None:
        for fname in fields:
            yield from _iter_string_leaves(getattr(value, fname), f"{location}.{fname}")
        return
    if isinstance(value, Sequence) and not isinstance(value, _PRIMITIVE):
        for idx, sub in enumerate(value):
            yield from _iter_string_leaves(sub, f"{location}[{idx}]")
        return
    if isinstance(value, (set, frozenset)):
        for sub in sorted(value, key=repr):
            yield from _iter_string_leaves(sub, f"{location}{{}}")
        return
    # Unknown object: stringify defensively so nothing is silently skipped.
    yield f"{location}.<repr>", str(value)


# ── Sweeps ──────────────────────────────────────────────────────────────────────


def sweep_positional_id_leaks(
    surfaces: Any,
    *,
    root_name: str = "dossier",
) -> IdentityAuditReport:
    """LS-12: flag any stored-surface value carrying a positional id (§2.8).

    ``surfaces`` may be any structure containing stored surfaces — a
    :class:`LegalAddress`, an :class:`IRStatute`/:class:`IRNode` tree, a list of
    edges, a projection row, or a tuple/dict bundling several. Every string leaf
    (and mapping key) is matched against the positional-id vocabulary. The
    ``source_rule_id`` exemption does NOT apply: a positional id is never a
    legal identity, even there.
    """
    findings: list[IdentityLeakFinding] = []
    for location, text in _iter_string_leaves(surfaces, root_name):
        for vocab, pattern in _POSITIONAL_ID_PATTERNS:
            if pattern.search(text):
                findings.append(
                    IdentityLeakFinding(
                        finding_kind="APPLY.POSITIONAL_ID_LEAK",
                        vocab=vocab,
                        location=location,
                        value=text,
                    )
                )
    return IdentityAuditReport(findings=tuple(findings))


def sweep_synthetic_label_leaks(
    surfaces: Any,
    *,
    root_name: str = "dossier",
) -> IdentityAuditReport:
    """LS-13: flag any synthetic marker reaching a stored surface (§2.9 test-6).

    Every string leaf is matched against the synthetic-marker vocabulary EXCEPT
    values reached via an ``attrs.source_rule_id`` slot, the one sanctioned home
    for a synthesized rule identifier. A marker in a :class:`LegalAddress` path,
    a :class:`ProvisionTimeline` key, an :class:`IRNode` label, an edge payload,
    or a projection-row key is a leak.
    """
    findings: list[IdentityLeakFinding] = []
    for location, text in _iter_string_leaves(surfaces, root_name):
        if _is_source_rule_id_slot(location):
            continue
        for vocab, pattern in _SYNTHETIC_MARKER_PATTERNS:
            if pattern.search(text):
                findings.append(
                    IdentityLeakFinding(
                        finding_kind="APPLY.SYNTHETIC_LABEL_LEAK",
                        vocab=vocab,
                        location=location,
                        value=text,
                    )
                )
    return IdentityAuditReport(findings=tuple(findings))


def sweep_identity_intrinsic(
    surfaces: Any,
    *,
    root_name: str = "dossier",
) -> IdentityAuditReport:
    """Run both sweeps and return the combined report (positional + synthetic)."""
    positional = sweep_positional_id_leaks(surfaces, root_name=root_name)
    synthetic = sweep_synthetic_label_leaks(surfaces, root_name=root_name)
    return IdentityAuditReport(findings=positional.findings + synthetic.findings)
