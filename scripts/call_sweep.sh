#!/usr/bin/env bash
# CALL-SYN sweep over llama.cpp router seats: load a seat (POST /models/load), wait until it answers, run
# tests/call.py, unload it. One seat resident at a time (4 GB card). Run under a GPU lease.
#   scripts/call_sweep.sh OUTDIR "seat:arm seat:arm ..."     arm = off | think (think = on, 256-token budget)
set -u
cd "$(dirname "$0")/.."
OUT="$1"; JOBS="$2"; ROUTER="${ROUTER:-http://localhost:8090}"
mkdir -p "$OUT"
for job in $JOBS; do
  seat="${job%%:*}"; arm="${job##*:}"
  echo "== $seat ($arm) $(date -u +%H:%M:%S)" | tee -a "$OUT/progress.log"
  for m in $(curl -s "$ROUTER/v1/models" | grep -o '"id":"[^"]*","aliases":\[[^]]*\],"tags":\[[^]]*\],"object":"model","owned_by":"llamacpp","created":[0-9]*,"status":{"value":"loaded"' | cut -d'"' -f4); do
    curl -s -X POST "$ROUTER/models/unload" -H 'content-type: application/json' -d "{\"model\":\"$m\"}" >/dev/null
  done
  sleep 6
  ready=0
  for i in $(seq 1 40); do
    curl -s -X POST "$ROUTER/models/load" -H 'content-type: application/json' -d "{\"model\":\"$seat\"}" >/dev/null
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 120 "$ROUTER/v1/chat/completions" -H 'content-type: application/json' \
      -d "{\"model\":\"$seat\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":4}")
    [ "$code" = 200 ] && { ready=1; break; }
    sleep 6
  done
  [ $ready = 1 ] || { echo "   LOAD FAILED $seat" | tee -a "$OUT/progress.log"; continue; }
  if [ "$arm" = think ]; then ARGS=(--think on --reasoning-budget 256 --max-tokens 2000 --timeout 300); else ARGS=(--think off --max-tokens 900 --timeout 300); fi
  BASE_URL="$ROUTER" MODEL="$seat" python3 tests/call.py "${ARGS[@]}" --out-dir "$OUT/raw" > "$OUT/$seat-$arm.log" 2>&1
  tail -1 "$OUT/$seat-$arm.log" | tee -a "$OUT/progress.log"
  if [ -x "$HOME/homelab/backlog/worker/worker-ctl" ] && "$HOME/homelab/backlog/worker/worker-ctl" status 2>/dev/null | grep -q 'gpu_pause: PAUSED'; then
    echo "PAUSED: stopping after $seat" | tee -a "$OUT/progress.log"; break
  fi
done
echo "ALL DONE $(date -u +%H:%M:%S)" | tee -a "$OUT/progress.log"
