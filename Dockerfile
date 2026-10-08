FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

ARG APP_UID=1000
ARG APP_GID=1000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        git \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${APP_GID}" mewcode \
    && useradd \
        --uid "${APP_UID}" \
        --gid "${APP_GID}" \
        --create-home \
        --shell /bin/bash \
        mewcode

WORKDIR /opt/mewcode

COPY pyproject.toml ./
COPY mewcode ./mewcode

RUN python -m pip install --no-cache-dir .

RUN mkdir -p /workspace /home/mewcode/.mewcode \
    && chown -R mewcode:mewcode /workspace /home/mewcode

USER mewcode

WORKDIR /workspace

ENTRYPOINT ["mewcode"]
