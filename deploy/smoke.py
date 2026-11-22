"""Check a running CyberFlow deployment, including the first OAuth redirect."""

import json
import re
import sys
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import parse_qs, urlsplit


def check(origin: str) -> None:
    site = urlsplit(origin)
    connection_type = HTTPSConnection if site.scheme == "https" else HTTPConnection

    def get(path: str, expected_status: int):
        connection = connection_type(site.hostname, site.port, timeout=10)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            assert response.status == expected_status, f"{path} returned HTTP {response.status}"
            return response.headers, response.read()
        finally:
            connection.close()

    _, body = get("/api/health", 200)
    assert json.loads(body) == {"status": "ok", "db": "ok"}
    _, index = get("/", 200)
    _, results = get("/results", 200)
    assert b'<div id="root">' in index and results == index, "React route did not serve index.html"
    asset = re.search(rb'src="(/assets/[^"]+\.js)"', index)
    assert asset, "Missing compiled JavaScript entry point"
    headers, _ = get(asset[1].decode(), 200)
    assert "javascript" in headers["Content-Type"], "Unexpected JavaScript content type"
    get("/assets/nonexistent-smoke-test.js", 404)
    headers, _ = get("/api/templates", 401)
    assert "application/json" in headers["Content-Type"], "Expected the API response"
    headers, _ = get("/api/auth/github/start", 303)
    redirect = urlsplit(headers["Location"])
    assert (redirect.scheme, redirect.netloc, redirect.path) == ("https", "github.com", "/login/oauth/authorize")
    assert parse_qs(redirect.query)["redirect_uri"] == [origin + "/api/auth/github/callback"]
    print("OK: API, PostgreSQL, frontend routes, static assets and GitHub OAuth redirect")


if __name__ == "__main__":
    check(sys.argv[1].rstrip("/"))
