"""release-notes-sibling-coverage 1.1 — the consumed-fragments manifest.

Clearing is owner-side (design D1); the manifest, not the directory state, is the
dedup authority. These pin the three release-channels scenarios: a sibling
fragment reaches the notes, a lagging clear cannot duplicate it, and clearing is
by exact manifest filename (README.md survives).
"""

from __future__ import annotations

from otaman_core.changelog_manifest import (
    HASH_ALGO,
    ConsumedFragment,
    Manifest,
    build_manifest,
    consumed_index,
    fragment_hash,
    from_dict,
    is_consumed,
    record_fragment,
    to_dict,
)

# ---------------------------------------------------------------------------
# hashing + recording


def test_hash_is_stable_and_content_addressed():
    assert fragment_hash("a note") == fragment_hash("a note")
    assert fragment_hash("a note") != fragment_hash("a different note")
    # str and equivalent bytes hash the same
    assert fragment_hash("a note") == fragment_hash(b"a note")


def test_record_fragment_uses_basename_and_hash():
    frag = record_fragment("otaman-cli", "changelog.d/148.feature.md", "Added a thing.\n")
    assert frag.repo == "otaman-cli"
    assert frag.filename == "148.feature.md"  # basename only
    assert frag.content_hash == fragment_hash("Added a thing.\n")
    assert frag.category == "feature"


def test_record_fragment_normalizes_backslash_paths():
    frag = record_fragment("otaman-core", r"changelog.d\12.fix.md", "x")
    assert frag.filename == "12.fix.md"


def test_category_is_empty_for_a_non_fragment_name():
    assert ConsumedFragment("r", "README.md", "h").category == ""


# ---------------------------------------------------------------------------
# manifest shape + round-trip


def test_manifest_groups_by_repo_and_lists_filenames():
    m = build_manifest(
        "v0.6.0",
        [
            record_fragment("otaman-cli", "148.feature.md", "cli feature"),
            record_fragment("otaman-core", "12.fix.md", "core fix"),
            record_fragment("otaman-cli", "150.fix.md", "cli fix"),
        ],
    )
    assert m.repos() == ["otaman-cli", "otaman-core"]
    assert sorted(m.filenames_for("otaman-cli")) == ["148.feature.md", "150.fix.md"]
    assert m.filenames_for("otaman-core") == ["12.fix.md"]


def test_to_dict_is_deterministic_and_grouped():
    m = build_manifest(
        "v0.6.0",
        [
            record_fragment("otaman-core", "12.fix.md", "core fix"),
            record_fragment("otaman-cli", "150.fix.md", "cli fix"),
            record_fragment("otaman-cli", "148.feature.md", "cli feature"),
        ],
    )
    d = to_dict(m)
    assert d["release"] == "v0.6.0"
    assert list(d["fragments"].keys()) == ["otaman-cli", "otaman-core"]  # repos sorted
    # fragments within a repo sorted by filename
    assert [e["filename"] for e in d["fragments"]["otaman-cli"]] == [
        "148.feature.md",
        "150.fix.md",
    ]
    assert d["fragments"]["otaman-core"][0][HASH_ALGO] == fragment_hash("core fix")


def test_round_trips_through_dict():
    m = build_manifest(
        "v0.6.0",
        [
            record_fragment("otaman-cli", "148.feature.md", "cli feature"),
            record_fragment("otaman-core", "12.fix.md", "core fix"),
        ],
    )
    assert from_dict(to_dict(m)) == m


def test_from_dict_tolerates_missing_and_malformed():
    assert from_dict(None) == Manifest(release="", fragments=())
    assert from_dict({}) == Manifest(release="", fragments=())
    # an entry without a filename is skipped
    parsed = from_dict(
        {
            "release": "v1",
            "fragments": {"r": [{"sha256": "h"}, {"filename": "1.fix.md", "sha256": "h2"}]},
        }
    )
    assert parsed.filenames_for("r") == ["1.fix.md"]


def test_from_dict_reads_content_hash_alias():
    parsed = from_dict(
        {"release": "v1", "fragments": {"r": [{"filename": "1.fix.md", "content_hash": "h"}]}}
    )
    assert parsed.for_repo("r")[0].content_hash == "h"


# ---------------------------------------------------------------------------
# scenario: sibling change reaches the notes (recorded with provenance)


def test_sibling_fragment_is_recorded_with_provenance():
    """A merged otaman-cli fragment is consumed at cut and recorded with its
    source repo, filename, and content hash for later clearing."""
    frag = record_fragment("otaman-cli", "changelog.d/148.feature.md", "Shiny new flag.\n")
    m = build_manifest("v0.6.0", [frag])
    (recorded,) = m.for_repo("otaman-cli")
    assert recorded.filename == "148.feature.md"
    assert recorded.content_hash == fragment_hash("Shiny new flag.\n")


# ---------------------------------------------------------------------------
# scenario: lagging clear cannot duplicate


def test_a_fragment_in_a_prior_manifest_is_skipped():
    frag = record_fragment("otaman-cli", "148.feature.md", "Shiny new flag.\n")
    release_n = build_manifest("v0.6.0", [frag])
    # release N+1: the owner has NOT cleared, so the same file is present again
    still_there = record_fragment("otaman-cli", "148.feature.md", "Shiny new flag.\n")
    assert is_consumed(still_there, [release_n]) is True


def test_an_uncleared_edit_is_a_new_fragment():
    """Same filename, new content → new hash → NOT already-consumed (it is a
    genuinely different note the next cut should publish)."""
    first = record_fragment("otaman-cli", "148.feature.md", "Shiny new flag.\n")
    release_n = build_manifest("v0.6.0", [first])
    edited = record_fragment("otaman-cli", "148.feature.md", "Shiny new flag, now documented.\n")
    assert is_consumed(edited, [release_n]) is False


def test_a_fresh_fragment_is_not_consumed():
    prior = build_manifest("v0.6.0", [record_fragment("otaman-cli", "148.feature.md", "old")])
    fresh = record_fragment("otaman-core", "12.fix.md", "new core fix")
    assert is_consumed(fresh, [prior]) is False


def test_consumed_index_unions_prior_manifests():
    m1 = build_manifest("v0.6.0", [record_fragment("otaman-cli", "1.fix.md", "a")])
    m2 = build_manifest("v0.6.1", [record_fragment("otaman-core", "2.fix.md", "b")])
    index = consumed_index([m1, m2])
    assert len(index) == 2
    # is_consumed accepts a precomputed index (cheap for many fragments)
    assert is_consumed(record_fragment("otaman-cli", "1.fix.md", "a"), index) is True
    assert is_consumed(record_fragment("otaman-core", "2.fix.md", "b"), index) is True


def test_same_filename_in_two_repos_stays_distinct():
    """Two repos can each carry `1.fix.md`; consumption is per repo."""
    cli_prior = build_manifest("v0.6.0", [record_fragment("otaman-cli", "1.fix.md", "same text")])
    core_frag = record_fragment("otaman-core", "1.fix.md", "same text")
    assert is_consumed(core_frag, [cli_prior]) is False


# ---------------------------------------------------------------------------
# scenario: clearing is owner-side and exact — README.md survives


def test_clearing_targets_only_manifest_filenames_never_readme():
    m = build_manifest(
        "v0.6.0",
        [
            record_fragment("otaman-core", "12.fix.md", "core fix"),
            record_fragment("otaman-core", "13.feature.md", "core feature"),
        ],
    )
    to_delete = m.filenames_for("otaman-core")
    assert to_delete == ["12.fix.md", "13.feature.md"]
    assert "README.md" not in to_delete  # never recorded, never cleared
