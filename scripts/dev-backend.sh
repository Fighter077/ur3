#!/usr/bin/env bash
# Backend lokal (ohne Docker) für schnelle Iteration starten.
set -e
cd "$(dirname "$0")/../backend"

if [ ! -d venv ]; then
  python3 -m venv venv
  ./venv/bin/pip install --upgrade pip
  ./venv/bin/pip install -r requirements.txt
fi

export ROBOT_MOCK="${ROBOT_MOCK:-true}"
exec ./venv/bin/python server.py
