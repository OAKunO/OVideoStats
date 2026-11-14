#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [ ! -d ui/node_modules ]; then
  (cd ui && npm ci)
fi
(cd ui && npm run build)
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python studio_app.py
fi
exec python3 studio_app.py
