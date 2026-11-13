#!/usr/bin/env bash
# Smoke test: start the mock server, run every ported test and both patterns against it, check that each one
# finishes and wrote a JSON record. No model, no GPU, no network beyond 127.0.0.1.
# A pass means the plumbing works (argparse, env vars, request shape, graders, result files); the scores are
# meaningless because the mock is not a model.
set -u
export PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")/.."

PORT="${SMOKE_PORT:-8099}"
OUT="$(mktemp -d "${TMPDIR:-/tmp}/sgb-smoke.XXXXXX")"
export BASE_URL="http://127.0.0.1:${PORT}"
export MODEL="mock-big"
export API_KEY="not-needed"

python3 mock/mock_server.py --port "$PORT" >"$OUT/mock.log" 2>&1 &
MOCK=$!
trap 'kill $MOCK 2>/dev/null; wait $MOCK 2>/dev/null' EXIT

for _ in $(seq 1 50); do
  python3 - "$PORT" <<'PY' 2>/dev/null && break
import sys, urllib.request
urllib.request.urlopen("http://127.0.0.1:%s/v1/models" % sys.argv[1], timeout=1)
PY
  sleep 0.1
done

FAILS=0
step() {   # step NAME CMD...
  local name="$1"; shift
  if "$@" >"$OUT/$name.log" 2>&1; then
    echo "ok    $name"
  else
    echo "FAIL  $name   (see $OUT/$name.log)"; tail -5 "$OUT/$name.log" | sed 's/^/        /'
    FAILS=$((FAILS + 1))
  fi
}
rec() {    # rec TEST  -> a result JSON for TEST must exist in the smoke output dir
  if ls "$OUT/raw/$1"-*.json >/dev/null 2>&1; then echo "ok    $1 wrote results/raw record"; else echo "FAIL  $1 wrote no record"; FAILS=$((FAILS + 1)); fi
}

# 1. offline self-tests (graders, fixtures, pattern logic: no server involved)
for t in red aud mon hrd tool_probe judge crit sum_test ocr; do step "selftest-$t" python3 tests/$t.py --selftest; done
step selftest-summarize_verify python3 patterns/summarize_verify.py --selftest
step selftest-escalate python3 patterns/escalate.py --selftest

# 2. every test end to end against the mock
R=(--out-dir "$OUT/raw")
step run-red        python3 tests/red.py "${R[@]}" --docs 4
step run-aud        python3 tests/aud.py "${R[@]}" --runs 1
step run-mon        python3 tests/mon.py "${R[@]}" --runs 2
step run-hrd        python3 tests/hrd.py "${R[@]}" --temps 0 --only money,kerf,undet_screws
step run-tool       python3 tests/tool_probe.py "${R[@]}" --runs 2
step run-judge      python3 tests/judge.py "${R[@]}" --runs 2
step run-crit       python3 tests/crit.py "${R[@]}" --runs 2 --judge-writer small
step run-sum        python3 tests/sum_test.py "${R[@]}" --runs 1 --pipeline
step run-ocr        python3 tests/ocr.py "${R[@]}" --runs 1
for T in RED AUD MON HRD TOOL JUDGE CRIT SUM OCR; do rec $T; done

# 3. the patterns end to end
step run-summarize-verify python3 patterns/summarize_verify.py tests/fixtures/demo/transcript.txt --quiet --out "$OUT/demo.summary.md"
# the mock quotes the source verbatim, so the quote-in-source check must verify every claim (0 flagged)
if grep -q " 0 flagged" "$OUT/demo.summary.md" && ! grep -q "unverified" "$OUT/demo.summary.md"; then
  echo "ok    summarize-verify: every claim verified against the source (0 flagged)"
else
  echo "FAIL  summarize-verify flagged claims on the mock"; tail -4 "$OUT/demo.summary.md" | sed 's/^/        /'; FAILS=$((FAILS + 1))
fi
step run-single-pass      python3 patterns/summarize_verify.py tests/fixtures/demo/transcript.txt --single --quiet
# escalation: the 'small' rung answers wrongly on purpose, so every item must be accepted by the 'big' rung
MODEL=mock-small MODEL_ESCALATE=mock-big BASE_URL_ESCALATE="$BASE_URL" python3 patterns/escalate.py >"$OUT/escalate.log" 2>&1
if grep -q "4/4 accepted" "$OUT/escalate.log" && grep -q "rung=escalate" "$OUT/escalate.log"; then
  echo "ok    run-escalate (small rung rejected, escalated rung accepted 4/4)"
else
  echo "FAIL  run-escalate"; cat "$OUT/escalate.log" | sed 's/^/        /'; FAILS=$((FAILS + 1))
fi

# 4. an unreachable endpoint must fail cleanly (recorded error, no traceback)
if BASE_URL="http://127.0.0.1:1" python3 tests/mon.py --out-dir "$OUT/raw-dead" --timeout 3 >"$OUT/dead.log" 2>&1 \
   && ! grep -q Traceback "$OUT/dead.log" && grep -q "FAIL" "$OUT/dead.log"; then
  echo "ok    unreachable endpoint is recorded as a failure, not a crash"
else
  echo "FAIL  unreachable endpoint handling"; tail -5 "$OUT/dead.log" | sed 's/^/        /'; FAILS=$((FAILS + 1))
fi

echo
if [ "$FAILS" -eq 0 ]; then echo "SMOKE: all steps passed (outputs in $OUT)"; else echo "SMOKE: $FAILS step(s) failed (outputs in $OUT)"; exit 1; fi
