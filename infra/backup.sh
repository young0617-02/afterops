#!/bin/sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
UPLOAD_DIR="${UPLOAD_DIR:-/data/uploads}"
ANCHOR_DIR="${ANCHOR_DIR:-/data/anchors}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"
PGHOST="${PGHOST:-postgres}"
PGUSER="${PGUSER:-afterops}"
PGDATABASE="${PGDATABASE:-afterops}"

mkdir -p "$BACKUP_DIR"

run_backup() {
  stamp=$(date -u +%Y%m%d-%H%M%S)
  dump="$BACKUP_DIR/afterops-$stamp.sql.gz"
  dump_tmp="$dump.tmp"
  uploads="$BACKUP_DIR/afterops-uploads-$stamp.tar.gz"
  uploads_tmp="$uploads.tmp"
  anchors="$BACKUP_DIR/afterops-anchors-$stamp.tar.gz"
  anchors_tmp="$anchors.tmp"

  pg_dump -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" | gzip > "$dump_tmp"
  mv "$dump_tmp" "$dump"
  (cd "$BACKUP_DIR" && sha256sum "$(basename "$dump")" > "$(basename "$dump").sha256")

  if [ -d "$UPLOAD_DIR" ]; then
    tar -czf "$uploads_tmp" -C "$UPLOAD_DIR" .
    mv "$uploads_tmp" "$uploads"
    (cd "$BACKUP_DIR" && sha256sum "$(basename "$uploads")" > "$(basename "$uploads").sha256")
  fi

  if [ -d "$ANCHOR_DIR" ]; then
    tar -czf "$anchors_tmp" -C "$ANCHOR_DIR" .
    mv "$anchors_tmp" "$anchors"
    (cd "$BACKUP_DIR" && sha256sum "$(basename "$anchors")" > "$(basename "$anchors").sha256")
  fi

  find "$BACKUP_DIR" -type f \
    \( -name 'afterops-*.sql.gz' -o -name 'afterops-uploads-*.tar.gz' \
    -o -name 'afterops-anchors-*.tar.gz' -o -name '*.sha256' \) \
    -mtime +"$RETENTION_DAYS" -delete

  echo "[backup] $stamp db=$(wc -c < "$dump")B uploads=$([ -f "$uploads" ] && wc -c < "$uploads" || echo 0)B anchors=$([ -f "$anchors" ] && wc -c < "$anchors" || echo 0)B retention=${RETENTION_DAYS}d"
}

if [ "${BACKUP_ONCE:-0}" = "1" ]; then
  run_backup
  exit 0
fi

while true; do
  run_backup || echo "[backup] failed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  sleep "$INTERVAL_SECONDS"
done
