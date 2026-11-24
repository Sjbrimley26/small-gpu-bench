#!/usr/bin/env bash
# CALL-CHAIN sweep: audit saved CALL-SYN pass-1 replies with a second seat. One audit seat resident at a time
# (4 GB card), all its arms run before the next seat loads. Run under a GPU lease.
#   scripts/call_chain_sweep.sh P1DIR OUTDIR "auditseat:pass1seat:variant ..."
# P1DIR holds raw/CALL-<pass1seat>-*.json from scripts/call_sweep.sh (think arm, replies saved in full).
# Jobs sharing an audit seat must be adjacent so the seat loads once.
set -u
cd "$(dirname "$0")/.."
P1="$1"; OUT="$2"; JOBS="$3"; ROUTER="${ROUTER:-http://localhost:8090}"
mkdir -p "$OUT"
cur=""
for job in $JOBS; do
  IFS=: read -r seat p1seat variant <<<"$job"
  if [ "$seat" != "$cur" ]; then
    echo "== audit seat $seat $(date -u +%H:%M:%S)" | tee -a "$OUT/progress.log"
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
    [ $ready = 1 ] || { echo "   LOAD FAILED $seat" | tee -a "$OUT/progress.log"; cur=""; continue; }
    cur="$seat"
  fi
  f=$(ls -1 "$P1"/raw/CALL-"$p1seat"-2*.json 2>/dev/null | tail -1)
  [ -n "$f" ] || { echo "   NO PASS-1 FILE for $p1seat" | tee -a "$OUT/progress.log"; continue; }
  echo "-- $p1seat -> $seat [$variant] $(date -u +%H:%M:%S)" | tee -a "$OUT/progress.log"
  BASE_URL="$ROUTER" MODEL="$seat" python3 tests/call_chain.py --pass1 "$f" --variant "$variant" --think on \
    --reasoning-budget 256 --max-tokens 2000 --timeout 300 --out-dir "$OUT/raw" > "$OUT/$p1seat-to-$seat-$variant.log" 2>&1
  grep '^CHAIN' "$OUT/$p1seat-to-$seat-$variant.log" | tee -a "$OUT/progress.log"
  if [ -x "$HOME/homelab/backlog/worker/worker-ctl" ] && "$HOME/homelab/backlog/worker/worker-ctl" status 2>/dev/null | grep -q 'gpu_pause: PAUSED'; then
    echo "PAUSED: stopping after $p1seat -> $seat" | tee -a "$OUT/progress.log"; break
  fi
done
echo "ALL DONE $(date -u +%H:%M:%S)" | tee -a "$OUT/progress.log"
