"""Download the CMS "Dialysis Facility - Listing by Facility" CSV into data/raw/.

Each new file is saved byte-for-byte as a snapshot and recorded in data/raw/manifest.json.
If a file with the same SHA-256 was already downloaded, nothing is saved (no-op).

Usage:
    python -m ingestion.download                 # URL from .env or the default below
    python -m ingestion.download --url <URL>
"""

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

# CMS changes this URL with each data release. Override with --url or CMS_FACILITY_CSV_URL.
DEFAULT_URL = (
    "https://data.cms.gov/provider-data/sites/default/files/resources/"
    "c04d84bc5c641284494bee4f20f17f9c_1781625941/DFC_FACILITY.csv"
)
DEFAULT_RAW_DIR = Path("data/raw")
MANIFEST_NAME = "manifest.json"
TIMEOUT_SECONDS = 60

# Columns V1 depends on. If any are missing, this is not the file we expect.
REQUIRED_COLUMNS = (
    "CMS Certification Number (CCN)",
    "Facility Name",
    "State",
    "ZIP Code",
    "Five Star",
    "Offers peritoneal dialysis",
    "Offers home hemodialysis training",
)


class DownloadError(Exception):
    """The file could not be downloaded (bad URL, network or HTTP error)."""


class MalformedCSVError(Exception):
    """The downloaded content is not a usable CSV."""


@dataclass
class SnapshotResult:
    status: str  # "downloaded" or "unchanged"
    entry: dict  # the manifest entry for this file


def fetch(url: str) -> bytes:
    """Download url and return the raw bytes, raising DownloadError on any failure."""
    if urlparse(url).scheme not in ("http", "https"):
        raise DownloadError(f"Not an http(s) URL: {url!r}")
    try:
        response = requests.get(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise DownloadError(f"HTTP {exc.response.status_code} downloading {url}") from exc
    except requests.RequestException as exc:
        raise DownloadError(f"Could not download {url}: {exc}") from exc
    return response.content


def validate_csv(content: bytes) -> int:
    """Check content is a well-formed CMS facility CSV and return its data row count."""
    try:
        text = content.decode("utf-8-sig")  # utf-8-sig also accepts a leading BOM
    except UnicodeDecodeError as exc:
        raise MalformedCSVError(f"File is not valid UTF-8 text: {exc}") from exc

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if not header or not any(name.strip() for name in header):
            raise MalformedCSVError("File is empty or has no header row.")

        missing = [col for col in REQUIRED_COLUMNS if col not in header]
        if missing:
            raise MalformedCSVError(f"Header is missing required columns: {missing}")

        row_count = 0
        for row in reader:
            if not row:  # skip completely blank lines
                continue
            if len(row) != len(header):
                raise MalformedCSVError(
                    f"Line {reader.line_num} has {len(row)} fields; header has {len(header)}."
                )
            row_count += 1
    except csv.Error as exc:
        raise MalformedCSVError(f"CSV parse error near line {reader.line_num}: {exc}") from exc

    if row_count == 0:
        raise MalformedCSVError("File has a header but no data rows.")
    return row_count


def load_manifest(raw_dir: Path) -> list[dict]:
    path = raw_dir / MANIFEST_NAME
    if not path.exists():
        return []
    return json.loads(path.read_text())["snapshots"]


def _write_atomically(path: Path, data: bytes) -> None:
    """Write to a temp file, then rename, so a crash never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_snapshot(content: bytes, source_url: str, raw_dir: Path, now: datetime) -> SnapshotResult:
    """Validate content and save it as a new snapshot, unless the same SHA-256 already exists."""
    row_count = validate_csv(content)  # raises before anything is written
    sha256 = hashlib.sha256(content).hexdigest()

    snapshots = load_manifest(raw_dir)
    for entry in snapshots:
        if entry["sha256"] == sha256:
            return SnapshotResult(status="unchanged", entry=entry)

    filename = Path(urlparse(source_url).path).name or "download.csv"
    snapshot_dir = raw_dir / now.strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    _write_atomically(snapshot_dir / filename, content)

    entry = {
        "source_url": source_url,
        "downloaded_at_utc": now.isoformat(),
        "sha256": sha256,
        "row_count": row_count,
        "filename": filename,
        "path": str((snapshot_dir / filename).relative_to(raw_dir)),
    }
    snapshots.append(entry)
    manifest = json.dumps({"snapshots": snapshots}, indent=2) + "\n"
    _write_atomically(raw_dir / MANIFEST_NAME, manifest.encode("utf-8"))
    return SnapshotResult(status="downloaded", entry=entry)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Download the CMS dialysis facility CSV.")
    parser.add_argument("--url", default=os.getenv("CMS_FACILITY_CSV_URL") or DEFAULT_URL)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    args = parser.parse_args(argv)

    try:
        content = fetch(args.url)
        result = save_snapshot(content, args.url, args.raw_dir, datetime.now(timezone.utc))
    except (DownloadError, MalformedCSVError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    e = result.entry
    if result.status == "unchanged":
        print(f"No change: identical file already downloaded at {e['downloaded_at_utc']}.")
        print(f"  Existing snapshot: {args.raw_dir / e['path']}  (nothing saved)")
    else:
        print(f"Downloaded new snapshot: {args.raw_dir / e['path']}")
        print(f"  rows: {e['row_count']}  sha256: {e['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
