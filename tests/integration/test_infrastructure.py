import urllib.request

import psycopg

from tests.integration.conftest import LoopbackServer


def test_empty_db_accepts_connections(empty_db_url: str) -> None:
    with psycopg.connect(empty_db_url) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_loopback_server_serves_bytes(loopback_http_server: LoopbackServer) -> None:
    loopback_http_server.routes["/a.csv"] = (200, {}, b"hello", None)
    with urllib.request.urlopen(f"{loopback_http_server.base_url}/a.csv", timeout=5) as resp:
        assert resp.read() == b"hello"
