# Complete Setup Guide — basheer.net

> Step-by-step guide to deploy your $0 automated blog from scratch.

---

## Table of Contents

1. [Step 1: Local Setup (Hugo)](#step-1-local-setup)
2. [Step 2: Push to GitHub](#step-2-push-to-github)
3. [Step 3: Configure GitHub Pages](#step-3-configure-github-pages)
4. [Step 4: Configure Cloudflare](#step-4-configure-cloudflare)
5. [Step 5: Set Up Gemini API](#step-5-set-up-gemini-api)
6. [Step 6: Deploy N8N on GCP](#step-6-deploy-n8n-on-gcp)
7. [Step 7: Configure N8N Workflows](#step-7-configure-n8n-workflows)
8. [Step 8: Submit to Search Engines](#step-8-submit-to-search-engines)
9. [Step 9: Set Up Telegram Bot](#step-9-set-up-telegram-bot)

---

## Step 1: Local Setup

Hugo is already installed and the project is initialized. Verify everything works:

```powershell
# Navigate to your project
cd C:\Users\moham\Documents\basheer.net

# Test the build
hugo --minify

# Start local dev server
hugo server -D

# Open http://localhost:1313 in your browser
```

You should see your blog with the PaperMod theme, the welcome post, and all navigation working.

---

## Step 2: Push to GitHub

### 2.1 Create the GitHub Repository

1. Go to [github.com/new](https://github.com/new)
2. **Repository name**: `basheer.net`
3. **Visibility**: Public (required for free GitHub Pages)
4. Do **NOT** check "Initialize with README" (we already have one)
5. Click **Create repository**

### 2.2 Push Your Code

```powershell
cd C:\Users\moham\Documents\basheer.net

git add -A
git commit -m "Initial commit: Hugo blog with SEO, AI generation, and N8N automation"

git remote add origin https://github.com/bash07/basheer.net.git
git branch -M main
git push -u origin main
```

### 2.3 Enable Workflow Permissions

1. Go to your repo → **Settings** → **Actions** → **General**
2. Scroll to **Workflow permissions**
3. Select **Read and write permissions** ✅
4. Click **Save**

GitHub Actions will automatically run on your push, building the site and creating the `gh-pages` branch.

---

## Step 3: Configure GitHub Pages

1. Go to your repo → **Settings** → **Pages**
2. Under **Build and deployment → Source**: select **Deploy from a branch**
3. Under **Branch**: select `gh-pages` / `/ (root)`
4. Under **Custom domain**: enter `basheer.net`
5. Click **Save**
6. Wait for DNS verification (a green checkmark ✅)
7. Once verified, check **Enforce HTTPS** ✅

---

## Step 4: Configure Cloudflare

### 4.1 Add Your Domain

1. Sign up / log in at [cloudflare.com](https://cloudflare.com)
2. Click **Add a Site** → enter `basheer.net`
3. Select the **Free** plan
4. Click **Continue**

### 4.2 Update Nameservers

Cloudflare will provide two nameservers. Go to your domain registrar and replace the existing nameservers with Cloudflare's. Example:
- `anna.ns.cloudflare.com`
- `bob.ns.cloudflare.com`

⏳ Wait up to 24 hours for propagation (usually much faster).

### 4.3 Configure DNS Records

In Cloudflare Dashboard → **DNS** → **Records**, add:

| Type | Name | Content | Proxy Status |
|------|------|---------|-------------|
| A | `@` | `185.199.108.153` | **DNS only** ⛅ |
| A | `@` | `185.199.109.153` | **DNS only** ⛅ |
| A | `@` | `185.199.110.153` | **DNS only** ⛅ |
| A | `@` | `185.199.111.153` | **DNS only** ⛅ |
| CNAME | `www` | `bash07.github.io` | **DNS only** ⛅ |

> ⚠️ Keep "DNS only" initially so GitHub can verify and issue SSL certificates.

### 4.4 Wait for GitHub SSL

1. Go back to GitHub → **Settings** → **Pages**
2. Wait until the green checkmark appears next to your custom domain
3. Check **Enforce HTTPS**

### 4.5 Enable Cloudflare Proxy

Once HTTPS is confirmed working on GitHub:

1. Go to Cloudflare → **DNS** → **Records**
2. Toggle **ALL** records from "DNS only" ⛅ to **Proxied** 🟠
3. Go to **SSL/TLS** → set mode to **Full (Strict)**

### 4.6 Enable Cloudflare Optimizations

- **Speed → Optimization → Auto Minify**: Enable HTML, CSS, JS ✅
- **Speed → Optimization → Brotli**: Enable ✅
- **Caching → Configuration → Browser Cache TTL**: Set to 4 hours
- **Rules → Page Rules** (optional): `basheer.net/posts/*` → Cache Level: Cache Everything

---

## Step 5: Set Up Gemini API

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Click **Create API Key**
3. Copy your key and save it securely
4. Test locally:

```powershell
# Install Python dependency
pip install google-generativeai

# Set your API key (PowerShell)
$env:GEMINI_API_KEY = "your-api-key-here"

# Generate a test post
python scripts/generate-post.py --topic "Introduction to RTOS" --category "Embedded Systems"

# Preview it
hugo server -D
```

---

## Step 6: Deploy N8N on GCP

### 6.1 Create GCP Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown → **New Project**
3. Name: `n8n-automation`
4. Click **Create**

### 6.2 Enable Compute Engine

1. Go to **APIs & Services** → **Enable APIs**
2. Search for **Compute Engine API**
3. Click **Enable**

### 6.3 Create VM Instance

Go to **Compute Engine** → **VM instances** → **Create Instance**:

| Setting | Value |
|---------|-------|
| **Name** | `n8n-server` |
| **Region** | `us-central1` (Iowa) — FREE TIER |
| **Zone** | `us-central1-a` |
| **Machine type** | `e2-micro` (0.25-2 vCPU, 1GB) — FREE TIER |
| **Boot disk** | Ubuntu 22.04 LTS, 30GB Standard — FREE TIER |
| **Firewall** | ✅ Allow HTTP, ✅ Allow HTTPS |

Click **Create**.

### 6.4 Reserve Static IP

1. Go to **VPC Network** → **IP addresses** → **External IP addresses**
2. Click **Reserve Static Address**
3. Name: `n8n-ip`
4. Region: `us-central1`
5. **Attached to**: your `n8n-server` VM
6. Click **Reserve**
7. Note the IP address

### 6.5 Add DNS Record for N8N

In Cloudflare Dashboard → **DNS** → **Records**, add:

| Type | Name | Content | Proxy Status |
|------|------|---------|-------------|
| A | `n8n` | `YOUR_STATIC_IP` | **DNS only** ⛅ |

### 6.6 Set Up the VM

1. Click **SSH** next to your VM in Google Cloud Console
2. Upload the setup files:

```bash
# Create the n8n directory
mkdir -p ~/n8n-docker
cd ~/n8n-docker
```

3. Create the files (copy from your repo's `n8n/` directory):

```bash
# Create docker-compose.yml
nano docker-compose.yml
# (paste contents from n8n/docker-compose.yml)
# ⚠️ CHANGE N8N_BASIC_AUTH_PASSWORD!

# Create Caddyfile
nano Caddyfile
# (paste contents from n8n/Caddyfile)

# Download and run setup script
curl -O https://raw.githubusercontent.com/bash07/basheer.net/main/n8n/setup.sh
chmod +x setup.sh
./setup.sh
```

4. Verify N8N is running:
```bash
sudo docker compose ps
# Both n8n and caddy should show "Up"
```

5. Open `https://n8n.basheer.net` in your browser — you should see the N8N login page.

---

## Step 7: Configure N8N Workflows

See [n8n/workflows/README.md](n8n/workflows/README.md) for detailed instructions on setting up:

1. **Webhook Blog Generator** — POST to generate and publish posts
2. **Weekly Content Ideas** — Cron job to send topic ideas via Telegram
3. **llms.txt Auto-Updater** — Keeps AI crawler guide current

### Quick Credential Setup in N8N

1. Open `https://n8n.basheer.net`
2. Go to **Settings** → **Credentials** → **Add Credential**
3. Add:
   - **Gemini API Key** (Header Auth: `x-goog-api-key`)
   - **GitHub PAT** (Header Auth: `Authorization: Bearer ...`)
   - **Telegram Bot** (see Step 9)

---

## Step 8: Submit to Search Engines

### Google Search Console

1. Go to [search.google.com/search-console](https://search.google.com/search-console)
2. Click **Add property** → enter `https://basheer.net`
3. Verify via **DNS** (add a TXT record in Cloudflare)
4. Once verified, go to **Sitemaps** → submit: `https://basheer.net/sitemap.xml`

### Bing Webmaster Tools

1. Go to [bing.com/webmasters](https://www.bing.com/webmasters)
2. Add your site: `https://basheer.net`
3. Verify via DNS (add a CNAME record in Cloudflare)
4. Submit sitemap: `https://basheer.net/sitemap.xml`

### IndexNow (Instant Bing/Yandex Indexing)

Cloudflare has built-in IndexNow support:
1. In Cloudflare → **Speed** → **Optimization** → **IndexNow**
2. Enable it ✅
3. New pages will be automatically submitted to Bing and Yandex

---

## Step 9: Set Up Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Name: `Basheer Blog Bot`  
4. Username: `basheer_blog_bot` (must be unique)
5. Copy the **bot token** you receive
6. Send a message to your new bot (just say "hello")
7. Get your **chat ID** by visiting:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
   Look for `"chat":{"id":YOUR_CHAT_ID}`
8. Add both the **bot token** and **chat ID** to your N8N Telegram credential

---

## ✅ Verification Checklist

After completing all steps, verify:

- [ ] `https://basheer.net` loads your blog ✅
- [ ] `https://basheer.net/posts/hello-world/` shows the seed post ✅
- [ ] `https://basheer.net/sitemap.xml` returns valid XML ✅
- [ ] `https://basheer.net/index.xml` returns RSS feed ✅
- [ ] `https://basheer.net/robots.txt` lists AI crawlers ✅
- [ ] `https://basheer.net/llms.txt` shows AI guide ✅
- [ ] View page source → find `<script type="application/ld+json">` ✅
- [ ] `https://n8n.basheer.net` shows N8N dashboard ✅
- [ ] Webhook test generates a blog post ✅
- [ ] Telegram receives notifications ✅
- [ ] Google Search Console shows sitemap submitted ✅
- [ ] Cloudflare shows traffic proxied ✅

---

## 🔧 Maintenance

### Updating N8N
```bash
# SSH into your GCP VM
cd ~/n8n-docker
sudo docker compose pull
sudo docker compose up -d
```

### Updating Hugo Theme
```bash
cd C:\Users\moham\Documents\basheer.net
git submodule update --remote themes/PaperMod
```

### Manual Post Creation
```bash
hugo new posts/my-new-post.md
# Edit content/posts/my-new-post.md
# Set draft: false when ready
git add -A && git commit -m "Add: my-new-post" && git push
```
