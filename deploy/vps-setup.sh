#!/usr/bin/env bash
# ==============================================================================
# OpenSponsor 1-Click Production VPS Setup Script (Ubuntu / Debian)
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/.../deploy/vps-setup.sh | bash
#   OR run directly on your VPS:
#   chmod +x deploy/vps-setup.sh && ./deploy/vps-setup.sh [DOMAIN_OR_IP]
# ==============================================================================

set -euo pipefail

DOMAIN="${1:-:80}"
INSTALL_DIR="/opt/opensponsor"

echo "========================================================"
echo "  Setting up OpenSponsor Platform on Linux VPS"
echo "  Target Domain / Bind: ${DOMAIN}"
echo "========================================================"

# 1. System Updates & Core Packages
echo "[1/6] Updating packages & installing system dependencies..."
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release ufw git sqlite3

# 2. Docker & Docker Compose Installation
echo "[2/6] Verifying Docker installation..."
if ! command -v docker &> /dev/null; then
    echo "Installing Docker CE..."
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# 3. Configure Firewall (UFW)
echo "[3/6] Configuring firewall (ports 22, 80, 443)..."
sudo ufw allow 22/tcp || true
sudo ufw allow 80/tcp || true
sudo ufw allow 443/tcp || true
sudo ufw --force enable || true

# 4. Prepare Application Directory
echo "[4/6] Setting up application directory at ${INSTALL_DIR}..."
sudo mkdir -p "${INSTALL_DIR}"
sudo chown -R "$USER":"$USER" "${INSTALL_DIR}"

if [ ! -f "${INSTALL_DIR}/docker-compose.yml" ]; then
    cp -r . "${INSTALL_DIR}/" || true
fi

cd "${INSTALL_DIR}"

# 5. Configure Production Environment
echo "[5/6] Creating production .env file..."
cat <<EOF > "${INSTALL_DIR}/.env"
DOMAIN=${DOMAIN}
BASE_URL=$([ "${DOMAIN}" = ":80" ] && echo "http://$(curl -s https://api.ipify.org)" || echo "https://${DOMAIN}")
SALT=$(openssl rand -hex 16 2>/dev/null || echo "prod-salt-$(date +%s)")
EOF

# 6. Launch Containers via Docker Compose
echo "[6/6] Building and launching OpenSponsor containers with automatic SSL..."
docker compose down || true
docker compose up -d --build

echo "========================================================"
echo "  OpenSponsor is LIVE and running in production!"
echo "  Web UI & API: $([ "${DOMAIN}" = ":80" ] && echo "http://$(curl -s https://api.ipify.org)" || echo "https://${DOMAIN}")"
echo "  Health Check: $([ "${DOMAIN}" = ":80" ] && echo "http://$(curl -s https://api.ipify.org)/health" || echo "https://${DOMAIN}/health")"
echo "========================================================"
