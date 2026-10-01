"""The harness version registry — the one authoritative pin (hvm 1.1).

The fleet runs an EXACT harness version (design D1/D2): a single pin declared once in
the platform harness registry (``runner.harnesses[].pin``), not a range —
conformance certifies a binary, not a floor. This module is the single home
(two-consumer rule) that reads it, consumed by deploy provisioning (install from the
pin, auto-update off — hvm 1.2) and the plugin doctor (installed-vs-pin per tenant,
freshness vocabulary — hvm 1.3).

The pin is **derived, never authored** here (schema note, hvm 1.1): a human moves it
only through the canary soak; provisioning and bootstrap read it and never write it.
So this module is a pure READER — there is deliberately no pin writer. An absent pin
is a reported condition (unpinned → ce-bootstrap fallback-with-warning), not an
error: :func:`harness_pin` returns ``None`` and the doctor renders it.

``pin`` (exact) supersedes the legacy ``min_version`` (a floor) for the version the
fleet actually runs; both are surfaced so a registry mid-migration still reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class HarnessRegistryError(ValueError):
    """A malformed ``runner.harnesses`` registry."""


@dataclass(frozen=True)
class Harness:
    """One harness declaration. ``pin`` is the exact fleet version (None = unpinned)."""

    id: str
    binary: str = ""
    pin: str | None = None
    min_version: str | None = None

    @property
    def pinned(self) -> bool:
        """Whether this harness carries an exact pin."""
        return bool(self.pin)


def _harnesses_raw(config: Mapping[str, Any]) -> list[Any]:
    runner = config.get("runner")
    if runner is None:
        return []
    if not isinstance(runner, Mapping):
        raise HarnessRegistryError("runner must be a mapping")
    harnesses = runner.get("harnesses", [])
    if harnesses in (None, []):
        return []
    if not isinstance(harnesses, list):
        raise HarnessRegistryError("runner.harnesses must be a list")
    return harnesses


def read_harness_registry(config: Mapping[str, Any]) -> tuple[Harness, ...]:
    """Parse ``runner.harnesses`` into :class:`Harness` entries (empty if absent).

    Validates shape only — an entry must be a mapping with a non-empty string ``id``;
    ``binary``/``pin``/``min_version`` are optional strings. A missing ``runner`` or
    ``harnesses`` is an empty registry, not an error (a pre-pin or runner-less config
    is legitimate — the doctor reports unpinned, hvm 1.3).
    """
    out: list[Harness] = []
    for i, raw in enumerate(_harnesses_raw(config)):
        where = f"runner.harnesses[{i}]"
        if not isinstance(raw, Mapping):
            raise HarnessRegistryError(f"{where} must be a mapping")
        hid = raw.get("id")
        if not isinstance(hid, str) or not hid:
            raise HarnessRegistryError(f"{where}.id must be a non-empty string")
        for key in ("binary", "pin", "min_version"):
            val = raw.get(key)
            if val is not None and not isinstance(val, str):
                raise HarnessRegistryError(f"{where}.{key} must be a string")
        out.append(
            Harness(
                id=hid,
                binary=raw.get("binary") or "",
                pin=raw.get("pin") or None,
                min_version=raw.get("min_version") or None,
            )
        )
    return tuple(out)


def find_harness(config: Mapping[str, Any], harness_id: str) -> Harness | None:
    """The :class:`Harness` with *harness_id*, or ``None`` if not declared."""
    return next((h for h in read_harness_registry(config) if h.id == harness_id), None)


def harness_pin(config: Mapping[str, Any], harness_id: str) -> str | None:
    """The exact pin for *harness_id*, or ``None`` if unknown or unpinned.

    The read-point for deploy provisioning (install this version) and the plugin
    doctor (compare the tenant's installed version against it). ``None`` is the
    honest "no pin" state the doctor renders as unpinned — never guessed.
    """
    harness = find_harness(config, harness_id)
    return harness.pin if harness is not None else None


__all__ = [
    "Harness",
    "HarnessRegistryError",
    "find_harness",
    "harness_pin",
    "read_harness_registry",
]
