#!/bin/bash
source "$(dirname "$0")/.venv/bin/activate"
caffeinate -i python main.py \
  --min-subscribers 1000 \
  --max-subscribers 10000 \
  --min-engagement 0.5 \
  --max-results 20 \
  "$@"
