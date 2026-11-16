#!/usr/bin/env bash
# Backend mit Python-Reload und Frontend mit Angular Live-Reload starten.
#
#   ./scripts/dev-watch.sh            # hochfahren (detached) + Logs folgen
#   ./scripts/dev-watch.sh down       # wieder beenden
#
# Für Normalbetrieb ohne Reload: podman compose up -d  (ohne das dev-Overlay).
set -e
cd "$(dirname "$0")/.."

if [ "${1:-}" = "down" ]; then
  exec podman compose down
fi

# .env (ROBOT_MOCK, GRIPPER_TYPE, OPENAI_* ...) laden, falls vorhanden.
if [ -f .env ]; then set -a; . ./.env; set +a; fi

podman compose -f docker-compose.dev.yml up -d --build
echo "[dev-watch] Backend mit Hot-Reload und Frontend mit ng serve auf Port 4200 gestartet."
echo "[dev-watch] Logs folgen (Strg+C beendet nur das Log-Tailing, nicht den Container):"
exec podman compose -f docker-compose.dev.yml logs -f backend frontend
