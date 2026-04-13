#!/usr/bin/env python3
"""
Fetch real AI × labor articles from Google News RSS, classify with keyword
heuristic, write articles.json for the discourse-feed visualization.

Run:
    python3 fetch_gnews.py
"""
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
OUT_FILE = SCRIPT_DIR / "articles.json"
WINDOW_DAYS = 10

QUERIES = [
    "AI jobs labor market",
    "AI layoffs white-collar",
    "AI workers automation",
    "AI hiring recruiters",
    "AI productivity paradox",
    "AI middle managers automation",
    "AI resume job search",
    "AI executive CEO displacement",
    "AI unemployment workforce",
    "ChatGPT jobs workers",
    "AI entry-level young workers",
    "AI workforce transformation",
    "generative AI work office",
    "AI augmentation employees",
    "AI replace workers",
    "AI job cuts 2026",
    "white-collar jobs disappearing AI",
    "AI agents workplace",
]

# Relevance filter — require article to be about jobs/work/labor.
RELEVANCE_PATTERNS = [
    r"\bjob", r"\bwork(er|force|place)?", r"\blabor", r"\bemploy", r"\bhir(e|ing)",
    r"\bcareer", r"\blayoff", r"\bdisplac", r"\btalent\b", r"\brecruit",
    r"\bwhite[- ]collar", r"\bblue[- ]collar", r"\bunemploy", r"\bpayroll",
    r"\bresume\b", r"\brésumé", r"\bsalar", r"\bwage", r"\boccupation",
    r"\bprofession", r"\bmanager", r"\bexecutive", r"\bCEO", r"\bCFO",
    r"\bentry[- ]level", r"\bgrad(uate|s)?\b",
]

# Noise filter — reject product roundups, tool listicles, SEO filler, vendor news.
NOISE_PATTERNS = [
    r"\b(best|top \d+|ultimate guide|how to use|review of)\b.*\btool",
    r"\b\d+ (best|top) ai\b", r"^top \d+ ai", r"^\d+ ai \w+ (tools|apps|platforms)",
    r"\bai (tools|apps) (for|to)\b", r"\bultimate guide\b",
    r"\bfree ai\b", r"\bbuy(er'?s)? guide\b",
    # M&A / partnership / product launch (vendor-news, not discourse)
    r"\bacquire[sd]?\b", r"\bto acquire\b", r"\bannounces? partnership\b",
    r"\blaunches? (new )?(course|tool|platform|product|app|suite)\b",
    r"\bintroduces? (the )?(future|new|next[- ]gen)\b",
    r"\bspotlight\b.*\b(summit|awards|conference|expo)\b",
    r"\bpositions? itself\b", r"\bhighlights? framework\b",
    r"\b\d+ mistakes\b", r"\b\d+ (tips|strategies|ways)\b",
    r"\bresume services?\b",
]

# Noise sources — PR wires, stock-news aggregators, vendor press rooms.
NOISE_SOURCES = {
    "pr newswire", "globenewswire", "business wire", "newswire",
    "tipranks", "stock titan", "stocktitan", "investing.com",
    "pulse 2.0", "seeking alpha", "aimultiple", "ai multiple",
    "prweb", "marketwatch press releases", "benzinga",
}

# Yellow: narrow — only clear paradox / rebuttal / washing language.
YELLOW_PATTERNS = [
    r"\bpara(dox|doxe?s)\b", r"\bai[- ]washing\b", r"\boverstat",
    r"\bisn'?t (causing|going to|killing|replacing)",
    r"\bmay not (kill|replace|take)", r"\bnot yet\b", r"\bmyth(s|ical)?\b",
    r"\brebut", r"\bmisconception", r"\b(got|delivers?) nothing\b",
    r"\bno (real )?impact\b", r"\bno productivity\b", r"\bmore work, not less\b",
    r"\bhas n'?o impact", r"\bgot nothing\b", r"\bisn'?t the (cause|reason)\b",
    r"\bexagger", r"\bhype\b.*(real|truth)", r"\bactually (not|isn'?t)\b",
    r"\bdoom (scenarios?|isn'?t|overstat)", r"\bnot (as|so) bad\b",
    r"\bmany jobs\b.*(safe|survive)",
    r"\bis ai (really|actually)\b",
    r"\bconfusing (your )?job\b",
    r"\btools? (vs|versus) jobs?\b",
    r"\bnot worth\b.*automat",
]

# Green: augmentation, empowerment, reshape-not-replace, creates-jobs.
GREEN_PATTERNS = [
    r"\baugment", r"\bempower", r"\breshape[sd]?\b.*\b(not|more than) replac",
    r"\bmore than (it )?replaces?\b", r"\bcomplement", r"\bcollaborat",
    r"\bupskill", r"\breskill", r"\bcreate[sd]? (new )?jobs?\b",
    r"\bnew roles?\b", r"\bsuperpower", r"\bamplif(y|ies|ied)",
    r"\breshape.*jobs?", r"\breshapes? work", r"\bopportunit.*\bjobs?",
    r"\bhelping (workers|employees|people)", r"\bthrive\b",
    r"\bproductivity (boost|gain|surge)", r"\bcenta(ur|urs)?\b",
    r"\bhuman[- ]ai collaborat",
]

