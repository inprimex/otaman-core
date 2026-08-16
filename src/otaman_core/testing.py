"""Shared test-isolation primitive (bus-test-isolation capability).

The forged-halt / rogue-root incidents came from test suites that inherit a
live session's ``OTAMAN_ROOT`` and then exercise real bus-write code, which
silently sprays fixtures into a live (or freshly-created rogue) bus. This
module makes that structurally impossible with one conftest line.

Usage — in a repo's ``tests/conftest.py``::

    from otaman_core.testing import isolate_bus  # noqa: F401

Registering ``isolate_bus`` as an autouse fixture (pytest discovers it by
name) makes the whole suite:

- strip ``OTAMAN_ROOT`` / ``MAESTRO_ROOT`` / ``OTAMAN_AGENT`` from the env;
- pin root resolution at a per-test tmp sandbox with a valid program-root
  shape (``platform.yaml`` + ``.agents/bus/active``);
- export the ``OTAMAN_TEST_MODE`` sentinel, so even a resolver call that
  slips past the fixture refuses to return any root outside the OS tmp tree
  (enforced in :mod:`otaman_core._resolve`).

``make_program_sandbox`` is pure filesystem setup (no pytest, no env
mutation) and is reusable outside pytest.
"""

from __future__ import annotations

from pathlib import Path

from otaman_core._resolve import TEST_MODE_ENV

#: Environment variables stripped from every test process. Stale copies of
#: these (inherited from a live session) are what let a suite reach a real bus.
STRIPPED_VARS = ("OTAMAN_ROOT", "MAESTRO_ROOT", "OTAMAN_AGENT")


def make_program_sandbox(root: Path, *, agent: str = "test-agent") -> Path:
    """Create a minimal valid program-root shape under *root*; return *root*.

    Shape: ``platform.yaml`` (the program marker the env step now requires)
    plus an empty ``.agents/bus/active`` tree. Pure filesystem — no env
    mutation, no pytest — so it is usable from any harness.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text(
        "project: test-sandbox\n"
        "version: '1.0'\n"
        "repos:\n"
        "  - name: test-repo\n"
        "    path: .\n"
        f"    owner: {agent}\n",
        encoding="utf-8",
    )
    (root / ".agents" / "bus" / "active").mkdir(parents=True, exist_ok=True)
    return root


try:
    import pytest
except ModuleNotFoundError:  # pragma: no cover - pytest always present under test
    pytest = None  # type: ignore[assignment]


if pytest is not None:

    @pytest.fixture(autouse=True)
    def isolate_bus(tmp_path, monkeypatch):
        """Autouse per-test bus isolation. See the module docstring.

        Yields the sandbox program-root path for tests that want to inspect
        what their bus writes produced. Tests exercising resolution internals
        may ``monkeypatch.delenv("OTAMAN_ROOT")`` / set their own root in the
        test body — the autouse fixture runs first, so those overrides win.
        """
        for var in STRIPPED_VARS:
            monkeypatch.delenv(var, raising=False)
        sandbox = make_program_sandbox(tmp_path / "otaman-sandbox")
        monkeypatch.setenv("OTAMAN_ROOT", str(sandbox))
        monkeypatch.setenv(TEST_MODE_ENV, "1")
        return sandbox
