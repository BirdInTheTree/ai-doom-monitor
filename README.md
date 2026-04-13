# AI Doom Monitor

Vertical news feed visualizing the last 10 days of English-language discourse about AI and labor markets. Each article is one coloured row; colour encodes which narrative frame the headline reinforces.

**Live demo:** https://birdinthetree.github.io/ai-doom-monitor/

## Taxonomy

- 🔴 **Red — AI-doomer.** Reinforces mass-displacement narrative: layoffs, job loss, unemployable workers, entry-level collapse, executives warning of doom.
- ⚪ **Grey — paradox / skeptic.** Reinforces rebuttal/paradox: AI-washing, productivity paradox, overstated claims, "not yet", tools-vs-jobs, mixed signals.
- 🟢 **Green — augmentation.** Reinforces AI-as-tool: explicit claims that workers benefit — new jobs created, upskilling, "reshape not replace", human-AI collaboration.

The classification is by **which narrative the headline amplifies**, not the article's surface tone. "CFOs admit 9x layoffs but only 0.4% of workforce" → grey (amplifies paradox), not red.

## Layout

- Reverse-chronological vertical feed, newest at top.
- Days 1–7 shown as full rows (time · source · headline, coloured background, click to open source URL).
- Days 8–10 collapsed into archive stripes showing colour proportions with counts.
- No filters, no search, no header. One column, one scroll.

## Pipeline

1. **`fetch_gnews.py`** — queries ~18 keyword permutations against the public Google News RSS endpoint, deduplicates, applies relevance + noise filters, writes real titles/URLs/sources.
2. **`classify_llm.py`** — classifies each article via Claude Haiku through the Messages Batches API. Preserves the heuristic pass as `heuristic_color`/`heuristic_why` for comparison.
3. **Shadow check** — a regex-based formal reviewer that flags likely LLM traps (corporate-speak greens, missed displacement, vendor-noise). Writes `shadow_flag`/`shadow_note` per article.

## Known limitations

- **Title-only classification.** Article bodies aren't fetched yet — headlines distort toward doom (doomer claims fit headline length; augmentation arguments need context). Upgrading to RSS `<description>` or full-body fetch is on the roadmap.
- **Heuristic vs LLM disagree ~32%.** Shadow flags a handful for review; the rest are edge cases where both readings are defensible.
- **No paywall handling.** Clicks on WSJ / FT / Bloomberg / Atlantic / Stratechery hit paywalls. Links preserved as-is.
- **English-language only.** Russian-language AI+labor coverage is sparse on this topic.

## Run locally

```bash
# Fetch fresh data
python3 fetch_gnews.py

# Classify with Claude (requires ANTHROPIC_API_KEY in .env)
python3 classify_llm.py

# Serve (fetch() requires HTTP, not file://)
python3 -m http.server 8765
# open http://localhost:8765/
```

## Stack

Pure static site: one HTML file with inline CSS/JS, one JSON file. No framework, no build step, no dependencies.

## Source

Spec: [../discourse-feed-spec.md](../discourse-feed-spec.md) (in the parent X Engineer research project).
