"""Shared --mode vocabulary for Finland replay tools.

The canonical comparison-target mode value is ``official_consolidation``:
replay output is compared against the official consolidated text published
by the jurisdiction's statute service. The pre-migration spelling
``finlex_oracle`` carried both a jurisdictional surface name and "oracle"
(implies correctness; comparison surfaces use comparison-target language).
It remains accepted as a CLI alias only and normalizes to the canonical
value at the argument-parsing boundary, so the canonical value is the one
used internally and emitted in any report output.
"""

from __future__ import annotations

REPLAY_MODE_CANONICAL = "official_consolidation"

# Legacy CLI alias -> canonical mode value. Alias acceptance is CLI-surface
# only; nothing internal may produce or compare the alias spelling.
REPLAY_MODE_LEGACY_ALIASES = {"finlex_oracle": REPLAY_MODE_CANONICAL}

REPLAY_MODE_CHOICES = (REPLAY_MODE_CANONICAL, "legal_pit")


def replay_mode_argument(value: str) -> str:
    """argparse ``type=`` hook: map the legacy alias to the canonical mode.

    Unrecognized values pass through unchanged so argparse ``choices``
    validation rejects them exactly as before.
    """
    return REPLAY_MODE_LEGACY_ALIASES.get(value, value)
