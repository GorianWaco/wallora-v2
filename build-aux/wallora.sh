#!/bin/sh
# Flatpak launcher for Wallora
export PYTHONPATH=/app/lib/python3.12/site-packages:$PYTHONPATH
exec python3 -m wallora "$@"
