#!/usr/bin/env python3
"""
Classify articles.json with Claude (Haiku) via the Messages Batches API.
Preserves the heuristic result as heuristic_color/heuristic_why, overwrites
color/why with the LLM result.

Run:
    python3 classify_llm.py

Loads ANTHROPIC_API_KEY from env, then from local .env, then from
../ai-career-tool/.env (shared across X Engineer subprojects).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ARTICLES = SCRIPT_DIR / "articles.json"
MODEL = "claude-haiku-4-5-20251001"
API_BASE = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"
POLL_SECONDS = 6

PROMPT_TEMPLATE = """You are classifying a news article about AI and labor markets into one of three narrative frames.

Frames:
- "red" = AI-doomer: reinforces mass-displacement narrative (layoffs, job loss, unemployable workers, entry-level collapse, AI replacing workers, catastrophic forecasts, executives warning of doom).
- "yellow" = paradox/skeptic: reinforces rebuttal/paradox narrative (AI delivers nothing, AI-washing, overstated claims, productivity paradox, methodological critique, "not yet", "tools vs jobs" framing, ambivalent worker reactions, mixed signals, no clear position).
- "green" = augmentation: reinforces AI-as-tool narrative — requires EXPLICIT claim that workers benefit: AI creates new jobs, empowers workers, upskills people, makes work easier FOR WORKERS, enables human-AI collaboration with workers gaining, generates opportunity that workers can seize.

CRITICAL ANTI-TRAP RULE:
Corporate-speak terms alone do NOT make an article green. Words like "transformation", "reshape", "AI-first era", "workforce evolution", "governance", "adapt", "change management" in isolation are NEUTRAL — they're often corporate euphemisms that can equally describe displacement or augmentation. Classify green ONLY if the article explicitly frames workers as beneficiaries (new jobs created, worker capability expanded, career improved, tools handed to workers). If the article only uses transformation rhetoric without a clear pro-worker claim, classify yellow.

Examples:
- "CFOs admit 9x layoffs but only 0.4% of workforce" → yellow (amplifies paradox, not doom)
- "AI will reshape jobs more than replace them" → green (explicit "more than replace" = pro-worker claim)
- "Governing the AI Workforce Transformation" → yellow (corporate-speak only, no explicit worker-benefit)
- "Paylocity Acquires AI Recruiting Firm Grayscale" → yellow (vendor M&A, no narrative claim)
- "AI adopters aren't cutting jobs, they're creating them" → green (explicit job-creation claim)
- "Anthropic warns: Great Recession for white-collar workers" → red (amplifies doom)
- "Yale economist: AGI won't automate most jobs" → yellow (amplifies rebuttal)
- "AI Already Took 3 Million Jobs" → red (explicit mass displacement)
- "Workforce Transformation as a Growth Strategy" → yellow (corporate frame, no explicit worker benefit)
- "Why AI Could Be the Best Thing for Your Professional Future" → green (explicit pro-worker framing)

Article:
Title: {title}
Source: {source}

