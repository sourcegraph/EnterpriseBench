# EnterpriseBench task: ccx-vuln-remed-281
# Standard Dockerfile: local clone from sg-evals mirrors
FROM node:20-bookworm

RUN apt-get update && apt-get install -y git curl ca-certificates jq xz-utils && \
    if ! python3 -c 'import tomllib' >/dev/null 2>&1; then apt-get install -y python3 python3-tomli; fi && \
    rm -rf /var/lib/apt/lists/*

# Pre-install Claude Code CLI (avoids per-container npm install at runtime)
RUN npm install -g @anthropic-ai/claude-code@latest

# Create non-root agent user and prepare workspace
RUN useradd -m -s /bin/bash agent && \
    mkdir -p /workspace && chown agent:agent /workspace

# Switch to agent user — all subsequent commands run as agent
USER agent

WORKDIR /workspace

# primary: https://github.com/nodejs/node @ v22.13.0
RUN git clone --depth 1 https://github.com/sg-evals/node--v22.13.0.git /workspace/node

WORKDIR /workspace
