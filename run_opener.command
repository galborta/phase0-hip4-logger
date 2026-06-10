#!/bin/bash
# Double-click this file in Finder to run the hands-off Phase 0 capture.
# It sets up everything and waits for kickoff on its own.
cd "$(dirname "$0")" || exit 1

# create venv + install deps on first run
if [ ! -d ".venv" ]; then
  echo "First run: setting up..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

echo "Starting hands-off capture. You can leave this window open and walk away."
echo "Come back after the match to data/REPORT.txt"
./.venv/bin/python run_phase0.py --config config.json --outdir data

echo ""
echo "Done. Report is in the data folder (REPORT.txt). Press any key to close."
read -n 1
