#!/usr/bin/env bash
# Stoppt und entfernt die FlexDA-Container.
set -euo pipefail
podman rm -f ur3-backend ur3-frontend ur3-backend-native 2>/dev/null || true
echo "[podman] Container gestoppt + entfernt."
