#!/usr/bin/env python3
"""
Source-based fetcher for AI Doom Monitor.

Reads sources.yaml. For each source either (a) fetches direct RSS/Atom feed
or (b) routes a `site:<domain>` query through Google News RSS. Applies per-
source relevance filter (ai_signal / labor_signal keyword match modes),
dedupes by URL + title similarity, enriches with region + tier metadata,
writes articles.json.

Run:
    python3 fetch_sources.py
"""
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent
SOURCES_FILE = SCRIPT_DIR / "sources.yaml"
OUT_FILE = SCRIPT_DIR / "articles.json"
WINDOW_DAYS = 10
UA = "Mozilla/5.0 (compatible; AiDoomMonitor/0.2; +https://github.com/BirdInTheTree/ai-doom-monitor)"
FEED_TIMEOUT = 20
PARALLEL = 10

# -----------------------------------------------------------------------------
# Keyword signals
# -----------------------------------------------------------------------------

AI_SIGNAL = re.compile(
    r"\b("
    r"ai|artificial intelligence|llm|llms|chatgpt|claude|openai|anthropic|"
    r"gemini|copilot|machine learning|neural net(work)?|generative ai|genai|"
    r"large language model|ai agent|agentic|algorithm(ic)?|automation"
    r")\b",
    re.IGNORECASE,
)

LABOR_SIGNAL = re.compile(
    r"\b("
    r"job|jobs|work|worker|workers|workforce|workplace|labor|labour|"
    r"employ(ment|ed|er|ee|ees)?|hir(e|ing|ed)|recruit(er|ers|ment|ing)?|"
    r"career|careers|layoff|layoffs|displac(e|ed|ement)|unemploy(ment|ed)?|"
    r"manager|managers|management|executive|executives|"
    r"ceo|cfo|cto|cio|cmo|c-suite|"
    r"entry.level|graduate|graduates|wage|wages|salary|"
    r"white.collar|blue.collar|hr|talent|"
    r"reskill|upskill|professional|staff|occupation|productivity"
    r")\b",
    re.IGNORECASE,
)


def load_config() -> dict:
    return yaml.safe_load(SOURCES_FILE.read_text())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def slug(s: str, n: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n]


def canonicalize(url: str) -> str:
    """Strip tracking params and fragments for dedup."""
    try:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
        keep = [(k, v) for k, v in query if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                         urllib.parse.urlencode(keep), ""))
    except Exception:
        return url


def title_tokens(t: str) -> set:
    return set(re.findall(r"[a-z]{3,}", t.lower()))


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# -----------------------------------------------------------------------------
# Fetching
# -----------------------------------------------------------------------------

def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=FEED_TIMEOUT) as resp:
        return resp.read()


def build_gnews_site_url(site: str) -> str:
    query = (
        f'site:{site} (AI OR "artificial intelligence" OR LLM OR ChatGPT) '
        f'(job OR jobs OR work OR workers OR hiring OR labor OR layoff OR workforce OR employment)'
    )
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


# -----------------------------------------------------------------------------
# Parsing RSS + Atom
# -----------------------------------------------------------------------------

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "").strip()


def parse_feed(xml_bytes: bytes) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items

    # RSS 2.0
    rss_items = root.findall("./channel/item")
    for it in rss_items:
        title = _text(it.find("title"))
        link = _text(it.find("link"))
        pub = _text(it.find("pubDate"))
        desc = _strip_html(_text(it.find("description")))
        src_el = it.find("source")
        source = src_el.text.strip() if (src_el is not None and src_el.text) else ""
        items.append(dict(title=title, url=link, pubdate=pub, summary=desc, source=source))

    # Atom
    atom_entries = root.findall("./atom:entry", NS) or root.findall("./{*}entry")
    for e in atom_entries:
        title = _text(e.find("atom:title", NS) or e.find("{*}title"))
        link_el = e.find("atom:link", NS) or e.find("{*}link")
        link = link_el.attrib.get("href", "") if link_el is not None else ""
        pub = _text(e.find("atom:published", NS) or e.find("atom:updated", NS)
                    or e.find("{*}published") or e.find("{*}updated"))
        summ = _strip_html(_text(e.find("atom:summary", NS)
                                 or e.find("atom:content", NS)
                                 or e.find("{*}summary") or e.find("{*}content")))
        items.append(dict(title=title, url=link, pubdate=pub, summary=summ, source=""))

    return items


def parse_timestamp(pub: str) -> datetime | None:
    if not pub:
        return None
    try:
        dt = parsedate_to_datetime(pub)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# -----------------------------------------------------------------------------
# Relevance filter
# -----------------------------------------------------------------------------

def passes_filter(title: str, summary: str, mode: str) -> bool:
    if mode == "accept_all":
        return True
    blob = f"{title}\n{summary or ''}"
    has_ai = bool(AI_SIGNAL.search(blob))
    has_labor = bool(LABOR_SIGNAL.search(blob))
    if mode == "strict":
        return has_ai and has_labor
    if mode == "broad":
        return has_ai or has_labor
    # unknown mode — default strict
    return has_ai and has_labor


def is_blacklisted(source_name: str, blacklist: list[str]) -> bool:
    s = (source_name or "").lower()
    return any(bl in s for bl in blacklist)


