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
from dataclasses import dataclass, replace
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

#: Lifecycle states (knowledge-v2). ``active`` is in the index; ``dormant`` has
#: decayed (past review-by, unreinforced) and is excluded from the index but
#: reversible; ``retired`` is deliberately withdrawn. Corrections are an EDGE
#: (``supersedes``), never a state — a superseded entry stays as it was written.
STATE_ACTIVE = "active"
STATE_DORMANT = "dormant"
STATE_RETIRED = "retired"
STATES = (STATE_ACTIVE, STATE_DORMANT, STATE_RETIRED)

#: The fixed, platform-owned ``function:`` enum — the knowledge partitions.
#: Exactly these 8 (Roman's taxonomy ruling 2026-09-23, pinned in the delta,
#: otaman-specs PR #484): additions are canon changes, never program-defined —
#: 'industry domain = vocabulary, function = fixed enum'. The partition-owner map
#: (``program.processes.knowledge.partitions``) is separate and sparser: only
#: some functions have an owner today, but the enum is the full 8. Distinct from
#: ``domain:``, which is validated against the PROGRAM's vocabulary registry (cli).
FUNCTION_MARKETING = "marketing"
FUNCTION_SALES = "sales"
FUNCTION_RESEARCH = "research"
FUNCTION_ANALYSIS = "analysis"
FUNCTION_DESIGN = "design"
FUNCTION_DEVELOPMENT = "development"
FUNCTION_QUALITY = "quality"
FUNCTION_SUPPORT = "support"
FUNCTIONS = (
    FUNCTION_MARKETING,
    FUNCTION_SALES,
    FUNCTION_RESEARCH,
    FUNCTION_ANALYSIS,
    FUNCTION_DESIGN,
    FUNCTION_DEVELOPMENT,
    FUNCTION_QUALITY,
    FUNCTION_SUPPORT,
)

