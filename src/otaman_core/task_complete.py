"""The one reader for filed ``task-complete`` bus messages (task-complete-reconciler).

`otaman complete` files a ``task-complete`` on the bus; the specs-owner applies the
`tasks.md` tick later. Between those moments the file still reads ``- [ ]``, so a
specs push re-dispatches finished work (measured: cli's srf 1.3 re-assigned twice by
later pushes). Three surfaces need to know "which change+task has a filing": the
dispatch consult (the tick-latency window), the ``otaman spec sweep`` reconciler, and
the ``otaman check`` drift counter. This module is that reader, homed once in core
(single-home rule, design D1) — extracted from the dispatch consult (otaman-plugin
28758fb) so the three consumers cannot drift apart.

What it does, and deliberately does not:
- Matches a filing on its ``type: task-complete`` FRONTMATTER, never on filename —
  the CLI's filenames happen to contain "task-complete", but that is a convention.
- Parses the four ``**Completed**:`` forms — ``all`` (the --all sentinel), a single
  id, a comma/space list, and an ``N.a-N.b`` range. Anything it cannot parse
  confidently yields nothing rather than guessing ``all`` (noisy re-dispatch beats
  silent loss).
- Honors RETRACTION: a filing older than a task's most recent un-tick (``[x]`` ->
  ``[ ]`` in git history) is stale evidence and is ignored — un-ticking is how a
  retraction is expressed, so an over-claimed task can re-dispatch.

Pure-ish: local file reads for the bus, and ``git log``/``git show`` for the
retraction scan (bounded); no clock, no policy about applying ticks (that is the
reconciler's job, cli 1.2).
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from otaman_core.frontmatter import parse_bus_timestamp

# The trailing ``(?:-[a-z0-9]+)?`` captures a hyphen suffix like ``1.7-bis`` as
# part of the id. Without it the match stopped at the word boundary before the
# hyphen, so ``1.7`` and ``1.7-bis`` — two distinct task lines that co-exist in the
# live corpus (architecture-dependency-graph/tasks.md) — collapsed to the same id,
# and a filing for one would tick both (cli #212 collision report). Keeping the
# suffix is safe under either ruling on whether ``-bis`` is a legitimate id form:
# if it is, the two lines tick independently; if spec-agent later rewrites it, this
# reader still never silently closes a task nobody did.
_TASK_ID_RE = re.compile(r"^(\d+[A-Za-z]?(?:\.\d+)*(?:-[a-z0-9]+)?)\b")
_COMPLETED_RE = re.compile(r"^\*\*Completed\*\*:\s*(.+)$", re.MULTILINE)
_RANGE_RE = re.compile(r"^(\d+)\.(\d+)\s*-\s*(\d+)\.(\d+)$")
_TIMESTAMP_RE = re.compile(r"^timestamp:\s*(\S+)", re.MULTILINE)
_TYPE_RE = re.compile(r"^type:\s*task-complete\s*$", re.MULTILINE)
_CHANGE_RE = re.compile(r"^change:\s*(.+?)\s*$", re.MULTILINE)

#: The ``otaman complete --all`` sentinel, which names no individual ids. Consumers
#: test ``COMPLETED_ALL in filed`` to mean "every task of the change is filed".
COMPLETED_ALL = "*"

#: How far back the retraction scan reads. A tasks.md does not accumulate thousands
#: of commits, and an unbounded ``git log -p`` is not something a post-commit hook
#: should run; on hitting this bound the scan reports "no un-tick found", not "none".
UNTICK_SCAN_COMMITS = 200


def task_id_of(task_text: str) -> str | None:
    """The leading ``1.2`` / ``2.1`` / ``1B.3`` / ``1.7-bis`` identifier of a line.

    A hyphen suffix (``-bis``) is part of the id, so ``1.7`` and ``1.7-bis`` are
    distinct — they are two different task lines in the corpus and must not collide.
    """
    match = _TASK_ID_RE.match(task_text.strip())
    return match.group(1) if match else None


def _parse_completed_spec(spec: str) -> set[str]:
    """Task ids named by a ``**Completed**:`` line (the four forms).

    Returns ``{COMPLETED_ALL}`` for an --all filing. Returns an EMPTY set for
    anything it cannot parse confidently — the safe direction is to re-dispatch a
    task that was already done (noisy, recoverable) rather than skip one that was
    not (silent, and the work is simply lost).
    """
    spec = spec.strip()
    if not spec:
        return set()
    if spec.lower().startswith("all"):
        return {COMPLETED_ALL}
    spec = re.sub(r"^tasks?\s+", "", spec, flags=re.IGNORECASE)
    out: set[str] = set()
    for raw in re.split(r"[,\s]+", spec):
        piece = raw.strip()
        if not piece:
            continue
        rng = _RANGE_RE.match(piece)
        if rng:
            major_a, minor_a, major_b, minor_b = (int(g) for g in rng.groups())
            if major_a == major_b and minor_a <= minor_b:
                out.update(f"{major_a}.{n}" for n in range(minor_a, minor_b + 1))
            continue
        if _TASK_ID_RE.match(piece):
            out.add(piece)
    return out


def _filing_time(text: str) -> datetime | None:
    match = _TIMESTAMP_RE.search(text)
    if not match:
        return None
    # The canonical bus-timestamp parse is single-homed in frontmatter (cli #234):
    # it accepts the producer's fractional-second isoformat that an inline strptime
    # would reject.
    return parse_bus_timestamp(match.group(1))


def _merge_newest(dst: dict[str, datetime | None], tid: str, when: datetime | None) -> None:
    """Record *when* as *tid*'s filing time, keeping the NEWEST across filings.

    ``None`` (a filing with no parseable timestamp) never overwrites a real time and
    only fills an absent id — "filed, age unknown" loses to any dated filing.
    """
    if tid not in dst:
        dst[tid] = when
    elif when is not None:
        prev = dst[tid]
        if prev is None or when > prev:
            dst[tid] = when


def filed_complete_by_change(
    project_root: Path, config: dict[str, Any]
) -> dict[str, dict[str, datetime | None]]:
    """All filed task-completes in ONE bus pass: change -> {task id -> newest time}.

    The batch form of :func:`filed_complete_at`, for the reconciler that asks about
    every change at once (cli tcr 2.2): the per-change call is O(changes x messages)
    — 61 whole-bus scans measured at ~23s on the largest tenant — and this collapses
    them to a single O(messages) pass. Same rules exactly: matched on the
    ``type: task-complete`` frontmatter (never filename), grouped by the ``change:``
    frontmatter, newest filing wins, both ``active`` and ``archive`` scanned.

    Deliberately does NOT touch git: the retraction rule (:func:`last_untick_at`,
    per task) is applied downstream by :func:`is_effectively_complete`, unchanged —
    batching the bus scan does not batch or weaken the un-tick check.
    """
    bus_rel = config.get("communication", {}).get("bus_path", ".agents/bus")
    out: dict[str, dict[str, datetime | None]] = {}
    for sub in ("active", "archive"):
        directory = project_root / bus_rel / sub
        if not directory.is_dir():
            continue
        for path in directory.glob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if not _TYPE_RE.search(text):
                continue
            change_match = _CHANGE_RE.search(text)
            if not change_match:
                continue
            bucket = out.setdefault(change_match.group(1), {})
            when = _filing_time(text)
            for completed in _COMPLETED_RE.finditer(text):
                for tid in _parse_completed_spec(completed.group(1)):
                    _merge_newest(bucket, tid, when)
    return out


def filed_complete_at(
    project_root: Path, change: str, config: dict[str, Any]
) -> dict[str, datetime | None]:
    """Task id -> the NEWEST filing time for it, for *change*.

    ``None`` as a value means a filing exists but carries no parseable timestamp;
    callers treat that as "filed, age unknown". Both ``active`` and ``archive`` are
    scanned so a swept bus does not resurrect finished work. Matched on the
    ``type: task-complete`` frontmatter, never on filename.

    A thin lookup over :func:`filed_complete_by_change` so single-change and
    fleet-wide callers share one matching implementation and cannot drift. A caller
    asking about many changes should call the batch function once instead of this
    per change (that is the ~23s vs one-pass difference; cli tcr 2.2).
    """
    return filed_complete_by_change(project_root, config).get(change, {})


def filed_complete_ids(project_root: Path, change: str, config: dict[str, Any]) -> set[str]:
    """Task ids with a filed ``task-complete`` for *change*, pending OR resolved.

    A resolved filing is still a filing; archive is scanned too. Does NOT apply the
    retraction rule — use :func:`is_effectively_complete` for the dispatch/tick
    decision, which weighs a filing against its task's most recent un-tick.
    """
    return set(filed_complete_at(project_root, change, config))


def last_untick_at(tasks_path: Path, task_id: str) -> datetime | None:
    """When *task_id* was most recently un-ticked (``[x]`` -> ``[ ]``) in git, if ever.

    Un-ticking is how a retraction is expressed, so a filing older than the most
    recent un-tick of THIS task is stale evidence. An ordinary tasks.md edit is NOT
    a retraction: only a commit that turns this specific line from ``[x]`` to
    ``[ ]`` counts — which keeps the tick-latency fix intact (a push that edits
    tasks.md without un-ticking is not a retraction).
    """
    import subprocess

    if not shutil.which("git"):
        return None
    repo = tasks_path.parent
    try:
        log = subprocess.run(
            ["git", "log", f"-{UNTICK_SCAN_COMMITS}", "--format=%H %cI", "--", tasks_path.name],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    marker = re.compile(rf"^-\s*-\s*\[[xX]\]\s+{re.escape(task_id)}\b")
    readded = re.compile(rf"^\+\s*-\s*\[ \]\s+{re.escape(task_id)}\b")
    for line in log.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        sha, when = parts
        try:
            diff = subprocess.run(
                ["git", "show", "--format=", "-U0", sha, "--", tasks_path.name],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        diff_lines = diff.splitlines()
        if any(marker.match(ln) for ln in diff_lines) and any(
            readded.match(ln) for ln in diff_lines
        ):
            try:
                return datetime.fromisoformat(when.replace("Z", "+00:00"))
            except ValueError:
                return None
    return None


def is_effectively_complete(
    task_id: str, filed: dict[str, datetime | None], tasks_path: Path
) -> bool:
    """Whether *task_id* counts as complete for dispatch/tick, honoring retraction.

    *filed* is a :func:`filed_complete_at` result. A task is filed if its id is in
    *filed* or an --all filing exists (:data:`COMPLETED_ALL`). A filing older than
    the task's most recent un-tick (:func:`last_untick_at`) is stale — the task is
    NOT effectively complete and goes back out — so an over-claimed task can
    re-dispatch instead of the consult citing a withdrawn filing forever.
    """
    key = task_id if task_id in filed else (COMPLETED_ALL if COMPLETED_ALL in filed else None)
    if key is None:
        return False
    filed_at = filed.get(key)
    untick_at = last_untick_at(tasks_path, task_id)
    if untick_at is not None and (filed_at is None or filed_at < untick_at):
        return False
    return True


__all__ = [
    "COMPLETED_ALL",
    "UNTICK_SCAN_COMMITS",
    "filed_complete_at",
    "filed_complete_by_change",
    "filed_complete_ids",
    "is_effectively_complete",
    "last_untick_at",
    "task_id_of",
]
