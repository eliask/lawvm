"""Shared --mode vocabulary for Finland replay tools.

The canonical comparison-target mode value is ``official_consolidation``:
replay output is compared against the official consolidated text published
by the jurisdiction's statute service.
"""

from __future__ import annotations


def replay_mode_argument(value: str) -> str:
    """argparse ``type=`` hook for the replay --mode argument.

    Values pass through unchanged; argparse ``choices`` validation rejects
    anything outside the accepted vocabulary.
    """
    return value
