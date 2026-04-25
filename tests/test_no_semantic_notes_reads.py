"""CI guard: no new semantic reads of parser-local note carriers outside known compat sites.

Parser-local notes on AST/surface nodes are a transitional carrier.
Shared provenance now lives in typed fields such as `provenance_tags`,
`is_exception`, and `move_clause_target_unit_kind`.
Reading note-like carriers to gate legal behavior in new code is forbidden.

Known compat sites are listed in the allowlists below. If a new site appears
outside those files, this test fails and forces an explicit decision:
  - If it is another compat/transition read, add it to the allowlist with a comment.
  - If a typed field already exists, use the typed field and do not extend the
    allowlist.
  - If no typed field exists yet, document the gap in IMPLEMENTATION_DIVERGENCE_LEDGER.md
    and add the file to the allowlist only as a temporary measure.
"""
from __future__ import annotations

import pathlib
import re

# ---------------------------------------------------------------------------
# Root of the source tree under examination
# ---------------------------------------------------------------------------

_SRC_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "lawvm"


def _source_files() -> list[pathlib.Path]:
    return list(_SRC_ROOT.rglob("*.py"))


# ---------------------------------------------------------------------------
# Pattern 1: "exception" membership check on parser-local note carriers
#
# The typed field is SurfaceTargetRef.is_exception (and ResolvedTargetRef,
# ClauseTargetRef). New code must use is_exception, not read the parser-local
# note tuple.
#
# Compat allowlist: lift_to_surface.py sets is_exception from parser-local
# notes — that is the bridge and is the only permitted crossing point.
# ---------------------------------------------------------------------------

_EXCEPTION_IN_NOTES_PATTERN = re.compile(
    r'"exception"\s+in\b'
)

_EXCEPTION_COMPAT_FILES = frozenset(
    {
        # Bridge: reads parser-local notes once to populate the typed is_exception field.
        "finland/johtolause/lift_to_surface.py",
        # surface_model.py contains a docstring explaining the compat note — not a read.
        "finland/johtolause/surface_model.py",
    }
)


# ---------------------------------------------------------------------------
# Pattern 2: move-tail chapter/part membership checks on parser-local note carriers
#
# Typed field: AmendmentOp.move_clause_target_unit_kind.
# New semantic gates must read the typed field, not the note carrier.
#
# No compat reads remain for move-tail chapter/part note carriers. The typed carrier is
# authoritative and the remaining write-side move-tail carriers are note-free.
# ---------------------------------------------------------------------------

_MOVE_CHAPTER_IN_PATTERN = re.compile(
    r'"move_clause_target_chapter"\s+(?:not\s+)?in\b'
)
_MOVE_PART_IN_PATTERN = re.compile(
    r'"move_clause_target_part"\s+(?:not\s+)?in\b'
)

_MOVE_COMPAT_FILES = frozenset()


# ---------------------------------------------------------------------------
# Pattern 3: explicit_scope_notes — reads "renumber_backref_clause" from
# parser-local provenance tags to gate scope-stripping behaviour.
#
# Compat allowlist: scope.py is the sole permitted site.
# ---------------------------------------------------------------------------

_EXPLICIT_SCOPE_NOTES_PATTERN = re.compile(
    r'"renumber_backref_clause"\s+in\b'
    r'|explicit_scope_notes\.intersection'
)

_SCOPE_COMPAT_FILES = frozenset(
    {
        # Uses explicit_scope_notes set to gate chapter-scope stripping.
        "finland/scope.py",
    }
)


# ---------------------------------------------------------------------------
# Pattern 4: Estonian provenance_tags semantic reads on the shared carrier
#
# Estonia's grafter extracts sentence indexes and Estonian text patterns from
# provenance tags to gate insert/replace behaviour.
#
# Compat allowlist: estonia/grafter.py only.
# ---------------------------------------------------------------------------

_EE_NOTE_TEXT_PATTERN = re.compile(
    r'note_text\s*=\s*"?\s*"?\s*\.join.*op\.provenance_tags'
    r'|"algust täiendatakse"\s+in\s+note_text'
    r'|"loetakse teiseks lauseks"\s+in\s+note_text'
    r'|_sentence_index(?:es)?_from_notes\s*\('
    r'|_subsection_labels_implied_by_plain_range_repeal'
)