Respond with ONLY a single-line JSON object, no markdown, no commentary:
{{"color": "red"|"yellow"|"green", "why": "one short sentence explaining which narrative the article amplifies"}}"""


def load_env():
    for p in [SCRIPT_DIR / ".env", SCRIPT_DIR.parent / "ai-career-tool" / ".env"]:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)
        if os.environ.get("ANTHROPIC_API_KEY"):
            print(f"[env] loaded ANTHROPIC_API_KEY from {p}")
            return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[env] ANTHROPIC_API_KEY not found. Set in .env or export.", file=sys.stderr)
        sys.exit(1)


def api_request(method: str, path: str, body: dict | None = None) -> dict:
    url = API_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        print(f"[api] {method} {path} → {e.code}\n{detail}", file=sys.stderr)
        raise


def get_raw(path: str) -> bytes:
    url = API_BASE + path
    headers = {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": API_VERSION,
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def build_requests(articles):
    reqs = []
    for i, a in enumerate(articles):
        user = PROMPT_TEMPLATE.format(title=a["title"], source=a["source"])
        reqs.append({
            "custom_id": f"art-{i}",
            "params": {
                "model": MODEL,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": user}],
            },
        })
    return reqs


# Shadow-check: formal rules to flag likely LLM failures.
# LLM reads meaning but gets lured by surface framing (corporate-speak "green");
# the shadow applies explicit rules we trust more than LLM on these specific traps.

CORPORATE_SPEAK = re.compile(
    r"\b(transformation|reshap(e|ing)|ai[- ]first|ai[- ]era|governance|"
    r"change management|adapt|adapting|workforce evolution|"
    r"future of (work|hiring)|strategic shift)\b",
    re.I,
)
WORKER_BENEFIT = re.compile(
    r"\b(creates? (new )?jobs?|empower|upskill|reskill|centa(ur|urs)|"
    r"human[- ]ai collaborat|workers? (win|gain|thrive)|new roles for|"
    r"opportunit(y|ies) for (workers|employees)|benefits? (workers|employees)|"
    r"not replac|more than (it )?replaces?|create(s|d)? jobs?|"
    r"better (for|your) (career|job)|(help|empower|support)s? (workers|employees|people))\b",
    re.I,
)
STRONG_DISPLACEMENT = re.compile(
    r"\b(layoff|mass displac|replaces? (workers|humans|employees)|"
    r"\d+[,.]?\d* ?(million|thousand|m |k) (jobs?|workers)|"
    r"took .*jobs?|destroys? (jobs?|work)|kills? (jobs?|careers?)|"
    r"wipes? out|unemploy|great recession|bloodbath|"
    r"doomsday|'?essentially unemployable'?|"
    r"half of .*(jobs?|white[- ]collar)|30% .*(unemploy|graduates?)|"
    r"cutting \d+|16[,.]?000 .*jobs?)\b",
    re.I,
)
VENDOR_MARKERS = re.compile(
    r"\b(acquires?|acquired|announces? partnership|introduces? (the )?future|"
    r"launches? (course|tool|platform|product)|summit \d{4}|awards \d{4}|"
    r"positions? itself|highlights? framework|expand(s|ed)? capabilities)\b",
    re.I,
)


def shadow_check(title: str, source: str, llm_color: str) -> dict:
    """Apply formal rules; return a flag if LLM likely fell for a textual trap."""
    # Rule 1: vendor / M&A / PR masquerading as discourse
    if VENDOR_MARKERS.search(title):
        return {
            "flag": "vendor_noise",
            "note": "title reads as M&A / PR / product launch, not discourse",
        }

    # Rule 2: LLM green that relies only on corporate-speak without worker-benefit
    if llm_color == "green":
        has_corp = bool(CORPORATE_SPEAK.search(title))
        has_benefit = bool(WORKER_BENEFIT.search(title))
        if has_corp and not has_benefit:
            return {
                "flag": "suspect_green",
                "note": "corporate-speak without explicit worker-benefit — likely yellow",
            }

    # Rule 3: LLM missed explicit displacement language → should be red
    if llm_color != "red" and STRONG_DISPLACEMENT.search(title):
        return {
            "flag": "suspect_missed_red",
            "note": "strong displacement language in title — LLM did not mark red",
        }

    return {"flag": "ok", "note": ""}


def parse_llm_output(text: str):
    # Strip code fences if any
    text = text.strip()
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    color = obj.get("color")
    why = obj.get("why")
    if color not in {"red", "yellow", "green"}:
        return None
    return color, (why or "").strip()


def main():
    load_env()
    articles = json.loads(ARTICLES.read_text())
    print(f"[+] loaded {len(articles)} articles")

    # Preserve heuristic before overwriting
    for a in articles:
        if "heuristic_color" not in a:
            a["heuristic_color"] = a["color"]
            a["heuristic_why"] = a["why"]

    reqs = build_requests(articles)
    print(f"[+] submitting batch with {len(reqs)} requests")
    batch = api_request("POST", "/messages/batches", {"requests": reqs})
    batch_id = batch["id"]
    print(f"[+] batch id: {batch_id}")

    while True:
        b = api_request("GET", f"/messages/batches/{batch_id}")
        status = b.get("processing_status")
        counts = b.get("request_counts", {})
        print(f"[poll] status={status} counts={counts}")
        if status == "ended":
            break
        time.sleep(POLL_SECONDS)

    results_url = b.get("results_url")
    if not results_url:
        print("[!] no results_url on ended batch", file=sys.stderr)
        sys.exit(1)
    path = results_url.split(API_BASE, 1)[-1] if API_BASE in results_url else results_url
    print(f"[+] fetching results from {path}")
    raw = get_raw(path)

    by_id = {}
    for line in raw.decode().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        cid = rec.get("custom_id")
        result = rec.get("result", {})
        if result.get("type") != "succeeded":
            by_id[cid] = None
            continue
        content = result.get("message", {}).get("content", [])
        text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
        by_id[cid] = parse_llm_output(text)

    # Merge back
    for i, a in enumerate(articles):
        parsed = by_id.get(f"art-{i}")
        if parsed is None:
            a["color"] = a["heuristic_color"]
            a["why"] = a["heuristic_why"] + " [llm failed]"
        else:
            color, why = parsed
            a["color"] = color
            a["why"] = why
        shadow = shadow_check(a["title"], a["source"], a["color"])
        a["shadow_flag"] = shadow["flag"]
        a["shadow_note"] = shadow["note"]

    ARTICLES.write_text(json.dumps(articles, indent=2, ensure_ascii=False))

    # Stats
    colors = {"red": 0, "yellow": 0, "green": 0}
    heur = {"red": 0, "yellow": 0, "green": 0}
    agree = 0
    changes = {}
    flags = {}
    for a in articles:
        colors[a["color"]] += 1
        heur[a["heuristic_color"]] += 1
        if a["color"] == a["heuristic_color"]:
            agree += 1
        else:
            k = f"{a['heuristic_color']} → {a['color']}"
            changes[k] = changes.get(k, 0) + 1
        f = a["shadow_flag"]
        flags[f] = flags.get(f, 0) + 1

    print(f"\n[+] LLM colors:       {colors}")
    print(f"[+] Heuristic colors: {heur}")
    print(f"[+] Agreement: {agree}/{len(articles)} ({100*agree/len(articles):.1f}%)")
    print(f"[+] Disagreement patterns:")
    for k, v in sorted(changes.items(), key=lambda x: -x[1]):
        print(f"       {k}: {v}")
    print(f"\n[+] Shadow verdict:")
    for k, v in sorted(flags.items(), key=lambda x: -x[1]):
        print(f"       {k}: {v}")

    suspect = [a for a in articles if a["shadow_flag"] != "ok"]
    if suspect:
        print(f"\n[+] Review queue ({len(suspect)} items):")
        for a in suspect[:30]:
            print(f"   [{a['shadow_flag']}] LLM={a['color']:6} | [{a['source']}] {a['title'][:80]}")
            print(f"       └ {a['shadow_note']}")


if __name__ == "__main__":
    main()
