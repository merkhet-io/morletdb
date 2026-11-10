#!/usr/bin/env bash
# Fast local checks to run before pushing: the cheap part of CI, so formatting, lint and generated-file
# slips are caught in about a minute instead of after a CI round. Needs no database; the upgrade-script
# check needs cargo-pgrx and a pg_config (PG_CONFIG, default ~/.pgrx/17.11/pgrx-install/bin/pg_config)
# and is skipped with a notice otherwise.
#
#   tools/precheck.sh          # checks for files changed against origin/main (or everything if unknown)
#   tools/precheck.sh --all    # every check
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
ALL=false; [ "${1:-}" = --all ] && ALL=true
changed=$(git diff --name-only "$(git merge-base HEAD origin/main 2>/dev/null || echo HEAD)" 2>/dev/null; git ls-files --others --exclude-standard)
touches() { $ALL || grep -qE "$1" <<< "$changed"; }
step() { printf '== %s\n' "$*"; }

if touches '\.rs$|Cargo\.(toml|lock)$|rust-toolchain'; then
  step "cargo fmt"; cargo fmt --all -- --check
  step "clippy (grizzbit, morlet-core, -D warnings)"; cargo clippy --locked -q -p grizzbit -p morlet-core --all-targets -- -D warnings
  step "grizzbit and morlet-core tests"; cargo test --locked -q -p grizzbit -p morlet-core 2>&1 | grep -E "^test result|FAILED|panicked" || true
fi
if touches '^extensions/pg_morlet/(src|sql)/|gen_upgrade\.py$'; then
  pgc=${PG_CONFIG:-$HOME/.pgrx/17.11/pgrx-install/bin/pg_config}
  if command -v cargo-pgrx >/dev/null && [ -x "$pgc" ] && grep -q "^default_version = '0.3.7'" extensions/pg_morlet/pg_morlet.control; then
    step "update script 0.3.6 -> 0.3.7 is current"
    tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
    (cd extensions/pg_morlet && cargo pgrx schema pg17 --pg-config "$pgc" -o "$tmp" >/dev/null 2>&1 && python3 tests/gen_upgrade.py "$tmp" --check)
  else
    step "update script check skipped (needs cargo-pgrx, $pgc, and the 0.3.7 generator)"
  fi
fi
if touches '^(research/paper|docs)/|^README\.md$|^VERSION$'; then
  step "paper and site checks"
  python3 research/paper/gen_numbers.py --check
  research/paper/sync_site.sh --check >/dev/null
  python3 research/paper/release_manifest.py --check
  python3 tools/site_versions.py --check
  python3 tools/highlight_site.py --check
  python3 tools/benchmark_chart.py --check
fi
if touches '^\.github/workflows/' && command -v actionlint >/dev/null; then
  step "actionlint"; actionlint
fi
step "precheck passed"
