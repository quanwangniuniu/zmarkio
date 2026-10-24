#!/bin/sh
set -eu

interval=${STUDIO_MIGRATION_INTERVAL_SECONDS:-60}
case "$interval" in
  ''|*[!0-9]*) echo 'STUDIO_MIGRATION_INTERVAL_SECONDS must be a positive integer' >&2; exit 2 ;;
esac
if ! [ "$interval" -gt 0 ]; then
  echo 'STUDIO_MIGRATION_INTERVAL_SECONDS must be a positive integer' >&2
  exit 2
fi

while :; do
  node --import tsx scripts/migrate.ts apply || echo 'Studio migration round failed; retrying after the interval.' >&2
  sleep "$interval"
done