#: How an amended (superseded) entry is flagged on every read surface.
AMENDED_FLAG = "[amended]"

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
    """One durable knowledge entry: typed frontmatter plus its body.

    knowledge-v2 fields (all default so v1 entries still parse):
    ``state`` (lifecycle), ``function`` (partition, fixed enum), ``domain``
    (program-vocabulary tag), ``supersedes`` (the amend edge — the stem this entry
    corrects), and ``accessed_at`` (last-read date; the decay-reinforcement signal).
    """

    type: str
    author: str
    created: str  # ISO date YYYY-MM-DD
    review_by: str  # ISO date YYYY-MM-DD
    anchor: str
    title: str
    body: str = ""
    state: str = STATE_ACTIVE
    function: str = ""
    domain: str = ""
    supersedes: str = ""  # stem of the entry this one corrects (the amend edge)
    accessed_at: str = ""  # ISO date of last access; bumped by `show`

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
    kinds; author and title must be present; created/review-by must be ISO dates;
    state must be a known lifecycle state; function, WHEN SET, must be one of the
    fixed partition enum — an empty function is a legacy/unassigned entry, read
    not rejected. ``domain`` is validated by the CLI against the program's
    vocabulary registry, not here.
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
    if entry.state not in STATES:
        errors.append(f"state must be one of {', '.join(STATES)} (got {entry.state!r})")
    # function is validated WHEN SET; an empty function is a legacy/unassigned
    # entry — read, not rejected (the blocked_entries.kind precedent: a field
    # added after entries existed is tolerated, not invalidated). The six v1
    # entries and gate 4.1's amend subject must stay operable; a NEW entry gets a
    # real function at the write surface (cli derives it from the partition map).
    if entry.function and entry.function not in FUNCTIONS:
        errors.append(
            f"function, when set, must be one of the fixed partitions "
            f"{', '.join(FUNCTIONS)} (got {entry.function!r})"
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
    fm: dict[str, str] = {
        "type": entry.type,
        "author": entry.author,
        "created": entry.created,
        "review-by": entry.review_by,
        "anchor": entry.anchor,
        "title": entry.title,
        "state": entry.state,
    }
    # Optional knowledge-v2 fields — emitted only when set, to keep frontmatter
    # clean (Obsidian-compatible) and to round-trip absent==default.
    for key, value in (
        ("function", entry.function),
        ("domain", entry.domain),
        ("supersedes", entry.supersedes),
        ("accessed-at", entry.accessed_at),
    ):
        if value:
            fm[key] = value
    front = yaml.safe_dump(
        fm,
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

    def _field(*keys: str) -> str:
        for key in keys:
            value = fm.get(key)
            if value is not None:
                return str(value).strip()
        return ""

    return KnowledgeEntry(
        type=_field("type"),
        author=_field("author"),
        created=_field("created"),
        review_by=_field("review-by", "review_by"),
        anchor=_field("anchor"),
        title=_field("title"),
        body=body.strip(),
        state=_field("state") or STATE_ACTIVE,
        function=_field("function"),
        domain=_field("domain"),
        supersedes=_field("supersedes"),
        accessed_at=_field("accessed-at", "accessed_at"),
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


def load_entry_by_stem(knowledge_dir: Path, stem: str) -> KnowledgeEntry | None:
    """The entry stored at ``<knowledge_dir>/<stem>.md``, or ``None`` if absent."""
    path = knowledge_dir / f"{stem}.md"
    if not path.is_file():
        return None
    try:
        return parse_entry(path.read_text(encoding="utf-8"))
    except OSError:
        return None


# ---------------------------------------------------------------------------
# The amend edge (knowledge-v2): correction is an edge, never an in-place edit.


class KnowledgeError(ValueError):
    """A knowledge operation that cannot be performed as asked."""


def amend_entry(knowledge_dir: Path, supersedes_stem: str, correcting: KnowledgeEntry) -> Path:
    """Record *correcting* as the entry that supersedes ``supersedes_stem``.

    Writes ONE file — the correcting entry carrying its ``supersedes:`` pointer —
    so the edge cannot be half-written (design D1). The superseded entry is NEVER
    rewritten or deleted: its bytes are the reader-level record of what was
    believed, and the ``[amended]`` flag + reverse cross-reference are DERIVED
    (:func:`is_amended`, :func:`superseded_by`) by scanning for who supersedes it.
    Raises :class:`KnowledgeError` if the superseded stem does not exist — an edge
    to nothing is the silent-dangling-pointer this forbids.
    """
    if load_entry_by_stem(knowledge_dir, supersedes_stem) is None:
        raise KnowledgeError(
            f"cannot amend {supersedes_stem!r}: no such entry to supersede "
            f"(an amend edge must point at an existing entry)"
        )
    return write_entry(knowledge_dir, replace(correcting, supersedes=supersedes_stem))


def superseded_by(entries: list[KnowledgeEntry], stem: str) -> list[str]:
    """Stems of entries whose ``supersedes`` points at *stem* (the reverse edge)."""
    return [e.stem for e in entries if e.supersedes == stem]


def is_amended(entry: KnowledgeEntry, entries: list[KnowledgeEntry]) -> bool:
    """Whether some entry in *entries* supersedes *entry* (renders ``[amended]``)."""
    return any(e.supersedes == entry.stem for e in entries)


# ---------------------------------------------------------------------------
# Lifecycle mutations (accessed-at reinforcement, decay to dormant). These DO
# rewrite the entry — they are metadata, not content; the no-in-place-edit rule
# is about CORRECTING facts (use amend), not about lifecycle bookkeeping.


def mark_accessed(knowledge_dir: Path, stem: str, accessed: str) -> Path | None:
    """Bump *stem*'s ``accessed_at`` to *accessed* (the decay-reinforcement signal).

    Returns the path written, or ``None`` if the entry is absent. Called by
    ``show``; a recently-accessed entry survives the decay sweep.
    """
    entry = load_entry_by_stem(knowledge_dir, stem)
    if entry is None:
        return None
    return write_entry(knowledge_dir, replace(entry, accessed_at=accessed))


def set_state(knowledge_dir: Path, stem: str, state: str) -> Path | None:
    """Move *stem* to *state* (e.g. the decay sweep to ``dormant``), reversibly.

    Returns the path written, or ``None`` if the entry is absent. Raises
    :class:`KnowledgeError` for an unknown state — a decay sweep must not invent
    one. The caller states the reason (loud, per no-silent-success); it is not
    persisted on the entry, only surfaced by the sweep.
    """
    if state not in STATES:
        raise KnowledgeError(f"unknown state {state!r} — expected one of {', '.join(STATES)}")
    entry = load_entry_by_stem(knowledge_dir, stem)
    if entry is None:
        return None
    return write_entry(knowledge_dir, replace(entry, state=state))


# ---------------------------------------------------------------------------
# The index (knowledge-v2): the ONE curated line every surface renders, and the
# scoping that keeps context flat as the corpus grows.


def active_entries(entries: list[KnowledgeEntry]) -> list[KnowledgeEntry]:
    """Only the ``active`` entries — the index preloads these, never dormant/retired."""
    return [e for e in entries if e.state == STATE_ACTIVE]


def in_partition(entries: list[KnowledgeEntry], function: str) -> list[KnowledgeEntry]:
    """Entries in the *function* partition — the per-agent index scoping unit."""
    return [e for e in entries if e.function == function]


def index_line(entry: KnowledgeEntry, *, amended: bool = False) -> str:
    """The one curated index line for *entry*: ``<id>  [<type>/<state>]  <title>``.

    The single-home index-line format every surface renders (``list``, generated
    instructions, and — per D5 — any future curated-index feature). ``id`` is the
    stem, ``description`` is the owner-authored title. Pass ``amended=True`` (from
    :func:`is_amended`) to append the ``[amended]`` flag; the caller adds any
    review-overdue flag, which needs today's date this pure formatter does not take.
    """
    line = f"{entry.stem}  [{entry.type}/{entry.state}]  {entry.title}"
    return f"{line} {AMENDED_FLAG}" if amended else line


__all__ = [
    "AMENDED_FLAG",
    "FUNCTIONS",
    "FUNCTION_ANALYSIS",
    "FUNCTION_DESIGN",
    "FUNCTION_DEVELOPMENT",
    "FUNCTION_MARKETING",
    "FUNCTION_QUALITY",
    "FUNCTION_RESEARCH",
    "FUNCTION_SALES",
    "FUNCTION_SUPPORT",
    "KINDS",
    "KIND_DECISION",
    "KIND_FACT",
    "KIND_LESSON",
    "KIND_REFERENCE",
    "KNOWLEDGE_DIRNAME",
    "STATES",
    "STATE_ACTIVE",
    "STATE_DORMANT",
    "STATE_RETIRED",
    "KnowledgeEntry",
    "KnowledgeError",
    "active_entries",
    "amend_entry",
    "anchor_is_recognized",
    "in_partition",
    "index_line",
    "is_amended",
    "is_past_due",
    "load_entries",
    "load_entry_by_stem",
    "mark_accessed",
    "parse_entry",
    "render_entry",
    "set_state",
    "superseded_by",
    "validate_entry",
    "write_entry",
]
