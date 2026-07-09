"""Neutral vision/ingest machinery — the two-level PDF→IR pipeline home.

Jurisdiction-agnostic machinery that turns source-document bytes into the core's
neutral evidence contracts (``lawvm.core.source_document``). It is neither
replay kernel nor a jurisdiction frontend: a shared infrastructure waist (like
``core``) that every jurisdiction's ingest may use.

Level 1 (faithful per-page simulacra) and Level 2 (holistic de-facsimile) both
live here; the frozen interface carriers (``simulacrum``, ``defacsimile``,
``metadata``) are what Tracks B & C compile against. See
``notes/SOURCE_DOCUMENT_TWO_LEVEL_PIPELINE.md``.
"""
