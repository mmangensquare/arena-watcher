#!/bin/bash
# Arena Daily Watcher — run via launchd each weekday morning
# Invokes the Claude CLI with the watcher prompt, which queries Arena MCP
# and redeploys the Blockcell site.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/watcher.log"
PROMPT_FILE="$SCRIPT_DIR/prompt.md"

echo "$(date '+%Y-%m-%d %H:%M:%S') — Starting Arena Watcher" >> "$LOG_FILE"

# Run Claude with the watcher prompt non-interactively.
# --dangerously-skip-permissions allows MCP tool calls without prompting.
/Users/mmangen/.local/bin/claude \
  --dangerously-skip-permissions \
  --print \
  -p "$(cat "$PROMPT_FILE")" \
  >> "$LOG_FILE" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') — Done" >> "$LOG_FILE"
