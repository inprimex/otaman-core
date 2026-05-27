"""WikiEntity — data type for a single wiki entity file."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import yaml

EntityKind = Literal["module", "component", "code-unit"]


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
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    created_by: str = "otaman-core/wiki-ingest"
    provenance: str = "static-analysis"
    confidence: float = 1.0
    parent_id: str | None = None

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

        body_parts: list[str] = []

        if self.docstring:
            body_parts.append("## Docstring\n")
            body_parts.append(self.docstring.strip())
            body_parts.append("")

        body_parts.append("## Synthesized description")
        body_parts.append("<!-- llm-managed:begin -->")
        body_parts.append("")
        body_parts.append("<!-- llm-managed:end -->")
        body_parts.append("")
        body_parts.append("## Human notes")
        body_parts.append("<!-- human-edited -->")
        body_parts.append("")

        return (
            "---\n"
            + yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
            + "---\n\n"
            + "\n".join(body_parts)
        )
