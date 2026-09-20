"""The ONE spec-change-request template, and the refusal that keeps it honest.

Measured: 54 of 119 SCRs ever filed (45%) carry unfilled TODO template
sections. The team-mode SCR — the largest change of its week — was APPROVED
with three of four sections reading TODO, and the drift that predicts actually
happened: two day-one implementation blockers a decision-grade request would
have settled. The counter-population proves the standard works — the incident
SCRs that carried measured repros and file:line anchors (B6, warn-mode-gate,
status-heartbeat) each went filing-to-shipped-fix in under a day.

The template also lived in TWO places (`commands/propose_team.py` and the
plugin's `bus_server.py`) and neither refused an empty section. This module is
the single source; the CLI propose path and the plugin's MCP propose path both
consume it and delete their copies (generated-artifact-quality 1.1/1.2). It
lives in otaman-core because both consumers already depend on core and
otaman-cli depends on otaman-plugin — a plugin import of otaman_cli would be
circular, so core is the only non-circular home for the shared source.

DECISION-GRADE, not a research assignment. Every section is answerable by the
requester from what they already know at filing time. A section that genuinely
does not apply says ``n/a because <reason>`` — an explicit, reviewable claim
that the requester considered it. That escape hatch is the whole reason a
refusal is fair: nothing here forces invention, it only forbids silence.

No scores. A computed completeness FACT and an author-declared evidence level
are the only quality signals; a numeric quality/confidence score is refused
until an independent critic exists to produce one. Precedents: the
RESERVED-slot rule, and the triage scorer that ranked rejected-cheap above
recommended.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: How sure the author is of their own claims. Author-DECLARED, not computed —
#: the point is that the reader knows which it is, not that a number ranks it.
EVIDENCE_LEVELS = ("measured", "reproduced", "observed-once", "inferred")


@dataclass(frozen=True)
class Section:
    """One decision-grade section: its heading and what makes it answerable."""

    key: str
    heading: str
    guidance: str


#: The seven decision-grade sections (proposal.md "What changes", tier 1).
#: Order is the reading order a human decides in: what happened, how do you
#: know, how bad, what now, what is out of scope, who decides, what are you
#: doing meanwhile.
SECTIONS: tuple[Section, ...] = (
    Section(
        "problem",
        "Problem as observed",
        "What you actually saw — the behaviour, not the fix you want.",
    ),
    Section(
        "evidence",
        "Evidence",
        "Per-claim provenance: file:line, requirement name, message stem, or a "
        "measured number. A claim about the existing system without an anchor "
        "is the thing this section exists to prevent.",
    ),
    Section(
        "impact",
        "Impact",
        "Frequency and blast radius — how often, and who or what is affected when it happens.",
    ),
    Section(
        "direction",
        "Proposed direction",
        "Where the fix should go. Not a design; the direction a decision picks.",
    ),
    Section(
        "scope",
        "Scope boundary",
        "What this deliberately does NOT cover, so approving it does not "
        "approve more than you asked.",
    ),
    Section(
        "routing",
        "Routing",
        "Which specs/repos/owners this lands on, and who is the next actor.",
    ),
    Section(
        "workaround",
        "Workaround in use",
        "What you are doing in the meantime — or `n/a because <reason>` when "
        "nothing is needed. Tells the reader how urgent this really is.",
    ),
)

SECTION_KEYS = tuple(s.key for s in SECTIONS)

#: Placeholder markers an author left behind. `TODO` is the historical one from
#: the old template; the others are the shapes people type instead. `\?{2,}` is
#: spelled separately because `\b` after a `?` never matches — `?` is not a word
#: character, so "??? fill this in" slipped through a single alternation.
_PLACEHOLDER_RE = re.compile(r"^\s*(?:(?:TODO|TBD|FIXME|XXX)\b|\?{2,})", re.IGNORECASE)

#: The explicit opt-out: `n/a because <reason>`. Anchored so the REASON is
#: mandatory.
_NA_RE = re.compile(r"^\s*n/?a\b[\s:,-]*because\s+\S+", re.IGNORECASE)

#: Any line that OPENS with n/a (or its synonyms). Combined with `_NA_RE`
#: above, the rule is: opening with n/a commits you to the full
#: `n/a because <reason>` form. "n/a", "n/a because" and "none" are all silence
#: with extra steps, and letting them pass would turn the escape hatch into a
#: way to skip every section.
_OPENS_NA_RE = re.compile(r"^\s*(n/?a|none|nothing)\b", re.IGNORECASE)


def render(
    title: str,
    *,
    sections: dict[str, str] | None = None,
    evidence_level: str | None = None,
) -> str:
    """The SCR body for *title*, filled from *sections* where provided.

    An unprovided section is rendered with its guidance as a prompt rather than
    a bare `TODO:` — a prompt tells the author what would make the section
    answerable, and the refusal below then holds them to it.
    """
    given = dict(sections or {})
    parts: list[str] = [f"## Subject: Spec change request: {title}", ""]
    if evidence_level:
        parts += [f"**Evidence level**: {evidence_level}", ""]
    for section in SECTIONS:
        parts.append(f"### {section.heading}")
        body = (given.get(section.key) or "").strip()
        parts.append(body if body else f"TODO: {section.guidance}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _section_bodies(body: str) -> dict[str, str]:
    """`{section key: its text}` for the sections this body actually carries."""
    found: dict[str, str] = {}
    headings = {s.heading.lower(): s.key for s in SECTIONS}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^#{2,4}\s+(.+?)\s*$", line)
        if match:
            if current is not None:
                found[current] = "\n".join(buf).strip()
            key = headings.get(match.group(1).strip().lower())
            current, buf = key, []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        found[current] = "\n".join(buf).strip()
    return found


def has_template(body: str) -> bool:
    """Does this body use the decision-grade template at all?

    Every SCR filed before this change used the old five headings ("What needs
    to change", "Why this is needed", …), so measuring them against the seven
    new ones reports 0/7 for well-written requests — a false accusation, and
    the confidently-wrong-surface failure that trains readers to ignore a line.
    Format is detected first, and a legacy body is reported as legacy rather
    than as empty.
    """
    return bool(_section_bodies(body))


def unfilled_sections(body: str) -> list[str]:
    """Headings of sections that are missing, empty, or still a placeholder.

    A section whose text starts with ``n/a because <reason>`` counts as FILLED:
    the author made a reviewable claim that it does not apply, which is exactly
    what the standard asks for instead of silence.
    """
    bodies = _section_bodies(body)
    out: list[str] = []
    for section in SECTIONS:
        text = bodies.get(section.key)
        if text is None or not text.strip():
            out.append(section.heading)
            continue
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        if _NA_RE.match(first):
            continue  # declared not-applicable, with a reviewable reason
        if _PLACEHOLDER_RE.match(first) or _OPENS_NA_RE.match(first):
            out.append(section.heading)
    return out


def validate_evidence_level(value: str | None) -> tuple[bool, str]:
    """Is *value* one of the declared evidence levels? (None is allowed.)"""
    if value is None or not str(value).strip():
        return True, ""
    if str(value).strip().lower() in EVIDENCE_LEVELS:
        return True, ""
    return False, (
        f"unknown evidence level {value!r} — expected one of: {', '.join(EVIDENCE_LEVELS)}"
    )


def validate(body: str, *, evidence_level: str | None = None) -> tuple[bool, list[str]]:
    """``(ok, errors)`` for an SCR body. Errors NAME the offending sections.

    Naming them is the point: "your SCR is incomplete" sends the author back to
    read the template, while "Evidence, Impact are still TODO" tells them what
    to type. The refusal is only fair because `n/a because <reason>` is always
    available.
    """
    errors: list[str] = []
    ok_level, level_err = validate_evidence_level(evidence_level)
    if not ok_level:
        errors.append(level_err)
    unfilled = unfilled_sections(body)
    if unfilled:
        errors.append(
            "unfilled section(s): "
            + ", ".join(unfilled)
            + " — fill each, or write 'n/a because <reason>'"
        )
    return (not errors), errors


def is_hollow(body: str) -> tuple[bool, str]:
    """``(hollow, why)`` — is this SCR body empty of decision content?

    Applied at the `otaman send` door, which writes an arbitrary body and so
    cannot simply be re-rendered from the template. plugin-agent found the same
    second entrance on their side (`otaman_send(msg_type='spec-change-request')`
    bypasses `otaman_propose` entirely); closing one door and not the other is
    how the two transports diverged in the first place.

    Deliberately NARROWER than :func:`validate`, which the propose path uses.
    A LEGACY-shaped body carrying real content passes: every SCR filed before
    the template uses the old five headings, and refusing those at a shared
    fleet door would break senders over a format change rather than over
    hollowness. What is refused is the thing the standard is actually about —
    a body that ANSWERS NOTHING.
    """
    if has_template(body):
        unfilled = unfilled_sections(body)
        if unfilled:
            return True, "unfilled section(s): " + ", ".join(unfilled)
        return False, ""
    # Not the template: refuse only if no line carries substance.
    for line in body.splitlines():
        text = line.strip()
        if not text or text.startswith("#") or text.startswith("## Subject:"):
            continue
        if _PLACEHOLDER_RE.match(text) or _OPENS_NA_RE.match(text):
            continue
        return False, ""
    return True, "every line is a placeholder — the request answers nothing"


def completeness(body: str) -> dict:
    """The FACT line inputs for the console approve view (1.3).

    Deliberately no score. `filled/total` and an anchor count are observations
    a reader can check; a single number would invite ranking SCRs by it, which
    is refused until an independent critic exists to produce one.
    """
    unfilled = unfilled_sections(body)
    total = len(SECTIONS)
    anchors = len(
        re.findall(
            r"[\w./-]+\.(?:py|md|yaml|yml|ts|tsx|js|json|toml):\d+"  # file:line
            r"|\b\d{8}T\d{6}[-\w]*"  # bus message stem
            r"|#### Scenario:|### Requirement:",  # requirement/scenario name
            body,
        )
    )
    return {
        "template": has_template(body),
        "sections_total": total,
        "sections_filled": total - len(unfilled),
        "unfilled": unfilled,
        "anchors": anchors,
        "evidence_level": declared_evidence_level(body),
    }


def declared_evidence_level(body: str) -> str | None:
    match = re.search(r"\*\*Evidence level\*\*:\s*([\w-]+)", body, re.IGNORECASE)
    return match.group(1).lower() if match else None


def completeness_line(body: str) -> str:
    """One FACT line, never a score — e.g.
    ``sections 5/7 filled (Evidence, Impact still TODO) · 3 anchors · evidence: measured``
    """
    facts = completeness(body)
    if not facts["template"]:
        # Legacy body: say so. "0/7 filled" here would be a measurement of the
        # template's absence, not of the author's work.
        parts = ["legacy format (filed before the decision-grade template)"]
    else:
        parts = [f"sections {facts['sections_filled']}/{facts['sections_total']} filled"]
        if facts["unfilled"]:
            parts[0] += f" ({', '.join(facts['unfilled'])} still TODO)"
    parts.append(f"{facts['anchors']} anchor{'s' if facts['anchors'] != 1 else ''}")
    if facts["evidence_level"]:
        parts.append(f"evidence: {facts['evidence_level']}")
    return " · ".join(parts)


__all__ = [
    "EVIDENCE_LEVELS",
    "SECTIONS",
    "SECTION_KEYS",
    "Section",
    "completeness",
    "completeness_line",
    "declared_evidence_level",
    "has_template",
    "is_hollow",
    "render",
    "unfilled_sections",
    "validate",
    "validate_evidence_level",
]
