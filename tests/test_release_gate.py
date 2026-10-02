"""release-completeness-gate 1.1 — cut-eligibility over the filed-completes reader.

Pins the three-state verdict (eligible / tasks-outstanding / gate-unpassed), the
counts, and that task-completeness is read from bus filings (retraction-aware) not
tasks.md ticks.
"""

from __future__ import annotations

from pathlib import Path

from otaman_core.release_gate import (
    ELIGIBLE,
    GATE_UNPASSED,
    TASKS_OUTSTANDING,
    cut_eligibility,
)

_CONFIG = {"communication": {"bus_path": ".agents/bus"}}


def _file_complete(active: Path, name: str, *, change: str, completed: str, ts: str):
    active.mkdir(parents=True, exist_ok=True)
    (active / name).write_text(
        f"---\nid: {name}\nfrom: core-agent\nto: spec-agent\ntype: task-complete\n"
        f"change: {change}\ntimestamp: {ts}\n---\n\n**Completed**: {completed}\n",
        encoding="utf-8",
    )


def _verdict(tmp_path, *, filed_tasks, task_ids, gate_passed):
    active = tmp_path / ".agents" / "bus" / "active"
    if filed_tasks:
        _file_complete(
            active,
            "c.md",
            change="chg",
            completed=", ".join(filed_tasks),
            ts="2026-01-01T00:00:00Z",
        )
    tasks_path = tmp_path / "tasks.md"
    tasks_path.write_text("# tasks\n", encoding="utf-8")
    return cut_eligibility(tmp_path, "chg", task_ids, _CONFIG, tasks_path, gate_passed=gate_passed)


def test_eligible_when_all_filed_and_gate_passed(tmp_path):
    v = _verdict(tmp_path, filed_tasks=["1.1", "1.2"], task_ids=["1.1", "1.2"], gate_passed=True)
    assert v.status == ELIGIBLE
    assert v.eligible is True
    assert v.total_tasks == 2 and v.complete_tasks == 2 and v.outstanding == ()


def test_tasks_outstanding_names_the_incomplete(tmp_path):
    v = _verdict(tmp_path, filed_tasks=["1.1"], task_ids=["1.1", "1.2"], gate_passed=True)
    assert v.status == TASKS_OUTSTANDING
    assert v.eligible is False
    assert v.outstanding == ("1.2",) and v.complete_tasks == 1


def test_gate_unpassed_when_tasks_done_but_gate_open(tmp_path):
    v = _verdict(tmp_path, filed_tasks=["1.1", "1.2"], task_ids=["1.1", "1.2"], gate_passed=False)
    assert v.status == GATE_UNPASSED
    assert v.eligible is False
    assert v.outstanding == ()


def test_tasks_outstanding_takes_priority_over_gate(tmp_path):
    # the nearest blocker is reported: an outstanding task masks a gate check
    v = _verdict(tmp_path, filed_tasks=[], task_ids=["1.1"], gate_passed=False)
    assert v.status == TASKS_OUTSTANDING


def test_all_filed_via_the_reader_not_tasks_md(tmp_path):
    # tasks.md has no ticks; eligibility comes from the bus filing
    v = _verdict(tmp_path, filed_tasks=["1.1"], task_ids=["1.1"], gate_passed=True)
    assert v.status == ELIGIBLE


def test_no_tasks_is_eligible_when_gate_passed(tmp_path):
    v = _verdict(tmp_path, filed_tasks=[], task_ids=[], gate_passed=True)
    assert v.status == ELIGIBLE and v.total_tasks == 0
