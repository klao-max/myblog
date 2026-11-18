from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


MANIFEST_VERSION = 1
DATABASE_NAME = "blog.db"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_file(backup_dir: Path, relative_name: str) -> Path:
    candidate = (backup_dir / relative_name).resolve()
    try:
        candidate.relative_to(backup_dir.resolve())
    except ValueError as exc:
        raise ValueError("backup manifest contains a path outside the backup directory") from exc
    return candidate


def backup_data(data_dir: Path, backup_dir: Path) -> Path:
    data_dir = Path(data_dir)
    backup_dir = Path(backup_dir)
    database_path = data_dir / DATABASE_NAME
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir()
    temporary_database = backup_dir / f".{DATABASE_NAME}.tmp"
    source = sqlite3.connect(database_path)
    destination = sqlite3.connect(temporary_database)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    temporary_database.replace(backup_dir / DATABASE_NAME)

    uploads = data_dir / "uploads"
    if uploads.is_dir():
        shutil.copytree(uploads, backup_dir / "uploads")

    files = []
    for path in sorted(backup_dir.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            relative = path.relative_to(backup_dir).as_posix()
            files.append({"path": relative, "sha256": _sha256(path), "size": path.stat().st_size})
    manifest = {
        "version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": DATABASE_NAME,
        "database_sha256": _sha256(backup_dir / DATABASE_NAME),
        "files": files,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return backup_dir


def verify_backup(backup_dir: Path) -> None:
    backup_dir = Path(backup_dir)
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("backup manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("backup manifest is invalid") from exc
    if manifest.get("version") != MANIFEST_VERSION or manifest.get("database") != DATABASE_NAME:
        raise ValueError("unsupported backup manifest")
    database_path = _relative_file(backup_dir, manifest["database"])
    if not database_path.is_file() or _sha256(database_path) != manifest.get("database_sha256"):
        raise ValueError("backup database checksum does not match")
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("backup file entry is invalid")
        path = _relative_file(backup_dir, entry["path"])
        if not path.is_file() or path.stat().st_size != entry.get("size") or _sha256(path) != entry.get("sha256"):
            raise ValueError(f"backup file checksum does not match: {entry['path']}")


def restore_data(backup_dir: Path, data_dir: Path, *, replace: bool = False) -> Path:
    backup_dir = Path(backup_dir)
    data_dir = Path(data_dir)
    verify_backup(backup_dir)
    if data_dir.exists() and not data_dir.is_dir():
        raise FileExistsError(data_dir)
    if data_dir.is_dir() and any(data_dir.iterdir()) and not replace:
        raise FileExistsError(f"restore target is not empty: {data_dir}")

    data_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{data_dir.name}.restore-", dir=data_dir.parent))
    try:
        shutil.copy2(backup_dir / DATABASE_NAME, staging / DATABASE_NAME)
        uploads = backup_dir / "uploads"
        if uploads.is_dir():
            shutil.copytree(uploads, staging / "uploads")
        old_target = None
        if data_dir.exists():
            old_target = data_dir.with_name(f".{data_dir.name}.before-restore-{uuid4().hex}")
            os.replace(data_dir, old_target)
        try:
            os.replace(staging, data_dir)
        except Exception:
            if old_target is not None:
                os.replace(old_target, data_dir)
            raise
        if old_target is not None:
            shutil.rmtree(old_target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return data_dir


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Back up, verify, or restore Signal Desk data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--data-dir", type=Path, required=True)
    backup_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--backup-dir", type=Path, required=True)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--backup-dir", type=Path, required=True)
    restore_parser.add_argument("--data-dir", type=Path, required=True)
    restore_parser.add_argument("--replace", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "backup":
        print(backup_data(args.data_dir, args.output))
    elif args.command == "verify":
        verify_backup(args.backup_dir)
        print("backup verified")
    else:
        print(restore_data(args.backup_dir, args.data_dir, replace=args.replace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
