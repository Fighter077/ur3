#!/usr/bin/env bash
# Frontend lokal (ohne Docker) im Dev-Server starten.
set -e
cd "$(dirname "$0")/../frontend"
[ -d node_modules ] || npm install
exec npm start
