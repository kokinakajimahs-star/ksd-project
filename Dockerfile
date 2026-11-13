# 本番のコンテナ（設計書 1.1・1.2・2.1。CR-020）。サーバが画面のビルドも同じオリジンで配る 1 コンテナ。
#
#   app （既定。最後の段）：uvicorn でサーバを起動する。Cloud Run のサービスと、DB の移行のジョブ（alembic）が使う
#   test（--target test） ：backend/tests を pytest で流す。PostgreSQL での検証を Cloud Run のジョブで流すため
#
# ビルドは Cloud Build で行う（deploy/cloudbuild.yaml）。基のイメージはタグとダイジェストで固定する
# （node は frontend/package.json の engines の範囲の LTS、python は設計書 1.1 の 3.12 の slim）。

ARG NODE_IMAGE=docker.io/library/node:24.22.3-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54
ARG PYTHON_IMAGE=docker.io/library/python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# --- 画面のビルド（frontend/dist） -------------------------------------------------------------
FROM ${NODE_IMAGE} AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- サーバの共通の部分（依存・コード。test と app が使う） -------------------------------------
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
# 業務の時間帯（PMT_BUSINESS_TIMEZONE。既定 Asia/Tokyo）を zoneinfo で引けるよう tzdata を入れ、引けることを確かめる
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && python -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Tokyo')"
WORKDIR /app
# 依存は版を固定した backend/requirements.lock から入れる（設計書 1.1）
COPY backend/requirements.lock backend/requirements.lock
RUN python -m venv /opt/venv \
    && pip install -r backend/requirements.lock
# root でない利用者で動かす（ファイルは root の持ち物のまま。読むだけ）
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin pmt
COPY backend/pyproject.toml backend/alembic.ini backend/
COPY backend/alembic backend/alembic
COPY backend/app backend/app

# --- PostgreSQL での検証（Cloud Run のジョブ。PMT_TEST_DATABASE_URL を渡す） --------------------
FROM base AS test
COPY backend/tests backend/tests
USER pmt
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "backend/tests"]

# --- 本番（既定の段） --------------------------------------------------------------------------
FROM base AS app
COPY --from=frontend /src/frontend/dist frontend/dist
ENV PMT_STATIC_DIR=/app/frontend/dist
USER pmt
EXPOSE 8080
# PORT は Cloud Run が渡す。Cloud Run の前段（Google のフロントエンド）が付ける X-Forwarded-* を信じる
CMD ["sh", "-c", "exec uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
