"""Canonical bus-endpoint addressing (bus-uri-addressing capability).

Pure functions — no filesystem access, no I/O. This is the addressing
layer only: it parses the three accepted input forms into the canonical
URI and reports org/program/agent segments. Whether a target is reachable,
permitted, or a valid broadcast is decided by callers (otaman-cli send
resolution, boundary enforcement), never here.

Accepted input forms (see the bus-uri-addressing spec):

- bare ``<agent>`` — same program; the local org+program supply the
  missing segments. Existing messages/scripts keep working unchanged.
- shorthand ``<agent>@<program>`` — same org; the local org supplies the
  org segment.
- full ``otaman://<org>/<program>/<agent>`` — globally unambiguous.

All three canonicalize to ``otaman://<org>/<program>/<agent>``, which is
the form the envelope stores and any future network transport routes on.
``from_org``/``to_org`` envelope fields are projections of :attr:`BusUri.org`.

Special recipients ``human`` and ``all`` are ordinary program-scoped agent
names here; the broadcast whitelist and boundary checks live in the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: URI scheme prefix. The explicit scheme is what makes an address
#: recognizably NOT a filesystem path to tooling.
SCHEME = "otaman://"

# org, program, and agent segments share the slug grammar already used for
# project/owner names in platform-schema.yaml (`^[a-z][a-z0-9-]{1,63}$`).
_SEGMENT = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


class BusUriError(ValueError):
    """Raised when a bus address is empty, malformed, or has an invalid segment."""


@dataclass(frozen=True)
class BusUri:
    """A parsed, canonical bus endpoint address.

    Instances are always fully qualified: every segment is present and
    validated. Build one with :func:`parse`; ``str(uri)`` returns the
    canonical form.
    """

    org: str
    program: str
    agent: str

    def __str__(self) -> str:
        return f"{SCHEME}{self.org}/{self.program}/{self.agent}"

    def is_cross_org(self, local_org: str) -> bool:
        """True if this endpoint is in a different org than ``local_org``."""
        return self.org != local_org

    def is_cross_program(self, local_org: str, local_program: str) -> bool:
        """True if this endpoint is outside the given local program."""
        return (self.org, self.program) != (local_org, local_program)


def _validate_segment(value: str, kind: str) -> str:
    if not value:
        raise BusUriError(f"empty {kind} segment")
    if not _SEGMENT.match(value):
        raise BusUriError(f"invalid {kind} segment {value!r}: must match [a-z][a-z0-9-]{{1,63}}")
    return value


def parse(raw: str, *, local_org: str, local_program: str) -> BusUri:
    """Canonicalize any accepted input form to a :class:`BusUri`.

    ``local_org`` / ``local_program`` supply the context that bare and
    shorthand forms omit; they are validated too, since they land in the
    result. Raises :class:`BusUriError` on any malformed input.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise BusUriError("empty bus address")
    raw = raw.strip()

    if raw.startswith(SCHEME):
        rest = raw[len(SCHEME) :]
        parts = rest.split("/")
        if len(parts) != 3:
            raise BusUriError(f"full URI must be {SCHEME}<org>/<program>/<agent>, got {raw!r}")
        org, program, agent = parts
    elif "@" in raw:
        if raw.count("@") != 1:
            raise BusUriError(f"shorthand must be <agent>@<program>, got {raw!r}")
        agent, program = raw.split("@")
        org = local_org
    else:
        agent = raw
        program = local_program
        org = local_org

    return BusUri(
        org=_validate_segment(org, "org"),
        program=_validate_segment(program, "program"),
        agent=_validate_segment(agent, "agent"),
    )


def canonicalize(raw: str, *, local_org: str, local_program: str) -> str:
    """Return the canonical URI string for any accepted input form."""
    return str(parse(raw, local_org=local_org, local_program=local_program))


def to_org(raw: str, *, local_org: str, local_program: str) -> str:
    """Extract the org segment — for ``from_org``/``to_org`` projection."""
    return parse(raw, local_org=local_org, local_program=local_program).org
