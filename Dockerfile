# aicodebox-base — agent-agnostic foundation image.
#
# Provides: Ubuntu 24.04, Node.js LTS, Python 3.14, Docker CE, `aicode` user
# with passwordless sudo, the `aicodebox` python package (adapters + modes),
# and a stock entrypoint that handles UID/GID rematch, docker socket GID,
# auth-env loading, and mode dispatch (api / telegram / cron).
#
# Child images:
#   FROM psyb0t/aicodebox
#   - install your agent binary
#   - uv pip install --system your adapter package
#   - ENV AICODEBOX_ADAPTER=yourpkg.adapter:YourAdapter
FROM ubuntu:24.04@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254 AS python-builder

ARG PYTHON_VERSION=3.14.8
ARG PYTHON_SHA256=c2215904f02b175596dc49351585104f4bc20341e1c47378b26a2c274360ce73

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    libbz2-dev \
    libffi-dev \
    libgdbm-dev \
    liblzma-dev \
    libncursesw5-dev \
    libreadline-dev \
    libsqlite3-dev \
    libssl-dev \
    uuid-dev \
    xz-utils \
    zlib1g-dev \
    && curl -fsSL "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz" \
        -o /tmp/python.tar.xz \
    && echo "${PYTHON_SHA256}  /tmp/python.tar.xz" | sha256sum -c - \
    && mkdir -p /tmp/python-src \
    && tar -xJf /tmp/python.tar.xz --strip-components=1 -C /tmp/python-src \
    && cd /tmp/python-src \
    && ./configure --prefix=/opt/python --with-ensurepip=install \
    && make -j"$(nproc)" \
    && make altinstall \
    && rm -rf /var/lib/apt/lists/* /tmp/python.tar.xz /tmp/python-src

FROM ubuntu:24.04@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget gnupg ca-certificates openssh-client sudo unzip xz-utils \
    software-properties-common lsb-release jq \
    libbz2-1.0 libffi8 libgdbm6 liblzma5 libncursesw6 libreadline8 \
    libsqlite3-0 libssl3 libuuid1 zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Node.js LTS. Install the official, checksum-verified archive so builds do not
# float with a third-party apt repository.
ARG NODE_VERSION=24.21.0
ARG NODE_SHA256_amd64=fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6
ARG NODE_SHA256_arm64=6ad1325edbdb5649c379b75a237147a666c95d4f9ae8d340fef2d1575d289ad2
ARG TARGETARCH
RUN ARCH="${TARGETARCH:-amd64}" && \
    case "$ARCH" in \
        amd64) NODE_ARCH=x64; NODE_SHA256="${NODE_SHA256_amd64}" ;; \
        arm64) NODE_ARCH=arm64; NODE_SHA256="${NODE_SHA256_arm64}" ;; \
        *) echo "unsupported TARGETARCH: ${ARCH}" >&2; exit 1 ;; \
    esac && \
    NODE_ARCHIVE="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" && \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_ARCHIVE}" \
        -o "/tmp/${NODE_ARCHIVE}" && \
    echo "${NODE_SHA256}  /tmp/${NODE_ARCHIVE}" | sha256sum -c - && \
    tar -xJf "/tmp/${NODE_ARCHIVE}" --strip-components=1 -C /usr/local && \
    rm -f "/tmp/${NODE_ARCHIVE}" && \
    node --version | grep -Fx "v${NODE_VERSION}"

COPY --from=python-builder /opt/python /usr/local
RUN ln -s /usr/local /opt/python && \
    ln -sf /usr/local/bin/python3.14 /usr/local/bin/python3 && \
    ln -sf /usr/local/bin/python3.14 /usr/local/bin/python && \
    ln -sf /usr/local/bin/pip3.14 /usr/local/bin/pip3 && \
    ln -sf /usr/local/bin/pip3.14 /usr/local/bin/pip && \
    python3 --version | grep -Fx "Python 3.14.8"

COPY --from=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1

# Docker CE for docker-in-docker workflows when the host socket is mounted.
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*

# `aicode` user — passwordless sudo + docker group.
RUN userdel -r ubuntu 2>/dev/null || true && \
    useradd -u 1000 -ms /bin/bash aicode && \
    usermod -aG sudo aicode && \
    usermod -aG docker aicode && \
    mkdir -p /home/aicode/.ssh && \
    ssh-keyscan github.com gitlab.com bitbucket.org >> /home/aicode/.ssh/known_hosts 2>/dev/null && \
    chown -R aicode:aicode /home/aicode

COPY <<EOF /etc/sudoers.d/aicode-nopass
aicode ALL=(ALL) NOPASSWD:ALL
EOF
RUN chmod 440 /etc/sudoers.d/aicode-nopass

# Install the aicodebox python package (adapters contract + api/telegram/cron/
# mcp modes). uv export turns the lockfile into a pinned requirements list;
# uv pip install --system installs those into the system Python so child images
# can layer packages on top without needing a venv.
COPY pyproject.toml uv.lock /opt/aicodebox/
COPY aicodebox /opt/aicodebox/aicodebox
RUN --mount=type=cache,target=/root/.cache/uv \
    cd /opt/aicodebox && \
    uv export --frozen --no-dev -o /tmp/aicodebox-reqs.txt && \
    uv pip install --system --break-system-packages -r /tmp/aicodebox-reqs.txt

RUN mkdir -p /workspace && chown -R aicode:aicode /workspace
WORKDIR /workspace

COPY entrypoint.sh /usr/local/bin/aicodebox-entrypoint
RUN chmod +x /usr/local/bin/aicodebox-entrypoint

# Scripts in the state dir's bin/ are on PATH for `docker exec` shells too; the
# entrypoint sets the same order for every mode it starts.
ENV AICODE_WORKSPACE=/workspace \
    PYTHONUNBUFFERED=1 \
    PATH="/home/aicode/.aicodebox/bin:${PATH}"

ENTRYPOINT ["/usr/local/bin/aicodebox-entrypoint"]
