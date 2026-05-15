#!/usr/bin/env bash
# DryBrief Suisse — Lokaler Entwicklungsserver
# Ausführen: bash start.sh  (oder ./start.sh nach chmod +x start.sh)

set -e

# Ab Projekt-Root starten (wichtig: data/ muss relativ zu frontend/ erreichbar sein)
cd "$(dirname "$0")"

PORT=8080
URL="http://localhost:${PORT}/frontend/"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       DryBrief Suisse — Dev-Server       ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  URL: ${URL}"
echo "  Stoppen: Ctrl+C"
echo ""

# Browser automatisch öffnen (optional, funktioniert auf den meisten Systemen)
if command -v xdg-open &>/dev/null; then
  sleep 1 && xdg-open "$URL" &
elif command -v open &>/dev/null; then
  sleep 1 && open "$URL" &
fi

python3 -m http.server "$PORT"
