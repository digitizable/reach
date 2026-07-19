#!/usr/bin/env bash
# Smoke test Composition III reverse (Python path) + unit tests.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$ROOT/scripts"
TOKEN="smoke-$(date +%s)"
LISTEN_PORT=18443
SOCKS_PORT=10808

echo "== unit tests =="
(cd "$ROOT/src" && python3 -m core.ingress_cn_test)

echo "== reverse tunnel =="
python3 "$SCRIPTS/spectre-reverse-accept.py" \
  --token "$TOKEN" --listen "127.0.0.1:$LISTEN_PORT" --socks "127.0.0.1:$SOCKS_PORT" \
  > /tmp/spectre-accept-smoke.log 2>&1 &
AC=$!
sleep 0.4
python3 "$SCRIPTS/spectre-reverse-agent.py" \
  --token "$TOKEN" --accept "127.0.0.1:$LISTEN_PORT" \
  > /tmp/spectre-agent-smoke.log 2>&1 &
AG=$!
sleep 1

CODE=$(curl -sS -o /tmp/spectre-smoke.html -w "%{http_code}" --max-time 15 \
  -x "socks5h://127.0.0.1:$SOCKS_PORT" http://example.com || echo 000)

kill "$AC" "$AG" 2>/dev/null || true
wait 2>/dev/null || true

if [[ "$CODE" != "200" ]]; then
  echo "FAIL curl http_code=$CODE"
  cat /tmp/spectre-accept-smoke.log /tmp/spectre-agent-smoke.log
  exit 1
fi
echo "OK reverse SOCKS → example.com (HTTP $CODE)"
echo "ALL SMOKE PASSED"
