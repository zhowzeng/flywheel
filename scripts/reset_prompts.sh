#!/usr/bin/env bash
# Reset prompts/ to initial state: only v1 exists and current == v1
set -euo pipefail

PROMPTS_DIR="$(cd "$(dirname "$0")/../prompts" && pwd)"

# Remove all prompt files except v1
for f in "$PROMPTS_DIR"/*.prompt.md; do
  filename="$(basename "$f")"
  if [[ "$filename" != "v1.prompt.md" ]]; then
    rm "$f"
    echo "Removed: $filename"
  fi
done

# Reset current to match v1
cp "$PROMPTS_DIR/v1.prompt.md" "$PROMPTS_DIR/current.prompt.md"
echo "Reset current.prompt.md to v1"
