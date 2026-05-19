# aicodebox-base — agent-agnostic foundation image.
#
# Provides: Ubuntu 24.04, Node.js LTS, Python 3.12, Docker CE, `aicode` user
# with passwordless sudo, the `aicodebox` python package (adapters + modes),
# and a stock entrypoint that handles UID/GID rematch, docker socket GID,
# auth-env loading, and mode dispatch (api / telegram / cron).
#
# Child images:
#   FROM aicodebox-base:local
#   - install your agent binary
#   - pip install your adapter package
#   - ENV AICODEBOX_ADAPTER=yourpkg.adapter:YourAdapter
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN sed -i 's|http://archive.ubuntu.com|http://cloudflaremirrors.com|g; s|http://security.ubuntu.com|http://cloudflaremirrors.com|g' /etc/apt/sources.list.d/ubuntu.sources || true

RUN apt-get update && apt-get install -y \
    git curl wget gnupg ca-certificates sudo unzip \
    software-properties-common lsb-release jq \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 (LTS at time of writing) — child agents that ship as npm
# packages reuse this.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# Python + shared deps used by all modes. Pinned floors keep behaviour stable;
# upper caps prevent surprise major bumps.
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

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
# mcp modes). Editable install so child Dockerfile changes don't require a
# rebuild of this layer if they overlay the package.
COPY pyproject.toml /opt/aicodebox/pyproject.toml
COPY aicodebox /opt/aicodebox/aicodebox
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed /opt/aicodebox

RUN mkdir -p /workspace && chown -R aicode:aicode /workspace
WORKDIR /workspace

COPY entrypoint.sh /usr/local/bin/aicodebox-entrypoint
RUN chmod +x /usr/local/bin/aicodebox-entrypoint

ENV AICODE_WORKSPACE=/workspace \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/local/bin/aicodebox-entrypoint"]
