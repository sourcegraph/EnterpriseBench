# EnterpriseBench task: ccx-migration-202
# Standard Dockerfile: local clone from sg-evals mirrors
FROM gcc:13-bookworm

RUN apt-get update && apt-get install -y git curl ca-certificates jq xz-utils && \
    if ! python3 -c 'import tomllib' >/dev/null 2>&1; then apt-get install -y python3 python3-tomli; fi && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js 20 via official tarball
RUN curl -fsSL https://nodejs.org/dist/v20.18.3/node-v20.18.3-linux-x64.tar.xz | \
    tar -xJ --strip-components=1 -C /usr/local

# Pre-install Claude Code CLI (avoids per-container npm install at runtime)
RUN npm install -g @anthropic-ai/claude-code@latest

# Create non-root agent user and prepare workspace
RUN useradd -m -s /bin/bash agent && \
    mkdir -p /workspace && chown agent:agent /workspace

# Switch to agent user — all subsequent commands run as agent
USER agent

WORKDIR /workspace

# primary: https://github.com/envoyproxy/envoy @ v1.31.2
RUN git clone --depth 1 https://github.com/sg-evals/envoy--v1.31.2.git /workspace/envoy

WORKDIR /workspace
