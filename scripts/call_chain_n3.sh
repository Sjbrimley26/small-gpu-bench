#!/usr/bin/env bash
# CALL-CHAIN n=3: repeat the winning arm (bolt proposes, frog audits) and its baseline on three fresh
# fixtures. Temperature 0 is deterministic on one fixture, so a repeat of the SAME seed measures nothing;
# the sample here is three fixture seeds (--seed), each a full 44-call set with its own gold.
#   scripts/call_chain_n3.sh OUTDIR SEED [SEED ...]
# Per seed: bolt-agent-think pass 1 (the baseline is that same run), frog-agent-think alone (the incumbent
# on false tasks), then frog auditing bolt's pass-1 replies. Frog stays loaded for the audit, so a seed
# costs two seat loads. Run under a GPU lease.
set -u
cd "$(dirname "$0")/.."
OUT="${1:?out dir}"; shift
SEEDS="${*:?at least one seed}"
ROUTER="${ROUTER:-http://localhost:8090}"
P1SEAT="${P1SEAT:-bolt-agent-think}"
AUDITSEAT="${AUDITSEAT:-frog-agent-think}"
mkdir -p "$OUT"
say() { echo "$*" | tee -a "$OUT/progress.log"; }

loaded_seats() {
  curl -s "$ROUTER/v1/models" | grep -o '"id":"[^"]*","aliases":\[[^]]*\],"tags":\[[^]]*\],"object":"model","owned_by":"llamacpp","created":[0-9]*,"status":{"value":"loaded"' \
    | cut -d'"' -f4
}

load_seat() {          # $1 seat -> 0 when it answers a smoke request
  local seat="$1" m
  for m in $(loaded_seats); do
    [ "$m" = "$seat" ] && { echo "   $seat already resident"; return 0; }
    curl -s -X POST "$ROUTER/models/unload" -H 'content-type: application/json' -d "{\"model\":\"$m\"}" >/dev/null
  done
  sleep 6
  local i code
  for i in $(seq 1 40); do
    curl -s -X POST "$ROUTER/models/load" -H 'content-type: application/json' -d "{\"model\":\"$seat\"}" >/dev/null
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 120 "$ROUTER/v1/chat/completions" -H 'content-type: application/json' \
      -d "{\"model\":\"$seat\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":4}")
    [ "$code" = 200 ] && return 0
    sleep 6
  done
  say "   LOAD FAILED $seat"
  return 1
}

paused() { [ -x "$HOME/homelab/backlog/worker/worker-ctl" ] && \
           "$HOME/homelab/backlog/worker/worker-ctl" status 2>/dev/null | grep -q 'gpu_pause: PAUSED'; }

START_SEAT="$(loaded_seats | head -1)"
say "== CALL-CHAIN n=3 seeds: $SEEDS (start seat: ${START_SEAT:-none}) $(date -u +%H:%M:%SZ)"
for seed in $SEEDS; do
  say "-- seed $seed: pass 1 $P1SEAT $(date -u +%H:%M:%SZ)"
  load_seat "$P1SEAT" || continue
  BASE_URL="$ROUTER" MODEL="$P1SEAT" python3 tests/call.py --seed "$seed" --think on --reasoning-budget 256 \
    --max-tokens 2000 --timeout 300 --out-dir "$OUT/p1-$seed/raw" > "$OUT/p1-$seed.log" 2>&1
  grep '^CALL ' "$OUT/p1-$seed.log" | tee -a "$OUT/progress.log"
  paused && { say "PAUSED before the frog solo leg"; break; }

  say "-- seed $seed: $AUDITSEAT alone $(date -u +%H:%M:%SZ)"
  load_seat "$AUDITSEAT" || continue
  BASE_URL="$ROUTER" MODEL="$AUDITSEAT" python3 tests/call.py --seed "$seed" --think on --reasoning-budget 256 \
    --max-tokens 2000 --timeout 300 --out-dir "$OUT/solo-$seed/raw" > "$OUT/solo-$seed.log" 2>&1
  grep '^CALL ' "$OUT/solo-$seed.log" | tee -a "$OUT/progress.log"
  paused && { say "PAUSED before the chain leg"; break; }

  p1=$(ls -1 "$OUT/p1-$seed/raw/CALL-$P1SEAT-2"*.json 2>/dev/null | tail -1)
  if [ -z "$p1" ]; then say "   NO PASS-1 FILE for seed $seed"; continue; fi
  say "-- seed $seed: $P1SEAT -> $AUDITSEAT [reject] $(date -u +%H:%M:%SZ)"
  BASE_URL="$ROUTER" MODEL="$AUDITSEAT" python3 tests/call_chain.py --pass1 "$p1" --seed "$seed" --variant reject \
    --think on --reasoning-budget 256 --max-tokens 2000 --timeout 300 --out-dir "$OUT/chain-$seed/raw" \
    > "$OUT/chain-$seed.log" 2>&1
  grep '^CHAIN' "$OUT/chain-$seed.log" | tee -a "$OUT/progress.log"
  paused && { say "PAUSED after seed $seed"; break; }
done
if [ -n "$START_SEAT" ]; then
  say "-- restoring start seat $START_SEAT $(date -u +%H:%M:%SZ)"
  load_seat "$START_SEAT" || true
fi
say "ALL DONE $(date -u +%H:%M:%SZ)"
