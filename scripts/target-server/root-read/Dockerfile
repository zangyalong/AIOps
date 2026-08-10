FROM debian:bookworm-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        curl \
        file \
        findutils \
        grep \
        jq \
        procps \
        python3-minimal \
        ripgrep \
        systemd \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.py /usr/local/libexec/aioms/root-read-entrypoint.py

RUN chown root:root /usr/local/libexec/aioms/root-read-entrypoint.py \
    && chmod 0555 /usr/local/libexec/aioms/root-read-entrypoint.py

ENV HOME=/tmp \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PAGER=cat \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SYSTEMD_PAGER=cat

ENTRYPOINT ["/usr/bin/python3", "/usr/local/libexec/aioms/root-read-entrypoint.py"]
