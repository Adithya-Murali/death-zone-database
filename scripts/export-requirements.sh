#!/usr/bin/env bash
# Regenerate requirements.txt from uv.lock.
#
# requirements.txt is a build artefact, not a source file: uv.lock is the source
# of truth. Edit pyproject.toml, then run `make lock`.
set -euo pipefail
cd "$(dirname "$0")/.."

# --quiet suppresses the copy uv also writes to stdout, which would otherwise
# dump the whole pinned set into terminal and CI output.
uv export \
    --quiet \
    --format requirements.txt \
    --no-dev \
    --extra dashboard \
    --no-emit-project \
    --output-file requirements.txt
