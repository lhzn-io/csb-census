#!/bin/bash
# Deploy the site to Cloudflare Pages after each P3 layers rebuild ("STEP layers" in ~/data/csb-p3.log).
# Runs alongside the P3 loop and stops when its tmux session ends.
LOG=~/data/csb-p3.log
count() { grep -c "^STEP layers" "$LOG" 2>/dev/null || echo 0; }
last=$(count)
echo "WATCH start $(date -u +%FT%TZ), layers builds so far: $last"
while tmux has-session -t csb-p3 2>/dev/null; do
  now=$(count)
  if [ "$now" -gt "$last" ]; then
    last=$now
    if "$(dirname "$0")/deploy-pages.sh" > ~/data/csb-deploy-last.log 2>&1; then
      tail -1 ~/data/csb-deploy-last.log
    else
      echo "DEPLOY FAILED $(date -u +%FT%TZ); see ~/data/csb-deploy-last.log"
    fi
  fi
  sleep 60
done
echo "WATCH end $(date -u +%FT%TZ)"
