#!/bin/sh
set -eu

work=/work
export MC_CONFIG_DIR="$work/.mc"
archive="$work/restore.dump"
mkdir -p "$work"
mc alias set store "$S3_ENDPOINT" "$S3_ACCESS_KEY" "$S3_SECRET_KEY" >/dev/null
mc cp "store/$S3_BUCKET/$OBJECT_KEY" "$archive" >/dev/null
if [ -n "${TRUNCATE_BYTES:-}" ]; then
	head -c "$TRUNCATE_BYTES" "$archive" >"$work/truncated.dump"
	mv "$work/truncated.dump" "$archive"
fi
actual_sha256="$(sha256sum "$archive" | awk '{print $1}')"
if [ -z "${TRUNCATE_BYTES:-}" ] && [ "$actual_sha256" != "$EXPECTED_SHA256" ]; then
	echo "archive checksum mismatch" >&2
	exit 20
fi

export PGPASSWORD="$POSTGRES_PASSWORD"
dropdb --if-exists --host "$POSTGRES_HOST" --username "$POSTGRES_USER" "$TARGET_DB"
createdb --host "$POSTGRES_HOST" --username "$POSTGRES_USER" \
	--template template0 "$TARGET_DB"
pg_restore --host "$POSTGRES_HOST" --username "$POSTGRES_USER" \
	--dbname "$TARGET_DB" --single-transaction --exit-on-error \
	--no-owner --no-acl "$archive"
printf 'D1_RESTORE_COMPLETE {"target_db":"%s","archive_sha256":"%s"}\n' \
	"$TARGET_DB" "$actual_sha256"
