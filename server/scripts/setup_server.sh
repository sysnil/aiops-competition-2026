#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "ERROR: run as root" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${ROOT_DIR}" != "/opt/aiops-contest-server" ]]; then
  echo "ERROR: copy the complete template to /opt/aiops-contest-server first" >&2
  exit 1
fi
if ! findmnt -rn -T /data -o SOURCE,FSTYPE | grep -Eq '^/dev/sdb1 +ext4$'; then
  echo "ERROR: expected ext4 data disk /dev/sdb1 mounted at /data" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
nginx_was_absent=0
if ! dpkg-query -W -f='${Status}' nginx 2>/dev/null | grep -q 'install ok installed'; then
  nginx_was_absent=1
  systemctl mask nginx.service >/dev/null
fi
apt-get update
apt-get install -y --no-install-recommends \
  git curl wget vim jq tmux htop rsync unzip tar lsof ca-certificates \
  build-essential python3-venv nginx
if [[ ${nginx_was_absent} -eq 1 ]]; then
  systemctl unmask nginx.service >/dev/null
fi

if ! id aiops-api >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin aiops-api
fi

install -d -m 0750 -o root -g aiops-api /etc/aiops-contest /var/log/aiops-contest
install -d -m 0750 -o root -g aiops-api \
  /data/aiops-contest/public /data/aiops-contest/public/cases /data/aiops-contest/public/manifests \
  /data/aiops-contest/mock-only /data/aiops-contest/mock-only/cases /data/aiops-contest/mock-only/manifests
install -d -m 0700 -o root -g root /data/aiops-contest/staging
install -d -m 0750 -o aiops-api -g aiops-api \
  /data/aiops-contest/indexes /data/aiops-contest/cache /data/aiops-contest/cache/duckdb

if ! command -v uv >/dev/null 2>&1; then
  installer="$(mktemp)"
  trap 'rm -f "${installer}"' EXIT
  curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh -o "${installer}"
  env UV_INSTALL_DIR=/usr/local/bin sh "${installer}"
  rm -f "${installer}"
  trap - EXIT
fi

export UV_PYTHON_INSTALL_DIR="${ROOT_DIR}/.python"
export UV_PYTHON_BIN_DIR="${ROOT_DIR}/.python-bin"
export PATH="${UV_PYTHON_BIN_DIR}:${PATH}"
uv python install 3.11
PYTHON_311="$(uv python find --system 3.11)"
cd "${ROOT_DIR}"
if [[ ! -f uv.lock ]]; then
  uv lock --python "${PYTHON_311}"
fi
if [[ -f .venv/pyvenv.cfg ]] && ! grep -Fq "home = $(dirname "${PYTHON_311}")" .venv/pyvenv.cfg; then
  backup="${ROOT_DIR}/.venv.pre-shared-python.$(date -u +%Y%m%dT%H%M%SZ)"
  mv .venv "${backup}"
  echo "Preserved incompatible virtual environment at ${backup}"
fi
uv sync --frozen --all-groups --python "${PYTHON_311}"

install -m 0640 -o root -g aiops-api config/config.yaml /etc/aiops-contest/config.yaml
PYTHONPATH="${ROOT_DIR}" .venv/bin/python scripts/manage_tokens.py \
  --db /etc/aiops-contest/tokens.json init-mock --count 17 \
  --client-file /etc/aiops-contest/mock-client-tokens.json
chown root:aiops-api /etc/aiops-contest/tokens.json
chmod 0640 /etc/aiops-contest/tokens.json
chown root:root /etc/aiops-contest/mock-client-tokens.json
chmod 0600 /etc/aiops-contest/mock-client-tokens.json

.venv/bin/python scripts/generate_mock_case.py
chown -R root:aiops-api /data/aiops-contest/mock-only
find /data/aiops-contest/mock-only -type d -exec chmod 0550 {} +
find /data/aiops-contest/mock-only -type f -exec chmod 0440 {} +

install -m 0644 -o root -g root config/aiops-contest-api.service /etc/systemd/system/aiops-contest-api.service
install -m 0644 -o root -g root config/nginx-aiops-contest.conf /etc/nginx/sites-available/aiops-contest
ln -sfn /etc/nginx/sites-available/aiops-contest /etc/nginx/sites-enabled/aiops-contest
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  unlink /etc/nginx/sites-enabled/default
fi
nginx -t
systemctl daemon-reload
systemctl enable --now aiops-contest-api.service nginx.service
systemctl restart aiops-contest-api.service nginx.service

for _ in {1..20}; do
  if curl -fsS http://127.0.0.1:8080/healthz >/dev/null; then
    echo "AIOps contest data API health check passed."
    exit 0
  fi
  sleep 1
done
echo "ERROR: API health check failed" >&2
journalctl -u aiops-contest-api.service -n 80 --no-pager >&2
exit 1
