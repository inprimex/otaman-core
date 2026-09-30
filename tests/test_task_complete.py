"""task-complete-reconciler 1.1 — the single-home filed-task-complete reader.

Extracted from the dispatch consult (otaman-plugin 28758fb) so the consult, the
`otaman spec sweep` reconciler, and the `otaman check` drift counter share one
reader. These pin the four `**Completed**:` forms, frontmatter-type matching
(never filename), newest-filing-wins, and the retraction rule (a filing older than
a task's most recent un-tick is ignored).
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from otaman_core.task_complete import (
    COMPLETED_ALL,
    _parse_completed_spec,
    filed_complete_at,
    filed_complete_by_change,
    filed_complete_ids,
    is_effectively_complete,
    last_untick_at,
    task_id_of,
)

_CONFIG = {"communication": {"bus_path": ".agents/bus"}}

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _file_complete(
    active: Path, name: str, *, change: str, completed: str, ts: str, mtype="task-complete"
):
    active.mkdir(parents=True, exist_ok=True)
    (active / name).write_text(
        f"---\nid: {name}\nfrom: cli-agent\nto: spec-agent\ntype: {mtype}\n"
        f"change: {change}\ntimestamp: {ts}\n---\n\n## Subject: done\n\n"
        f"**Completed**: {completed}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# task_id_of


@pytest.mark.parametrize(
    "text,expected",
    [
        # task_id_of takes the task TEXT (post checkbox-strip), as the consult calls it
        ("1.2 do a thing", "1.2"),
        ("2.1 @otaman-core something", "2.1"),
        ("1B.3 legacy id", "1B.3"),
        # a -bis suffix is part of the id: 1.7 and 1.7-bis must NOT collapse
        # (cli #212 collision — both lines live in the corpus)
        ("1.7 @otaman-bridge Spike", "1.7"),
        ("1.7-bis @otaman-bridge codegraph", "1.7-bis"),
        ("no id here", None),
    ],
)
def test_task_id_of(text, expected):
    assert task_id_of(text) == expected


def test_bis_suffix_does_not_collide_with_base_id():
    assert task_id_of("1.7 x") != task_id_of("1.7-bis x")


def test_completed_spec_keeps_bis_suffix_distinct():
    # a filing that names 1.7 does not silently cover 1.7-bis
    assert _parse_completed_spec("1.7") == {"1.7"}
    assert _parse_completed_spec("1.7-bis") == {"1.7-bis"}
    assert _parse_completed_spec("1.7, 1.7-bis") == {"1.7", "1.7-bis"}


# ---------------------------------------------------------------------------
# the four Completed forms


def test_completed_all_sentinel():
    assert _parse_completed_spec("all") == {COMPLETED_ALL}
    assert _parse_completed_spec("ALL tasks for the change") == {COMPLETED_ALL}


def test_completed_single_and_list():
    assert _parse_completed_spec("1.2") == {"1.2"}
    assert _parse_completed_spec("1.2, 1.3") == {"1.2", "1.3"}
    assert _parse_completed_spec("tasks 2.1 2.2") == {"2.1", "2.2"}


def test_completed_range():
    assert _parse_completed_spec("2.1-2.3") == {"2.1", "2.2", "2.3"}


def test_completed_unparseable_yields_empty_not_all():
    assert _parse_completed_spec("") == set()
    assert _parse_completed_spec("some prose with no ids") == set()


# ---------------------------------------------------------------------------
# filed_complete_at — frontmatter match, newest wins, active + archive


def test_filed_matched_on_frontmatter_type_not_filename(tmp_path):
    active = tmp_path / ".agents" / "bus" / "active"
    _file_complete(
        active, "20260101T000000-x.md", change="chg", completed="1.1", ts="2026-01-01T00:00:00Z"
    )
    # a file that merely NAMES task-complete but is type: info is not a filing
    _file_complete(
        active,
        "20260101T010000-task-complete-lookalike.md",
        change="chg",
        completed="1.2",
        ts="2026-01-01T01:00:00Z",
        mtype="info",
    )
    filed = filed_complete_at(tmp_path, "chg", _CONFIG)
    assert "1.1" in filed and "1.2" not in filed


def test_filed_ignores_other_change(tmp_path):
    active = tmp_path / ".agents" / "bus" / "active"
    _file_complete(active, "a.md", change="other", completed="1.1", ts="2026-01-01T00:00:00Z")
    assert filed_complete_at(tmp_path, "chg", _CONFIG) == {}


def test_filed_newest_filing_wins(tmp_path):
    active = tmp_path / ".agents" / "bus" / "active"
    _file_complete(active, "older.md", change="chg", completed="1.1", ts="2026-01-01T00:00:00Z")
    _file_complete(active, "newer.md", change="chg", completed="1.1", ts="2026-02-01T00:00:00Z")
    filed = filed_complete_at(tmp_path, "chg", _CONFIG)
    assert filed["1.1"] == datetime(2026, 2, 1, tzinfo=UTC)


def test_filed_scans_archive_too(tmp_path):
    archive = tmp_path / ".agents" / "bus" / "archive"
    _file_complete(archive, "old.md", change="chg", completed="1.1", ts="2026-01-01T00:00:00Z")
    assert "1.1" in filed_complete_at(tmp_path, "chg", _CONFIG)


def test_filed_complete_ids(tmp_path):
    active = tmp_path / ".agents" / "bus" / "active"
    _file_complete(active, "a.md", change="chg", completed="1.1, 1.2", ts="2026-01-01T00:00:00Z")
    assert filed_complete_ids(tmp_path, "chg", _CONFIG) == {"1.1", "1.2"}


def test_missing_bus_dir_is_empty(tmp_path):
    assert filed_complete_at(tmp_path, "chg", _CONFIG) == {}


# ---------------------------------------------------------------------------
# filed_complete_by_change — the one-pass batch reader (cli tcr 2.2)


def test_batch_groups_by_change(tmp_path):
    active = tmp_path / ".agents" / "bus" / "active"
    _file_complete(active, "a.md", change="alpha", completed="1.1", ts="2026-01-01T00:00:00Z")
    _file_complete(active, "b.md", change="alpha", completed="1.2", ts="2026-01-02T00:00:00Z")
    _file_complete(active, "c.md", change="beta", completed="2.1", ts="2026-01-03T00:00:00Z")
    by_change = filed_complete_by_change(tmp_path, _CONFIG)
    assert set(by_change) == {"alpha", "beta"}
    assert set(by_change["alpha"]) == {"1.1", "1.2"}
    assert set(by_change["beta"]) == {"2.1"}


def test_batch_agrees_with_per_change(tmp_path):
    active = tmp_path / ".agents" / "bus" / "active"
    _file_complete(active, "older.md", change="chg", completed="1.1", ts="2026-01-01T00:00:00Z")
    _file_complete(active, "newer.md", change="chg", completed="1.1", ts="2026-02-01T00:00:00Z")
    _file_complete(active, "other.md", change="chg", completed="1.2", ts="2026-01-05T00:00:00Z")
    # the per-change function is now a lookup over the batch — they must match exactly
    assert filed_complete_by_change(tmp_path, _CONFIG)["chg"] == filed_complete_at(
        tmp_path, "chg", _CONFIG
    )
    assert filed_complete_at(tmp_path, "chg", _CONFIG)["1.1"] == datetime(2026, 2, 1, tzinfo=UTC)


def test_batch_change_prefix_does_not_bleed(tmp_path):
    # a change name that is a prefix of another must not collect the other's filings
    active = tmp_path / ".agents" / "bus" / "active"
    _file_complete(active, "a.md", change="foo", completed="1.1", ts="2026-01-01T00:00:00Z")
    _file_complete(active, "b.md", change="foo-bar", completed="2.2", ts="2026-01-01T00:00:00Z")
    by_change = filed_complete_by_change(tmp_path, _CONFIG)
    assert set(by_change["foo"]) == {"1.1"}
    assert set(by_change["foo-bar"]) == {"2.2"}
    assert set(filed_complete_at(tmp_path, "foo", _CONFIG)) == {"1.1"}


def test_batch_ignores_non_task_complete(tmp_path):
    active = tmp_path / ".agents" / "bus" / "active"
    _file_complete(active, "a.md", change="chg", completed="1.1", ts="2026-01-01T00:00:00Z")
    _file_complete(
        active, "b.md", change="chg", completed="9.9", ts="2026-01-01T00:00:00Z", mtype="info"
    )
    assert set(filed_complete_by_change(tmp_path, _CONFIG)["chg"]) == {"1.1"}


def test_batch_missing_bus_is_empty(tmp_path):
    assert filed_complete_by_change(tmp_path, _CONFIG) == {}


# ---------------------------------------------------------------------------
# retraction (git) + is_effectively_complete


def _git(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


@needs_git
def test_last_untick_detects_x_to_blank(git_repo):
    tasks = git_repo / "tasks.md"
    tasks.write_text("# tasks\n- [x] 1.1 do a thing\n", encoding="utf-8")
    _git(git_repo, "add", "tasks.md")
    _git(git_repo, "commit", "-q", "-m", "tick 1.1")
    tasks.write_text("# tasks\n- [ ] 1.1 do a thing\n", encoding="utf-8")
    _git(git_repo, "add", "tasks.md")
    _git(git_repo, "commit", "-q", "-m", "untick 1.1")
    assert last_untick_at(tasks, "1.1") is not None


@needs_git
def test_last_untick_ignores_a_plain_edit(git_repo):
    tasks = git_repo / "tasks.md"
    tasks.write_text("# tasks\n- [ ] 1.1 do a thing\n", encoding="utf-8")
    _git(git_repo, "add", "tasks.md")
    _git(git_repo, "commit", "-q", "-m", "add 1.1")
    tasks.write_text("# tasks\n- [ ] 1.1 do a thing, reworded\n", encoding="utf-8")
    _git(git_repo, "add", "tasks.md")
    _git(git_repo, "commit", "-q", "-m", "reword (not an untick)")
    assert last_untick_at(tasks, "1.1") is None


@needs_git
def test_effectively_complete_true_when_filed_and_not_retracted(git_repo):
    tasks = git_repo / "tasks.md"
    tasks.write_text("- [ ] 1.1 x\n", encoding="utf-8")
    _git(git_repo, "add", "tasks.md")
    _git(git_repo, "commit", "-q", "-m", "init")  # no un-tick in history
    filed = {"1.1": datetime(2026, 1, 1, tzinfo=UTC)}
    assert is_effectively_complete("1.1", filed, tasks) is True


@needs_git
def test_effectively_complete_false_when_filing_predates_untick(git_repo):
    tasks = git_repo / "tasks.md"
    tasks.write_text("- [x] 1.1 x\n", encoding="utf-8")
    _git(git_repo, "add", "tasks.md")
    _git(git_repo, "commit", "-q", "-m", "tick")
    tasks.write_text("- [ ] 1.1 x\n", encoding="utf-8")
    _git(git_repo, "add", "tasks.md")
    _git(git_repo, "commit", "-q", "-m", "untick — retraction")
    # filing is OLDER than the un-tick → retracted → not effectively complete
    filed = {"1.1": datetime(2020, 1, 1, tzinfo=UTC)}
    assert is_effectively_complete("1.1", filed, tasks) is False


def test_effectively_complete_false_when_not_filed(tmp_path):
    assert is_effectively_complete("9.9", {"1.1": None}, tmp_path / "tasks.md") is False


@needs_git
def test_all_sentinel_covers_any_task(git_repo):
    tasks = git_repo / "tasks.md"
    tasks.write_text("- [ ] 3.7 x\n", encoding="utf-8")
    _git(git_repo, "add", "tasks.md")
    _git(git_repo, "commit", "-q", "-m", "init")
    filed = {COMPLETED_ALL: datetime(2026, 1, 1, tzinfo=UTC)}
    assert is_effectively_complete("3.7", filed, tasks) is True
