#!/bin/bash

# Resolve the repository root from this script's own location, so the batch run
# works from any working directory and on any machine.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILE="$PROJECT_ROOT/ScraperMiniApp/rerun.txt"

if [ -f "$FILE" ]; then
    while IFS= read -r line; do
        echo "Processing bot: $line"
        # Detach stdin: without it the first bot consumes the rest of the list.
        python3 "$PROJECT_ROOT/Automator.py" "$line" < /dev/null
    done < "$FILE"
else
    echo "File $FILE not found."
fi
