"""Single-source templates for pzi-managed helper services."""

from __future__ import annotations

TRANSLATION_SERVER_CONTAINERFILE = """FROM node:lts-alpine

RUN apk add --no-cache git

WORKDIR /app

RUN git clone --depth=1 https://github.com/zotero/translation-server.git . && \
    git clone --depth=1 https://github.com/zotero/translators.git modules/translators/ && \
    git clone --depth=1 https://github.com/zotero/utilities.git modules/utilities/ && \
    git clone --depth=1 https://github.com/zotero/translate.git modules/translate/ && \
    git clone --depth=1 https://github.com/zotero/zotero-schema.git modules/zotero-schema/

RUN npm install

EXPOSE 1969
CMD ["npm", "start"]
"""

_BASE_COMPOSE = """services:
  translation-server:
    build:
      context: ./containers/translation-server
      dockerfile: Containerfile
    ports:
      - "1969:1969"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://127.0.0.1:1969"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
"""

_FLARESOLVERR_COMPOSE = """
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    ports:
      - "8191:8191"
    restart: unless-stopped
    environment:
      - LOG_LEVEL=info
      - LOG_HTML=false
      - CAPTCHA_SOLVER=none
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:8191/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
"""


def render_compose(*, with_flaresolverr: bool) -> str:
    """Render managed compose YAML for local helper services."""
    text = _BASE_COMPOSE
    if with_flaresolverr:
        text += _FLARESOLVERR_COMPOSE
    return text
