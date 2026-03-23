#!/bin/bash
# ============================================================
# Master Agent Launcher (Phase D)
# ============================================================

PROJECT_ROOT="/Users/macbook/Documents/API-WRAPPER/copilot-openai-wrapper"

usage() {
    echo "Usage: ./cluster-start.sh [agent-name]"
    echo "Agents: aider (C2), claude (C5), kilocode (C6), openclaw (C7)"
    echo "Special: status (Global Health Check)"
    exit 1
}

if [ -z "$1" ]; then
    usage
fi

cd "$PROJECT_ROOT" || exit 1

case "$1" in
    aider|c2)
        docker compose run -T --rm agent-terminal ask "$2"
        ;;
    claude|c5)
        docker compose run -T --rm claude-code-terminal ask "$2"
        ;;
    kilocode|c6)
        docker compose run -T --rm kilocode ask "$2"
        ;;
    openclaw|c7)
        docker compose run -T --rm openclaw-cli ask "$2"
        ;;
    status)
        echo "── Global Stack Health ──"
        docker compose ps
        echo "── C1 Connectivity ──"
        curl -s http://localhost:8000/health || echo "❌ C1 OFFLINE"
        ;;
    *)
        usage
        ;;
esac
