import json
import ssl
import urllib.request


def call_openai_chat(model, messages, api_key, api_base):
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    req = urllib.request.Request(
        url=api_base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(req, context=context, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def choose_skill_with_llm(task, skills, model, api_base, api_key):
    if not skills:
        return "", ""
    if len(skills) == 1:
        return skills[0]["name"], ""

    brief = []
    for s in skills:
        brief.append(
            {
                "name": s["name"],
                "description": s["description"],
                "path": s["path"],
            }
        )

    system = (
        "You select exactly one skill for a task. "
        "Return strict JSON only with key selected_skill as a skill name string."
    )
    user = {
        "task": task,
        "skills": brief,
        "output": {"selected_skill": "skill-name"},
        "constraints": [
            "Select one name from the provided skill list",
            "No extra keys",
            "No explanation text",
        ],
    }

    content = call_openai_chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        api_key=api_key,
        api_base=api_base,
    )
    data = json.loads(content)
    picked = str(data.get("selected_skill", "")).strip()
    return picked, ""


def llm_score_candidates_batch(
    candidates,
    interest,
    model,
    api_base,
    api_key,
    skill_requirements="",
    area_order_map=None,
    task_label="metadata batch scoring",
):
    compact = []
    for c in candidates or []:
        compact.append(
            {
                "arxiv_id": c.get("arxiv_id", ""),
                "title": c.get("title", ""),
                "abstract": (c.get("summary") or "")[:1200],
                "categories": c.get("categories", []),
                "published": c.get("published", ""),
                "selected_area": c.get("selected_area", ""),
                "selected_area_index": (area_order_map or {}).get((c.get("selected_area") or "").strip()),
            }
        )

    system = (
        "Score candidate papers for selection. "
        "Use research-interest relevance and provided metadata as primary signal. "
        "Return strict JSON only with key scored as an array of objects: {arxiv_id, score, reason}."
    )
    if skill_requirements:
        system += "\n\nSkill requirements to apply during scoring:\n" + skill_requirements[:12000]

    user = {
        "task": task_label,
        "research_interest": interest,
        "candidates": compact,
        "output": {
            "scored": [
                {"arxiv_id": "id", "score": 95, "reason": "short reason"}
            ]
        },
        "constraints": [
            "Use only provided candidate IDs",
            "Score should be 0-100",
            "No extra keys",
            "No explanation text outside JSON",
        ],
    }

    content = call_openai_chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        api_key=api_key,
        api_base=api_base,
    )
    data = json.loads(content)
    scored = data.get("scored", [])
    if not isinstance(scored, list):
        scored = []
    out = []
    for item in scored:
        pid = str(item.get("arxiv_id", "")).strip()
        if not pid:
            continue
        try:
            score = float(item.get("score", 0) or 0)
        except Exception:
            score = 0.0
        reason = str(item.get("reason", "") or "").strip()
        out.append({"arxiv_id": pid, "score": score, "reason": reason})
    return out


def llm_rerank_topk_for_final(
    top_candidates,
    interest,
    model,
    api_base,
    api_key,
    skill_requirements="",
):
    compact = []
    for e in top_candidates:
        compact.append(
            {
                "arxiv_id": e.get("arxiv_id", ""),
                "title": e.get("title", ""),
                "abstract": (e.get("summary") or "")[:1200],
                "categories": e.get("categories", []),
                "published": e.get("published", ""),
                "selected_area": e.get("selected_area", ""),
                "selected_area_index": e.get("selected_area_index"),
                "stage2_score": float(e.get("llm_stage2_score", 0) or 0),
                "stage2_reason": e.get("llm_stage2_reason", ""),
            }
        )

    system = (
        "Rerank a top-K candidate set for final deep-reading selection. "
        "Use research-interest relevance and candidate metadata as primary signal. "
        "Respect skill constraints as soft preferences when possible. "
        "Return strict JSON only with key ranked_ids as an array of arXiv IDs in descending priority order."
    )
    if skill_requirements:
        system += "\n\nSkill requirements to apply during final selection:\n" + skill_requirements[:12000]

    user = {
        "task": "Final rerank of top-K metadata candidates",
        "research_interest": interest,
        "candidates": compact,
        "output": {"ranked_ids": ["id1", "id2"]},
        "constraints": [
            "Select IDs only from provided candidates",
            "Return each candidate at most once",
            "No extra keys",
            "No explanation text outside JSON",
        ],
    }

    content = call_openai_chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        api_key=api_key,
        api_base=api_base,
    )
    data = json.loads(content)
    selected = data.get("ranked_ids", [])
    if not isinstance(selected, list):
        selected = []
    out = []
    seen = set()
    for x in selected:
        pid = str(x or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def llm_summarize_full_paper(paper, full_text, interest, model, api_base, api_key, skill_requirements=""):
    text = (full_text or "").strip()
    if not text:
        text = (paper.get("summary") or "").strip()

    system = (
        "Summarize a research paper for a personalized daily brief. "
        "Return strict JSON with keys relevance_score, summary, related_highlights."
    )
    if skill_requirements:
        system += "\n\nSkill requirements to apply:\n" + skill_requirements[:12000]

    user = {
        "paper": {
            "arxiv_id": paper.get("arxiv_id", ""),
            "title": paper.get("title", ""),
            "selected_area": paper.get("selected_area", ""),
        },
        "research_interest": interest,
        "full_text": text,
        "output": {
            "relevance_score": 90,
            "summary": "short paragraph",
            "related_highlights": ["point 1", "point 2"],
        },
        "constraints": [
            "relevance_score range is 0-100",
            "related_highlights must focus on connections to research interest",
            "No extra keys",
            "No explanation text outside JSON",
        ],
    }

    content = call_openai_chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        api_key=api_key,
        api_base=api_base,
    )
    data = json.loads(content)
    try:
        score = float(data.get("relevance_score", 0))
    except Exception:
        score = 0.0
    summary = str(data.get("summary", "")).strip()
    highlights = data.get("related_highlights", [])
    if not isinstance(highlights, list):
        highlights = []
    highlights = [str(x).strip() for x in highlights if str(x).strip()]
    return {
        "relevance_score": score,
        "summary": summary,
        "related_highlights": highlights,
    }
