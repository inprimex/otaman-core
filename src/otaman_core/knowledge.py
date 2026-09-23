"""The one schema + reader/writer for durable agent knowledge (shared-agent-memory).

`.agents/knowledge/` holds durable OPERATIONAL facts — the memory tier distinct
from the bus (transient delivery), the specs repo (ratified artifacts), and
status/sessions (live state). It stayed empty for months because it was a
directory convention with no verb and no entry shape; this module supplies the
shape and the IO, and `otaman knowledge add|list|show` (cli) is the verb over it.

An entry is a markdown file with typed frontmatter and a body::

    ---
    type: lesson                         # fact | lesson | decision | reference
    author: core-agent                   # the agent that recorded it
    created: 2026-09-23
    review-by: 2026-12-23                 # past-due entries render flagged, never
    anchor: src/otaman_core/spawn.py:88  # evidence: file:line, msg stem, or a
    title: worktree sessions need …      #   measured number (the gaq anchor rule)
    ---
    The durable fact, written for the next agent.

Single-home per shared-logic-single-home: this is the ONE parser/writer; every
surface that reads or writes knowledge imports it. It reuses the kernel's own
consolidated primitives — :func:`otaman_core.frontmatter.parse` for the
frontmatter and :func:`otaman_core.bus_stem.slugify` for the filename — rather
than re-deriving either.

Pure schema + local-file IO only: no clock (callers pass ``today``) and no
policy about WHEN to write (that is the incident-priced duty in the generated
instructions, task 1.4).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from otaman_core.bus_stem import slugify
from otaman_core.frontmatter import parse as parse_frontmatter

#: The four entry kinds. A ruling is a ``decision`` (with the approval stem as
#: its anchor); a pointer to an external resource is a ``reference``.
KIND_FACT = "fact"
KIND_LESSON = "lesson"
KIND_DECISION = "decision"
KIND_REFERENCE = "reference"
KINDS = (KIND_FACT, KIND_LESSON, KIND_DECISION, KIND_REFERENCE)

#: The directory (relative to the otaman folder) knowledge lives in.
KNOWLEDGE_DIRNAME = "knowledge"

#: An evidence anchor is one of: a ``file:line`` reference, a bus message stem
#: (``YYYYMMDDTHHMMSS…``), or a measured number. Used to RECOGNISE a well-shaped
#: anchor; the enforced rule (see :func:`validate_entry`) is only that one is
#: PRESENT — "measured number" is too open to hard-match, and the standard
#: forbids silence, not informality.
_ANCHOR_RE = re.compile(
    r"[\w./-]+\.[a-z0-9]+:\d+"  # file:line
    r"|\b\d{8}T\d{6}[-\w]*"  # bus message stem
    r"|\d",  # a digit — the loosest "measured number" floor
    re.IGNORECASE,
)


@dataclass(frozen=True)
class KnowledgeEntry:
    """One durable knowledge entry: typed frontmatter plus its body."""

    type: str
    author: str
    created: str  # ISO date YYYY-MM-DD
    review_by: str  # ISO date YYYY-MM-DD
    anchor: str
    title: str
    body: str = ""

    @property
    def stem(self) -> str:
        """The filename stem an entry is stored under: ``<created>-<slug-of-title>``.

        Deterministic and shared with :func:`write_entry` so the same entry always
        names the same file. Uses the kernel's one slugifier.
        """
        return f"{self.created}-{slugify(self.title)}"


def anchor_is_recognized(anchor: str) -> bool:
    """Whether *anchor* looks like a file:line, a bus stem, or a measured number.

    Advisory only — a surface may warn on an unrecognised-but-present anchor; the
    hard rule is presence (:func:`validate_entry`).
    """
    return bool(_ANCHOR_RE.search(anchor or ""))


def _is_iso_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat((value or "").strip())
    except ValueError:
        return False
    return True


def validate_entry(entry: KnowledgeEntry) -> list[str]:
    """Errors that make *entry* unfit to record, empty when it is fit.

    The anchor rule is the one the standard exists for: an entry with no evidence
    anchor is refused, naming the rule — a durable fact without provenance is the
    thing this memory tier must not accumulate. Type must be one of the four
    kinds; author and title must be present; created/review-by must be ISO dates.
    """
    errors: list[str] = []
    if entry.type not in KINDS:
        errors.append(f"type must be one of {', '.join(KINDS)} (got {entry.type!r})")
    if not entry.author.strip():
        errors.append("author is required — the agent that recorded the entry")
    if not entry.title.strip():
        errors.append("title is required")
    if not _is_iso_date(entry.created):
        errors.append(f"created must be an ISO date (YYYY-MM-DD), got {entry.created!r}")
    if not _is_iso_date(entry.review_by):
        errors.append(f"review-by must be an ISO date (YYYY-MM-DD), got {entry.review_by!r}")
    if not entry.anchor.strip():
        errors.append(
            "evidence anchor is required — a file:line, a bus message stem, or a "
            "measured number; a durable fact must carry its provenance"
        )
    return errors


def is_past_due(entry: KnowledgeEntry, today: str) -> bool:
    """Whether *entry*'s ``review-by`` is before *today* (both ISO ``YYYY-MM-DD``).

    A past-due entry is rendered flagged by ``list`` — corrected or retired, never
    silently trusted forever (the risk-register SLA pattern). A malformed or
    absent ``review-by`` is NOT past-due here; :func:`validate_entry` is what
    catches a bad date.
    """
    if not _is_iso_date(entry.review_by) or not _is_iso_date(today):
        return False
    return datetime.date.fromisoformat(entry.review_by) < datetime.date.fromisoformat(today)


def render_entry(entry: KnowledgeEntry) -> str:
    """The on-disk markdown for *entry*: typed frontmatter then the body.

    Frontmatter is emitted through a YAML dumper so every value is quoted when its
    content requires it. Hand-formatting ``title: {value}`` truncated any title
    carrying ``#`` (a PR ref like ``#73`` — YAML reads it as a comment) and
    mangled one carrying ``:`` — memory silently losing what it was told, in the
    tier meant to hold durable facts (cli-agent 20260923T074504). Field order is
    preserved; a round-trip test guards it.
    """
    front = yaml.safe_dump(
        {
            "type": entry.type,
            "author": entry.author,
            "created": entry.created,
            "review-by": entry.review_by,
            "anchor": entry.anchor,
            "title": entry.title,
        },
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    body = entry.body.strip()
    out = f"---\n{front}\n---\n"
    if body:
        out += f"\n{body}\n"
    return out


def parse_entry(text: str) -> KnowledgeEntry | None:
    """Parse a knowledge file into a :class:`KnowledgeEntry`, or ``None``.

    Returns ``None`` when the text has no frontmatter mapping (so a caller
    iterating the directory skips a stray non-entry file). Field values are read
    through the kernel's typed frontmatter parser; ``review-by`` is accepted with
    either spelling.
    """
    fm, body = parse_frontmatter(text)
    if not fm:
        return None
    review_by = fm.get("review-by")
    if review_by is None:
        review_by = fm.get("review_by")
    return KnowledgeEntry(
        type=str(fm.get("type") or "").strip(),
        author=str(fm.get("author") or "").strip(),
        created=str(fm.get("created") or "").strip(),
        review_by=str(review_by or "").strip(),
        anchor=str(fm.get("anchor") or "").strip(),
        title=str(fm.get("title") or "").strip(),
        body=body.strip(),
    )


def load_entries(knowledge_dir: Path) -> list[KnowledgeEntry]:
    """Every entry in *knowledge_dir*, sorted by created date then title.

    Missing directory yields ``[]``; unreadable or non-entry files are skipped
    (a README in the pile is not an entry).
    """
    if not knowledge_dir.is_dir():
        return []
    entries: list[KnowledgeEntry] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        entry = parse_entry(text)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda e: (e.created, e.title))


def write_entry(knowledge_dir: Path, entry: KnowledgeEntry) -> Path:
    """Write *entry* to ``<knowledge_dir>/<entry.stem>.md`` and return the path.

    Creates the directory if absent. Overwrites an entry of the same stem
    deliberately — the stem is ``<created>-<slug>``, so a re-write of the same
    day's same-titled fact is a correction of that entry, not a second copy.
    """
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    path = knowledge_dir / f"{entry.stem}.md"
    path.write_text(render_entry(entry), encoding="utf-8")
    return path


__all__ = [
    "KINDS",
    "KIND_DECISION",
    "KIND_FACT",
    "KIND_LESSON",
    "KIND_REFERENCE",
    "KNOWLEDGE_DIRNAME",
    "KnowledgeEntry",
    "anchor_is_recognized",
    "is_past_due",
    "load_entries",
    "parse_entry",
    "render_entry",
    "validate_entry",
    "write_entry",
]
