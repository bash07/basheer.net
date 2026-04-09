# N8N Workflow Configurations

This directory documents the N8N workflows to set up in your N8N dashboard at `https://n8n.basheer.net`.

## Prerequisites

Before creating workflows, add these credentials in N8N (Settings → Credentials):

| Credential | Type | How to Get |
|------------|------|------------|
| **Gemini API Key** | Header Auth | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **GitHub PAT** | Header Auth | GitHub → Settings → Developer settings → Fine-grained tokens (needs `contents: write` on `bash07/basheer.net`) |
| **Telegram Bot Token** | Telegram API | Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` |

---

## Workflow 1: Webhook-Triggered Blog Post Generator

**Trigger**: Webhook (POST)
**Purpose**: Generate and publish a blog post on demand

### Flow

```
Webhook POST /webhook/generate-post
  ↓ (receives: { "topic": "...", "category": "..." })
HTTP Request → Gemini API
  ↓ (returns generated post JSON)
Code Node → Build Hugo front matter + markdown
  ↓ (creates base64-encoded .md file content)
HTTP Request → GitHub API (Create File)
  ↓ PUT /repos/bash07/basheer.net/contents/content/posts/{slug}.md
Telegram → Send notification "✅ New post published: {title}"
```

### Nodes Configuration

#### Node 1: Webhook
- **Method**: POST
- **Path**: `generate-post`
- **Authentication**: Header Auth (set a secret key for security)
- **Response Mode**: Last Node

#### Node 2: HTTP Request (Gemini API)
- **Method**: POST
- **URL**: `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={{$credentials.geminiApiKey}}`
- **Body (JSON)**:
```json
{
  "contents": [{
    "parts": [{
      "text": "You are an expert technical blog writer. Write a comprehensive blog post about: {{$json.body.topic}}. Category: {{$json.body.category}}. Return as JSON with fields: title, description, summary, tags (array), keywords (array), content (markdown body). Return ONLY valid JSON."
    }]
  }]
}
```

#### Node 3: Code Node (Build Markdown)
```javascript
const response = JSON.parse($input.first().json.candidates[0].content.parts[0].text);
const now = new Date().toISOString();
const slug = response.title.toLowerCase().replace(/[^\w\s-]/g, '').replace(/[\s]+/g, '-').trim();

const frontMatter = `---
title: "${response.title}"
date: ${now}
lastmod: ${now}
draft: false
description: "${response.description}"
summary: "${response.summary}"
tags: ${JSON.stringify(response.tags)}
categories: ["${$input.first().json.body.category || 'Technology'}"]
keywords: ${JSON.stringify(response.keywords)}
author: "Basheer"
showToc: true
ShowReadingTime: true
ShowBreadCrumbs: true
ShowPostNavLinks: true
ShowWordCount: true
---

${response.content}`;

const content = Buffer.from(frontMatter).toString('base64');

return [{
  json: {
    slug: slug,
    title: response.title,
    filename: `content/posts/${slug}.md`,
    content: content,
    message: `Add post: ${response.title}`
  }
}];
```

#### Node 4: HTTP Request (GitHub API — Create File)
- **Method**: PUT
- **URL**: `https://api.github.com/repos/bash07/basheer.net/contents/{{$json.filename}}`
- **Headers**: `Authorization: Bearer {{$credentials.githubPat}}`
- **Body (JSON)**:
```json
{
  "message": "{{$json.message}}",
  "content": "{{$json.content}}",
  "branch": "main"
}
```

#### Node 5: Telegram (Notification)
- **Operation**: Send Message
- **Chat ID**: Your Telegram chat ID
- **Text**: `✅ New blog post published!\n\n📝 {{$json.title}}\n🔗 https://basheer.net/posts/{{$json.slug}}/`

---

## Workflow 2: Weekly Content Ideas Generator

**Trigger**: Cron (Every Monday at 9:00 AM IST)
**Purpose**: Generate 5 blog topic ideas and send via Telegram

### Flow

