"""The consumed-fragments manifest for cross-repo release assembly.

Design D1 of release-notes-sibling-coverage: clearing consumed changelog
fragments is the OWNING repo's act, never a cross-repo write by the release cut
(fleet law is one writer per repo; the `fix-otaman-complete-task-drift` and
`git reset --hard` incidents are why that has no sanctioned exception). The cut
instead records a manifest — per repo, the exact filenames and content hashes it
consumed — into the release record beside the notes. The manifest, not the
directory state, is the authority for two things:

- **Idempotent assembly.** The assembler skips any fragment already listed in a
  prior release's manifest, so a lagging owner-side clear can never duplicate a
  note — it is carried exactly once, in the release that first consumed it.
- **Exact-filename clearing.** The cut broadcasts a `fragments-consumed` signal;
  each owner deletes ONLY the manifest-named filenames in its own repo, never by
  glob — a glob once deleted `changelog.d/README.md` on the first live cut.

Homed in otaman-core per shared-logic-single-home: deploy's assembler writes the
manifest at cut, owning agents read it to clear, and one (de)serialization means
the two sides cannot disagree about its shape. Pure: content hashing and
dict<->object mapping only — callers own the file I/O and the YAML/JSON envelope
of the release record.
"""

from __future__ import annotations

import hashlib
import posixpath
from collections.abc import Iterable
from dataclasses import dataclass

#: The digest algorithm recorded for every fragment. Named so a future manifest
#: can carry a different one without the reader guessing.
HASH_ALGO = "sha256"


def fragment_hash(content: str | bytes) -> str:
    """The lowercase hex ``sha256`` of a fragment's content.

    Accepts bytes (a file read in binary) or str (utf-8 encoded here). The
    assembler hashes each fragment once at cut and stores it; on a later cut the
    same untouched file hashes identically, which is what lets a lagging clear be
    recognised and skipped.
    """
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ConsumedFragment:
    """One fragment a cut consumed: its source repo, filename, and content hash."""

    repo: str
    filename: str
    content_hash: str

    @property
    def category(self) -> str:
        """The category segment of ``<stem>.<category>.md``, or ``""`` if unnamed."""
        parts = posixpath.basename(self.filename).split(".")
        if len(parts) >= 3 and parts[-1].lower() == "md":
            return parts[-2].lower()
        return ""

    @property
    def key(self) -> tuple[str, str, str]:
        """The dedup identity: (repo, filename, content_hash).

        An edited fragment (same name, new content) hashes differently and so is
        a NEW fragment; a re-created identical file hashes the same and is skipped.
        """
        return (self.repo, self.filename, self.content_hash)


def record_fragment(repo: str, filename: str, content: str | bytes) -> ConsumedFragment:
    """Build a :class:`ConsumedFragment` from a fragment's repo, name, and content."""
    return ConsumedFragment(
        repo=repo,
        filename=posixpath.basename(str(filename).replace("\\", "/")),
        content_hash=fragment_hash(content),
    )


@dataclass(frozen=True)
class Manifest:
    """What one release cut consumed, grouped by source repo."""

    release: str
    fragments: tuple[ConsumedFragment, ...] = ()

    def for_repo(self, repo: str) -> list[ConsumedFragment]:
        """This release's consumed fragments from *repo*."""
        return [f for f in self.fragments if f.repo == repo]

    def filenames_for(self, repo: str) -> list[str]:
        """The exact filenames an owner clears in *repo* — never a glob."""
        return [f.filename for f in self.for_repo(repo)]

    def repos(self) -> list[str]:
        """Every repo that contributed a consumed fragment, sorted."""
        return sorted({f.repo for f in self.fragments})

    def keys(self) -> set[tuple[str, str, str]]:
        """Every ``(repo, filename, content_hash)`` this manifest consumed."""
        return {f.key for f in self.fragments}


def build_manifest(release: str, fragments: Iterable[ConsumedFragment]) -> Manifest:
    """A manifest for *release* over *fragments*, deterministically ordered."""
    ordered = tuple(sorted(fragments, key=lambda f: (f.repo, f.filename, f.content_hash)))
    return Manifest(release=str(release), fragments=ordered)


def to_dict(manifest: Manifest) -> dict:
    """The manifest as a plain dict for the release record (repo → entries).

    Deterministic: repos sorted, fragments sorted by filename, so the recorded
    manifest diffs cleanly between cuts.
    """
    grouped: dict[str, list[dict[str, str]]] = {}
    for frag in sorted(manifest.fragments, key=lambda f: (f.repo, f.filename)):
        grouped.setdefault(frag.repo, []).append(
            {"filename": frag.filename, HASH_ALGO: frag.content_hash}
        )
    return {"release": manifest.release, "fragments": grouped}


def from_dict(data: dict | None) -> Manifest:
    """Parse a manifest recorded by :func:`to_dict` (tolerant of a missing block)."""
    data = data or {}
    release = str(data.get("release") or "")
    grouped = data.get("fragments") or {}
    fragments: list[ConsumedFragment] = []
    if isinstance(grouped, dict):
        for repo, entries in grouped.items():
            for entry in entries or []:
                if not isinstance(entry, dict):
                    continue
                filename = str(entry.get("filename") or "").strip()
                if not filename:
                    continue
                content_hash = str(entry.get(HASH_ALGO) or entry.get("content_hash") or "").strip()
                fragments.append(
                    ConsumedFragment(repo=str(repo), filename=filename, content_hash=content_hash)
                )
    return build_manifest(release, fragments)


def consumed_index(manifests: Iterable[Manifest]) -> set[tuple[str, str, str]]:
    """The union of every prior manifest's keys — what the assembler skips."""
    index: set[tuple[str, str, str]] = set()
    for manifest in manifests:
        index |= manifest.keys()
    return index


def is_consumed(
    fragment: ConsumedFragment,
    prior: Iterable[Manifest] | set[tuple[str, str, str]],
) -> bool:
    """Whether *fragment* was already consumed by a prior release.

    *prior* may be prior manifests or a precomputed :func:`consumed_index` (build
    the index once when checking many fragments against many cuts).
    """
    index = prior if isinstance(prior, set) else consumed_index(prior)
    return fragment.key in index


__all__ = [
    "HASH_ALGO",
    "ConsumedFragment",
    "Manifest",
    "build_manifest",
    "consumed_index",
    "fragment_hash",
    "from_dict",
    "is_consumed",
    "record_fragment",
    "to_dict",
]
