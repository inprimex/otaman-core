#!/usr/bin/env bash
# audit-maestro-refs.sh — fail if any unannotated 'maestro' reference remains.
#
# Scans SRC_DIR for bare-word 'maestro' references and fails if any matching
# line is not annotated with 'legacy:' or 'migration:' on the same line and
# is not part of an explicitly allowed file (test fixtures).
#
# Usage:
#   scripts/audit-maestro-refs.sh [src_dir]
#
# Default src_dir: src/
#
# Annotation convention:
#   - Add  # legacy: <reason>  or  # migration: <reason>  inline on any line
#     that intentionally contains 'maestro' as a backward-compat reference.
#   - Bare-word grep uses \bmaestro\b (case-insensitive) so identifiers like
#     MAESTRO_ROOT and maestro_root are matched; otaman_root is NOT matched.
#
# Consumer repos can run this script against their own source tree:
#   bash /path/to/otaman-core/scripts/audit-maestro-refs.sh src/

set -euo pipefail

SRC_DIR="${1:-src}"

if [[ ! -d "$SRC_DIR" ]]; then
    echo "ERROR: source directory '$SRC_DIR' not found." >&2
    exit 2
fi

# Files (relative to repo root) that are exempt from the audit.
# Add any intentional exception files here (e.g. test fixtures, migration docs).
ALLOWED_FILES=(
    # No permanent exceptions — annotate inline instead.
)

FAILURES=0

while IFS= read -r match; do
    file="${match%%:*}"
    line_content="${match#*:}"  # everything after the first colon (line number + content)

    # Strip leading line number to get just the source line.
    source_line="${line_content#*:}"

    # Check if file is in the explicit allow-list.
    allowed=false
    for allowed_file in "${ALLOWED_FILES[@]}"; do
        if [[ "$file" == "$allowed_file" ]]; then
            allowed=true
            break
        fi
    done
    $allowed && continue

    # Allow lines that carry an inline annotation.
    if echo "$source_line" | grep -qiE '(legacy:|migration:)'; then
        continue
    fi

    echo "FAIL [$file]: unannotated maestro reference: $source_line"
    FAILURES=$((FAILURES + 1))

done < <(grep -rnwE -i '\bmaestro\b' "$SRC_DIR" 2>/dev/null || true)

if [[ $FAILURES -gt 0 ]]; then
    echo ""
    echo "Found $FAILURES unannotated 'maestro' reference(s) in $SRC_DIR."
    echo "Fix: annotate with '# legacy: <reason>' or '# migration: <reason>',"
    echo "     or migrate to the 'otaman' name."
    exit 1
fi

echo "OK: no unannotated maestro references in $SRC_DIR."
