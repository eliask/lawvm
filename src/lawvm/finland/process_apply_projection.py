"""Post-apply evidence projection for ``process_muutoslaki``."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from lawvm.core.mutation_accounting import MutationAccountingResult
from lawvm.core.mutation_boundary import tree_path_to_diagnostic_string
from lawvm.core.provenance import MigrationEvent
from lawvm.finland.migration_ledger import MigrationLedger


@dataclass(slots=True)
class ProcessApplyProjectionContext:
    amendment_id: str
    observed_touch_results: list[MutationAccountingResult]
    elaboration_observations: list[dict[str, object]]
    migration_ledger: MigrationLedger
    migration_ledger_initial_len: int
    migration_events_out: Optional[list[MigrationEvent]]
    logger: logging.Logger

    def project(self) -> None:
        self._project_observed_touch_results()
        self._export_new_migration_events()
        self._log_migration_ledger()

    def _project_observed_touch_results(self) -> None:
        # Stage-0 passive observed-vs-declared cross-check results: surface on
        # the elaboration-observation rail as non-blocking findings. They never
        # gate replay; they only record which ops touched tree paths their
        # mutation events do not declare.
        for result in self.observed_touch_results:
            self.elaboration_observations.append(
                {
                    "kind": "APPLY.REPLAY_UNDECLARED_TREE_TOUCH",
                    "code": result.code,
                    "source_statute": self.amendment_id,
                    "op_id": result.op_id,
                    "helper": result.helper,
                    "undeclared_paths": [
                        tree_path_to_diagnostic_string(path)
                        for path in result.out_of_scope_paths
                    ],
                    "declared_paths": [
                        tree_path_to_diagnostic_string(path)
                        for path in result.allowed_paths
                    ],
                    "blocking": False,
                }
            )

    def _export_new_migration_events(self) -> None:
        if self.migration_events_out is None:
            return
        if len(self.migration_ledger) <= self.migration_ledger_initial_len:
            return
        self.migration_events_out.extend(
            self.migration_ledger.events[self.migration_ledger_initial_len:]
        )

    def _log_migration_ledger(self) -> None:
        if not self.migration_ledger:
            return
        self.logger.debug(
            "[%s] migration_ledger: %d event(s)",
            self.amendment_id,
            len(self.migration_ledger),
        )
