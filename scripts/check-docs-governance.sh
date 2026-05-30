#!/usr/bin/env bash
# Fail if public SDK surface changed without documentation / changelog updates.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE="${DOCS_GOV_BASE_REF:-origin/main}"
if ! git rev-parse "$BASE" >/dev/null 2>&1; then
  BASE="HEAD~1"
fi

CHANGED="$(git diff --name-only "$BASE"...HEAD 2>/dev/null || git diff --name-only --cached)"

surface_changed=false
docs_changed=false

while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  case "$f" in
    conrrad_sdk/*|kernell_sdk/__init__.py|kap_escrow/__init__.py|pyproject.toml|schemas/*)
      surface_changed=true
      ;;
    docs/*.md|docs/**/*.md|CHANGELOG.md)
      docs_changed=true
      ;;
  esac
done <<< "$CHANGED"

if $surface_changed && ! $docs_changed; then
  echo "DOCS GOVERNANCE FAIL: public surface changed without docs/ or CHANGELOG.md update."
  echo "Changed files:"
  echo "$CHANGED"
  echo "Update docs per docs/DOCS_GOVERNANCE.md"
  exit 1
fi

echo "DOCS GOVERNANCE OK"
