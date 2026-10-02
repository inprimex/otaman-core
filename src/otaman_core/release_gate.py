"""Cut-eligibility: a release ships only completed, gate-passed changes (rcg 1.1).

A release cut must assert that every change with commits in the candidate bundle is
*cut-eligible*, and FAIL the cut naming any that is not — never advisory (when gate
throughput constrains cutting, the remedy is throughput, not relaxing the gate). This
module is the single home for that per-change verdict, the FOURTH consumer of the
filed-completes reader (:mod:`otaman_core.task_complete`), consumed by the deploy
release.yml gate step (rcg 1.2) and the ``otaman spec status`` pre-cut view (rcg 1.3).

A change is cut-eligible when BOTH hold:

- every implementation task is filed complete — read from the single-home
  filed-completes reader (bus ``task-complete`` filings, NOT tasks.md ticks, which lag;
  retraction-aware via :func:`~otaman_core.task_complete.is_effectively_complete`), and
- its verification gate has passed.

The verdict distinguishes the two failure directions so the cut names the real blocker:
:data:`TASKS_OUTSTANDING` (work not filed complete) vs :data:`GATE_UNPASSED` (work done,
gate not yet passed). ``gate_passed`` is supplied by the caller (it owns the gate-state
read — stage or gate record); this module owns the task-completeness side and the
combination. Pure: bus reads via the reader, no clock, no cut side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from otaman_core.task_complete import filed_complete_at, is_effectively_complete

#: Every task filed complete AND the verification gate passed — the cut may ship it.
ELIGIBLE = "eligible"
#: One or more implementation tasks are not filed complete (retraction-aware).
TASKS_OUTSTANDING = "tasks-outstanding"
#: All tasks filed complete, but the verification gate has not passed.
GATE_UNPASSED = "gate-unpassed"


@dataclass(frozen=True)
class CutVerdict:
    """A change's cut-eligibility, with the counts that name the blocker.

    ``status`` is one of :data:`ELIGIBLE` / :data:`TASKS_OUTSTANDING` /
    :data:`GATE_UNPASSED`. ``outstanding`` lists the task ids not filed complete (empty
    once the task side is satisfied). ``eligible`` is the convenience boolean the gate
    step asserts.
    """

    change: str
    status: str
    total_tasks: int
    complete_tasks: int
    outstanding: tuple[str, ...]
    gate_passed: bool

    @property
    def eligible(self) -> bool:
        return self.status == ELIGIBLE


def cut_eligibility(
    project_root: Path,
    change: str,
    task_ids: list[str] | tuple[str, ...],
    config: dict[str, Any],
    tasks_path: Path,
    *,
    gate_passed: bool,
    filed: dict[str, datetime | None] | None = None,
) -> CutVerdict:
    """The cut-eligibility :class:`CutVerdict` for *change*.

    *task_ids* are the change's implementation task ids (from its tasks.md — the caller
    supplies the list; this module decides which are *done* from the bus, not the file).
    A task counts complete via :func:`~otaman_core.task_complete.is_effectively_complete`
    with ``honor_all=False`` — a bus filing of THAT task's id, not older than its most
    recent un-tick. The ``--all`` sentinel does NOT count for a cut: a blanket claim
    from one agent must not close another agent's unfinished task (cli rcg-1.3). So a
    swept bus or a lagging tasks.md tick does not change the verdict, a retracted filing
    re-opens the task, and a cut demands a per-task filing.

    *filed* lets a fleet-wide caller pass a pre-read filings map (one
    :func:`~otaman_core.task_complete.filed_complete_by_change` pass for the whole bus)
    instead of this paying one :func:`~otaman_core.task_complete.filed_complete_at` scan
    per change (cli's seam ask). Omit it for the single-change path.

    Task side first: any task not filed complete -> :data:`TASKS_OUTSTANDING` (naming
    them). Only once the task side is clean does *gate_passed* decide
    :data:`ELIGIBLE` vs :data:`GATE_UNPASSED` — so the cut reports the nearest real
    blocker, not a gate failure that an outstanding task masks.
    """
    if filed is None:
        filed = filed_complete_at(project_root, change, config)
    ids = list(task_ids)
    outstanding = tuple(
        t for t in ids if not is_effectively_complete(t, filed, tasks_path, honor_all=False)
    )
    complete = len(ids) - len(outstanding)
    if outstanding:
        status = TASKS_OUTSTANDING
    elif not gate_passed:
        status = GATE_UNPASSED
    else:
        status = ELIGIBLE
    return CutVerdict(
        change=change,
        status=status,
        total_tasks=len(ids),
        complete_tasks=complete,
        outstanding=outstanding,
        gate_passed=gate_passed,
    )


__all__ = [
    "ELIGIBLE",
    "GATE_UNPASSED",
    "TASKS_OUTSTANDING",
    "CutVerdict",
    "cut_eligibility",
]
