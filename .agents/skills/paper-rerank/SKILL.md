---
name: paper-rerank
description: Use when reranking arXiv papers with an LLM. Applies quality and soft area-mix preferences for relevance, technical depth, novelty, safety, and practical impact.
---

# Paper Rerank Skill

## Purpose
This skill defines the quality bar for LLM reranking in the daily arXiv pipeline.
- LLM role scope: execute Stage-2 scoring/reranking and final metadata-based top-5 decision support; Stage-1 retrieval/filtering is handled by pipeline code.

## Tunable Config Knobs
- Pipeline sizing parameters are controlled in `arxiv_daily_config.json`.
- `stage1_top_per_area`: number of papers taken directly from each area's arXiv retrieval order for Stage-2 input.
- `stage2_per_area_llm_top`: number kept per area after area-level LLM scoring.
- `final_top`: final number of papers for deep read and summary.

## Reranking Requirements
- Always read and use the research interest profile as the primary personalization target.
- For stage-2 area-level scoring, use arXiv metadata as main evidence: title, abstract (`summary[:1200]`), categories, and publication context.
- For each area, LLM-score the provided area candidate set and keep top papers by score.
- Merge all per-area top-3 candidates, then perform one unified metadata LLM scoring pass and global rerank.
- Use the unified global scores/rerank to determine final top 5.
- Include candidate-level scores and short reasons in the daily brief.
- Prioritize papers that match the research interest and area-specific intent.
- Strongly prioritize methodology-first contributions, especially core ML/AI methodology.
- Prefer papers with clear algorithmic, modeling, optimization, inference, evaluation, or systems-method novelty.
- Prefer methodological and mechanistic depth over benchmark-only gains.
- Treat application papers as secondary unless they introduce broadly reusable methods or substantial methodological innovation.
- Reward work that improves reasoning, controllability, efficiency, safety, or scientific usefulness.
- Reward forward-looking novelty with likely 2-5 year impact.
- Deprioritize incremental papers with weak conceptual contribution.
- Deprioritize papers with weak grounding, vague claims, or limited technical signal.
- Deprioritize application-only work with limited methodological transfer to the broader ML/AI community.

## Soft Area-Mix Preferences For Final Top-N
- Treat area mix as soft guidance, not hard constraints.
- `selected_area_index` follows the area order in `area_search_plan`.
- Prefer final top-5 to approximately satisfy:
- area 1: at most 3, and preferably at least 1
- area 2: at most 1
- area 3: at most 2, and preferably at least 1
- area 4: at most 1
- area 5: at most 1
- area 2 + area 4: preferably at least 1 in total
- If quality/relevance strongly conflicts with area mix, prioritize quality/relevance and explain briefly in reasons.

## Final Top-N Deep Read
- For final selected top N papers, read full PDF text and produce paper-level summaries.
- Highlight parts most related to the research interest profile.
- Provide a relevance score and concise rationale for each selected paper.
- Output should support generating one unified daily brief in the same day folder as downloaded papers.
- The brief should contain both: (1) per-area top-3 final scoring table, (2) final top-N deep-read summaries.

## Output Constraints
- Select only from provided paper IDs.
- Return strict JSON only.
- Do not add explanation text outside the JSON response.
