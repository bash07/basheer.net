# Deployment & Infrastructure Guide

This service is deployed to a Google Cloud Platform (GCP) `e2-micro` virtual machine.

## 💾 Server Specs
- **Instance Type:** `e2-micro` (1GB RAM, 2 vCPU).
- **OS:** Ubuntu 22.04 LTS.
- **Network:** Requires a reserved Static IP attached to the instance. Firewall rules must open TCP ports `80` and `443` (HTTP/HTTPS).

## 🔄 Deployment Process

Because the server is lightweight, we build identical code locally and push it via the Google Cloud CLI, then restart Docker on the VM.

```bash
# 1. Push code from your local machine to the GCP VM
gcloud compute scp --recurse "/path/to/local/automation" my-server: --zone=your-gcp-zone

# 2. SSH into the instance
gcloud compute ssh my-server --zone=your-gcp-zone

# 3. Pull new images and restart the containers
cd ~/automation
docker compose up -d --build
```

*(Note: During initial setup, copy `cp .env.example .env` and fill in the required keys before running the docker command).*

## 🔒 DNS & SSL (Cloudflare + Caddy)
- **Cloudflare DNS:** You must have an `A` record pointing to the VM's static IP.
- **Proxy Status:** Important! The proxy status must be **Grey Cloud (DNS Only)**. If you enable Cloudflare's Orange proxy, the internal `Caddy` container won't be able to authenticate with Let's Encrypt, and SSL certificates will fail.
- **Caddy:** The `Caddyfile` handles mapping incoming requests at `ai.yourdomain.com` to the FastAPI backend running on port `8000`.

## 📦 Database Backups
Because we optimized out heavy dependencies like PostgreSQL, your entire database is a single SQLite file located inside the Docker container's volume.

You can pull a backup of your entire database directly to your local PC at any time with this command:

```bash
gcloud compute scp my-server:~/automation/data/automation.db "/path/to/local/automation_backup.db" --zone=your-gcp-zone
```
