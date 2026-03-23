#!/usr/bin/env bash
# Recover C3 (browser-auth) when Docker Desktop fails with:
#   failed to mount ... mkdir .../overlayfs/... read-only file system
#
# That error is from Docker's internal graph driver (Linux VM disk), not from
# this repo's compose file. This script removes the stuck container so a fresh
# overlay can be created after Docker is healthy again.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

echo "=== C3 Docker recovery ==="
echo "1) Removing old C3 container (if any)..."
docker rm -f C3_browser-auth 2>/dev/null || true

echo "2) Rebuilding browser-auth image (use --no-cache if layers look corrupt)..."
docker compose build browser-auth

echo "3) Starting C3..."
if ! docker compose up -d browser-auth; then
  echo ""
  echo ">>> Still failing? Docker Desktop's VM disk is likely read-only or corrupt."
  echo "    Try in order:"
  echo "    a) Quit Docker Desktop completely (menu bar whale → Quit), wait 10s, reopen."
  echo "    b) Docker Desktop → Troubleshoot → Restart."
  echo "    c) Free disk space on your Mac (Docker.raw needs room)."
  echo "    d) Docker Desktop → Troubleshoot → Clean / Purge data (removes unused images)."
  echo "    e) Last resort: Troubleshoot → Reset to factory defaults."
  exit 1
fi

echo "4) Status:"
docker ps -a --filter name=C3_browser-auth --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo ""
echo "Smoke checks:"
sleep 2
curl -sS --connect-timeout 5 http://localhost:8001/health && echo "" || echo "(health not ready yet — wait a few seconds)"
