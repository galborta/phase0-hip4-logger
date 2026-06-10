#!/bin/bash
# Hands-off launcher for a Linux VPS (Hostinger etc.).
# Sets up the environment and runs the capture DETACHED so it survives
# logging out of SSH. Start it any time before kickoff and disconnect.
#
#   bash run_on_vps.sh                 # start detached, waits for kickoff
#   bash run_on_vps.sh --force-start   # start logging immediately
#
# Check progress:   tail -f data/run.log
# Stop early:       kill $(cat run.pid)
# Get the result:   cat data/REPORT.txt   (or scp it back to your laptop)

cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
  echo "First run: creating venv + installing deps..."
  python3 -m venv .venv
  if [ ! -x ".venv/bin/python" ]; then
    echo ""
    echo "ERROR: venv was not created. On Ubuntu/Debian install the venv package first:"
    echo "    sudo apt-get update && sudo apt-get install -y python3-venv"
    echo "    (on Ubuntu 24.04 the package is python3.12-venv)"
    echo "Then re-run:  bash run_on_vps.sh"
    exit 1
  fi
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt || { echo "ERROR: pip install failed"; exit 1; }
fi

# sanity: deps importable
./.venv/bin/python -c "import websockets" 2>/dev/null || {
  echo "ERROR: websockets not installed in venv. Run: ./.venv/bin/pip install -r requirements.txt"; exit 1; }

mkdir -p data
echo "Launching detached. It will wait for kickoff and write data/REPORT.txt when done."
nohup ./.venv/bin/python run_phase0.py --config config.json --outdir data "$@" >> data/run.log 2>&1 &
echo $! > run.pid
echo "Started PID $(cat run.pid). Follow it with:  tail -f data/run.log"
echo "You can safely close this SSH session now."
