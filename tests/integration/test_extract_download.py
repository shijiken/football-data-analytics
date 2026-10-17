import dataclasses
import hashlib
import socket
import stat
from pathlib import Path

import psycopg
import pytest

from football_stats import pipeline
from football_stats.config import Settings
from football_stats.errors import SourceUnavailableError
from football_stats.extract import fetch_to_raw
from tests.conftest import fixture_path
from tests.integration import helpers
from tests.integration.conftest import LoopbackServer

FIXTURE = "matches_valid.csv"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_download_keeps_unmodified_copy(tmp_path: Path) -> None:
    source = fixture_path(FIXTURE)
    raw = fetch_to_raw(source.as_uri(), tmp_path / "raw")
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert raw.sha256 == digest
    assert raw.bytes == len(data)
    assert raw.source_url == source.as_uri()
    assert raw.path == tmp_path / "raw" / f"matches-{digest}.csv"
    assert raw.path.read_bytes() == data
    assert stat.S_IMODE(raw.path.stat().st_mode) == 0o444


def test_local_path_is_copied_the_same_way(tmp_path: Path) -> None:
    source = fixture_path(FIXTURE)
    raw = fetch_to_raw(source, tmp_path / "raw")
    assert raw.path.read_bytes() == source.read_bytes()
    assert raw.source_url == source.resolve().as_uri()


def test_download_http_loopback_streams_and_hashes(
    tmp_path: Path, loopback_http_server: LoopbackServer
) -> None:
    body = fixture_path(FIXTURE).read_bytes() * 50  # a few hundred KB: several chunks
    loopback_http_server.routes["/Matches.csv"] = (200, {}, body, None)
    url = f"{loopback_http_server.base_url}/Matches.csv"
    raw = fetch_to_raw(url, tmp_path / "raw")
    assert raw.sha256 == hashlib.sha256(body).hexdigest()
    assert raw.bytes == len(body)
    assert raw.source_url == url
    assert raw.path.read_bytes() == body


def test_existing_raw_copy_reused(tmp_path: Path) -> None:
    source = fixture_path(FIXTURE).as_uri()
    first = fetch_to_raw(source, tmp_path / "raw")
    second = fetch_to_raw(source, tmp_path / "raw")
    assert first == second
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == [first.path.name]


def test_unreachable_source_raises(tmp_path: Path, loopback_http_server: LoopbackServer) -> None:
    with pytest.raises(SourceUnavailableError):
        fetch_to_raw(f"{loopback_http_server.base_url}/missing.csv", tmp_path / "raw")
    with pytest.raises(SourceUnavailableError):
        fetch_to_raw(f"http://127.0.0.1:{_free_port()}/x.csv", tmp_path / "raw")
    with pytest.raises(SourceUnavailableError):
        fetch_to_raw(tmp_path / "does-not-exist.csv", tmp_path / "raw")
    assert not (tmp_path / "raw").exists() or not any((tmp_path / "raw").iterdir())


def test_unparsable_content_length_is_source_unavailable(
    tmp_path: Path, loopback_http_server: LoopbackServer
) -> None:
    loopback_http_server.routes["/x.csv"] = (200, {"Content-Length": "abc"}, b"a,b\n", None)
    with pytest.raises(SourceUnavailableError):
        fetch_to_raw(f"{loopback_http_server.base_url}/x.csv", tmp_path / "raw")


def test_unwritable_raw_dir_is_source_unavailable(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(SourceUnavailableError):
        fetch_to_raw(fixture_path(FIXTURE), blocker / "raw")


def test_interrupted_download_leaves_no_file_and_db_unchanged(
    settings: Settings, loopback_http_server: LoopbackServer, db_conn: psycopg.Connection
) -> None:
    body = fixture_path(FIXTURE).read_bytes()
    loopback_http_server.routes["/short.csv"] = (
        200,
        {"Content-Length": str(len(body))},
        body,
        len(body) // 2,
    )
    broken = dataclasses.replace(settings, source_url=f"{loopback_http_server.base_url}/short.csv")
    before = helpers.snapshot(db_conn)
    with pytest.raises(SourceUnavailableError):
        pipeline.run(broken)
    raw_dir = settings.raw_data_dir
    assert not raw_dir.exists() or not list(raw_dir.iterdir())
    assert helpers.snapshot(db_conn) == before
