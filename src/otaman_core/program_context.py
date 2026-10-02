"""The one program-context resolver (program-crud-and-context-resolution 1.1).

Every program-scoped surface (project, program, -i, knowledge, spec …) needs the same
answer: which program is this command about? A surface-local resolver per command
drifts — they disagreed on precedence and on what to do when the answer is ambiguous.
This module is the single home (cli 1.2 rewires every surface onto it and deletes the
locals), so there is exactly one notion of program context.

Resolution precedence (ruled): an explicit parameter → a cwd walk → a TTY picker
callback → a non-interactive refusal that lists the programs and how to target one.
The resolver does NOT create programs — ``otaman init`` / ``scan`` own creation, so the
refusal advises ``init`` ONLY when there are zero programs; with programs present it
names them and the ``--program`` form, never ``init`` (init-advice-only-at-zero).

Enumeration (:func:`enumerate_programs`) is the read primitive: the
``<workspace>/orgs/<org>/programs/<program>`` layout, each program's lifecycle state
read through :func:`otaman_core.lifecycle.read_program_state` (the one state read
point) so an ``archived`` program is marked, not hidden.

Pure except for the directory scan and the lifecycle-registry read; the picker is a
caller-supplied callback (the cli provides the ``-i`` selection UX), so this module
holds no TTY logic — the caller decides interactivity and passes ``picker`` or not.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from otaman_core.lifecycle import DEFAULT_STATE, read_program_state

#: A caller-supplied picker: given the available programs, return the chosen one (or
#: ``None`` to decline). The cli passes its ``-i`` selection UX here; core stays
#: TTY-agnostic. A picker is passed only when the caller has decided the session is
#: interactive — its presence IS the "on a TTY" signal in the precedence chain.
Picker = Callable[["list[Program]"], "Program | None"]


@dataclass(frozen=True)
class Program:
    """One resolved program: its name, org, path, and lifecycle state."""

    name: str
    org: str
    path: Path
    state: str = DEFAULT_STATE

    @property
    def archived(self) -> bool:
        return self.state == "archived"


class ProgramContextError(ValueError):
    """No program could be resolved — the non-interactive refusal.

    Carries the enumerated :attr:`programs` so a surface can render the ruled refusal
    (list the names + the ``--program`` form; advise ``init`` only when the list is
    empty) without re-enumerating. ``message`` is a sensible default of that shape.
    """

    def __init__(self, message: str, programs: list[Program]):
        super().__init__(message)
        self.programs = programs


def _has_program_marker(d: Path) -> bool:
    """Whether *d* actually holds a program: a child dir with a ``platform.yaml``.

    The Otaman/meta folder of a real program carries ``platform.yaml`` (and a bus);
    the path shape alone does not — a botched-copy directory under ``programs/`` (just a
    ``LICENSE`` or a ``scripts/`` dir, no meta) matches the shape but is not a program
    (cli #258: enumerate reported 3 where 1 exists). Requiring the marker makes core's
    enumeration agree with cli's picker gate (a meta dir holding platform.yaml).
    """
    try:
        return any((c / "platform.yaml").is_file() for c in d.iterdir() if c.is_dir())
    except OSError:
        return False


def _is_program_dir(d: Path) -> bool:
    """Whether *d* is a real ``<...>/orgs/<org>/programs/<program>`` program directory.

    Both the path shape AND a program marker — the shape locates candidates, the marker
    rejects debris that merely sits under ``programs/``.
    """
    return (
        d.parent.name == "programs"
        and d.parent.parent.parent.name == "orgs"
        and d.parent.parent.name != ""
        and _has_program_marker(d)
    )


def program_of_path(path: Path) -> Program | None:
    """The :class:`Program` whose tree contains *path*, by walking up, or ``None``.

    A program dir is ``<...>/orgs/<org>/programs/<program>``; its org is the directory
    two levels up, and its state is read from that org's registry. Walks *path* and its
    parents so a cwd deep inside a repo (``.../programs/foo/some-repo/src``) still
    resolves to program ``foo``.
    """
    for d in (path, *path.parents):
        if _is_program_dir(d):
            org_root = d.parent.parent
            return Program(
                name=d.name,
                org=org_root.name,
                path=d,
                state=read_program_state(org_root, d.name),
            )
    return None


def enumerate_programs(workspace_root: Path) -> list[Program]:
    """Every program under ``<workspace_root>/orgs/<org>/programs/``, with state.

    The read primitive for listing and for the picker/refusal. Returns an empty list
    when there is no ``orgs/`` tree (a machine with no programs — the init-advice case).
    Sorted by (org, name) so listings are stable; archived programs are included and
    marked (via :attr:`Program.archived`), never dropped.
    """
    out: list[Program] = []
    orgs = workspace_root / "orgs"
    if not orgs.is_dir():
        return out
    for org_dir in sorted(orgs.iterdir()):
        programs_dir = org_dir / "programs"
        if not org_dir.is_dir() or not programs_dir.is_dir():
            continue
        for prog_dir in sorted(programs_dir.iterdir()):
            if not prog_dir.is_dir() or not _has_program_marker(prog_dir):
                continue  # skip debris under programs/ that has no meta/platform.yaml
            out.append(
                Program(
                    name=prog_dir.name,
                    org=org_dir.name,
                    path=prog_dir,
                    state=read_program_state(org_dir, prog_dir.name),
                )
            )
    return out


def _refusal_message(programs: list[Program]) -> str:
    if not programs:
        return "no program found and none exist on this machine — run `otaman init` to create one"
    names = ", ".join(p.name for p in programs)
    return (
        f"no program in context and not on a TTY to pick one; target one with "
        f"--program <name> (available: {names})"
    )


def resolve_program(
    *,
    explicit: str | None = None,
    cwd: Path | None = None,
    workspace_root: Path | None = None,
    picker: Picker | None = None,
) -> Program:
    """Resolve the program in context by the ruled precedence, or refuse.

    1. **explicit** — *explicit* names a program; it resolves against the enumeration
       and, if unknown, refuses naming the available programs (an explicit miss is an
       error, not a fall-through — the caller asked for a specific one).
    2. **cwd walk** — *cwd* inside a program tree resolves to that program
       (:func:`program_of_path`), no enumeration needed.
    3. **picker** — on a TTY the caller passes *picker*; it is called with the
       enumerated programs and its choice is used. Declining (``None``) falls through.
    4. **refusal** — otherwise raise :class:`ProgramContextError` listing the programs
       and the ``--program`` form (or advising ``init`` only when none exist).

    *workspace_root* is needed for enumeration (steps 1, 3, 4); step 2 needs only *cwd*.
    """
    programs: list[Program] | None = None

    def _programs() -> list[Program]:
        nonlocal programs
        if programs is None:
            programs = enumerate_programs(workspace_root) if workspace_root is not None else []
        return programs

    if explicit is not None:
        match = next((p for p in _programs() if p.name == explicit), None)
        if match is not None:
            return match
        raise ProgramContextError(
            f"no program named {explicit!r}; available: "
            f"{', '.join(p.name for p in _programs()) or '(none)'}",
            _programs(),
        )

    if cwd is not None:
        found = program_of_path(cwd)
        if found is not None:
            return found

    if picker is not None:
        chosen = picker(_programs())
        if chosen is not None:
            return chosen

    raise ProgramContextError(_refusal_message(_programs()), _programs())


__all__ = [
    "Picker",
    "Program",
    "ProgramContextError",
    "enumerate_programs",
    "program_of_path",
    "resolve_program",
]