# Red: broad displacement / layoffs / fear / loss language.
RED_PATTERNS = [
    r"\blayoff", r"\bdisplac", r"\breplace[sd]?\b",
    r"\bkill(ing|ed|s)?\b", r"\beliminat", r"\bunemploy",
    r"\blost jobs?", r"\bjob cuts?\b", r"\bbloodbath", r"\brecession",
    r"\bdisappear", r"\bdestroy", r"\bcrush", r"\bthreat", r"\bat risk\b",
    r"\bcollapse", r"\bobsolete", r"\bwipe out", r"\btake(s|n)? (your|our) job",
    r"\bcome for\b", r"\bfired\b", r"\bslash", r"\bcasualt", r"\bbrunt\b",
    r"\bkiller\b", r"\bdevour", r"\bgut(ted|ting)?\b", r"\bcull",
    r"\bstruggl(e|ing) to (find|get) (a )?job", r"\bscarring (effect|impact)",
    r"\bpay cut", r"\bhit by\b", r"\bdoomsday", r"\bunemploy",
    r"\btak(e|ing) over", r"\bentry[- ]level\b.*\b(disappear|vanish|cut|gone)",
    r"\bno jobs? for", r"\bjob (losses|loss)\b", r"\banxiety\b",
    r"\btraining (their|our) replacements?\b", r"\bsqueez", r"\bladder\b.*\bbroken",
    r"\bscare(d|s)?\b.*\bAI", r"\bbloody\b", r"\bhit\b.*\blayoff",
    r"\bcost\b.*\bjobs?\b", r"\b(would|will) replace\b",
    r"\btook .*\bjobs?\b", r"\b\d+ million jobs?\b", r"\bmillion jobs?\b",
    r"\bstol(e|en|es|en away)\b.*\bjobs?\b",
    r"\bjobs?\b (gone|vanish|lost to|taken by|evaporat)",
    r"\btake(n|s)?\b.*\bjobs?\b.*\b(already|now|million|billion|thousand)",
    r"\btak(en|ing) over .*\bjobs?\b",
]


def is_relevant(title: str, source: str) -> bool:
    t = title.lower()
    if source.strip().lower() in NOISE_SOURCES:
        return False
    if any(re.search(p, t) for p in NOISE_PATTERNS):
        return False
    return any(re.search(p, t) for p in RELEVANCE_PATTERNS)


def classify(title: str) -> tuple[str, str]:
    t = title.lower()
    yellow = sum(1 for p in YELLOW_PATTERNS if re.search(p, t))
    if yellow > 0:
        return "yellow", f"paradox/rebuttal ({yellow})"
    red = sum(1 for p in RED_PATTERNS if re.search(p, t))
    green = sum(1 for p in GREEN_PATTERNS if re.search(p, t))
    if green > red and green > 0:
        return "green", f"augmentation ({green})"
    if red > 0:
        return "red", f"displacement ({red})"
    return "yellow", "ambivalent (no strong signal)"


def fetch_rss(query: str) -> bytes:
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_rss(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall("./channel/item"):
        title_raw = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        source = source_el.text.strip() if (source_el is not None and source_el.text) else ""

        title = title_raw
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        elif " - " in title_raw and not source:
            left, right = title_raw.rsplit(" - ", 1)
            title, source = left, right

        if not title or not link or not pub:
            continue
        try:
            dt = parsedate_to_datetime(pub)
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        items.append({
            "title": title.strip(),
            "url": link,
            "source": source.strip() or "Unknown",
            "timestamp": dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
    return items


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WINDOW_DAYS)
    seen = {}
    fetched_total = 0

    for q in QUERIES:
        print(f"[+] fetching: {q}")
        try:
            xml = fetch_rss(q)
            items = parse_rss(xml)
            fetched_total += len(items)
            kept_this_query = 0
            for it in items:
                dt = datetime.fromisoformat(it["timestamp"].replace("Z", "+00:00"))
                if dt < cutoff or dt > now + timedelta(hours=1):
                    continue
                if not is_relevant(it["title"], it["source"]):
                    continue
                key = (it["title"].lower(), it["source"].lower())
                if key in seen:
                    continue
                seen[key] = it
                kept_this_query += 1
            print(f"    → {len(items)} raw, {kept_this_query} new in window")
        except Exception as e:
            print(f"    ! failed: {e}")

    items = sorted(seen.values(), key=lambda x: x["timestamp"])
    print(f"\n[+] total fetched: {fetched_total}")
    print(f"[+] unique in {WINDOW_DAYS}-day window: {len(items)}")

    articles = []
    for it in items:
        color, why = classify(it["title"])
        dt = datetime.fromisoformat(it["timestamp"].replace("Z", "+00:00"))
        art_id = f"{dt.date().isoformat()}-{slug(it['source'])}-{slug(it['title'])}"[:130]
        articles.append({
            "id": art_id,
            "timestamp": it["timestamp"],
            "source": it["source"],
            "title": it["title"],
            "url": it["url"],
            "color": color,
            "why": why,
        })

    OUT_FILE.write_text(json.dumps(articles, indent=2, ensure_ascii=False))

    counts = {"red": 0, "yellow": 0, "green": 0}
    for a in articles:
        counts[a["color"]] += 1
    by_day = {}
    for a in articles:
        day = a["timestamp"][:10]
        by_day[day] = by_day.get(day, 0) + 1

    print(f"\n[+] wrote {len(articles)} articles → {OUT_FILE}")
    print(f"[+] colors: {counts}")
    print(f"[+] by day:")
    for day, n in sorted(by_day.items()):
        print(f"       {day}: {n}")


if __name__ == "__main__":
    main()
