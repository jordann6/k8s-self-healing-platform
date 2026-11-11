#!/opt/d1/bin/python
"""Object-store client for the D1 cloud backups (ADR-0022).

Backends, chosen by D1_STORE:
  s3      Amazon S3 (or any S3 API) through boto3. Credentials come from the default
          chain, which reads EKS Pod Identity container credentials.
  azblob  Azure Blob Storage through azure-storage-blob. Credentials come from
          DefaultAzureCredential, which reads AKS workload identity.

Commands print one JSON object (or one line) on stdout. A failure prints
`<ErrorCode>: <message>` on stderr and exits 1, so a Job's log carries the store's
own refusal (AccessDenied, AuthorizationPermissionMismatch, an immutability error)
for the runner to classify. Nothing here reads or prints a credential.

  put <file> <key>        upload, print {"versionID", "size"}
  get <key> <file>        download the current version
  stat <key>              print {"versionID", "size", "mode", "retainUntilDate"}
  retention <key>         print the bucket or blob retention in the D1 log format
  delete <key> [version]  delete a key or a version (used only to prove denial)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from typing import Any

CONTAINER = os.environ.get("D1_CONTAINER") or os.environ.get("S3_BUCKET", "")


def iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def retention_line(mode: str, days: int) -> str:
    """The exact shape the runner parses (it matches `mc retention info --default`)."""
    return f"Object locking '{mode}' is configured for {days}DAYS"


def days_between(start: dt.datetime, end: dt.datetime) -> int:
    return max(0, round((end - start).total_seconds() / 86400))


class S3:
    def __init__(self) -> None:
        import boto3

        self.client = boto3.client("s3")
        self.bucket = CONTAINER

    def put(self, path: str, key: str) -> dict[str, Any]:
        with open(path, "rb") as stream:
            # The organization's require-s3-encryption SCP denies any PutObject without an explicit
            # x-amz-server-side-encryption header; the bucket default does not satisfy it (EKS 2026-11-10).
            response = self.client.put_object(Bucket=self.bucket, Key=key, Body=stream,
                                              ServerSideEncryption="AES256")
        return {"versionID": response.get("VersionId"), "size": os.path.getsize(path)}

    def get(self, key: str, path: str) -> None:
        self.client.download_file(self.bucket, key, path)

    def stat(self, key: str) -> dict[str, Any]:
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        return {"versionID": head.get("VersionId"), "size": head.get("ContentLength"),
                "mode": head.get("ObjectLockMode"),
                "retainUntilDate": iso(head.get("ObjectLockRetainUntilDate"))}

    def retention(self, _key: str) -> str:
        config = self.client.get_object_lock_configuration(Bucket=self.bucket)
        default = config["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]
        return retention_line(default["Mode"], int(default["Days"]))

    def delete(self, key: str, version: str | None) -> None:
        if version:
            self.client.delete_object(Bucket=self.bucket, Key=key, VersionId=version)
        else:
            self.client.delete_object(Bucket=self.bucket, Key=key)


class AzBlob:
    def __init__(self) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient(os.environ["AZ_ACCOUNT_URL"], credential=DefaultAzureCredential())
        self.container = service.get_container_client(CONTAINER)

    def put(self, path: str, key: str) -> dict[str, Any]:
        with open(path, "rb") as stream:
            # ContainerClient.upload_blob returns a BlobClient, only BlobClient.upload_blob
            # returns the properties dict that carries version_id.
            response = self.container.get_blob_client(key).upload_blob(stream, overwrite=False)
        return {"versionID": response.get("version_id"), "size": os.path.getsize(path)}

    def get(self, key: str, path: str) -> None:
        with open(path, "wb") as stream:
            self.container.download_blob(key).readinto(stream)

    def _properties(self, key: str) -> Any:
        return self.container.get_blob_client(key).get_blob_properties()

    def stat(self, key: str) -> dict[str, Any]:
        props = self._properties(key)
        policy = props.get("immutability_policy")
        mode = getattr(policy, "policy_mode", None) or (policy or {}).get("policy_mode") if policy else None
        until = getattr(policy, "expiry_time", None) or (policy or {}).get("expiry_time") if policy else None
        return {"versionID": props.get("version_id"), "size": props.get("size"),
                "mode": str(mode).capitalize() if mode else None, "retainUntilDate": iso(until)}

    def retention(self, key: str) -> str:
        # The account-level default is a control-plane value. The data plane shows
        # what a blob actually inherited: its policy mode and expiry against its
        # creation time, which is the one-day window the account enforces.
        props = self._properties(key)
        policy = props.get("immutability_policy")
        mode = getattr(policy, "policy_mode", None) or (policy or {}).get("policy_mode") if policy else None
        until = getattr(policy, "expiry_time", None) or (policy or {}).get("expiry_time") if policy else None
        created = props.get("creation_time")
        if not (mode and until and created):
            raise RuntimeError("the blob carries no immutability policy, so no retention can be read")
        return retention_line(str(mode).capitalize(), days_between(created, until))

    def delete(self, key: str, version: str | None) -> None:
        self.container.get_blob_client(key).delete_blob(version_id=version) if version \
            else self.container.delete_blob(key)


def backend() -> Any:
    kind = os.environ.get("D1_STORE", "")
    if kind == "s3":
        return S3()
    if kind == "azblob":
        return AzBlob()
    raise SystemExit(f"D1_STORE must be s3 or azblob, got {kind!r}")


def describe(error: Exception) -> str:
    code = (getattr(error, "error_code", None)
            or getattr(error, "response", {}).get("Error", {}).get("Code")
            or type(error).__name__)
    return f"{code}: {error}"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command, args = argv[1], argv[2:]
    try:
        store = backend()
        if command == "put" and len(args) == 2:
            print(json.dumps(store.put(args[0], args[1])))
        elif command == "get" and len(args) == 2:
            store.get(args[0], args[1])
        elif command == "stat" and len(args) == 1:
            print(json.dumps(store.stat(args[0])))
        elif command == "retention" and len(args) == 1:
            print(store.retention(args[0]))
        elif command == "delete" and len(args) in (1, 2):
            store.delete(args[0], args[1] if len(args) == 2 else None)
            print("deleted")
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except Exception as error:  # noqa: BLE001 - the store's own refusal is the evidence
        print(describe(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
