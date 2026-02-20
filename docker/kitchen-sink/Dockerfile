# syntax=docker/dockerfile:1.6
FROM ubuntu:24.04
ARG OPENCODE_VERSION=1.2.6

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    N_PREFIX=/opt/n \
    BUN_INSTALL=/opt/bun \
    PYENV_ROOT=/opt/pyenv \
    OPENCODE_HOME=/opt/opencode \
    PATH=/opt/pyenv/shims:/opt/pyenv/bin:$N_PREFIX/bin:$BUN_INSTALL/bin:$CARGO_HOME/bin:$OPENCODE_HOME/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN --mount=type=cache,target=/var/lib/apt/lists,id=forklift-apt-lists,sharing=locked \
    --mount=type=cache,target=/var/cache/apt,id=forklift-apt-cache,sharing=locked \
    apt-get update && \
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
    ln -s /usr/bin/fdfind /usr/local/bin/fd

# Prepare forklift user and writable tool directories ahead of installs
RUN if id -u ubuntu >/dev/null 2>&1; then userdel -r ubuntu || true; fi && \
    if ! getent group opencode >/dev/null 2>&1; then groupadd --system opencode; fi && \
    if ! id -u forklift >/dev/null 2>&1; then useradd -m -u 1000 -U forklift; fi && \
    usermod -aG opencode forklift && \
    install -d -o forklift -g forklift /opt/forklift /opt/forklift/harness "$CARGO_HOME" "$RUSTUP_HOME" "$BUN_INSTALL" "$N_PREFIX" "$PYENV_ROOT" /var/cache/forklift-installers /home/forklift/.npm /home/forklift/.local/state /home/forklift/.local/share/opencode/log && \
    chown -R forklift:forklift /home/forklift/.local

USER forklift

# Install Node.js via n (LTS)
RUN --mount=type=cache,target=/home/forklift/.npm,id=forklift-npm-cache,uid=1000,gid=1000 \
    export NPM_CONFIG_PREFIX="$N_PREFIX" && \
    npm install -g n && \
    "$N_PREFIX/bin/n" lts

# Install Bun
RUN --mount=type=cache,target=/var/cache/forklift-installers,id=forklift-installers,sharing=locked,uid=1000,gid=1000 \
    INSTALLER_DIR=/var/cache/forklift-installers && \
    install -d "$INSTALLER_DIR" && \
    INSTALLER="$INSTALLER_DIR/bun-install.sh" && \
    if [ ! -f "$INSTALLER" ]; then \
        curl -fsSL https://bun.sh/install -o "$INSTALLER"; \
    fi && \
    bash "$INSTALLER"

# Install Rust toolchain (minimal profile, stable)
RUN --mount=type=cache,target=/var/cache/forklift-installers,id=forklift-installers,sharing=locked,uid=1000,gid=1000 \
    INSTALLER_DIR=/var/cache/forklift-installers && \
    install -d "$INSTALLER_DIR" && \
    INSTALLER="$INSTALLER_DIR/rustup-init.sh" && \
    if [ ! -f "$INSTALLER" ]; then \
        curl https://sh.rustup.rs -sSf -o "$INSTALLER"; \
    fi && \
    sh "$INSTALLER" -y --profile minimal --default-toolchain stable

# Install PyEnv
RUN git clone https://github.com/pyenv/pyenv.git "$PYENV_ROOT"

# Install OpenCode CLI via official installer
USER root
RUN --mount=type=cache,target=/var/cache/forklift-installers,id=forklift-installers,sharing=locked,uid=1000,gid=1000 \
    set -euxo pipefail && \
    INSTALLER_DIR=/var/cache/forklift-installers && \
    install -d "$INSTALLER_DIR" && \
    INSTALLER="$INSTALLER_DIR/opencode-install.sh" && \
    if [ ! -f "$INSTALLER" ]; then \
        curl -fsSL https://opencode.ai/install -o "$INSTALLER"; \
    fi && \
    chmod +x "$INSTALLER" && \
    "$INSTALLER" --no-modify-path --version "$OPENCODE_VERSION" && \
    install -d "$OPENCODE_HOME/bin" && \
    mv /root/.opencode/bin/opencode "$OPENCODE_HOME/bin/opencode" && \
    chmod 755 "$OPENCODE_HOME/bin/opencode" && \
    rm -rf /root/.opencode


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

COPY --chown=forklift:forklift harness/ /opt/forklift/harness/
RUN chmod +x /opt/forklift/harness/run.sh

COPY opencode/start_server.sh /opt/opencode/start_server.sh
COPY opencode/entrypoint.sh /opt/opencode/entrypoint.sh
RUN chmod 700 /opt/opencode/start_server.sh /opt/opencode/entrypoint.sh && \
    chown root:root /opt/opencode/start_server.sh /opt/opencode/entrypoint.sh

WORKDIR /workspace

ENTRYPOINT ["/opt/opencode/entrypoint.sh"]
