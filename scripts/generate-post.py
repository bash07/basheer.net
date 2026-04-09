#!/usr/bin/env python3
"""
Blog Post Generator using Google Gemini API (Free Tier)
Generates SEO-optimized markdown blog posts with proper Hugo front matter.

Usage:
  python generate-post.py --topic "Your topic here"
  python generate-post.py --topic "Your topic here" --category "AI"

Environment:
  GEMINI_API_KEY  — Your Google Gemini API key (from aistudio.google.com/apikey)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

try:
    import google.generativeai as genai
except ImportError:
    print("ERROR: google-generativeai package not found.")
    print("Install with: pip install google-generativeai")
    sys.exit(1)


def slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text.strip('-')


def generate_post(topic: str, category: str = "Technology") -> dict:
    """Generate a blog post using Gemini API."""

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set. "
                         "Get one free at https://aistudio.google.com/apikey")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    prompt = f"""You are an expert technical blog writer. Write a comprehensive,
engaging blog post about the following topic:

Topic: {topic}
Category: {category}

Requirements:
1. Write in a conversational yet professional tone
2. Include practical examples and code snippets where relevant
3. Structure with clear headings (## and ###)
4. Include a compelling introduction and conclusion
5. Aim for 1500-2500 words
6. Use emoji sparingly for visual interest
7. Include actionable takeaways
8. Write in a way that demonstrates genuine expertise

IMPORTANT: Return your response as a JSON object with these exact fields:
{{
  "title": "Compelling SEO-friendly title (50-60 chars ideal)",
  "description": "Meta description for SEO (150-160 chars)",
  "summary": "Brief 1-2 sentence summary for listing pages",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "content": "The full markdown body of the post (no front matter, use ## for top headings)"
}}

Return ONLY valid JSON. No markdown code fences around it."""

    response = model.generate_content(prompt)

    # Parse the JSON response
    response_text = response.text.strip()
    # Remove potential markdown code fences
    if response_text.startswith("```"):
        response_text = re.sub(r'^```(?:json)?\n?', '', response_text)
        response_text = re.sub(r'\n?```$', '', response_text)

    return json.loads(response_text)


def create_hugo_post(post_data: dict, category: str = "Technology") -> str:
    """Create a Hugo-compatible markdown file from generated post data."""

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    slug = slugify(post_data["title"])

    # Escape double quotes in title and description for YAML safety
    safe_title = post_data['title'].replace('"', '\\"')
    safe_desc = post_data['description'].replace('"', '\\"')
    safe_summary = post_data['summary'].replace('"', '\\"')

    front_matter = f'''---
title: "{safe_title}"
date: {date_str}
lastmod: {date_str}
draft: false
description: "{safe_desc}"
summary: "{safe_summary}"
tags: {json.dumps(post_data['tags'])}
categories: ["{category}"]
keywords: {json.dumps(post_data['keywords'])}
author: "Basheer"
showToc: true
TocOpen: false
ShowReadingTime: true
ShowBreadCrumbs: true
ShowPostNavLinks: true
ShowWordCount: true
cover:
    image: ""
    alt: "{safe_title}"
    caption: ""
---

{post_data['content']}
'''

    filename = f"{slug}.md"
    filepath = os.path.join("content", "posts", filename)

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(front_matter)

    return filepath


def main():
    parser = argparse.ArgumentParser(
        description="Generate SEO-optimized blog posts with Google Gemini AI",
        epilog="Example: python generate-post.py --topic 'Getting Started with RTOS' --category 'Embedded Systems'"
    )
    parser.add_argument("--topic", required=True, help="Topic for the blog post")
    parser.add_argument("--category", default="Technology", help="Post category (default: Technology)")
    args = parser.parse_args()

    print(f"🤖 Generating post about: {args.topic}")
    print(f"📂 Category: {args.category}")
    print(f"⏳ Calling Gemini API...")

    try:
        post_data = generate_post(args.topic, args.category)
        filepath = create_hugo_post(post_data, args.category)
        print(f"✅ Post created: {filepath}")
        print(f"📝 Title: {post_data['title']}")
        print(f"🏷️  Tags: {', '.join(post_data['tags'])}")
        # Machine-readable output for automation
        print(f"::filepath::{filepath}")
        print(f"::slug::{slugify(post_data['title'])}")
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse Gemini response as JSON: {e}")
        print("   Try running again — the model occasionally returns malformed JSON.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
