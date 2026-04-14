#!/usr/bin/env python3
"""
Add a `roles: []` list to every article in articles.json.

Reads articles.json, for each article asks Claude Haiku which roles are
the primary subject (not just mentioned in passing), writes result back.
Preserves all existing fields (color, why, shadow_flag, etc.).

Roles taxonomy (closed set):
    CEO | CFO | CTO_CIO | CMO | middle_manager | entry_level |
    engineer_ic | general_workforce | other

Run:
    python3 extract_roles.py
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Reuse API helpers from classify_llm.py
sys.path.insert(0, str(Path(__file__).parent))
from classify_llm import api_request, load_env, parse_llm_output  # noqa: E402

SCRIPT_DIR = Path(__file__).parent
ARTICLES = SCRIPT_DIR / "articles.json"
MODEL = "claude-haiku-4-5-20251001"

ROLES = {
    "CEO", "CFO", "CTO_CIO", "CMO",
    "middle_manager", "entry_level", "engineer_ic", "general_workforce",
}

PROMPT = """You are tagging a news article about AI and labor markets with which job roles it centrally discusses.

Closed set of roles (use these exact tokens):
- "CEO" — chief executive officer as the subject (Dimon, Altman CEO comments, "CEO job at risk")
- "CFO" — chief financial officer, finance function
- "CTO_CIO" — chief technology officer, chief information officer, technology leadership
- "CMO" — chief marketing officer, marketing function
- "middle_manager" — middle management, "bosses", "managers" without executive context, span-of-control stories
- "entry_level" — new graduates, entry-level white-collar, Gen Z starting careers, early-career workers
- "engineer_ic" — software engineers, developers, individual contributors in tech
- "general_workforce" — "workers", "employees", "white-collar workers" without specific role, broad labor stats

Rules:
- Return ONLY roles that are the PRIMARY SUBJECT of the article, not every role mentioned in passing.
- If the article is about AI affecting "workers" broadly, use "general_workforce".
- If no role from the closed set fits, return empty array [].
- Usually 1-2 roles per article. Rarely 3.

Article:
Source: {source}
Title: {title}
Summary: {summary}

Respond with ONLY a single-line JSON object, no markdown, no commentary:
{{"roles": ["CEO"], "why": "one short sentence explaining which roles are primary"}}"""


def parse_roles_output(text: str) -> tuple[list[str], str] | None:
    text = text.strip()
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    roles = obj.get("roles", [])
    if not isinstance(roles, list):
        return None
    roles = [r for r in roles if r in ROLES]
    why = (obj.get("why") or "").strip()
    return roles, why


def extract_one(article: dict) -> tuple[list[str], str] | None:
    prompt = PROMPT.format(
        title=article.get("title", ""),
        source=article.get("source", ""),
        summary=(article.get("summary", "") or "")[:500],
    )
    body = {
        "model": MODEL,
        "max_tokens": 150,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = api_request("POST", "/messages", body)
    except Exception as e:
        print(f"  ! extract failed: {e}")
        return None
    content = resp.get("content", [])
    text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
    return parse_roles_output(text)


def main():
    load_env()
    articles = json.loads(ARTICLES.read_text())
    print(f"[+] loaded {len(articles)} articles")

    to_process = [i for i, a in enumerate(articles) if "roles" not in a]
    print(f"[+] extracting roles for {len(to_process)} articles (skipping {len(articles)-len(to_process)} already tagged)")
    if not to_process:
        print("  nothing to do")
        return

    t_start = time.time()
    results: dict[int, tuple] = {}

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(extract_one, articles[i]): i for i in to_process}
        done = 0
        for fut in as_completed(futures):
            i = futures[fut]
            parsed = fut.result()
            results[i] = parsed
            done += 1
            if done % 20 == 0:
                print(f"  [{done}/{len(to_process)}] extracted")

    for i, parsed in results.items():
        if parsed is None:
            articles[i]["roles"] = []
            articles[i]["roles_why"] = "extraction failed"
        else:
            roles, why = parsed
            articles[i]["roles"] = roles
            articles[i]["roles_why"] = why

    ARTICLES.write_text(json.dumps(articles, indent=2, ensure_ascii=False))
    print(f"[+] wrote {ARTICLES} in {time.time()-t_start:.1f}s")

    # Stats
    from collections import Counter
    role_counts = Counter()
    zero = 0
    for a in articles:
        r = a.get("roles", [])
        if not r:
            zero += 1
        for x in r:
            role_counts[x] += 1
    print(f"\n[+] Role distribution:")
    for role, n in role_counts.most_common():
        print(f"    {role:20s} {n}")
    print(f"    {'(no role)':20s} {zero}")


if __name__ == "__main__":
    main()
