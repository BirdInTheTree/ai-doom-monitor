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
    # Wave 1 — olympians
    "CEO", "CFO", "COO", "CTO", "CIO", "CMO", "CLO",
    # Wave 2 — ~2000-2015
    "CHRO", "CISO", "chief_data", "chief_commercial", "chief_compliance",
    # Wave 3 — ~2020-2026
    "CAIO", "chief_diversity", "chief_sustainability",
    # Catch-all for articles not about a specific C-suite role
    "non_csuite",
}

PROMPT = """You are tagging a news article about AI and labor markets with which C-suite role it centrally discusses.

This taxonomy matches the user's research corpus on executive roles (C-suite pantheon). Use EXACT tokens.

Wave 1 (olympians):
- "CEO" — chief executive officer; top-leader stories, Altman/Dimon/Huang quotes about the CEO role itself
- "CFO" — chief financial officer, finance function; CFO-led AI, finance-team workflows
- "COO" — chief operating officer; operations, supply chain, day-to-day execution
- "CTO" — chief technology officer; R&D, product-tech leadership
- "CIO" — chief information officer; enterprise IT, systems, internal platforms
- "CMO" — chief marketing officer; brand, creative, content, advertising. **Creative workers (illustrators, designers, copywriters, video editors, artists, brand-content producers) map to CMO** — they organizationally sit under marketing.
- "CLO" — chief legal officer / general counsel; legal function, regulation of AI in-house

Wave 2 (~2000-2015):
- "CHRO" — chief human resources officer; talent, hiring, HR-tech, layoff decisions
- "CISO" — chief information security officer; cybersecurity, AI-threat defense
- "chief_data" — Chief Data Officer; data platforms, analytics function
- "chief_commercial" — Chief Commercial Officer (CCO); sales, growth, revenue
- "chief_compliance" — Chief Compliance Officer; regulatory, audit

Wave 3 (~2020-2026):
- "CAIO" — Chief AI Officer; AI adoption leadership, AI strategy, AI governance
- "chief_diversity" — Chief Diversity Officer; DEI, representation
- "chief_sustainability" — Chief Sustainability Officer; ESG, climate in AI (data-center carbon footprints)

Catch-all:
- "non_csuite" — article isn't about a specific C-suite role; covers general workforce, middle managers, entry-level, engineers / IC workers, specific professions (teachers, nurses, truck drivers, etc.), or macro-labor statistics

Rules:
- Return ONLY roles that are the PRIMARY SUBJECT of the article, not every role mentioned in passing.
- If the article is about "workers" broadly, middle managers, Gen Z graduates, software engineers, or any non-executive group, use "non_csuite".
- If the article is about creative labor being disrupted by AI (artists, illustrators, designers), use "CMO".
- If no role fits, return empty array [].
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
