# syntax=docker/dockerfile:1.6
FROM ubuntu:24.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    N_PREFIX=/opt/n \
    BUN_INSTALL=/opt/bun \
    PYENV_ROOT=/opt/pyenv \
    PATH=/opt/pyenv/shims:/opt/pyenv/bin:$N_PREFIX/bin:$BUN_INSTALL/bin:$CARGO_HOME/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    build-essential \
    cmake \
    pkg-config \
    python3 \
    python3-venv \
    python3-pip \
    curl \
    wget \
    unzip \
    ca-certificates \
    openssl \
    libssl-dev \
    libffi-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    zlib1g-dev \
    liblzma-dev \
    jq \
    ripgrep \
    fd-find \
    tree \
    make \
    bash-completion \
    npm \
    nodejs \
    sudo \
    gnupg \
    xz-utils && \
    ln -s /usr/bin/fdfind /usr/local/bin/fd && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js via n (LTS)
RUN npm install -g n && \
    n lts

# Install Bun
RUN curl -fsSL https://bun.sh/install | bash

# Install Rust toolchain (minimal profile, stable)
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y --profile minimal --default-toolchain stable

# Install PyEnv
RUN git clone https://github.com/pyenv/pyenv.git "$PYENV_ROOT"

# Remove default ubuntu user if present so we can reuse UID 1000
RUN if id -u ubuntu >/dev/null 2>&1; then userdel -r ubuntu || true; fi


# Create forklift user and harness directory
RUN useradd -m -u 1000 -U forklift && \
    mkdir -p /opt/forklift/harness && \
    chown -R forklift:forklift /opt/forklift "$CARGO_HOME" "$RUSTUP_HOME" "$BUN_INSTALL" "$N_PREFIX"

# Profile setup for pyenv + cargo + bun + n
RUN cat <<'EOF' >/etc/profile.d/forklift.sh
export PYENV_ROOT="$PYENV_ROOT"
export CARGO_HOME="$CARGO_HOME"
export RUSTUP_HOME="$RUSTUP_HOME"
export N_PREFIX="$N_PREFIX"
export BUN_INSTALL="$BUN_INSTALL"
export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$N_PREFIX/bin:$BUN_INSTALL/bin:$CARGO_HOME/bin:$PATH"
if command -v pyenv >/dev/null 2>&1; then
  eval "$(pyenv init -)"
fi
EOF

COPY harness/ /opt/forklift/harness/
RUN chmod +x /opt/forklift/harness/run.sh && \
    chown -R forklift:forklift /opt/forklift "$CARGO_HOME" "$RUSTUP_HOME" "$BUN_INSTALL" "$N_PREFIX" "$PYENV_ROOT"

USER forklift
WORKDIR /workspace

ENTRYPOINT ["/opt/forklift/harness/run.sh"]
