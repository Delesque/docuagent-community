#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -f "../web-next/index.html" ]; then
  echo "[1/3] Building DocuAgent frontend..."
  npm --prefix ../frontend ci
  (cd .. && npm --prefix frontend run build)
fi

if [ ! -d "node_modules/electron/dist" ]; then
  echo "[2/3] Installing Electron..."
  npm install
fi

echo "[3/3] Starting DocuAgent desktop..."
npm start
