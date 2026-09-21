"""Changelog-fragment evaluation — the merge-time discipline for shipped-code PRs.

Live incident 2026-09-11: AI-drafted release notes for v0.5.2–v0.5.4, summarized
from commit and PR history, spliced raw internal CLI error/refusal strings
verbatim into public-facing copy. Summarization-from-raw-history is garbage-in
regardless of how careful the drafting is, so the fix is disciplined SOURCE
material: every PR touching shipped code carries one human-written,
customer-facing fragment, and release-cut assembles ONLY those.

This module is the evaluation half — the predicate CI blocks on at merge, and
the same classification the release assembler reuses. It is pure: no git, no
I/O, no policy loading. The caller supplies the changed paths, the resolved
policy rules and any exemption text; everything here is a decision over those
inputs, which is what makes the shipped-code classification testable rather than
a regex buried in a workflow file.

Homed in otaman-core per the shared-logic-single-home rule (D3): deploy's
assembler must evaluate fragments and cannot import otaman-cli (the scr_template
wheel bind). The evaluators live here once; otaman-cli re-exports and otaman-deploy
imports core — no second evaluator may be written in deploy or plugin.

It is also the core-invokable merge-time gate (release-notes-sibling-coverage
1.3): ``python -m otaman_core.changelog_fragment --check --base <ref>
[--pr-body-file <f>]`` is the ONE identical CI line every sibling repo wires,
including repos that cannot install or import otaman-cli. cli's
``otaman policy check-changelog`` is the human-facing wrapper over the same
logic; the git-diff I/O lives in :func:`main`, and :func:`evaluate` stays pure.

The config shape (``dir``, ``filename``, ``categories``, ``exemption_marker``)
is core's, from ``GIT_STANDARD_RULES["changelog_fragment"]`` — read, never
redefined, so the scaffolder, this check and the release assembly cannot
disagree about where fragments live.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

#: Fallbacks matching core's shipped standard, used only when a program's policy
#: omits the block — never a redefinition of it.
_DEFAULT_DIR = "changelog.d"
_DEFAULT_CATEGORIES = ("feature", "fix", "doc", "removal", "misc")
_DEFAULT_EXEMPTION = "changelog: exempt"


@dataclass(frozen=True)
class FragmentConfig:
    """Where fragments live and what marks a PR exempt (core's convention)."""

    dir: str = _DEFAULT_DIR
    categories: tuple[str, ...] = _DEFAULT_CATEGORIES
    exemption_marker: str = _DEFAULT_EXEMPTION
    filename: str = "<pr>.<category>.md"

    def example_path(self, pr: str | int | None = None) -> str:
        """A concrete path to name in the refusal, e.g. ``changelog.d/148.feature.md``."""
        token = str(pr) if pr not in (None, "") else "<pr>"
        category = self.categories[0] if self.categories else "misc"
        return posixpath.join(self.dir, f"{token}.{category}.md")


def resolve_fragment_config(rules: dict | None) -> FragmentConfig:
    """Read the ``changelog_fragment`` block out of an effective policy's rules."""
    block = (rules or {}).get("changelog_fragment")
    if not isinstance(block, dict):
        return FragmentConfig()
    cats = block.get("categories")
    categories = (
        tuple(str(c) for c in cats if str(c).strip())
        if isinstance(cats, list) and cats
        else _DEFAULT_CATEGORIES
    )
    return FragmentConfig(
        dir=str(block.get("dir") or _DEFAULT_DIR).strip("/"),
        categories=categories,
        exemption_marker=str(block.get("exemption_marker") or _DEFAULT_EXEMPTION),
        filename=str(block.get("filename") or "<pr>.<category>.md"),
    )


def fragment_required_by_policy(rules: dict | None) -> bool:
    """Whether ``require_changelog_fragment`` is in force (core's narrow-only rule)."""
    return bool((rules or {}).get("require_changelog_fragment"))


# ---------------------------------------------------------------------------
# the shipped-code classification
#
# Deliberately an ALLOWLIST of not-shipped patterns with everything else treated
# as shipped, so the check fails toward REQUIRING a fragment. A false block is
# visible and clearable in one line (the exemption marker); a false pass silently
# drops a customer-facing note from the release, and nothing downstream can
# recover it — the pile is all release-cut has.

#: Paths that are not shipped to customers: docs, CI/tooling config, tests, and
#: the fragment pile itself.
_NOT_SHIPPED = (
    re.compile(r"^\.github/"),
    re.compile(r"^docs?/"),
    re.compile(r"^tests?/"),
    re.compile(r"^\.[^/]+$"),  # dotfiles at the root (.gitignore, .editorconfig)
    re.compile(r"^(README|CHANGELOG|LICENSE|CONTRIBUTING|CODEOWNERS|NOTICE)", re.IGNORECASE),
    re.compile(r"\.md$", re.IGNORECASE),
)


def _normalize(path: str) -> str:
    """Repo-relative POSIX form.

    NOTE the prefix strip is deliberate and NOT ``lstrip("./")`` — that strips a
    character SET, so `.github/workflows/ci.yml` would lose its leading dot and
    escape the CI-path pattern entirely (a false "shipped code" verdict, caught
    by a smoke test against real paths).
    """
    norm = str(path).replace("\\", "/").strip()
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.lstrip("/")


def is_shipped_path(path: str, cfg: FragmentConfig | None = None) -> bool:
    """Whether *path* is shipped code — i.e. a change a customer could notice.

    Not shipped: ``.github/``, ``docs/``, ``tests/``, root dotfiles, the
    conventional top-level docs, any ``.md``, and the fragment directory. A
    ``.md`` under ``src/`` is still documentation; a ``.py`` under ``docs/`` is
    still docs tooling — both stay out.
    """
    norm = _normalize(path)
    if not norm:
        return False
    fragment_dir = (cfg or FragmentConfig()).dir
    if fragment_dir and (norm == fragment_dir or norm.startswith(f"{fragment_dir}/")):
        return False
    return not any(pat.search(norm) for pat in _NOT_SHIPPED)


def shipped_paths(paths: list[str], cfg: FragmentConfig | None = None) -> list[str]:
    """The subset of *paths* that is shipped code."""
    return [p for p in paths if is_shipped_path(p, cfg)]


def fragment_paths(paths: list[str], cfg: FragmentConfig) -> list[str]:
    """Changed paths that ARE fragments: ``<dir>/<stem>.<category>.md``."""
    out: list[str] = []
    cats = {c.lower() for c in cfg.categories}
    for p in paths:
        norm = _normalize(p)
        if not cfg.dir or not norm.startswith(f"{cfg.dir}/"):
            continue
        name = posixpath.basename(norm)
        parts = name.split(".")
        # <stem>.<category>.md — a category is required so a stray note in the
        # pile is not mistaken for a fragment.
        if len(parts) >= 3 and parts[-1].lower() == "md" and parts[-2].lower() in cats:
            out.append(norm)
    return out


def has_exemption(text: str | None, cfg: FragmentConfig) -> bool:
    """Whether *text* (a PR body / commit message) carries the exemption marker.

    Matched case-insensitively and whitespace-tolerantly around the colon, so
    ``Changelog: Exempt`` and ``changelog:exempt`` both count — the marker is
    typed by humans in a PR body, not generated.
    """
    if not text:
        return False
    marker = cfg.exemption_marker.strip()
    if not marker:
        return False
    if ":" in marker:
        key, _, value = marker.partition(":")
        pattern = re.escape(key.strip()) + r"\s*:\s*" + re.escape(value.strip())
    else:
        pattern = re.escape(marker)
    return re.search(pattern, text, re.IGNORECASE) is not None


@dataclass(frozen=True)
class Verdict:
    """The merge-time decision, with everything the refusal needs to be actionable."""

    required: bool
    ok: bool
    reason: str
    shipped: list[str] = field(default_factory=list)
    fragments: list[str] = field(default_factory=list)
    exempted: bool = False
    expected_path: str = ""


def evaluate(
    changed_paths: list[str],
    rules: dict | None,
    *,
    pr: str | int | None = None,
    exemption_text: str | None = None,
) -> Verdict:
    """Decide whether a PR satisfies the fragment requirement.

    A fragment is required when the policy rule is in force AND the PR touches
    shipped code AND no exemption marker is present. The requirement is satisfied
    by any validly-named fragment among the changed paths; when *pr* is known, a
    fragment named for THIS pr counts first (so a PR cannot ride on a sibling's
    fragment file that merely appears in its diff).
    """
    cfg = resolve_fragment_config(rules)

    if not fragment_required_by_policy(rules):
        return Verdict(
            required=False,
            ok=True,
            reason="policy does not require changelog fragments (require_changelog_fragment off)",
            expected_path=cfg.example_path(pr),
        )

    paths = [_normalize(p) for p in changed_paths if _normalize(p)]
    shipped = shipped_paths(paths, cfg)
    found = fragment_paths(paths, cfg)
    expected = cfg.example_path(pr)

    if not shipped:
        return Verdict(
            required=False,
            ok=True,
            reason="no shipped-code changes — docs/CI/test-only PR",
            fragments=found,
            expected_path=expected,
        )

    if pr not in (None, ""):
        own = [f for f in found if posixpath.basename(f).split(".")[0] == str(pr)]
        if own:
            return Verdict(
                required=True,
                ok=True,
                reason=f"fragment present for PR {pr}",
                shipped=shipped,
                fragments=own,
                expected_path=expected,
            )

    if found:
        return Verdict(
            required=True,
            ok=True,
            reason="changelog fragment present",
            shipped=shipped,
            fragments=found,
            expected_path=expected,
        )

    if has_exemption(exemption_text, cfg):
        return Verdict(
            required=True,
            ok=True,
            reason=f"exempted by marker {cfg.exemption_marker!r}",
            shipped=shipped,
            exempted=True,
            expected_path=expected,
        )

    return Verdict(
        required=True,
        ok=False,
        reason=(
            f"{len(shipped)} shipped-code file(s) changed with no changelog fragment "
            f"and no exemption marker"
        ),
        shipped=shipped,
        expected_path=expected,
    )


__all__ = [
    "FragmentConfig",
    "Verdict",
    "evaluate",
    "fragment_paths",
    "fragment_required_by_policy",
    "has_exemption",
    "is_shipped_path",
    "resolve_fragment_config",
    "shipped_paths",
]


# ---------------------------------------------------------------------------
# the core-invokable gate (release-notes-sibling-coverage 1.3)
#
# The one CI line every sibling repo wires; the git-diff I/O lives here so
# evaluate() above stays pure. `otaman policy check-changelog` is the cli wrapper
# over the same logic, and this mirrors its diff (three-dot merge-base) and exit
# codes so the two give identical verdicts.

#: Exit code for a refused merge — matches cli's `_GUARD_REFUSED` so a sibling's
#: gate and the cli wrapper block with the same status.
_REFUSED = 3


def _changed_paths_from_git(base: str) -> tuple[list[str], str | None]:
    """``(paths, error)`` — files changed against *base* via the merge-base diff.

    Three-dot ``base...HEAD`` (changes since the merge-base), identical to cli's
    ``otaman policy check-changelog`` so the core gate and the wrapper agree.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"git diff failed: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return [], f"git diff against {base!r} failed: {detail[0] if detail else 'unknown error'}"
    return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()], None


def main(argv: list[str] | None = None) -> int:
    """``python -m otaman_core.changelog_fragment --check --base <ref> [--pr-body-file <f>]``.

    The core-invokable merge-time gate. Diffs ``base...HEAD``, honours the PR-body
    exemption marker, and returns non-zero when a shipped-code change carries no
    changelog fragment. Rules are core's shipped ``GIT_STANDARD_RULES``: a sibling
    repo's CI has no otaman project to resolve an effective policy from, and every
    sibling shares core's one convention — which is exactly what cli's wrapper
    resolves to when unoverridden, so the verdicts match.
    """
    import argparse
    import sys
    from pathlib import Path

    from otaman_core.policy import GIT_STANDARD_RULES

    parser = argparse.ArgumentParser(
        prog="python -m otaman_core.changelog_fragment",
        description="Core-invokable merge-time changelog-fragment gate.",
    )
    parser.add_argument("--check", action="store_true", help="run the gate (required)")
    parser.add_argument(
        "--base", default="origin/main", help="base ref to diff against (default: origin/main)"
    )
    parser.add_argument("--pr", default=None, help="PR number, so its own fragment counts first")
    parser.add_argument(
        "--pr-body-file",
        dest="pr_body_file",
        default=None,
        help="file whose text is scanned for the exemption marker",
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="changed paths to check instead of a git diff (CI/testing escape hatch)",
    )
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    args = parser.parse_args(argv)

    if not args.check:
        parser.error("nothing to do — pass --check")

    rules = dict(GIT_STANDARD_RULES)

    if args.paths is not None:
        paths = [p.strip() for p in args.paths if p.strip()]
    else:
        paths, err = _changed_paths_from_git(args.base)
        if err:
            print(
                f"ERROR: {err}\n  Pass the changed files explicitly instead: --paths <f> ...",
                file=sys.stderr,
            )
            return 2

    exemption_text = ""
    if args.pr_body_file:
        try:
            exemption_text = Path(args.pr_body_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"ERROR: cannot read --pr-body-file {args.pr_body_file!r}: {exc}", file=sys.stderr
            )
            return 2

    verdict = evaluate(paths, rules, pr=args.pr, exemption_text=exemption_text)
    cfg = resolve_fragment_config(rules)

    if args.json:
        import json

        print(
            json.dumps(
                {
                    "ok": verdict.ok,
                    "required": verdict.required,
                    "reason": verdict.reason,
                    "exempted": verdict.exempted,
                    "shipped_files": verdict.shipped,
                    "fragments": verdict.fragments,
                    "expected_path": verdict.expected_path,
                    "exemption_marker": cfg.exemption_marker,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if verdict.ok else _REFUSED

    if verdict.ok:
        print(f"changelog fragment: OK — {verdict.reason}")
        for frag in verdict.fragments:
            print(f"  {frag}")
        return 0

    print(f"Refused — {verdict.reason}.", file=sys.stderr)
    print(f"  Expected a fragment at: {verdict.expected_path}", file=sys.stderr)
    print(f"  Categories: {', '.join(cfg.categories)}", file=sys.stderr)
    print("  Write it for a CUSTOMER: what changed and why it matters to them.", file=sys.stderr)
    print(f"  Docs/CI-only PR? Add '{cfg.exemption_marker}' to the PR body.", file=sys.stderr)
    if verdict.shipped:
        shown = verdict.shipped[:10]
        print(f"  Shipped-code files ({len(verdict.shipped)}):", file=sys.stderr)
        for path in shown:
            print(f"    {path}", file=sys.stderr)
        if len(verdict.shipped) > len(shown):
            print(f"    … and {len(verdict.shipped) - len(shown)} more", file=sys.stderr)
    return _REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
