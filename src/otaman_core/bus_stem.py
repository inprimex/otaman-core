"""The one writer/parser pair for the bus filename/stem convention.

A bus message file is named::

    <YYYYMMDDTHHMMSS>-<sender>-to-<recipient>-<slug>.md

Four sites hand-built that string (cc-copy naming, spec-change notify, the
console decision audit, the lifecycle nudge) and several more hand-parsed it,
each with its own timestamp regex and its own slugifier — the drift this rule
(shared-logic-single-home) removes. This module is the single home: one
:func:`build_stem` that owns the ``-to-`` route format and ordering, one shared
:func:`slugify`, and one :func:`parse_stem` that reads a stem back.

The recipient/slug boundary is genuinely ambiguous from the string alone —
``sender``, ``recipient`` and ``slug`` all carry hyphens (``core-agent``,
``spec-change-approved``) and there is no reserved delimiter between recipient
and slug. So :func:`parse_stem` recovers the timestamp and sender unconditionally
and resolves ``recipient``/``slug`` only when handed the set of known recipients
(the agent registry). That is exactly what makes it correct where a
first-hyphen split would guess wrong; a field a caller cannot resolve is left
``None`` rather than approximated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The compact, citeable timestamp form (`approved_by` notes and the disposition
#: ledger reference a broadcast by this). A stem's leading token may carry extra
#: digits (a same-second counter); the compact form is the first 15 chars.
TIMESTAMP_RE = re.compile(r"\d{8}T\d{6}")

#: `<leading-time-token>-<rest>`. The time token is the compact stamp plus any
#: trailing counter digits; everything after the first hyphen is the route+slug.
_LEADING_TS_RE = re.compile(r"^(\d{8}T\d{6}\d*)-(.*)$", re.DOTALL)

#: Splits the route from the recipient+slug tail. A literal, unambiguous anchor —
#: sender and recipient may both contain hyphens, but the `-to-` join does not.
_ROUTE_SEP = "-to-"


def slugify(text: str, *, max_len: int | None = None, fallback: str = "item") -> str:
    """A filename-safe slug: lowercase, non-alphanumerics collapsed to hyphens.

    ``max_len`` truncates (then re-trims a trailing hyphen) for the sites that
    cap the slug; ``fallback`` is returned when the input slugs to nothing, so a
    stem never ends in a bare ``-``.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if max_len is not None and len(slug) > max_len:
        slug = slug[:max_len].strip("-")
    return slug or fallback


def _safe(segment: str) -> str:
    """A single stem segment with the path separator neutralized.

    A recipient like ``org/team`` would otherwise open a subdirectory in the
    filename; ``/`` becomes ``-`` (the cc-copy writer already did this).
    """
    return str(segment).replace("/", "-")


def build_stem(*, timestamp: str, sender: str, recipient: str, slug: str) -> str:
    """The stem ``<timestamp>-<sender>-to-<recipient>-<slug>`` (no extension).

    *slug* is used verbatim — compose it with :func:`slugify` (and any fixed
    prefix like ``nudge-`` or ``console-<verb>-``) before calling, so every site
    slugs the same way.
    """
    return f"{timestamp}-{_safe(sender)}{_ROUTE_SEP}{_safe(recipient)}-{slug}"


def build_filename(*, timestamp: str, sender: str, recipient: str, slug: str) -> str:
    """:func:`build_stem` plus the ``.md`` extension."""
    return build_stem(timestamp=timestamp, sender=sender, recipient=recipient, slug=slug) + ".md"


@dataclass(frozen=True)
class ParsedStem:
    """A bus stem read back into its parts.

    ``timestamp`` and ``sender`` are always recovered. ``recipient`` and ``slug``
    are set only when :func:`parse_stem` was given the known recipients to
    disambiguate the tail; otherwise they are ``None`` and ``tail`` carries the
    raw ``<recipient>-<slug>`` for a caller that wants to match it another way.
    """

    timestamp: str
    sender: str
    recipient: str | None
    slug: str | None
    tail: str


def _split_recipient(tail: str, known_recipients: set[str] | None) -> tuple[str | None, str | None]:
    """``(recipient, slug)`` from *tail*, using *known_recipients* to disambiguate.

    The LONGEST known recipient that is *tail* itself or a hyphen-prefix of it
    wins — so ``core-agent`` beats ``core`` on ``core-agent-request-…`` and the
    slug is what remains. Without the set the boundary is unknowable, so both are
    ``None``.
    """
    if not known_recipients:
        return None, None
    best: str | None = None
    for name in known_recipients:
        is_prefix = tail == name or tail.startswith(f"{name}-")
        if is_prefix and (best is None or len(name) > len(best)):
            best = name
    if best is None:
        return None, None
    slug = tail[len(best) :].lstrip("-")
    return best, (slug or None)


def parse_stem(name: str, *, known_recipients: set[str] | None = None) -> ParsedStem | None:
    """Parse a bus stem (or ``…​.md`` filename), or ``None`` if it is not one.

    Returns ``None`` when there is no leading ``YYYYMMDDTHHMMSS`` timestamp — the
    single reliable marker of a bus stem — so a caller iterating a directory can
    skip non-messages. ``recipient``/``slug`` are resolved only when
    *known_recipients* is supplied (see :func:`_split_recipient`).
    """
    stem = name[:-3] if name.endswith(".md") else name
    match = _LEADING_TS_RE.match(stem)
    if not match:
        return None
    ts_token, remainder = match.group(1), match.group(2)
    ts_match = TIMESTAMP_RE.match(ts_token)
    timestamp = ts_match.group() if ts_match else ts_token
    sender, sep, tail = remainder.partition(_ROUTE_SEP)
    if not sep:
        # No `-to-` route in the stem (e.g. a non-message file that still leads
        # with a timestamp); sender is unknowable, recipient/slug absent.
        return ParsedStem(timestamp=timestamp, sender="", recipient=None, slug=None, tail=remainder)
    recipient, slug = _split_recipient(tail, known_recipients)
    return ParsedStem(timestamp=timestamp, sender=sender, recipient=recipient, slug=slug, tail=tail)


def timestamp_of(name: str) -> str:
    """The compact ``YYYYMMDDTHHMMSS`` a stem leads with, or ``""``.

    The narrow reader for the many sites that only need the citeable timestamp
    (approval ledgers, lifecycle rows) without the rest of the parse.
    """
    match = TIMESTAMP_RE.match(name)
    return match.group() if match else ""


__all__ = [
    "TIMESTAMP_RE",
    "ParsedStem",
    "build_filename",
    "build_stem",
    "parse_stem",
    "slugify",
    "timestamp_of",
]