_EE_COMPAT_FILES = frozenset(
    {
        # All semantic parser-local note reads for Estonia are concentrated here.
        "estonia/grafter.py",
    }
)


# ---------------------------------------------------------------------------
# Helper: relative path from src root (for allowlist matching)
# ---------------------------------------------------------------------------

def _rel(p: pathlib.Path) -> str:
    """Return path relative to _SRC_ROOT, using forward slashes."""
    return p.relative_to(_SRC_ROOT).as_posix()


# ---------------------------------------------------------------------------
# Guard function
# ---------------------------------------------------------------------------

def _violations(
    pattern: re.Pattern[str],
    compat_files: frozenset[str],
) -> list[tuple[str, int, str]]:
    """Return (relpath, lineno, line) for each pattern hit outside compat_files."""
    hits: list[tuple[str, int, str]] = []
    for py_file in _source_files():
        rel = _rel(py_file)
        if rel in compat_files:
            continue
        text = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            # Skip comment-only lines — guard targets executable code
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                hits.append((rel, lineno, line.rstrip()))
    return hits


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_new_exception_note_reads() -> None:
    """\"exception\" membership check on parser-local notes must not appear outside compat sites.

    New code must use the typed is_exception field instead.
    """
    hits = _violations(_EXCEPTION_IN_NOTES_PATTERN, _EXCEPTION_COMPAT_FILES)
    if hits:
        lines = "\n".join(f"  {rel}:{ln}: {code}" for rel, ln, code in hits)
        raise AssertionError(
            "Semantic read of 'exception' from parser-local notes found outside compat sites.\n"
            "Use the typed `is_exception` field instead.\n"
            "Known compat files: " + ", ".join(sorted(_EXCEPTION_COMPAT_FILES)) + "\n"
            "Violations:\n" + lines
        )


def test_no_new_move_clause_target_note_reads() -> None:
    """move_clause_target_chapter/part membership checks must not appear outside compat sites.

    New code must use the typed AmendmentOp.move_clause_target_unit_kind field.
    """
    chapter_hits = _violations(_MOVE_CHAPTER_IN_PATTERN, _MOVE_COMPAT_FILES)
    part_hits = _violations(_MOVE_PART_IN_PATTERN, _MOVE_COMPAT_FILES)
    all_hits = chapter_hits + part_hits
    if all_hits:
        lines = "\n".join(f"  {rel}:{ln}: {code}" for rel, ln, code in all_hits)
        raise AssertionError(
            "Semantic read of move_clause_target_chapter/part from parser-local notes found "
            "outside compat sites.\n"
            "Use the typed `move_clause_target_unit_kind` field instead.\n"
            "Known compat files: " + ", ".join(sorted(_MOVE_COMPAT_FILES)) + "\n"
            "Violations:\n" + lines
        )


def test_no_new_scope_notes_reads() -> None:
    """renumber_backref_clause membership checks must stay in scope.py.

    These reads are now on parser-local provenance tags, not the shared core
    carrier. If you need this pattern in a new file, add it to
    IMPLEMENTATION_DIVERGENCE_LEDGER.md first, then extend the compat
    allowlist with a justification comment.
    """
    hits = _violations(_EXPLICIT_SCOPE_NOTES_PATTERN, _SCOPE_COMPAT_FILES)
    if hits:
        lines = "\n".join(f"  {rel}:{ln}: {code}" for rel, ln, code in hits)
        raise AssertionError(
            "Semantic read of scope-provenance tags found outside compat sites.\n"
            "Known compat files: " + ", ".join(sorted(_SCOPE_COMPAT_FILES)) + "\n"
            "Violations:\n" + lines
        )


def test_no_new_estonia_note_text_reads() -> None:
    """Estonian note_text semantic reads must stay in estonia/grafter.py.

    If you add a new file that reads note_text for semantic gating, first
    document the typed-field gap in IMPLEMENTATION_DIVERGENCE_LEDGER.md and
    extend the compat allowlist with a justification comment.
    """
    hits = _violations(_EE_NOTE_TEXT_PATTERN, _EE_COMPAT_FILES)
    if hits:
        lines = "\n".join(f"  {rel}:{ln}: {code}" for rel, ln, code in hits)
        raise AssertionError(
            "Estonian note_text semantic read found outside compat sites.\n"
            "Known compat files: " + ", ".join(sorted(_EE_COMPAT_FILES)) + "\n"
            "Violations:\n" + lines
        )
