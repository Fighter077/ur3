#!/usr/bin/env bash
# Baut und startet FlexDA (Backend + Frontend) mit Podman.
#
# Warum Podman statt Docker Desktop: Docker Desktop läuft auf Linux in einer VM; dadurch
# traversiert der WebRTC-Medienstrom (UDP) nicht und die Verbindung bleibt auf "connecting"
# hängen. Rootless Podman nutzt das echte Host-Netz -> `--network=host` funktioniert inkl.
# UDP, und der WebRTC-Stream kommt an.
set -euo pipefail
cd "$(dirname "$0")/.."

# .env (falls vorhanden) einlesen, ohne bereits gesetzte Umgebungsvariablen zu überschreiben
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

# Mount-Verzeichnisse sicherstellen (frischer Clone hat sie evtl. nicht)
mkdir -p backend/models backend/data

ROBOT_MOCK="${ROBOT_MOCK:-true}"
ROBOT_IP="${ROBOT_IP:-192.168.1.100}"
YOLO_DEVICE="${YOLO_DEVICE:-cpu}"
YOLO_MODEL_PATH="${YOLO_MODEL_PATH:-yolo11n.pt}"

echo "[podman] Baue Images (erster Lauf lädt Torch/ultralytics, dauert etwas)…"
podman build -t ur3-backend:latest ./backend
podman build -t ur3-frontend:latest ./frontend

echo "[podman] Entferne evtl. alte Container…"
podman rm -f ur3-backend ur3-frontend ur3-backend-native 2>/dev/null || true

echo "[podman] Starte Backend (host-network, für WebRTC/UDP)…"
podman run -d --name ur3-backend \
  --network=host \
  --restart unless-stopped \
  -e ROBOT_MOCK="$ROBOT_MOCK" \
  -e ROBOT_IP="$ROBOT_IP" \
  -e INSPECT_FLIP_SPEED="${INSPECT_FLIP_SPEED:-0.15}" \
  -e ROBOT_TCP_SPEED="${ROBOT_TCP_SPEED:-0.12}" \
  -e INSPECT_STILL_S="${INSPECT_STILL_S:-0.3}" \
  -e YOLO_DEVICE="$YOLO_DEVICE" \
  -e YOLO_MODEL_PATH="$YOLO_MODEL_PATH" \
  -e PYTHONUNBUFFERED=1 \
  -v "$(pwd)/backend/models:/app/models:z" \
  -v "$(pwd)/backend/data:/app/data:z" \
  ur3-backend:latest >/dev/null

echo "[podman] Starte Frontend (:4200)…"
podman run -d --name ur3-frontend \
  --restart unless-stopped \
  -p 4200:80 \
  ur3-frontend:latest >/dev/null

echo
echo "[podman] Läuft:"
podman ps --filter name=ur3- --format "  {{.Names}}\t{{.Status}}"
echo
echo "  Frontend:  http://localhost:4200"
echo "  Backend:   http://localhost:8080/health"
echo
echo "  Logs:      podman logs -f ur3-backend"
echo "  Stoppen:   ./scripts/podman-down.sh"
