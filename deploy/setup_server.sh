#!/usr/bin/env bash
# 在天翼云 / Linux 服务器上安装并启动智能备课系统。
# 用法：在项目根目录执行  sudo bash deploy/setup_server.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 项目目录: $ROOT"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 或 sudo 运行此脚本" >&2
  exit 1
fi

# ---- 交换分区（2G 内存机器建议）----
if [[ ! -f /swapfile ]]; then
  echo "==> 创建 2G swap"
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ---- 系统包 ----
install_pkgs() {
  # CTyunOS / RHEL 系可能没有独立的 python3-venv 包，python3 -m venv 仍可用
  local pkgs=(python3 python3-pip nginx git curl tar gcc make)
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y "${pkgs[@]}" gcc-c++ || dnf install -y "${pkgs[@]}"
    dnf install -y python3-venv 2>/dev/null || true
  elif command -v yum >/dev/null 2>&1; then
    yum install -y "${pkgs[@]}" gcc-c++ || yum install -y "${pkgs[@]}"
    yum install -y python3-venv 2>/dev/null || true
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y python3 python3-pip python3-venv nginx git curl tar build-essential
  else
    echo "未识别的包管理器，请手动安装 python3/nginx/git/node" >&2
    exit 1
  fi
}

echo "==> 安装系统依赖"
install_pkgs

# ---- Node.js 20 ----
need_node=0
if ! command -v node >/dev/null 2>&1; then
  need_node=1
elif [[ "$(node -v 2>/dev/null | sed 's/v//' | cut -d. -f1)" -lt 18 ]]; then
  need_node=1
fi
if [[ "$need_node" -eq 1 ]]; then
  echo "==> 安装 Node.js 20"
  if curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -; then
    dnf install -y nodejs 2>/dev/null || yum install -y nodejs 2>/dev/null || true
  fi
  if ! command -v node >/dev/null 2>&1; then
    if curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; then
      apt-get install -y nodejs 2>/dev/null || true
    fi
  fi
  if ! command -v node >/dev/null 2>&1; then
    echo "==> 回退：安装 Node 官方二进制包"
    arch="$(uname -m)"
    case "$arch" in
      x86_64|amd64) node_arch=x64 ;;
      aarch64|arm64) node_arch=arm64 ;;
      *) echo "不支持的架构: $arch" >&2; exit 1 ;;
    esac
    tmp=/tmp/node-v20.19.0-linux-${node_arch}.tar.xz
    curl -fsSL -o "$tmp" "https://nodejs.org/dist/v20.19.0/node-v20.19.0-linux-${node_arch}.tar.xz"
    tar -xJf "$tmp" -C /usr/local --strip-components=1
  fi
fi

echo "==> Node $(node -v) / npm $(npm -v)"

# ---- Python 3.12+ venv（系统 python3.9 无法安装 langgraph>=1.0）----
echo "==> 准备 Python 3.12+"
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv python install 3.12
rm -rf .venv
uv venv .venv --python 3.12 --seed
.venv/bin/python -V
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt "pymupdf>=1.24,<2" -i https://pypi.tuna.tsinghua.edu.cn/simple

# ---- 前端构建 ----
echo "==> 构建前端"
cd web
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
npm run build
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "警告: 未找到 .env，请先写入 OPENAI_API_KEY 等配置" >&2
fi

# ---- Nginx ----
echo "==> 配置 Nginx"
if [[ -d /etc/nginx/conf.d ]]; then
  cp deploy/nginx.conf /etc/nginx/conf.d/lesson-prep.conf
  # 关掉可能抢占 default_server 的默认站点
  [[ -f /etc/nginx/conf.d/default.conf ]] && mv /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.bak || true
elif [[ -d /etc/nginx/sites-available ]]; then
  cp deploy/nginx.conf /etc/nginx/sites-available/lesson-prep
  ln -sfn /etc/nginx/sites-available/lesson-prep /etc/nginx/sites-enabled/lesson-prep
  rm -f /etc/nginx/sites-enabled/default
fi
nginx -t
systemctl enable nginx
systemctl restart nginx

# ---- Redis（任务队列）----
echo "==> 检查 Redis"
if command -v redis-cli >/dev/null 2>&1; then
  redis-cli ping || echo "警告: redis-cli ping 失败，请先安装并启动 Redis" >&2
else
  echo "警告: 未找到 redis-cli。请安装 Redis，或在 .env 设 JOB_BACKEND=memory" >&2
fi

# ---- systemd ----
echo "==> 配置 systemd 服务"
cp deploy/lesson-prep.service /etc/systemd/system/lesson-prep.service
cp deploy/lesson-prep-worker.service /etc/systemd/system/lesson-prep-worker.service
# 若项目不在 /opt/lessonPreparation，改写路径
if [[ "$ROOT" != "/opt/lessonPreparation" ]]; then
  sed -i "s|/opt/lessonPreparation|$ROOT|g" /etc/systemd/system/lesson-prep.service
  sed -i "s|/opt/lessonPreparation|$ROOT|g" /etc/systemd/system/lesson-prep-worker.service
  sed -i "s|/opt/lessonPreparation|$ROOT|g" /etc/nginx/conf.d/lesson-prep.conf 2>/dev/null || true
  sed -i "s|/opt/lessonPreparation|$ROOT|g" /etc/nginx/sites-available/lesson-prep 2>/dev/null || true
  nginx -t && systemctl reload nginx
fi
systemctl daemon-reload
systemctl enable lesson-prep lesson-prep-worker
systemctl restart lesson-prep
systemctl restart lesson-prep-worker

echo ""
echo "==> 部署完成"
echo "    健康检查: curl -s http://127.0.0.1/api/health"
echo "    公网访问: http://<公网IP>/"
systemctl --no-pager --full status lesson-prep || true
systemctl --no-pager --full status lesson-prep-worker || true
