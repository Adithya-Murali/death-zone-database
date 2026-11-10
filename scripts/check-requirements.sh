#!/usr/bin/env bash
# Fail if requirements.txt no longer matches uv.lock.
#
# A committed requirements.txt that drifts from the lockfile is worse than none
# at all: it tells pip users a version set that was never tested. This runs in
# CI and in pre-commit so the two cannot diverge silently.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f requirements.txt ]]; then
    echo "requirements.txt is missing. Run: make lock" >&2
    exit 1
fi

expected="$(mktemp)"
trap 'rm -f "$expected"' EXIT

# --locked also asserts that uv.lock itself is current with pyproject.toml.
uv export \
    --quiet \
    --format requirements.txt \
    --no-dev \
    --extra dashboard \
    --no-emit-project \
    --locked \
    --output-file "$expected"

# Ignore comment lines: the generated header records the invoking uv version,
# which would otherwise make the check fail on a uv upgrade alone.
if ! diff -u \
    --label "requirements.txt (committed)" <(grep -v '^#' requirements.txt) \
    --label "uv.lock (expected)" <(grep -v '^#' "$expected"); then
    echo >&2
    echo "requirements.txt is stale. Run: make lock" >&2
    exit 1
fi

echo "requirements.txt matches uv.lock"
