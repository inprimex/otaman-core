"""program-crud-and-context-resolution 1.1 — the single-home program resolver.

Pins the precedence chain (explicit → cwd walk → picker → refusal), enumeration over
the orgs/<org>/programs layout with lifecycle state, and the init-advice-only-at-zero
refusal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_core.program_context import (
    ProgramContextError,
    enumerate_programs,
    program_of_path,
    resolve_program,
)


def _make_program(workspace: Path, org: str, program: str, *, state: str | None = None) -> Path:
    prog = workspace / "orgs" / org / "programs" / program
    prog.mkdir(parents=True, exist_ok=True)
    if state is not None:
        reg = workspace / "orgs" / org / "config" / "lifecycle.yaml"
        reg.parent.mkdir(parents=True, exist_ok=True)
        # append this program's state entry (simple YAML; one line per program)
        existing = reg.read_text() if reg.exists() else "programs:\n"
        reg.write_text(existing + f"  {program}:\n    state: {state}\n")
    return prog


# --- enumeration -------------------------------------------------------------


def test_enumerate_lists_programs_with_state(tmp_path):
    _make_program(tmp_path, "acme", "alpha", state="active")
    _make_program(tmp_path, "acme", "beta", state="archived")
    _make_program(tmp_path, "other", "gamma")  # no registry entry -> active
    progs = enumerate_programs(tmp_path)
    assert [(p.org, p.name, p.state) for p in progs] == [
        ("acme", "alpha", "active"),
        ("acme", "beta", "archived"),
        ("other", "gamma", "active"),
    ]
    assert progs[1].archived is True


def test_enumerate_no_orgs_tree_is_empty(tmp_path):
    assert enumerate_programs(tmp_path) == []


# --- cwd walk ----------------------------------------------------------------


def test_program_of_path_walks_up_from_a_repo(tmp_path):
    prog = _make_program(tmp_path, "acme", "alpha", state="active")
    deep = prog / "some-repo" / "src"
    deep.mkdir(parents=True)
    found = program_of_path(deep)
    assert found is not None and found.name == "alpha" and found.org == "acme"


def test_program_of_path_outside_any_program_is_none(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    assert program_of_path(tmp_path / "elsewhere") is None


# --- precedence --------------------------------------------------------------


def test_explicit_wins(tmp_path):
    _make_program(tmp_path, "acme", "alpha")
    _make_program(tmp_path, "acme", "beta")
    p = resolve_program(explicit="beta", workspace_root=tmp_path, cwd=tmp_path)
    assert p.name == "beta"


def test_explicit_miss_refuses_naming_programs(tmp_path):
    _make_program(tmp_path, "acme", "alpha")
    with pytest.raises(ProgramContextError) as ei:
        resolve_program(explicit="ghost", workspace_root=tmp_path)
    assert "ghost" in str(ei.value)
    assert [p.name for p in ei.value.programs] == ["alpha"]


def test_cwd_walk_used_when_no_explicit(tmp_path):
    prog = _make_program(tmp_path, "acme", "alpha", state="active")
    p = resolve_program(cwd=prog / "repo", workspace_root=tmp_path)
    assert p.name == "alpha"


def test_picker_used_when_not_in_a_program(tmp_path):
    _make_program(tmp_path, "acme", "alpha")
    _make_program(tmp_path, "acme", "beta")
    picked = []

    def picker(programs):
        picked.append([p.name for p in programs])
        return next(p for p in programs if p.name == "beta")

    p = resolve_program(cwd=tmp_path, workspace_root=tmp_path, picker=picker)
    assert p.name == "beta"
    assert picked == [["alpha", "beta"]]  # the picker saw the enumerated list


def test_picker_declining_falls_through_to_refusal(tmp_path):
    _make_program(tmp_path, "acme", "alpha")
    with pytest.raises(ProgramContextError):
        resolve_program(cwd=tmp_path, workspace_root=tmp_path, picker=lambda progs: None)


# --- refusal (init-advice-only-at-zero) --------------------------------------


def test_refusal_with_programs_lists_them_not_init(tmp_path):
    _make_program(tmp_path, "acme", "alpha")
    with pytest.raises(ProgramContextError) as ei:
        resolve_program(cwd=tmp_path, workspace_root=tmp_path)  # no picker = non-TTY
    msg = str(ei.value)
    assert "--program" in msg and "alpha" in msg
    assert "init" not in msg  # never advises init when programs exist


def test_refusal_with_zero_programs_advises_init(tmp_path):
    with pytest.raises(ProgramContextError) as ei:
        resolve_program(cwd=tmp_path, workspace_root=tmp_path)
    assert "otaman init" in str(ei.value)
    assert ei.value.programs == []
