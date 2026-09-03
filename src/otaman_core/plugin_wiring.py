"""Plugin-tree wiring doctor check (ce-bootstrap-plugin-wiring 1.2).

CE bootstrap vendors the org's plugin tree to
``~/.otaman/otaman-plugin-tree`` and — since ce-bootstrap-plugin-wiring 1.1
— wires ``runner.agent_bootstrap.plugin_dir`` to it in ``platform.yaml``.
The runner spawner treats an absent ``plugin_dir`` as a spec-compliant
no-op (``runner-spawn-session-parity``): it drops ``--plugin-dir`` and the
spawned Path-B session comes up with MCP tools but NO slash commands. That
degradation is log-only, so a vendored-but-unwired tree — or a wired path
whose directory has since vanished — is otherwise invisible.

This module surfaces both halves of that gap as ``otaman doctor`` WARNs.
Following the ``check_lifecycle`` / ``check_approver_config`` pattern, the
check function is PURE: the caller (``otaman doctor``) resolves disk state
and passes it in, so the rule is unit-testable without a filesystem.
:func:`resolve_plugin_wiring` is the thin disk-facing convenience the CLI
wrapper uses to build those inputs from a platform config + home dir.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from otaman_core.human_roster import DoctorFinding

#: Default vendored plugin-tree location, relative to the org home. Mirrors
#: ce-bootstrap.sh's ``PLUGIN_TREE_DEST=${ORG_HOME}/.otaman/otaman-plugin-tree``.
DEFAULT_PLUGIN_TREE_RELPATH = ".otaman/otaman-plugin-tree"

#: The one-line reconcile fix named in both WARNs.
_RECONCILE_FIX = (
    "re-run ce-bootstrap.sh (reconcile), or set "
    "runner.agent_bootstrap.plugin_dir to the tree path in platform.yaml"
)


def default_plugin_tree(home: Path) -> Path:
    """Return the default vendored plugin-tree path for an org ``home``."""
    return home / DEFAULT_PLUGIN_TREE_RELPATH


def check_plugin_wiring(
    *,
    vendored_tree_present: bool,
    plugin_dir: str | None,
    plugin_dir_present: bool | None = None,
    vendored_tree_path: str = "",
) -> list[DoctorFinding]:
    """Doctor checks for plugin-tree ↔ ``plugin_dir`` wiring.

    - WARN when the vendored tree exists (``vendored_tree_present``) but
      ``plugin_dir`` is absent (``None``) — every runner Path-B session
      silently comes up without slash commands; naming the reconcile fix.
    - WARN when ``plugin_dir`` is set but its directory is missing
      (``plugin_dir_present is False``) — the wired path dangles; naming it.

    Healthy states (tree present + ``plugin_dir`` set & present, or neither
    tree nor key) yield no findings. ``plugin_dir_present`` is only consulted
    when ``plugin_dir`` is set; pass the resolved existence of that path.

    Returns findings in a stable order (unwired first); empty when healthy.
    The caller renders them. See :func:`resolve_plugin_wiring` for the
    disk-facing helper that builds these inputs.
    """
    findings: list[DoctorFinding] = []

    if plugin_dir is None:
        if vendored_tree_present:
            where = f" at {vendored_tree_path}" if vendored_tree_path else ""
            findings.append(
                DoctorFinding(
                    "warn",
                    f"vendored plugin tree present{where} but "
                    "runner.agent_bootstrap.plugin_dir is absent from platform.yaml — "
                    "runner Path-B sessions spawn without --plugin-dir, so slash "
                    f"commands (/otaman:*) are silently unavailable; fix: {_RECONCILE_FIX}",
                )
            )
    elif plugin_dir_present is False:
        findings.append(
            DoctorFinding(
                "warn",
                f"runner.agent_bootstrap.plugin_dir is set to {plugin_dir!r} but that "
                "directory is missing — the wired path dangles and the runner drops "
                f"--plugin-dir; fix: point it at the vendored tree, or {_RECONCILE_FIX}",
            )
        )

    return findings


def _agent_bootstrap(platform_config: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return ``runner.agent_bootstrap`` (or an empty mapping) from config."""
    runner = platform_config.get("runner")
    if not isinstance(runner, Mapping):
        return {}
    bootstrap = runner.get("agent_bootstrap")
    return bootstrap if isinstance(bootstrap, Mapping) else {}


def resolve_plugin_wiring(
    platform_config: Mapping[str, Any],
    *,
    home: Path,
    platform_dir: Path | None = None,
) -> list[DoctorFinding]:
    """Resolve disk state from a platform config, then run the wiring check.

    Reads ``runner.agent_bootstrap.plugin_dir`` from ``platform_config``,
    resolves the vendored tree at :func:`default_plugin_tree` (under ``home``),
    checks both paths on disk, and delegates to :func:`check_plugin_wiring`.

    ``plugin_dir`` values are interpreted as: absolute paths as-is, ``~`` /
    ``$VAR`` expanded, and relative paths against ``platform_dir`` (the dir
    holding ``platform.yaml``, per the schema) when given, else ``home``.
    """
    bootstrap = _agent_bootstrap(platform_config)
    raw = bootstrap.get("plugin_dir")
    plugin_dir = str(raw) if isinstance(raw, str) and raw else None

    tree = default_plugin_tree(home)
    vendored_present = tree.is_dir()

    plugin_dir_present: bool | None = None
    if plugin_dir is not None:
        expanded = Path(os.path.expandvars(plugin_dir)).expanduser()
        if not expanded.is_absolute():
            base = platform_dir if platform_dir is not None else home
            expanded = base / expanded
        plugin_dir_present = expanded.is_dir()

    return check_plugin_wiring(
        vendored_tree_present=vendored_present,
        plugin_dir=plugin_dir,
        plugin_dir_present=plugin_dir_present,
        vendored_tree_path=str(tree),
    )


__all__ = [
    "DEFAULT_PLUGIN_TREE_RELPATH",
    "DoctorFinding",
    "check_plugin_wiring",
    "default_plugin_tree",
    "resolve_plugin_wiring",
]
