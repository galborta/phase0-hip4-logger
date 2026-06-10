#!/bin/bash
# Install the Phase 0 capture as a systemd service so it survives SSH logout
# AND reboots (auto-starts on boot, auto-resumes if the box restarts before or
# during the match). Run once, as root, after the venv exists.
#
#   bash install_service.sh
#
# Useful afterwards:
#   systemctl status phase0 --no-pager
#   journalctl -u phase0 -f            # live logs
#   tail -f data/run.log               # same info, file form
#   systemctl stop phase0              # stop early
#   systemctl disable phase0           # don't auto-start anymore
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "venv not found at $PY"
  echo "Build it first:  bash run_on_vps.sh   (it creates the venv, then Ctrl-C / let it run)"
  echo "Or:  python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
  exit 1
fi

# stop any existing nohup-launched run so we don't double-capture
if [ -f "$DIR/run.pid" ]; then
  kill "$(cat "$DIR/run.pid")" 2>/dev/null || true
  rm -f "$DIR/run.pid"
fi

cat > /etc/systemd/system/phase0.service <<EOF
[Unit]
Description=Phase 0 HIP-4 World Cup capture
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$PY $DIR/run_phase0.py --config config.json --outdir data
Restart=on-failure
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable phase0.service
systemctl restart phase0.service

echo ""
echo "Installed and started. It will auto-start on every boot until the match is done."
echo "  status:  systemctl status phase0 --no-pager"
echo "  logs:    journalctl -u phase0 -f   (or: tail -f $DIR/data/run.log)"
