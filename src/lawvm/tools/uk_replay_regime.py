"""Shared UK replay-regime normalization for diagnostic tools."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import sys
from typing import Any


UK_APPLICABILITY_MODE_CHOICES = (
    "effective_date_only",
    "effective_date_plus_feed_applied",
    "effective_date_plus_requires_applied",
)
UK_AUTHORITY_MODE_CHOICES = ("current_mixed", "source_text_only")


@dataclass(frozen=True)
class UKReplayRegime:
    allow_metadata_backfill: bool = True
    allow_oracle_alignment: bool = True
    applicability_mode: str = "effective_date_plus_feed_applied"
    authority_mode: str = "current_mixed"
    allow_metadata_only_effects: bool = True


def add_uk_replay_regime_arguments(
    parser: argparse.ArgumentParser,
    *,
    help_prefix: str = "[-j uk]",
    include_metadata_only_effects: bool = False,
) -> None:
    """Add shared UK replay-regime flags to one CLI parser.

    These flags change source authority and replay admission semantics.  Keep
    the parser surface centralized so every UK diagnostic entrypoint exposes
    the same regime vocabulary.
    """
    prefix = f"{help_prefix} " if help_prefix else ""
    parser.add_argument(
        "--metadata-backfill",
        dest="uk_allow_metadata_backfill",
        action="store_true",
        default=None,
        help=f"{prefix}allow metadata-only payload fallback during UK replay",
    )
    parser.add_argument(
        "--no-metadata-backfill",
        dest="uk_allow_metadata_backfill",
        action="store_false",
        help=f"{prefix}disable metadata-only payload fallback during UK replay",
    )
    parser.add_argument(
        "--oracle-alignment",
        dest="uk_allow_oracle_alignment",
        action="store_true",
        default=None,
        help=f"{prefix}allow oracle-assisted EID alignment during UK replay",
    )
    parser.add_argument(
        "--no-oracle-alignment",
        dest="uk_allow_oracle_alignment",
        action="store_false",
        help=f"{prefix}disable replay-time oracle-assisted EID alignment",
    )
    parser.add_argument(
        "--respect-feed-applied",
        dest="uk_respect_feed_applied",
        action="store_true",
        default=None,
        help=f"{prefix}require UK feed Applied status for replay applicability",
    )
    parser.add_argument(
        "--ignore-feed-applied",
        dest="uk_respect_feed_applied",
        action="store_false",
        help=f"{prefix}use effective dates without requiring UK feed Applied status",
    )
    parser.add_argument(
        "--applicability-mode",
        dest="uk_applicability_mode",
        choices=UK_APPLICABILITY_MODE_CHOICES,
        default=None,
        help=f"{prefix}explicit UK replay applicability mode",
    )
    if include_metadata_only_effects:
        parser.add_argument(
            "--allow-metadata-only-effects",
            dest="uk_allow_metadata_only_effects",
            action="store_true",
            default=None,
            help=f"{prefix}allow metadata-only UK effects to participate in replay selection",
        )
        parser.add_argument(
            "--no-metadata-only-effects",
            dest="uk_allow_metadata_only_effects",
            action="store_false",
            help=f"{prefix}keep UK replay selection source-backed by excluding metadata-only effects",
        )
    parser.add_argument(
        "--source-first-candidate",
        dest="uk_source_first_candidate",
        action="store_true",
        help=(
            f"{prefix}source-first regime: no metadata backfill, no oracle "
            "alignment, source-text authority"
        ),
    )
    parser.add_argument(
        "--authority-mode",
        dest="uk_authority_mode",
        choices=UK_AUTHORITY_MODE_CHOICES,
        default=None,
        help=f"{prefix}UK operation authority mode",
    )


def normalize_uk_replay_regime(args: Any) -> UKReplayRegime:
    """Normalize UK replay toggles into one explicit regime.

    The default regime preserves existing benchmark/replay behavior.  The
    source-first candidate regime is stricter and intentionally disables both
    metadata fallback and oracle EID alignment.
    """
    explicit_applicability_mode = getattr(args, "uk_applicability_mode", None)
    respect_feed_applied = getattr(args, "uk_respect_feed_applied", None)
    if explicit_applicability_mode is not None and respect_feed_applied is not None:
        implied_mode = (
            "effective_date_plus_feed_applied"
            if respect_feed_applied
            else "effective_date_only"
        )
        if explicit_applicability_mode != implied_mode:
            print(
                "error: --applicability-mode conflicts with --respect-feed-applied/--ignore-feed-applied",
                file=sys.stderr,
            )
            sys.exit(2)

    if explicit_applicability_mode is not None:
        applicability_mode = explicit_applicability_mode
    elif respect_feed_applied is False:
        applicability_mode = "effective_date_only"
    else:
        applicability_mode = "effective_date_plus_feed_applied"

    allow_metadata_backfill = getattr(args, "uk_allow_metadata_backfill", None)
    if allow_metadata_backfill is None:
        allow_metadata_backfill = True
    allow_oracle_alignment = getattr(args, "uk_allow_oracle_alignment", None)
    if allow_oracle_alignment is None:
        allow_oracle_alignment = True
    authority_mode = getattr(args, "uk_authority_mode", None) or "current_mixed"
    allow_metadata_only_effects = getattr(args, "uk_allow_metadata_only_effects", None)
    if allow_metadata_only_effects is None:
        allow_metadata_only_effects = True

    if getattr(args, "uk_source_first_candidate", False):
        conflicts = []
        if allow_metadata_backfill is True and getattr(args, "uk_allow_metadata_backfill", None) is True:
            conflicts.append("--metadata-backfill")
        if allow_oracle_alignment is True and getattr(args, "uk_allow_oracle_alignment", None) is True:
            conflicts.append("--oracle-alignment")
        if explicit_applicability_mode is not None and explicit_applicability_mode != "effective_date_plus_feed_applied":
            conflicts.append("--applicability-mode")
        if respect_feed_applied is False:
            conflicts.append("--ignore-feed-applied")
        if getattr(args, "uk_authority_mode", None) == "current_mixed":
            conflicts.append("--authority-mode current_mixed")
        if getattr(args, "uk_allow_metadata_only_effects", None) is True:
            conflicts.append("--allow-metadata-only-effects")
        if conflicts:
            print(
                "error: --source-first-candidate conflicts with " + ", ".join(conflicts),
                file=sys.stderr,
            )
            sys.exit(2)
        return UKReplayRegime(
            allow_metadata_backfill=False,
            allow_oracle_alignment=False,
            applicability_mode="effective_date_plus_feed_applied",
            authority_mode="source_text_only",
            allow_metadata_only_effects=False,
        )

    return UKReplayRegime(
        allow_metadata_backfill=bool(allow_metadata_backfill),
        allow_oracle_alignment=bool(allow_oracle_alignment),
        applicability_mode=applicability_mode,
        authority_mode=authority_mode,
        allow_metadata_only_effects=bool(allow_metadata_only_effects),
    )
