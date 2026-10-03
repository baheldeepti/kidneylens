"""Tests for ingestion/download.py. No network: HTTP calls are replaced with fakes."""

import json
from datetime import datetime, timezone

import pytest
import requests

from ingestion import download
from ingestion.download import (
    DownloadError,
    MalformedCSVError,
    fetch,
    main,
    save_snapshot,
    validate_csv,
)

URL = "https://example.test/files/DFC_FACILITY.csv"
NOW = datetime(2026, 10, 3, 8, 15, 30, tzinfo=timezone.utc)
HEADER = ",".join(download.REQUIRED_COLUMNS)

# Two facilities: leading-zero CCN/ZIP, a blank star rating, a quoted name with a comma.
GOOD_CSV = (
    HEADER + "\n"
    '012306,CHILDRENS HOSPITAL DIALYSIS,AL,03561,,Yes,No\n'
    '"052345","DIALYSIS CENTER, NORTH",CA,90001,4,No,\n'
).encode("utf-8")


def fake_response(status: int, content: bytes = b"") -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = content
    response.url = URL
    return response


# --- validate_csv -------------------------------------------------------------

def test_validate_counts_data_rows():
    assert validate_csv(GOOD_CSV) == 2


def test_validate_accepts_byte_order_mark():
    assert validate_csv(b"\xef\xbb\xbf" + GOOD_CSV) == 2


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "empty"),
        (HEADER.encode() + b"\n", "no data rows"),
        (b"Facility Name,State\nX,AL\n", "missing required columns"),
        (GOOD_CSV + b"999999,TOO,FEW\n", "has 3 fields"),
        (GOOD_CSV + b'"unterminated quote,AL\n', "parse error"),
        (b"\xff\xfe\x00bad", "not valid UTF-8"),
        (b"<!DOCTYPE html><html>Service unavailable</html>", "missing required columns"),
    ],
    ids=["empty", "header-only", "wrong-columns", "ragged-row", "bad-quote", "not-utf8", "html-page"],
)
def test_validate_rejects_malformed(content, message):
    with pytest.raises(MalformedCSVError, match=message):
        validate_csv(content)


# --- save_snapshot ------------------------------------------------------------

def test_save_snapshot_preserves_file_and_records_metadata(tmp_path):
    result = save_snapshot(GOOD_CSV, URL, tmp_path, NOW)

    assert result.status == "downloaded"
    saved = tmp_path / "20261003T081530Z" / "DFC_FACILITY.csv"
    assert saved.read_bytes() == GOOD_CSV  # byte-for-byte original, leading zeros intact

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["snapshots"] == [
        {
            "source_url": URL,
            "downloaded_at_utc": "2026-10-03T08:15:30+00:00",
            "sha256": download.hashlib.sha256(GOOD_CSV).hexdigest(),
            "row_count": 2,
            "filename": "DFC_FACILITY.csv",
            "path": "20261003T081530Z/DFC_FACILITY.csv",
        }
    ]


def test_same_sha256_is_a_noop(tmp_path):
    first = save_snapshot(GOOD_CSV, URL, tmp_path, NOW)
    later = datetime(2026, 11, 1, tzinfo=timezone.utc)
    second = save_snapshot(GOOD_CSV, URL, tmp_path, later)

    assert second.status == "unchanged"
    assert second.entry == first.entry
    assert not (tmp_path / "20261101T000000Z").exists()
    assert len(json.loads((tmp_path / "manifest.json").read_text())["snapshots"]) == 1


def test_changed_content_creates_new_snapshot(tmp_path):
    save_snapshot(GOOD_CSV, URL, tmp_path, NOW)
    changed = GOOD_CSV + b"063333,NEW FACILITY,TX,75001,3,Yes,Yes\n"
    result = save_snapshot(changed, URL, tmp_path, datetime(2026, 11, 1, tzinfo=timezone.utc))

    assert result.status == "downloaded"
    assert result.entry["row_count"] == 3
    assert len(json.loads((tmp_path / "manifest.json").read_text())["snapshots"]) == 2


def test_malformed_csv_writes_nothing(tmp_path):
    with pytest.raises(MalformedCSVError):
        save_snapshot(b"not,a,cms,file\n1,2,3,4\n", URL, tmp_path, NOW)
    assert list(tmp_path.iterdir()) == []


# --- fetch --------------------------------------------------------------------

def test_fetch_returns_bytes(monkeypatch):
    monkeypatch.setattr(download.requests, "get", lambda url, timeout: fake_response(200, GOOD_CSV))
    assert fetch(URL) == GOOD_CSV


def test_fetch_http_error_fails_clearly(monkeypatch):
    monkeypatch.setattr(download.requests, "get", lambda url, timeout: fake_response(404))
    with pytest.raises(DownloadError, match="HTTP 404"):
        fetch(URL)


def test_fetch_network_error_fails_clearly(monkeypatch):
    def boom(url, timeout):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(download.requests, "get", boom)
    with pytest.raises(DownloadError, match="Could not download"):
        fetch(URL)


def test_fetch_rejects_non_http_url():
    with pytest.raises(DownloadError, match="Not an http"):
        fetch("file:///etc/passwd")


# --- main (command line) ------------------------------------------------------

def test_main_downloads_then_reports_noop(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(download.requests, "get", lambda url, timeout: fake_response(200, GOOD_CSV))
    args = ["--url", URL, "--raw-dir", str(tmp_path)]

    assert main(args) == 0
    assert "Downloaded new snapshot" in capsys.readouterr().out

    assert main(args) == 0
    assert "No change" in capsys.readouterr().out


def test_main_returns_1_on_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(download.requests, "get", lambda url, timeout: fake_response(500))

    assert main(["--url", URL, "--raw-dir", str(tmp_path)]) == 1
    assert "ERROR: HTTP 500" in capsys.readouterr().err
