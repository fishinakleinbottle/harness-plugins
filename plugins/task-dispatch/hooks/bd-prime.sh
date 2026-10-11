#!/usr/bin/env bash
# Loads bd's workflow context into the session, but only in a repo that uses
# bd. Anywhere else this hook does nothing, so the plugin is safe to enable
# globally.
set -u
if [ -d ".beads" ] && command -v bd >/dev/null 2>&1; then
  bd prime --hook-json 2>/dev/null || bd prime 2>/dev/null || true
fi
exit 0
