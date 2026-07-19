#!/usr/bin/env bash
# Multi-agent reverse: two agents, kill one, traffic still works.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$ROOT/scripts"
TOKEN="multi-$(date +%s)"
LISTEN_PORT=18453
SOCKS_PORT=10818

cleanup() {
  kill "$AC" "$AG1" "$AG2" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

echo "== multi-agent reverse =="
python3 "$SCRIPTS/spectre-reverse-accept.py" \
  --token "$TOKEN" --listen "127.0.0.1:$LISTEN_PORT" --socks "127.0.0.1:$SOCKS_PORT" \
  > /tmp/spectre-accept-multi.log 2>&1 &
AC=$!
sleep 0.4

python3 "$SCRIPTS/spectre-reverse-agent.py" \
  --token "$TOKEN" --accept "127.0.0.1:$LISTEN_PORT" --agent-id peer-a \
  > /tmp/spectre-agent-a.log 2>&1 &
AG1=$!
python3 "$SCRIPTS/spectre-reverse-agent.py" \
  --token "$TOKEN" --accept "127.0.0.1:$LISTEN_PORT" --agent-id peer-b \
  > /tmp/spectre-agent-b.log 2>&1 &
AG2=$!
sleep 1

CODE1=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 15 \
  -x "socks5h://127.0.0.1:$SOCKS_PORT" http://example.com || echo 000)
if [[ "$CODE1" != "200" ]]; then
  echo "FAIL both-up curl=$CODE1"
  cat /tmp/spectre-accept-multi.log /tmp/spectre-agent-a.log /tmp/spectre-agent-b.log
  exit 1
fi
echo "OK both agents up → HTTP $CODE1"

# Kill peer-a; peer-b must still serve
kill "$AG1" 2>/dev/null || true
AG1=
sleep 0.8

CODE2=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 15 \
  -x "socks5h://127.0.0.1:$SOCKS_PORT" http://example.com || echo 000)
if [[ "$CODE2" != "200" ]]; then
  echo "FAIL after kill peer-a curl=$CODE2"
  cat /tmp/spectre-accept-multi.log /tmp/spectre-agent-b.log
  exit 1
fi
echo "OK failover peer-b only → HTTP $CODE2"

# Reconnect peer-a; both again
python3 "$SCRIPTS/spectre-reverse-agent.py" \
  --token "$TOKEN" --accept "127.0.0.1:$LISTEN_PORT" --agent-id peer-a \
  > /tmp/spectre-agent-a2.log 2>&1 &
AG1=$!
sleep 1

CODE3=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 15 \
  -x "socks5h://127.0.0.1:$SOCKS_PORT" http://example.com || echo 000)
if [[ "$CODE3" != "200" ]]; then
  echo "FAIL rejoin curl=$CODE3"
  exit 1
fi
echo "OK peer-a rejoined → HTTP $CODE3"

# Pool should have logged two ids without kicking
if ! grep -q "id=peer-a" /tmp/spectre-accept-multi.log; then
  echo "FAIL accept log missing peer-a"
  cat /tmp/spectre-accept-multi.log
  exit 1
fi
if ! grep -q "id=peer-b" /tmp/spectre-accept-multi.log; then
  echo "FAIL accept log missing peer-b"
  cat /tmp/spectre-accept-multi.log
  exit 1
fi
if ! grep -q "pool=2" /tmp/spectre-accept-multi.log; then
  echo "WARN: no pool=2 line (timing); log:"
  cat /tmp/spectre-accept-multi.log
fi

echo "ALL MULTI-AGENT SMOKE PASSED"
