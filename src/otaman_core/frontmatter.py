"""The one typed parser for bus-message YAML frontmatter.

Homed here per the shared-logic-single-home rule: seven frontmatter parsers
were scattered across otaman-cli and otaman-plugin, none in core, and they
DISAGREED on types. plugin's parser (servers/bus_server.py) was a regex
line-splitter that returned every value as a string — so ``cc: [spec-agent]``
came back as the string ``"[spec-agent]"`` and ``x-cc: true`` as the string
``"true"``, forcing a bespoke ``_parse_cc_field()`` and a ``== "true"`` check to
recover the types its own parser had thrown away. cli used real YAML and got a
list and a bool. Same file, different typed result depending on which transport
read it — the exact divergence this change removes.

This module parses frontmatter as YAML (typed), with the fastest safe loader
available, and gives the two protocol fields that are NOT scalars their canonical
readers: ``cc`` is a list of agent names (bus-message-cc-field), and ``x-cc`` is
the boolean CC-copy marker. Every other frontmatter field is a scalar and comes
back as whatever YAML types it (str/int/None). Pure: text in, ``(dict, body)``
out — no file I/O, so callers keep their own reading and caching.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import yaml

#: The fastest available safe loader. libyaml (CSafeLoader) is present wherever
#: PyYAML ships its C extension; the pure-Python class is the fallback so this
#: module never depends on the build. Fast typing is also why plugin no longer
#: needs a regex parser to avoid the slow loader.
_LOADER = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader

#: `---\n<frontmatter>\n---` at the top of the file, then the body. Tolerates a
#: trailing space after either fence, CRLF line endings, empty frontmatter, and
#: an absent trailing newline / empty body.
_FRONTMATTER_RE = re.compile(
    r"^---[ \t]*\r?\n(.*?)\r?\n?---[ \t]*(?:\r?\n(.*))?$",
    re.DOTALL,
)


def parse(text: str) -> tuple[dict[str, Any], str]:
    """``(frontmatter, body)`` for a bus message.

    The frontmatter is YAML-typed: ``cc`` is a list, ``x-cc`` a bool, and every
    scalar field is whatever YAML makes it. Returns ``({}, text)`` when there is
    no leading ``---`` fence or the block is not a YAML mapping — the message is
    treated as bodied-but-frontmatterless rather than raising, since a read
    surface iterating a bus directory must skip non-messages, not crash on one.
    """
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, text or ""
    block = match.group(1) or ""
    body = match.group(2) or ""
    try:
        parsed = yaml.load(block, Loader=_LOADER)  # noqa: S506 - safe loader, not full
    except yaml.YAMLError:
        return {}, text or ""
    if not isinstance(parsed, dict):
        return {}, text or ""
    return parsed, body


def cc_recipients(fm: dict[str, Any]) -> list[str]:
    """The ``cc:`` list as agent names, whatever shape it took on disk.

    A YAML list ``cc: [a, b]`` or block list is already a list; a lone scalar
    ``cc: a`` becomes ``["a"]``; absent, empty, or blank yields ``[]``. This is
    the reader that replaces plugin's ``_parse_cc_field`` — the same normalization
    for every transport.
    """
    value = fm.get("cc")
    if value is None:
        return []
    if isinstance(value, str):
        name = value.strip()
        return [name] if name else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def is_cc_copy(fm: dict[str, Any]) -> bool:
    """Whether this message is a delivered CC copy (``x-cc: true``).

    True for the boolean ``x-cc: true`` a YAML parser yields, and for the string
    ``"true"`` a type-losing parser (or a hand-edited file) may leave behind, so
    a copy written by either transport reads the same. Absent means primary.
    """
    value = fm.get("x-cc")
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def parse_bus_timestamp(value: Any) -> datetime | None:
    """Parse a bus-message ``timestamp:`` value into an aware datetime, or ``None``.

    The canonical reader for the frontmatter ``timestamp:`` field, homed once per the
    shared-logic-single-home rule: ``task_complete`` and cli's ``cleanup_bus`` each
    parsed it inline and drifted — cli's three-``strptime`` parser rejected the
    FRACTIONAL SECONDS the producer's ``datetime.now(UTC).isoformat()`` emits, so
    ``otaman cleanup`` aged 98.4% of the bus as unparseable and archived nothing
    (deploy root-cause 20261001T142130).

    Accepts the producer's ISO-8601 via :meth:`datetime.fromisoformat` (with or
    without fractional seconds), normalizing a trailing ``Z`` to ``+00:00``. A value
    that is ALREADY a :class:`datetime` is returned as-is — a YAML frontmatter loader
    types an ISO timestamp into a datetime, while the regex/string readers hand over a
    str, and both callers use this one parser. Returns ``None`` for an absent, blank,
    or unparseable value — the honest "no usable timestamp" the writer validation
    (:mod:`otaman_core.validate_message`) now refuses up front, rather than a message
    that cannot be aged or ordered.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "cc_recipients",
    "is_cc_copy",
    "parse",
    "parse_bus_timestamp",
]