```
Cron (Monday 9 AM)
  ↓
HTTP Request → Gemini API (generate ideas)
  ↓
Code Node → Format ideas
  ↓
Telegram → Send ideas list to you
```

### Nodes Configuration

#### Node 1: Cron
- **Mode**: Every Week
- **Day**: Monday
- **Hour**: 9
- **Timezone**: Asia/Kolkata

#### Node 2: HTTP Request (Gemini API)
- **URL**: Same as Workflow 1
- **Prompt**: `Generate 5 trending, unique blog post ideas for a tech blog covering AI, embedded systems, and software engineering. For each idea, provide: topic (string), category (string), reasoning (string explaining why this is timely). Return as a JSON array.`

#### Node 3: Code Node (Format)
```javascript
const ideas = JSON.parse($input.first().json.candidates[0].content.parts[0].text);

let message = "📋 *Weekly Blog Ideas*\n\n";
ideas.forEach((idea, i) => {
  message += `${i+1}. *${idea.topic}*\n`;
  message += `   📂 ${idea.category}\n`;
  message += `   💡 ${idea.reasoning}\n\n`;
});

message += "Reply with a number to generate that post, or type a custom topic!";

return [{ json: { message } }];
```

#### Node 4: Telegram
- **Text**: `{{$json.message}}`
- **Parse Mode**: Markdown

---

## Workflow 3: Weekly llms.txt Updater

**Trigger**: Cron (Every Sunday at midnight)
**Purpose**: Auto-update the llms.txt file with the latest posts

### Flow

```
Cron (Sunday midnight)
  ↓
HTTP Request → GitHub API (list posts directory)
  ↓
Code Node → Generate updated llms.txt content
  ↓
HTTP Request → GitHub API (get current llms.txt SHA)
  ↓
HTTP Request → GitHub API (update llms.txt)
```

### Nodes Configuration

#### Node 1: HTTP Request (List Posts)
- **Method**: GET
- **URL**: `https://api.github.com/repos/bash07/basheer.net/contents/content/posts`
- **Headers**: `Authorization: Bearer {{$credentials.githubPat}}`

#### Node 2: Code Node (Generate llms.txt)
```javascript
const files = $input.first().json;
const posts = files
  .filter(f => f.name.endsWith('.md'))
  .map(f => {
    const name = f.name.replace('.md', '');
    const title = name.replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    return `- [${title}](https://basheer.net/posts/${name}/): ${title}`;
  });

const llmsTxt = `# Basheer's Blog

> A personal blog about technology, AI, embedded systems, and software engineering by Basheer.

## About
- [About Basheer](https://basheer.net/about/): Background, interests, and what this blog covers.

## Key Topics
- [All Posts](https://basheer.net/posts/): Complete archive of blog posts.

## Posts
${posts.join('\n')}

## Feeds
- [RSS Feed](https://basheer.net/index.xml): Subscribe to new posts via RSS.
- [Sitemap](https://basheer.net/sitemap.xml): Full site map for crawlers.
`;

return [{ json: { content: Buffer.from(llmsTxt).toString('base64') } }];
```

#### Node 3: HTTP Request (Get current SHA)
- **Method**: GET
- **URL**: `https://api.github.com/repos/bash07/basheer.net/contents/static/llms.txt`

#### Node 4: HTTP Request (Update file)
- **Method**: PUT
- **URL**: `https://api.github.com/repos/bash07/basheer.net/contents/static/llms.txt`
- **Body**: `{ "message": "Auto-update llms.txt", "content": "{{$json.content}}", "sha": "{{SHA_FROM_STEP_3}}", "branch": "main" }`

---

## Testing Your Workflows

### Test Workflow 1 (Blog Generator)
```bash
curl -X POST https://n8n.basheer.net/webhook/generate-post \
  -H "Content-Type: application/json" \
  -d '{"topic": "Getting Started with RTOS for Embedded Systems", "category": "Embedded Systems"}'
```

### Verify
1. Check GitHub repo for new file in `content/posts/`
2. Wait ~2 minutes for GitHub Actions to build
3. Visit `https://basheer.net/posts/` to see the new post
4. Check Telegram for the notification
