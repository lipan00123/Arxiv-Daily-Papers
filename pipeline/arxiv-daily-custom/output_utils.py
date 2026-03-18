import textwrap
from pathlib import Path


def md_escape(s: str):
    return (s or "").replace("|", "\\|").strip()


def generate_personalized_brief(papers, area_top3_rows, out_md: Path, interest: str):
    lines = ["# Personalized Paper Brief", ""]
    lines.append("## Research Interest")
    lines.append(interest or "(empty)")
    lines.append("")
    lines.append("## Per-Area Top-3 Scoring")
    lines.append("")
    lines.append("| # | Area | arXiv ID | Title | Score | Selected | Reason |")
    lines.append("|---|---|---|---|---:|---|---|")
    for i, c in enumerate(area_top3_rows or [], start=1):
        lines.append(
            "| "
            + f"{i} | {md_escape(c.get('selected_area', ''))} | {md_escape(c.get('arxiv_id', ''))} | {md_escape(c.get('title', ''))} | "
            + f"{float(c.get('score', 0) or 0):.1f} | {'yes' if c.get('selected') else 'no'} | "
            + f"{md_escape(c.get('reason', ''))} |"
        )

    lines.append("")
    lines.append("## Final Selection")
    lines.append("")
    lines.append("| # | arXiv ID | Title | Stage2 Score | Relevance Score |")
    lines.append("|---|---|---|---:|---:|")

    for i, p in enumerate(papers, start=1):
        lines.append(
            "| "
            + f"{i} | {md_escape(p.get('arxiv_id', ''))} | {md_escape(p.get('title', ''))} | "
            + f"{p.get('llm_stage2_score', 0):.1f} | {p.get('llm_relevance_score', 0):.1f} |"
        )

    lines.append("")
    for i, p in enumerate(papers, start=1):
        lines.append(f"## {i}. {p.get('title', '')}")
        lines.append(f"- arXiv: {p.get('entry_url', '')}")
        lines.append(f"- Area: {p.get('selected_area', '')}")
        lines.append(f"- Stage2 score: {p.get('llm_stage2_score', 0):.1f}")
        lines.append(f"- Relevance score: {p.get('llm_relevance_score', 0):.1f}")
        if p.get("llm_stage2_reason"):
            lines.append(f"- Why selected: {p.get('llm_stage2_reason')}")
        lines.append("- Summary:")
        lines.append(textwrap.fill(p.get("llm_summary", "") or "", width=100))
        highlights = p.get("llm_related_highlights", []) or []
        if highlights:
            lines.append("- Related highlights:")
            for h in highlights:
                lines.append(f"  - {h}")
        lines.append("")

    out_md.write_text("\n".join(lines), encoding="utf-8")


def generate_digest(papers, out_md: Path):
    lines = ["# Daily arXiv Digest", ""]
    for i, p in enumerate(papers, start=1):
        score_val = float(p.get("llm_stage2_score", p.get("score", 0)) or 0)
        lines.append(f"## {i}. {p['title']}")
        if p.get("selected_area"):
            lines.append(f"- Area: {p['selected_area']}")
        lines.append(f"- Score: {score_val:.2f}")
        lines.append(f"- Published: {p['published']}")
        lines.append(f"- Authors: {', '.join(p['authors'][:8])}")
        lines.append(f"- Categories: {', '.join(p['categories'])}")
        lines.append(f"- arXiv: {p['entry_url']}")
        lines.append(f"- PDF: {p['pdf_url']}")
        if p["hits"]:
            lines.append(f"- Keyword hits: {', '.join(p['hits'])}")
        lines.append("- Summary:")
        lines.append(textwrap.fill(p["summary"], width=100))
        lines.append("")
    out_md.write_text("\n".join(lines), encoding="utf-8")


def generate_commute_script(papers, out_txt: Path):
    lines = []
    lines.append(f"Today we have {len(papers)} arXiv papers worth your attention.")
    lines.append("")
    for i, p in enumerate(papers, start=1):
        lines.append(f"Paper {i}: {p['title']}.")
        if p["hits"]:
            lines.append(f"Why relevant: it matches {', '.join(p['hits'])}.")
        lines.append("Key idea:")
        lines.append(textwrap.fill(p["summary"], width=95))
        lines.append("")
    lines.append("That is your daily arXiv briefing.")
    out_txt.write_text("\n".join(lines), encoding="utf-8")
