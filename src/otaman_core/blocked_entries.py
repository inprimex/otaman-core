"""The one parser/writer for `.agents/blocked/<agent>.md` entries.

Homed here per the single-home rule (shared-logic-single-home): logic that
interprets or produces a shared contract has exactly one implementation, in
otaman-core, and every consumer imports it. Blocked-entry state is read by
CLI subcommands, the console, and MCP (`otaman_check`); a surface-local regex
is a conformance defect, and it is the reason the surface drifted — the
founding defect of this change is an entry only one transport could see.

Entries were parsed by ad-hoc regexes in at least five places, each with its
own idea of what an entry is and how to recognise a tombstone. This module is
the single definition. It is pure text-in/text-out: no I/O, no clock, no
policy about WHEN an entry terminates — callers own that.

Entry shape (additive only):

    ## Blocked: <human title>
    - **Proposal**: 20260913T144246-cli-agent-to-human-spec-change-request
    - **Kind**: awaiting-approval          <- inferred when absent
    - **Blocked since**: 2026-09-13T14:42:46Z

Well-formedness (shared-logic-single-home, D2): the well-formed header is
``## Blocked: `` — the trailing SPACE and a non-empty title, which is the
reference hook's own test (check-blocked.sh:96, ``'## Blocked: '*``). The bare
``^## Blocked:`` is the DETECTION FLOOR, not the shape: an entry matching the
floor but not the form is surfaced as ``[malformed]`` (``display_title``) and
kept — visibility over silence. It stays parseable and terminable, because an
entry that is invisible in one transport, or permanently un-tombstonable, is
exactly the failure this change forbids.

A TOMBSTONED entry is the same block wrapped in an HTML comment with a
``cleared <date> — <reason>`` trailer — plugin's format, matched here rather
than redefined, so an entry cleared by either side reads the same to both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: An entry waiting on a human approval decision. Task completion NEVER clears
#: one of these (the ruling) — only the approval/rejection, or the archive sweep.
KIND_APPROVAL = "awaiting-approval"
#: An entry waiting on another task/change. Cleared when that work completes.
KIND_DEPENDENCY = "awaiting-dependency"

KINDS = (KIND_APPROVAL, KIND_DEPENDENCY)

#: How a malformed entry surfaces on every read surface (shared-logic-single-home).
MALFORMED_TITLE = "[malformed]"

#: The DETECTION FLOOR: a live entry starts a line; a tombstoned one is preceded
#: by the comment open. Deliberately matches ``## Blocked:`` without demanding the
#: well-formed ``## Blocked: <title>`` shape, so a malformed entry is FOUND (and
#: surfaced) rather than silently skipped.
_ENTRY_RE = re.compile(r"^(<!--\s*)?## Blocked:(.*?)(?=^(?:<!--\s*)?## Blocked:|\Z)", re.M | re.S)
_FIELD_RE = re.compile(r"^\s*-\s*\*\*(?P<key>[^*]+)\*\*:\s*(?P<value>.*?)\s*$", re.M)
_CLEARED_RE = re.compile(r"cleared\s+(\d{4}-\d{2}-\d{2})\s*—\s*(?P<reason>[^>]*?)\s*-->")


@dataclass(frozen=True)
class BlockedEntry:
    """One `## Blocked:` block, parsed."""

    title: str
    block: str
    fields: dict[str, str] = field(default_factory=dict)
    tombstoned: bool = False
    cleared_reason: str = ""
    malformed: bool = False

    def get(self, key: str) -> str:
        """A field's value, case-insensitively (``Proposal`` == ``proposal``)."""
        return self.fields.get(key.strip().lower(), "")

    @property
    def proposal(self) -> str:
        """The proposal message stem — the stable ref for an approval wait."""
        return self.get("proposal")

    @property
    def change(self) -> str:
        """The change slug — the stable ref for a dependency wait."""
        return self.get("change")

    @property
    def display_title(self) -> str:
        """What every read surface SHOWS for this entry.

        ``[malformed]`` for an entry that matched the detection floor but not the
        well-formed shape (empty or space-less title), so the same flag appears
        identically on the CLI, the console, and MCP — never a blank line one
        surface silently drops.
        """
        return MALFORMED_TITLE if self.malformed else self.title

    @property
    def kind(self) -> str:
        """The entry's kind, INFERRED when the field is absent.

        Legacy entries predate the field, so they are read, not rejected: one
        carrying a proposal ref is an approval wait — that is what
        `otaman propose` writes and what its text says ("waiting for human
        approval") — and anything else is a dependency wait.
        """
        declared = self.get("kind").lower()
        if declared in KINDS:
            return declared
        return KIND_APPROVAL if self.proposal else KIND_DEPENDENCY

    @property
    def ref(self) -> str:
        """The stable identifier this entry terminates against, or ``""``.

        Proposal first: an approval wait is keyed by its proposal stem, which is
        what the existing matcher (plugin's ``_auto_tombstone_blocked``) uses.
        """
        return self.proposal or self.change

    @property
    def has_ref(self) -> bool:
        return bool(self.ref)


