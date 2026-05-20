# aicodebox-base — agent-agnostic foundation image.
#
# Provides: Ubuntu 24.04, Node.js LTS, Python 3.12, Docker CE, `aicode` user
# with passwordless sudo, the `aicodebox` python package (adapters + modes),
# and a stock entrypoint that handles UID/GID rematch, docker socket GID,
# auth-env loading, and mode dispatch (api / telegram / cron).
#
# Child images:
#   FROM psyb0t/aicodebox
#   - install your agent binary
#   - uv pip install --system your adapter package
#   - ENV AICODEBOX_ADAPTER=yourpkg.adapter:YourAdapter
FROM ubuntu:24.04@sha256:c4a8d5503dfb2a3eb8ab5f807da5bc69a85730fb49b5cfca2330194ebcc41c7b

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git curl wget gnupg ca-certificates sudo unzip \
    software-properties-common lsb-release jq \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 (LTS at time of writing) — child agents that ship as npm
# packages reuse this.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# Python — uv manages all package installs.
RUN apt-get update && apt-get install -y \
    python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /usr/local/bin/uv

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

ENV AICODE_WORKSPACE=/workspace \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/local/bin/aicodebox-entrypoint"]
