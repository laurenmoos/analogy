#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif [ -x "$SCRIPT_DIR/../../.venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/../../.venv/bin/python"
else
    PYTHON=python3
fi

HAS_PROBLEM=0
for ARG in "$@"; do
    if [ "$ARG" = "--problem" ]; then
        HAS_PROBLEM=1
        break
    fi
done

if [ "$HAS_PROBLEM" -eq 0 ]; then
    set -- \
        --problem imp_intro_01 \
        --problem imp_intro_03 \
        --problem and_intro_05 \
        "$@"
fi

exec "$PYTHON" "$SCRIPT_DIR/scripts/standalone_analogy_recovery.py" \
    --poetry-limit 8 \
    --physics-limit 8 \
    --retrieval-count 4 \
    --candidate-count 4 \
    --max-expansions 8 \
    --max-verifier-calls 32 \
    --theorem-timeout 600 \
    "$@"
