#!/bin/sh
set -eu

work=/work
export MC_CONFIG_DIR="$work/.mc"
backup_id="${BACKUP_ID:-$(hostname)}"
object_key="backups/${backup_id}.dump"
manifest_key="backups/${backup_id}.json"
archive="$work/backup.dump"
manifest="$work/manifest.json"

mkdir -p "$work"
export PGPASSWORD="$POSTGRES_PASSWORD"
pg_dump --host "$POSTGRES_HOST" --username "$POSTGRES_USER" \
	--dbname "$POSTGRES_DB" --format custom --no-owner --no-acl \
	--file "$archive"
archive_sha256="$(sha256sum "$archive" | awk '{print $1}')"
source_max_seq="$(psql --host "$POSTGRES_HOST" --username "$POSTGRES_USER" \
	--dbname "$POSTGRES_DB" --tuples-only --no-align \
	--command 'SELECT max(seq) FROM records')"
created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '{"backup_id":"%s","archive_key":"%s","archive_sha256":"%s","source_max_seq":%s,"created_at":"%s"}\n' \
	"$backup_id" "$object_key" "$archive_sha256" "$source_max_seq" "$created_at" >"$manifest"
manifest_sha256="$(sha256sum "$manifest" | awk '{print $1}')"

mc alias set store "$S3_ENDPOINT" "$S3_ACCESS_KEY" "$S3_SECRET_KEY" >/dev/null
mc cp "$archive" "store/$S3_BUCKET/$object_key" >/dev/null
mc cp "$manifest" "store/$S3_BUCKET/$manifest_key" >/dev/null
printf 'D1_BACKUP_COMPLETE '
cat "$manifest"
printf 'D1_MANIFEST_SHA256 %s\n' "$manifest_sha256"
printf 'D1_ARCHIVE_STAT '
mc stat --json "store/$S3_BUCKET/$object_key"
printf 'D1_RETENTION_INFO '
mc retention info --default "store/$S3_BUCKET"