# -----------------------------------------------------------------------------
# Google News title cleanup
# -----------------------------------------------------------------------------

def clean_gnews_title(title: str, source: str) -> tuple[str, str]:
    """Google News RSS titles look like 'Real title - Source'. Split them."""
    if source and title.endswith(" - " + source):
        return title[: -len(" - " + source)], source
    if " - " in title:
        left, right = title.rsplit(" - ", 1)
        if len(right) < 80:
            return left, right
    return title, source


# -----------------------------------------------------------------------------
# Fetch one source
# -----------------------------------------------------------------------------

def fetch_source(src: dict, blacklist: list[str], cutoff: datetime, now: datetime) -> list[dict]:
    name = src["name"]
    tier = src["tier"]
    region = src.get("region", "Unknown")
    filter_mode = src.get("filter", "strict")

    if src["kind"] == "rss":
        url = src["url"]
    elif src["kind"] == "gnews_site":
        url = build_gnews_site_url(src["site"])
    else:
        return []

    try:
        xml = http_get(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError) as e:
        print(f"  ! [{name}] fetch failed: {e}")
        return []

    raw_items = parse_feed(xml)
    kept = []
    for item in raw_items:
        ts = parse_timestamp(item["pubdate"])
        if not ts or ts < cutoff or ts > now + timedelta(hours=1):
            continue
        title = (item["title"] or "").strip()
        if not title:
            continue

        # For gnews_site sources, GN returns "title - source" and inner <source>
        inner_source = item["source"]
        if src["kind"] == "gnews_site":
            title, inner_source = clean_gnews_title(title, inner_source)
            # The actual publisher (inner_source) may differ from our `name`
            # which is just a logical grouping. Use inner_source for display.
            display_source = inner_source or name
        else:
            display_source = name

        if is_blacklisted(display_source, blacklist):
            continue

        summary = (item.get("summary", "") or "")[:400]
        if not passes_filter(title, summary, filter_mode):
            continue

        kept.append({
            "id": f"{ts.date().isoformat()}-{slug(display_source, 30)}-{slug(title, 60)}"[:130],
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "source": display_source,
            "title": title,
            "url": item["url"],
            "summary": summary[:400],
            "region": region,
            "tier": tier,
            "feed": name,
            "color": "",
            "why": "",
        })
    return kept


# -----------------------------------------------------------------------------
# Dedup across sources
# -----------------------------------------------------------------------------

def dedupe(items: list[dict], title_sim_threshold: float = 0.78) -> list[dict]:
    seen_urls = set()
    seen_titles = []
    out = []
    # Prefer higher-tier sources first: within same story, Tier 1 wins
    items.sort(key=lambda a: (a["tier"], a["timestamp"]))
    for a in items:
        url_c = canonicalize(a["url"])
        url_h = hashlib.md5(url_c.encode()).hexdigest()
        if url_h in seen_urls:
            continue
        toks = title_tokens(a["title"])
        if any(jaccard(toks, prev) >= title_sim_threshold for prev in seen_titles):
            continue
        seen_urls.add(url_h)
        seen_titles.append(toks)
        out.append(a)
    return out


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    cfg = load_config()
    sources = cfg["sources"]
    blacklist = [s.lower() for s in cfg.get("blacklist_sources", [])]

    now = now_utc()
    cutoff = now - timedelta(days=WINDOW_DAYS)

    print(f"[+] loaded {len(sources)} sources from sources.yaml")
    print(f"[+] window: {cutoff.isoformat()} → {now.isoformat()}")
    print(f"[+] blacklist: {len(blacklist)} strings\n")

    all_items = []
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        futures = {ex.submit(fetch_source, s, blacklist, cutoff, now): s for s in sources}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                items = fut.result()
            except Exception as e:
                print(f"  ! [{src['name']}] unexpected: {e}")
                items = []
            kind_tag = "RSS" if src["kind"] == "rss" else "GN  "
            print(f"  [{kind_tag}] {src['name']:45s} {len(items):3d} items")
            all_items.extend(items)

    print(f"\n[+] fetched {len(all_items)} items (before dedup) in {time.time()-t_start:.1f}s")
    deduped = dedupe(all_items)
    print(f"[+] after dedup: {len(deduped)}")

    # Stable sort by timestamp ascending
    deduped.sort(key=lambda a: a["timestamp"])

    OUT_FILE.write_text(json.dumps(deduped, indent=2, ensure_ascii=False))
    print(f"[+] wrote {OUT_FILE}")

    # Stats
    from collections import Counter
    by_region = Counter(a["region"] for a in deduped)
    by_tier = Counter(a["tier"] for a in deduped)
    by_day = Counter(a["timestamp"][:10] for a in deduped)
    top_sources = Counter(a["source"] for a in deduped).most_common(15)

    print(f"\n[+] by region: {dict(by_region)}")
    print(f"[+] by tier:   {dict(by_tier)}")
    print(f"[+] by day:")
    for d, n in sorted(by_day.items()):
        print(f"       {d}: {n}")
    print(f"[+] top sources:")
    for s, n in top_sources:
        print(f"       {n:3d}  {s}")


if __name__ == "__main__":
    main()
