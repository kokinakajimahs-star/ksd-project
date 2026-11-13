"""構成づくりの試験：アプリが外へ出すもの（設計書 1.2・6.1、CR-019 の 1・CR-020）。

- API の説明（``/docs``・``/redoc``・``/api/v1/openapi.json``）は ``PMT_API_DOCS`` が真のときだけ出す
- ``PMT_STATIC_DIR`` を指定すると、画面のビルドを同じオリジンで配る（``/api`` で始まらないパスでファイルが無ければ ``index.html``）
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

DOCS_PATHS = ("/docs", "/redoc", "/api/v1/openapi.json", "/docs/oauth2-redirect")
NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "対象が見つかりません", "details": []}}
INDEX_HTML = '<!doctype html><html lang="ja"><body><div id="root"></div></body></html>\n'
ASSET_JS = "console.log('pmt');\n"
SECRET = "dist の外の秘密\n"
LONG_CACHE = "public, max-age=31536000, immutable"

BuildApp = Callable[..., FastAPI]


@pytest.fixture()
def build_app(settings: Settings) -> Iterator[BuildApp]:
    """設定の一部を差し替えてアプリを作る関数（環境変数の PMT_API_DOCS・PMT_STATIC_DIR に左右されないよう明示する）。"""
    built: list[FastAPI] = []

    def build(*, api_docs: bool = False, static_dir: Path | None = None) -> FastAPI:
        application = create_app(settings.model_copy(update={"api_docs": api_docs, "static_dir": static_dir}))
        built.append(application)
        return application

    yield build
    for application in built:
        application.state.engine.dispose()


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    """画面のビルドの代わり（index.html・ハッシュ付きの assets・名前の変わらないファイル）と、その外の秘密のファイル。"""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "assets" / "index-3f9a1c.js").write_text(ASSET_JS, encoding="utf-8")
    (root / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    (root / "leak.txt").symlink_to(tmp_path / "secret.txt")
    return root


# ---------------------------------------------------------------------------
# API の説明（6.1・CR-019 の 1）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_api_docs_are_hidden_by_default(build_app: BuildApp, path: str) -> None:
    """既定（PMT_API_DOCS が偽）では API の説明を出さない。無いパスと同じ 404（2.4 の形）。"""
    with TestClient(build_app()) as client:
        response = client.get(path)
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_api_docs_default_setting_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PMT_API_DOCS", raising=False)
    assert Settings().api_docs is False


def test_api_docs_are_served_when_enabled(build_app: BuildApp) -> None:
    with TestClient(build_app(api_docs=True)) as client:
        schema = client.get("/api/v1/openapi.json")
        docs = client.get("/docs")
        redoc = client.get("/redoc")
    assert schema.status_code == 200
    assert "/api/v1/health" in schema.json()["paths"]
    assert docs.status_code == 200 and "/api/v1/openapi.json" in docs.text
    assert redoc.status_code == 200 and "/api/v1/openapi.json" in redoc.text


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_api_docs_stay_hidden_when_serving_screens(build_app: BuildApp, dist: Path, path: str) -> None:
    """画面を配るときも、API の説明のパスは index.html にせず 404 のまま（画面のルートと取り違えない）。"""
    with TestClient(build_app(static_dir=dist)) as client:
        response = client.get(path, headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_api_docs_win_over_screens_when_enabled(build_app: BuildApp, dist: Path) -> None:
    with TestClient(build_app(api_docs=True, static_dir=dist)) as client:
        response = client.get("/docs")
    assert response.status_code == 200
    assert "/api/v1/openapi.json" in response.text


# ---------------------------------------------------------------------------
# 画面を配る（1.2・CR-020）
# ---------------------------------------------------------------------------


def test_screens_are_not_served_without_static_dir(build_app: BuildApp) -> None:
    """指定が無ければ今と同じ：/api の外も無いパスの 404（2.4 の形）。"""
    with TestClient(build_app()) as client:
        for path in ("/", "/projects/1/gantt", "/assets/index-3f9a1c.js"):
            response = client.get(path)
            assert response.status_code == 404, path
            assert response.json() == NOT_FOUND


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_index_is_served_without_cache(build_app: BuildApp, dist: Path, path: str) -> None:
    with TestClient(build_app(static_dir=dist)) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.text == INDEX_HTML
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path", ["/projects/12/gantt", "/projects/12/status-reports/3/meeting", "/shared/abc", "/login", "/apiary"]
)
def test_deep_links_fall_back_to_index(build_app: BuildApp, dist: Path, path: str) -> None:
    """ファイルの無いパス（画面のルート。BrowserRouter の深いリンク）は index.html を返し、画面が経路を解く。"""
    with TestClient(build_app(static_dir=dist)) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.text == INDEX_HTML
    assert response.headers["cache-control"] == "no-cache"


def test_hashed_assets_get_long_cache(build_app: BuildApp, dist: Path) -> None:
    with TestClient(build_app(static_dir=dist)) as client:
        response = client.get("/assets/index-3f9a1c.js")
        head = client.head("/assets/index-3f9a1c.js")
    assert response.status_code == 200
    assert response.text == ASSET_JS
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == LONG_CACHE
    assert head.status_code == 200 and head.content == b""
    assert head.headers["cache-control"] == LONG_CACHE


def test_other_files_are_revalidated(build_app: BuildApp, dist: Path) -> None:
    """名前に内容のハッシュを持たないファイル（assets/ の外）は長く持たせない。"""
    with TestClient(build_app(static_dir=dist)) as client:
        response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "no-cache"


def test_missing_asset_falls_back_without_long_cache(build_app: BuildApp, dist: Path) -> None:
    """無いファイルの代わりの index.html を、長いキャッシュで返さない（次の配置で取り違えたままにならない）。"""
    with TestClient(build_app(static_dir=dist)) as client:
        response = client.get("/assets/index-old.js")
    assert response.status_code == 200
    assert response.text == INDEX_HTML
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/api", "/api/", "/api/v1/no-such-resource", "/api/v2/projects"])
def test_api_paths_keep_json_not_found(build_app: BuildApp, dist: Path, path: str) -> None:
    """/api で始まるパスは画面に渡さない。無ければ今どおり 404（2.4 の形）。ブラウザで開いても index.html にしない。"""
    with TestClient(build_app(static_dir=dist)) as client:
        response = client.get(path, headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_api_still_works_when_serving_screens(build_app: BuildApp, dist: Path) -> None:
    with TestClient(build_app(static_dir=dist)) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("static", [False, True])
def test_api_method_mismatch_stays_405(build_app: BuildApp, dist: Path, static: bool) -> None:
    """API のパスでメソッドだけが違えば、画面を配るときも 405（画面のルートが GET を横取りしない）。"""
    application = build_app(static_dir=dist if static else None)

    @application.post("/api/v1/_probe/post-only")
    def probe() -> dict[str, str]:
        return {"ok": "yes"}

    with TestClient(application) as client:
        response = client.get("/api/v1/_probe/post-only")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_writes_to_screen_paths_are_not_served(build_app: BuildApp, dist: Path) -> None:
    with TestClient(build_app(static_dir=dist)) as client:
        response = client.post("/projects/12/gantt")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.parametrize(
    "path",
    [
        "/..%2fsecret.txt",
        "/%2e%2e/secret.txt",
        "/%2e%2e%2fsecret.txt",
        "/assets/..%2f..%2fsecret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/leak.txt",  # dist の中から外を指すシンボリックリンク
        "/a%00b",
    ],
)
def test_paths_outside_dist_are_never_read(build_app: BuildApp, dist: Path, path: str) -> None:
    """パスの横断・外を指すリンクで dist の外を読ませない（index.html を返すだけ）。"""
    with TestClient(build_app(static_dir=dist)) as client:
        response = client.get(path)
    assert SECRET not in response.text
    assert response.status_code == 200
    assert response.text == INDEX_HTML


def test_static_dir_without_index_fails_fast(build_app: BuildApp, tmp_path: Path) -> None:
    empty = tmp_path / "empty-dist"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="index.html"):
        build_app(static_dir=empty)
