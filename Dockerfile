# kikimiru — stdlibのみのPythonサーバをCOPYするだけの単段イメージ
FROM python:3.12-slim

LABEL org.opencontainers.image.title="kikimiru" \
      org.opencontainers.image.description="Self-hosted audio player with synchronized slides" \
      org.opencontainers.image.source="https://github.com/cosara22/kikimiru" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

# 非root実行(固定UID/GID)。/state は認証・セッション・進捗の置き場
RUN groupadd -g 10001 kikimiru \
    && useradd -u 10001 -g kikimiru -M -s /usr/sbin/nologin kikimiru \
    && mkdir -p /state /library \
    && chown 10001:10001 /state

WORKDIR /app
COPY LICENSE README.md ./
COPY server/ server/
COPY web/ web/
COPY demo/ demo/

USER 10001:10001
EXPOSE 8484

# 認証必須のため /api/* は401になる。認証免除のシェルで生存確認する
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s CMD \
  ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8484/web/player.html', timeout=4).status==200 else 1)"]

# コンテナ内は0.0.0.0が前提(公開範囲はホスト側のポートバインドで絞る)。
# 設定はCLI引数一本: docker run ... ghcr.io/cosara22/kikimiru --library 本棚=/library
ENTRYPOINT ["python", "/app/server/kikimiru_server.py", "--bind", "0.0.0.0", "--state-dir", "/state"]
CMD []
