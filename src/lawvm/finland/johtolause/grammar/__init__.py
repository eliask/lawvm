"""grammar — the rewritten Finnish johtolause parser (in progress).

A clean two-layer replacement for the hand-written ``surface_parse.py``:

    tokens / scan annotations
      -> Layer 1: local syntactic recognizers (context-free, spans only)
      -> Layer 2: discourse transducer (explicit DiscourseState, named transitions)
      -> compatibility emitter (the 13 frozen Surface* nodes + 34 witness rules)

This package is built bottom-up and validated against the characterization golden
(`lawvm parse-characterize`). Until the swap, the authoritative parser remains
``surface_parse.parse``; this package runs only in shadow / under test.

See `notes/FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md` for the exact output contract.
"""
