#!/usr/bin/env python3
"""Package only this task's reviewed deliverables, never sibling Word/PPT files."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    "data": {".json"},
    "docs": {".md"},
    "exports": {".json", ".csv", ".md", ".html", ".sql"},
    "schema": {".sql"},
    "scripts": {".py"},
    "tests": {".py"},
}


def release_files():
    files = [ROOT / name for name in (".gitignore", "README.md", "loom.db")]
    for folder, suffixes in ALLOWED.items():
        files.extend(p for p in (ROOT / folder).glob("*") if p.is_file() and p.suffix in suffixes)
    return sorted(files, key=lambda p: p.relative_to(ROOT).as_posix())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    output = args.output or ROOT / "dist" / f"Loom数据库_{day}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    files = release_files()
    checksums = []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            name = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            archive.writestr("loom-database/" + name, content)
            checksums.append(hashlib.sha256(content).hexdigest() + "  " + name)
        archive.writestr("loom-database/SHA256SUMS.txt", "\n".join(checksums) + "\n")
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(files) + 1
    print(f"Created {output.resolve()} ({len(files)} files + SHA256SUMS)")


if __name__ == "__main__":
    main()