def parse_entries(text: str, *, include_tombstoned: bool = False) -> list[BlockedEntry]:
    """Every entry in *text*, malformed ones included and flagged.

    Tombstoned entries are EXCLUDED by default — they are already terminated, and
    every read-side caller wants live blocks. Migration passes
    ``include_tombstoned=True`` precisely so it can recognise them as done and
    never "migrate" them back into the live set.

    A malformed entry (matched the ``## Blocked:`` floor but missing the trailing
    space or a non-empty title) is returned with ``malformed=True`` rather than
    dropped: no read surface may make it silently disappear.
    """
    out: list[BlockedEntry] = []
    for match in _ENTRY_RE.finditer(text or ""):
        commented = bool(match.group(1))
        body = match.group(2)
        block = match.group(0)
        first_line = (body.splitlines() or [""])[0]
        title = first_line.strip()
        # Well-formed is `## Blocked: <title>`: the trailing space AND a non-empty
        # title (check-blocked.sh:96). Anything else matched only the floor.
        malformed = not (first_line.startswith(" ") and title)
        # a tombstoned block ends with `cleared <date> — <reason> -->`
        cleared = _CLEARED_RE.search(block)
        tombstoned = commented or bool(cleared)
        if tombstoned and not include_tombstoned:
            continue
        fields = {
            m.group("key").strip().lower(): m.group("value").strip()
            for m in _FIELD_RE.finditer(body)
        }
        out.append(
            BlockedEntry(
                title=title,
                block=block,
                fields=fields,
                tombstoned=tombstoned,
                cleared_reason=(cleared.group("reason").strip() if cleared else ""),
                malformed=malformed,
            )
        )
    return out


def render_entry(
    title: str,
    *,
    kind: str = "",
    ref: str = "",
    ref_label: str = "Proposal",
    since: str = "",
    extra: dict[str, str] | None = None,
) -> str:
    """One entry block, with the ``Kind`` field.

    ``kind`` defaults from the presence of a ref label: a Proposal ref means an
    approval wait. Fields other than Kind keep their existing names and order so
    the existing matcher — and anything already parsing these files — is
    untouched.
    """
    if kind not in KINDS:
        kind = KIND_APPROVAL if ref_label == "Proposal" else KIND_DEPENDENCY
    lines = [f"## Blocked: {title}"]
    if ref:
        lines.append(f"- **{ref_label}**: {ref}")
    lines.append(f"- **Kind**: {kind}")
    if since:
        lines.append(f"- **Blocked since**: {since}")
    for key, value in (extra or {}).items():
        lines.append(f"- **{key}**: {value}")
    return "\n".join(lines) + "\n"


def tombstone(text: str, entries: list[BlockedEntry], *, reason: str, today: str) -> str:
    """Wrap each of *entries* in plugin's tombstone comment, returning new text.

    Deliberately the SAME format plugin's ``_auto_tombstone_blocked`` writes —
    an entry cleared by either side must read identically to both, and clearing
    must never destroy the record of why. Entries already tombstoned are left
    alone, so this is idempotent. A malformed entry is still terminable: it is
    matched by its captured block like any other, so nothing becomes permanently
    un-tombstonable.
    """
    out = text or ""
    for entry in entries:
        if entry.tombstoned or entry.block not in out:
            continue
        # parse_entries captures the trailing blank line(s) into entry.block; a
        # bare rstrip() here would eat them, gluing the closing `-->` onto the
        # NEXT entry's header so it is no longer line-leading and parse_entries
        # cannot see it — clearing one entry would silently hide every live entry
        # after it (plugin-agent repro 20260921T151120), the exact failure this
        # module exists to kill. Preserve the separator by re-appending it.
        stripped = entry.block.rstrip()
        trailing = entry.block[len(stripped) :]
        trailer = f"\ncleared {today} — {reason} -->"
        out = out.replace(entry.block, "<!-- " + stripped + trailer + trailing, 1)
    return out


def stale_reason(entry: BlockedEntry, *, known_refs: set[str]) -> str:
    """Why *entry* cannot be resolved, or ``""`` when it is live.

    An entry is STALE when nothing it points at can be found: no ref recorded at
    all, or a ref that matches no known proposal/change. Stale is deliberately
    *reported*, never auto-removed — the whole defect being fixed here is entries
    disappearing or persisting without anyone being able to tell which, so the
    surface says which it is and a human decides.

    Pure: the caller supplies *known_refs* (bus message stems, change slugs),
    which is the only part that needs I/O.
    """
    if not entry.has_ref:
        return "no stable ref recorded — cannot be resolved to a proposal or change"
    if entry.ref not in known_refs:
        label = "proposal" if entry.kind == KIND_APPROVAL else "change"
        return f"{label} {entry.ref!r} not found — it may have been archived or renamed"
    return ""


def find_by_ref(text: str, ref: str, *, kinds: tuple[str, ...] | None = None) -> list[BlockedEntry]:
    """Live entries whose stable ref equals *ref*, optionally limited to *kinds*.

    Ref matching — NOT the change-name-in-title substring match that could never
    fire. Exact, because a stable id is exact; a substring match over free text is
    how an unrelated entry gets cleared by someone else's completion.
    """
    ref = (ref or "").strip()
    if not ref:
        return []
    found = [e for e in parse_entries(text) if e.ref == ref]
    if kinds is not None:
        found = [e for e in found if e.kind in kinds]
    return found


__all__ = [
    "KINDS",
    "KIND_APPROVAL",
    "KIND_DEPENDENCY",
    "MALFORMED_TITLE",
    "BlockedEntry",
    "find_by_ref",
    "parse_entries",
    "render_entry",
    "stale_reason",
    "tombstone",
]
