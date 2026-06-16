# syntax=docker/dockerfile:1.6
FROM ubuntu:24.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    N_PREFIX=/opt/n \
    BUN_INSTALL=/opt/bun \
    PYENV_ROOT=/opt/pyenv \
    PATH=/opt/forklift/venv/bin:/opt/pyenv/shims:/opt/pyenv/bin:$N_PREFIX/bin:$BUN_INSTALL/bin:$CARGO_HOME/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN --mount=type=cache,target=/var/lib/apt/lists,id=forklift-apt-lists,sharing=locked \
    --mount=type=cache,target=/var/cache/apt,id=forklift-apt-cache,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    git-lfs \
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

# Install uv via Astral's installer so FORK setup commands can use uv immediately.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# Prepare forklift user and writable tool directories ahead of installs
RUN if id -u ubuntu >/dev/null 2>&1; then userdel -r ubuntu || true; fi && \
    if ! id -u forklift >/dev/null 2>&1; then useradd -m -u 1000 -U forklift; fi && \
    install -d -o forklift -g forklift /opt/forklift /opt/forklift/harness /run/forklift "$CARGO_HOME" "$RUSTUP_HOME" "$BUN_INSTALL" "$N_PREFIX" "$PYENV_ROOT" /var/cache/forklift-installers /home/forklift/.npm /home/forklift/.local/state && \
    chown -R forklift:forklift /home/forklift/.local

USER forklift

RUN git lfs install --skip-repo

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

USER root


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
RUN chown -R root:root /opt/forklift/harness && \
    chmod -R go-w /opt/forklift/harness && \
    chmod +x /opt/forklift/harness/run.sh /opt/forklift/harness/entrypoint.sh /opt/forklift/harness/includes/bin/git

# Build the in-container Python venv with the in-process harness package + deps.
# Install the base interpreter into a world-readable dir (the default uv cache lives
# under root's home, which the unprivileged forklift user cannot traverse/execute).
ENV UV_PYTHON_INSTALL_DIR=/opt/forklift/python
# Silence uv's progress spinners so build and live setup logs stay line-oriented.
ENV UV_NO_PROGRESS=1
RUN uv python install 3.13 && \
    uv venv --python 3.13 /opt/forklift/venv && \
    VIRTUAL_ENV=/opt/forklift/venv uv pip install /opt/forklift/harness/py && \
    chmod -R a+rX /opt/forklift/python /opt/forklift/venv


WORKDIR /workspace

ENTRYPOINT ["/opt/forklift/harness/entrypoint.sh"]
