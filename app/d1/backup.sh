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

# D1_STORE selects the object store. The default is the kind MinIO through mc.
# s3 and azblob are the EKS and AKS stores through d1store.py (ADR-0022); the
# markers below keep the same shape either way.
store="${D1_STORE:-mc}"
if [ "$store" = mc ]; then
	mc alias set store "$S3_ENDPOINT" "$S3_ACCESS_KEY" "$S3_SECRET_KEY" >/dev/null
	mc cp "$archive" "store/$S3_BUCKET/$object_key" >/dev/null
	mc cp "$manifest" "store/$S3_BUCKET/$manifest_key" >/dev/null
else
	d1store.py put "$archive" "$object_key" >/dev/null
	d1store.py put "$manifest" "$manifest_key" >/dev/null
fi
printf 'D1_BACKUP_COMPLETE '
cat "$manifest"
printf 'D1_MANIFEST_SHA256 %s\n' "$manifest_sha256"
printf 'D1_ARCHIVE_STAT '
if [ "$store" = mc ]; then
	mc stat --json "store/$S3_BUCKET/$object_key"
else
	d1store.py stat "$object_key"
fi
printf 'D1_RETENTION_INFO '
if [ "$store" = mc ]; then
	mc retention info --default "store/$S3_BUCKET"
else
	d1store.py retention "$object_key"
fi
