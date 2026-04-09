#!/bin/bash
# ============================================================
# N8N Setup Script for GCP e2-micro (Always Free Tier)
# ============================================================
# Run this script on your GCP VM after SSH-ing in:
#   chmod +x setup.sh && ./setup.sh
#
# Prerequisites:
#   - GCP e2-micro VM in us-central1, us-east1, or us-west1
#   - Ubuntu 22.04 LTS with 30GB standard persistent disk
#   - Firewall rules allowing ports 80 and 443
#   - Static external IP reserved and assigned
#   - DNS A record: n8n.basheer.net → YOUR_STATIC_IP
# ============================================================

set -euo pipefail

echo "================================================"
echo "  N8N Server Setup for basheer.net"
echo "================================================"

# --- 1. System Update ---
echo ""
echo "[1/6] Updating system packages..."
sudo apt update && sudo apt upgrade -y

# --- 2. Add Swap (Critical for 1GB RAM) ---
echo ""
echo "[2/6] Creating 2GB swap file..."
if [ ! -f /swapfile ]; then
    # Use 'dd' instead of 'fallocate' — GCP persistent disks reject sparse
    # swap files created by fallocate (causes "swapon failed: Invalid argument")
    sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "✅ Swap file created and enabled"
else
    echo "⏭️  Swap file already exists, skipping"
fi

# --- 3. Install Docker ---
echo ""
echo "[3/6] Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker "$USER"
    rm get-docker.sh
    echo "✅ Docker installed"
else
    echo "⏭️  Docker already installed"
fi

# --- 4. Install Docker Compose ---
echo ""
echo "[4/6] Installing Docker Compose plugin..."
sudo apt install -y docker-compose-plugin
echo "✅ Docker Compose ready"

# --- 5. Create N8N Directory & Config ---
echo ""
echo "[5/6] Setting up N8N configuration..."
mkdir -p ~/n8n-docker
cd ~/n8n-docker

# Copy docker-compose.yml (should be in same dir as this script)
if [ ! -f docker-compose.yml ]; then
    echo "⚠️  docker-compose.yml not found in ~/n8n-docker"
    echo "   Copy it from the repository's n8n/ directory"
fi

if [ ! -f Caddyfile ]; then
    echo "⚠️  Caddyfile not found in ~/n8n-docker"
    echo "   Copy it from the repository's n8n/ directory"
fi

# --- 6. Start Services ---
echo ""
echo "[6/6] Starting N8N and Caddy..."
cd ~/n8n-docker

if [ -f docker-compose.yml ] && [ -f Caddyfile ]; then
    sudo docker compose up -d
    echo ""
    echo "================================================"
    echo "  ✅ N8N is running!"
    echo "  🌐 Access at: https://n8n.basheer.net"
    echo "  📋 Default login: admin / (check docker-compose.yml)"
    echo ""
    echo "  ⚠️  IMPORTANT: Change the default password!"
    echo "  Edit docker-compose.yml → N8N_BASIC_AUTH_PASSWORD"
    echo "  Then run: sudo docker compose up -d"
    echo "================================================"
else
    echo ""
    echo "================================================"
    echo "  ⚠️  Config files missing!"
    echo "  Copy these files to ~/n8n-docker/:"
    echo "    - docker-compose.yml"
    echo "    - Caddyfile"
    echo "  Then run: sudo docker compose up -d"
    echo "================================================"
fi
