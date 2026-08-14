"""WikiEntity — data type for a single wiki entity file."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import yaml

EntityKind = Literal["module", "component", "code-unit"]

# (relation_label, target_entity_id, target_display_title)
Relation = tuple[str, str, str]


@dataclass
class WikiEntity:
    """Represents one markdown entity file in `.otaman/wiki/`."""

    id: str
    title: str
    kind: EntityKind
    source_file: str
    source_line: int
    docstring: str | None = None
    lens_tag: str = "c4"
    status: str = "draft"
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    created_by: str = "otaman-core/wiki-ingest"
    provenance: str = "static-analysis"
    confidence: float = 1.0
    parent_id: str | None = None
    # Resolved graph relations — populated by the two-pass ingest
    relations: list[Relation] = field(default_factory=list)

    def filename(self) -> str:
        return f"{self.id}.md"

    def to_markdown(self) -> str:
        fm: dict = {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "lens-tag": self.lens_tag,
            "status": self.status,
            "created-at": self.created_at,
            "created-by": self.created_by,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "source-file": self.source_file,
            "source-line": self.source_line,
        }
        if self.parent_id:
            fm["parent"] = self.parent_id

        body: list[str] = []

        # --- Graph relations block (Obsidian-visible wikilinks) ---
        if self.relations:
            # Group by label
            groups: dict[str, list[str]] = {}
            for label, target_id, display in self.relations:
                link = f"[[{target_id}|{display}]]"
                groups.setdefault(label, []).append(link)

            for label, links in groups.items():
                if len(links) == 1:
                    body.append(f"**{label}** {links[0]}  ")
                else:
                    body.append(f"**{label}**  ")
                    body.append("  ".join(links) + "  ")
            body.append("")

        # --- Docstring ---
        if self.docstring:
            body.append("## Docstring\n")
            body.append(self.docstring.strip())
            body.append("")

        # --- LLM-managed region ---
        body.append("## Synthesized description")
        body.append("<!-- llm-managed:begin -->")
        body.append("")
        body.append("<!-- llm-managed:end -->")
        body.append("")
        body.append("## Human notes")
        body.append("<!-- human-edited -->")
        body.append("")

        return (
            "---\n"
            + yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
            + "---\n\n"
            + "\n".join(body)
        )
