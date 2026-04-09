# basheer.net — Fully Automated, SEO-Optimized Personal Blog

[![Deploy Hugo Site](https://github.com/bash07/basheer.net/actions/workflows/deploy.yml/badge.svg)](https://github.com/bash07/basheer.net/actions/workflows/deploy.yml)

A **zero-cost**, fully automated personal blog powered by Hugo, GitHub Pages, AI content generation, and workflow automation.

🌐 **Live**: [https://basheer.net](https://basheer.net)

---

## 🏗️ Architecture

```
Content Generation → Source Control → CI/CD Build → Static Hosting → CDN
   (Gemini AI)        (GitHub)      (GH Actions)   (GH Pages)    (Cloudflare)
       ↑                  ↑
   N8N Automation ────────┘
   (GCP e2-micro)
```

## 💰 Cost: $0/month

| Service | What It Does | Free Tier Limit |
|---------|-------------|----------------|
| **GitHub Pages** | Static hosting | 1GB storage, 100GB bandwidth/mo |
| **Cloudflare** | CDN, DNS, SSL | Unlimited requests |
| **GitHub Actions** | CI/CD pipeline | 2,000 min/mo |
| **Google Gemini** | AI content generation | 15 RPM, 1M tokens/day |
| **GCP e2-micro** | N8N automation server | 1 vCPU, 1GB RAM, 30GB disk |

## 📁 Project Structure

```
basheer.net/
├── .github/workflows/deploy.yml    # CI/CD: Auto-build & deploy on push
├── archetypes/default.md           # Hugo post template with SEO fields
├── content/
│   ├── posts/                      # Blog posts (AI-generated + manual)
│   ├── about.md                    # About page
│   └── search.md                   # Built-in search (Fuse.js)
├── layouts/
│   ├── partials/
│   │   ├── jsonld.html             # Schema.org structured data
│   │   └── head-extra.html         # Extra SEO meta tags
│   └── robots.txt                  # Custom robots.txt (AI bots allowed)
├── static/
│   ├── llms.txt                    # AI crawler guide (llms.txt spec)
│   └── llms-full.txt               # Full content for AI consumption
├── scripts/
│   ├── generate-post.py            # Gemini AI blog post generator
│   └── requirements.txt            # Python dependencies
├── n8n/
│   ├── setup.sh                    # GCP VM setup script
│   ├── docker-compose.yml          # N8N + Caddy containers
│   ├── Caddyfile                   # Reverse proxy config
│   └── workflows/README.md         # N8N workflow documentation
├── hugo.toml                       # Site configuration
└── README.md                       # This file
```

## 🚀 Quick Start

### Prerequisites
- [Hugo Extended](https://gohugo.io/installation/) installed
- [Git](https://git-scm.com/) installed
- [Python 3.8+](https://python.org/) (for AI post generation)

### Local Development
```bash
# Clone the repo
git clone https://github.com/bash07/basheer.net.git
cd basheer.net

# Initialize theme submodule
git submodule update --init --recursive

# Start local dev server
hugo server -D

# Open http://localhost:1313
```

### Generate an AI Blog Post
```bash
# Install dependencies
pip install -r scripts/requirements.txt

# Set your Gemini API key
export GEMINI_API_KEY="your-api-key-here"  # Get one at aistudio.google.com/apikey

# Generate a post
python scripts/generate-post.py --topic "Your Topic Here" --category "Technology"

# Preview locally
hugo server -D
```

## 🔍 SEO Features

- ✅ **JSON-LD Schema.org** structured data (Article, WebSite, CollectionPage)
- ✅ **Open Graph** meta tags (Facebook, LinkedIn)
- ✅ **Twitter Cards** meta tags
- ✅ **XML Sitemap** auto-generated at `/sitemap.xml`
- ✅ **RSS Feed** at `/index.xml`
- ✅ **robots.txt** explicitly allowing AI crawlers
- ✅ **llms.txt** for AI discoverability
- ✅ **Semantic HTML** with proper heading hierarchy
- ✅ **Canonical URLs** on every page
- ✅ **Clean permalinks** (`/posts/your-slug/`)

## 🤖 AI Discoverability

This blog is optimized for discovery by AI systems:

| File | Purpose |
|------|---------|
| `/llms.txt` | Concise site directory for AI crawlers |
| `/llms-full.txt` | Full content dump for AI context windows |
| `/robots.txt` | Explicitly allows GPTBot, Claude, PerplexityBot, etc. |
| `/sitemap.xml` | Standard sitemap for all crawlers |

## 📖 Documentation

- [N8N Workflow Setup](n8n/workflows/README.md) — How to configure automation workflows
- [SETUP_GUIDE.md](SETUP_GUIDE.md) — Complete step-by-step deployment guide

## 📄 License

This project is open source. Feel free to fork and adapt for your own blog.
