from fastapi.testclient import TestClient

from app.main import UI_DIR, app

client = TestClient(app)


def test_ui_files_are_served_from_the_ui_folder():
    response = client.get("/README.md")
    assert response.status_code == 200
    assert response.text == (UI_DIR / "README.md").read_text(encoding="utf-8")


def test_api_routes_and_docs_match_before_the_ui():
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api").json()["docs"] == "/docs"
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_unknown_paths_are_not_found():
    assert client.get("/api/no-such-route").status_code == 404
    assert client.get("/no-such-file.js").status_code == 404


def test_paths_outside_the_ui_folder_are_not_served():
    assert client.get("/../app/main.py").status_code == 404
    assert client.get("/%2e%2e/app/main.py").status_code == 404


def test_cockpit_files_are_revalidated_on_every_load():
    assert client.get("/js/demo.js").headers["cache-control"] == "no-cache"
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/api/health").headers
