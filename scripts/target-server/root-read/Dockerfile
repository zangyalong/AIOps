ARG BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

RUN sed -Ei \
        's|https?://deb.debian.org/debian$|http://mirrors.tuna.tsinghua.edu.cn/debian|' \
        /etc/apt/sources.list.d/debian.sources \
    && grep -q \
        '^URIs: http://mirrors.tuna.tsinghua.edu.cn/debian$' \
        /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
        'Acquire::Retries "3";' \
        'Acquire::http::Timeout "30";' \
        'Acquire::https::Timeout "30";' \
        'Acquire::http::Pipeline-Depth "0";' \
        > /etc/apt/apt.conf.d/99aiops-build \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        curl \
        file \
        findutils \
        grep \
        jq \
        libcap2 \
        procps \
        ripgrep \
        systemd \
    && /usr/local/bin/python3.12 -c \
        'import ctypes, json, os, re, selectors, signal, subprocess, sys, time; assert sys.version_info[:2] == (3, 12), sys.version; ctypes.CDLL("libcap.so.2"); from typing import NoReturn' \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/apt/apt.conf.d/99aiops-build

COPY entrypoint.py /usr/local/libexec/aiops/root-read-entrypoint.py

RUN chown root:root /usr/local/libexec/aiops/root-read-entrypoint.py \
    && chmod 0555 /usr/local/libexec/aiops/root-read-entrypoint.py

ENV HOME=/tmp \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PAGER=cat \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SYSTEMD_PAGER=cat

ENTRYPOINT ["/usr/local/bin/python3.12", "/usr/local/libexec/aiops/root-read-entrypoint.py"]
