#!/usr/bin/env bash
# Run the full pipeline with real LLM agents.
# Requires ANTHROPIC_API_KEY in agent_system/.env
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "=== Task 1: palindrome prefix/suffix ==="
python orchestrator.py examples/task1_palindrome_prefix_suffix.json --live --render md

echo ""
echo "=== Task 2: grammar S→SaSb ==="
python orchestrator.py examples/task2_grammar_sasb.json --live --render md

echo ""
echo "=== Task 3: regex with backreference ==="
python orchestrator.py examples/task3_regex_backref.json --live --render md
