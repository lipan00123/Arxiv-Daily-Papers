---
name: arxiv-daily-custom
description: Daily arXiv fetcher with area quotas, de-dup across recent days, PDF download, digest generation, and commute script output.
---

# arXiv Daily Custom

This skill runs a local Python script to:
- query recent arXiv papers by area plan
- stage-1 collect recent candidates (10+) without reranking
- download all candidate PDFs first
- stage-2 route and apply LLM rerank skill from `.agents/skills/*/SKILL.md`
- rerank with each candidate PDF first-1000-token evidence
- full-read top-N papers and produce profile-aware summaries/highlights
- de-duplicate against recent download history
- download PDFs
- generate `digest.md` and `commute_script.txt`
- generate `personalized_brief.md` in the day folder

## Files

- `arxiv_daily.py`: main script
- `C:\Users\pli77\.openclaw\workspace\arxiv_management\arxiv_keywords.txt`: keyword config
- LLM skills root: `C:\Users\pli77\.openclaw\workspace\arxiv_management\.agents\skills`
- Output root: `C:\Users\pli77\.openclaw\workspace\arxiv_management\arxiv-daily`
- Tracker: `C:\Users\pli77\.openclaw\workspace\arxiv_management\arxiv-daily-tracker\download_history.jsonl`

## Run

```powershell
python C:\Users\pli77\.openclaw\workspace\arxiv_management\pipeline\arxiv-daily-custom\arxiv_daily.py --config C:\Users\pli77\.openclaw\workspace\arxiv_management\arxiv_daily_config.json
```

## Notes

- LLM reranking now uses router mode only.
- The script discovers available skills and selects one skill before reranking papers.
- `--no-llm` forces heuristic fallback for testing.

## Keyword file format

One keyword per line. Lines starting with `#` are ignored.

Example:

```text
agentic workflow
llm safety
retrieval augmented generation
multimodal reasoning
```
