# Python 3.11 系 (.tool-versions / CI と同じメジャー・マイナー)
FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

# Claude Code を vscode ユーザーの ~/.local/bin にインストールする。
# プロキシが必要な環境では Docker 側のプロキシ設定 (Docker Desktop の Proxies /
# daemon.json の proxies) がビルド時に http_proxy / https_proxy として渡される。
USER vscode
RUN curl -fsSL https://claude.ai/install.sh | bash
USER root

# claude と pip install --user の両方がここに入る
ENV PATH="/home/vscode/.local/bin:${PATH}"

CMD ["sleep", "infinity"]
