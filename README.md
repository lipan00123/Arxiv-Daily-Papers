# arXiv Daily Pipeline

This project runs a daily arXiv workflow:

1. Fetch candidates by your research areas.
2. Rank in two stages.
3. Download final PDFs.
4. Generate digest/brief outputs.
5. Track history and deduplicate recent downloads.

## Concepts

### What is an `area`?

An area is a research sub-topic, such as `Graph/Structure + LLMs` or `Agent Safety`.
The pipeline searches per-area first, then merges results for final ranking.

### What are keywords?

- `keywords_for_arxiv`: used for retrieval (arXiv API query construction)
- `keywords_for_llm`: used for LLM scoring/summarization preference

Each area can also define `arxiv_query` for precise boolean search.

### What is `category`?

arXiv subject classes (for example, `cs.LG`, `cs.CL`).
Configured via `categories_core`, `categories_extended`, and `category_scope`.

### Profile vs config

- `arxiv_research_profile.json`: what to search (research intent)
- `arxiv_daily_config.json`: how to run (scale, filters, outputs)

### Naming note: `skills/` vs `.agents/skills`

- `skills/` in this repo stores Python code modules (pipeline implementation).
- `.agents/skills/` stores LLM prompt skills (`SKILL.md`) used as soft constraints.

So they are different layers: code execution vs LLM policy guidance.

## Workflow

Per run:

1. Load config and profile.
2. Choose active areas via `selected_areas` and `area_search_plan`.
3. Build retrieval from area profile fields (`keywords_for_arxiv`, `arxiv_query`) + category/date constraints.
4. Stage-1 keeps top candidates per area.
5. Stage-2 LLM scoring keeps top candidates per area.
6. Merge and rerank globally, keep `final_top`.
7. Download PDFs, append tracker, write output files.

## Where to set research interest

Main location: `arxiv_research_profile.json`

Key fields:

- `research_interest_summary`
- `core_objective`
- `research_interests[]` with:
- `area`
- `focus`
- `keywords_for_arxiv`
- `keywords_for_llm`
- `arxiv_query`

Areas actually used in a run are controlled in `arxiv_daily_config.json`:

- `selected_areas`
- `area_search_plan[].area`

Use the same area names in profile and config.

## Output and tracking

Set output folder in `arxiv_daily_config.json`:

```json
"output_root": "C:\\Users\\<you>\\paper-reading\\arxiv-download"
```

Each run writes date-based output folders containing:

- PDFs
- `digest_<run_stamp>.md`
- `personalized_brief_<run_stamp>.md`
- `commute_script.txt`
- `papers.json`
- `run_config_snapshot.json`

History tracker is separate (set by `tracker_root` and `tracker_file`).

## Key parameters

In `arxiv_daily_config.json`:

- `final_top`: final number of papers to download
- `stage1_fetch_max_per_area`: max fetched per area
- `stage1_top_per_area`: stage-1 keep count per area
- `stage2_per_area_llm_top`: stage-2 keep count per area
- `days`: lookback window for retrieval
- `dedup_lookback_days`: lookback window for dedup

LLM settings:

- `llm_enabled`
- `llm_model`
- `llm_api_base`
- `llm_skill_roots` (where skill `SKILL.md` files are discovered)
- env var `OPENAI_API_KEY`

PowerShell example:

```powershell
$env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY','User')
```

## Skill-based soft constraints

You can define skills to guide ranking behavior with soft preferences.

What this means:

- Skills do not hard-filter papers.
- Skills provide additional preference rules during LLM scoring/reranking.
- Final selection still depends on relevance + candidate quality + available pool.

How it works in this pipeline:

1. The pipeline discovers skill files from `llm_skill_roots`.
2. It selects one relevant skill (router behavior).
3. Skill requirements are injected into:
- per-area LLM scoring
- global reranking
- final full-paper summarization

Where to configure:

- In config: `llm_skill_roots` (for example, `.agents/skills`)
- In skill file: `<skill-folder>/SKILL.md` (frontmatter + body requirements)

Practical recommendation:

- Keep research intent in `arxiv_research_profile.json`.
- Keep method/style constraints in skills (for example, novelty preference, safety bias, practical-impact bias).
- Treat skills as policy hints, not deterministic rules.

Debug tip:

- Check `run_config_snapshot.json` fields:
- `llm_skill_mode`
- `llm_skill_discovered`
- `llm_skill_selected`
- `llm_skill_route_error`

## FAQ

### How many papers are downloaded each run?

Primary control is `final_top`.
Actual downloads can be lower if candidates are filtered by date/category/dedup or if download fails.

### Can I schedule it daily?

Yes.

- Windows: use Task Scheduler with `pipeline/arxiv-daily-custom/run_arxiv_daily.ps1`
- macOS: use `cron` or `launchd` with `python3 ... --config ...`

### How dedup works

Dedup uses recent `arxiv_id` values from tracker, within `dedup_lookback_days`.

Important: an ID is recorded only if the paper is selected in final set and PDF download succeeds.

### How to clear tracker

Default file:

- `arxiv-daily-tracker/download_history.jsonl`

PowerShell:

```powershell
Set-Content "C:\Users\pli77\.openclaw\workspace\arxiv_management\arxiv-daily-tracker\download_history.jsonl" ""
```

## macOS migration checklist

Update path fields in `arxiv_daily_config.json`:

- `profile_path`
- `output_root`
- `tracker_root`

Use Unix-style paths like `/Users/<you>/...`.

Set API key in `zsh`:

```bash
export OPENAI_API_KEY="<your_key>"
```

Run manually:

```bash
python3 pipeline/arxiv-daily-custom/arxiv_daily.py --config arxiv_daily_config.json
```

## File index

- Main script: `pipeline/arxiv-daily-custom/arxiv_daily.py`
- arXiv API module: `pipeline/arxiv-daily-custom/arxiv_api_utils.py`
- LLM module: `pipeline/arxiv-daily-custom/llm_utils.py`
- Output module: `pipeline/arxiv-daily-custom/output_utils.py`
- Tracker module: `pipeline/arxiv-daily-custom/tracker_utils.py`
- Windows run script: `pipeline/arxiv-daily-custom/run_arxiv_daily.ps1`
