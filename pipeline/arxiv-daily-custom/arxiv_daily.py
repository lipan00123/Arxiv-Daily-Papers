#!/usr/bin/env python3
import argparse
import datetime as dt
import importlib
import importlib.util
import json
import os
import re
import ssl
import urllib.request
from pathlib import Path

PdfReader = None
try:
    _pypdf = importlib.import_module("pypdf")
    PdfReader = getattr(_pypdf, "PdfReader", None)
except Exception:
    PdfReader = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "arxiv_daily_config.json"
DEFAULT_PROFILE = PROJECT_ROOT / "arxiv_research_profile.json"
DEFAULT_LLM_SKILL_ROOT = PROJECT_ROOT / ".agents" / "skills"


def _load_local_module(module_name: str, file_name: str):
    module_path = Path(__file__).resolve().parent / file_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load local module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_output_utils = _load_local_module("arxiv_output_utils", "output_utils.py")
generate_commute_script = _output_utils.generate_commute_script
generate_digest = _output_utils.generate_digest
generate_personalized_brief = _output_utils.generate_personalized_brief

_tracker_utils = _load_local_module("arxiv_tracker_utils", "tracker_utils.py")
append_tracker_rows = _tracker_utils.append_tracker_rows
load_tracker_history = _tracker_utils.load_tracker_history
recent_download_ids = _tracker_utils.recent_download_ids
resolve_tracker_paths = _tracker_utils.resolve_tracker_paths
tracker_is_empty = _tracker_utils.tracker_is_empty

_arxiv_api_utils = _load_local_module("arxiv_api_utils", "arxiv_api_utils.py")
build_search_query = _arxiv_api_utils.build_search_query
build_submitted_date_clause = _arxiv_api_utils.build_submitted_date_clause
fetch_arxiv = _arxiv_api_utils.fetch_arxiv
fetch_entries_for_scope = _arxiv_api_utils.fetch_entries_for_scope
filter_by_exact_date = _arxiv_api_utils.filter_by_exact_date
filter_recent = _arxiv_api_utils.filter_recent
parse_date = _arxiv_api_utils.parse_date
resolve_arxiv_sort_config = _arxiv_api_utils.resolve_arxiv_sort_config
resolve_categories = _arxiv_api_utils.resolve_categories

_llm_utils = _load_local_module("arxiv_llm_utils", "llm_utils.py")
call_openai_chat = _llm_utils.call_openai_chat
choose_skill_with_llm = _llm_utils.choose_skill_with_llm
llm_rerank_topk_for_final = _llm_utils.llm_rerank_topk_for_final
llm_score_candidates_batch = _llm_utils.llm_score_candidates_batch
llm_summarize_full_paper = _llm_utils.llm_summarize_full_paper


def load_keywords(path: Path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def parse_inline_keywords(raw: str):
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def load_json(path: Path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_text(path: Path):
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8-sig").strip()


def strip_yaml_frontmatter(text: str):
    s = (text or "").lstrip()
    if not s.startswith("---"):
        return text

    lines = s.splitlines()
    if not lines:
        return text

    # SKILL.md frontmatter is delimited by the first two '---' lines.
    if lines[0].strip() != "---":
        return text

    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :]).strip()
    return text


def _resolve_project_path(raw_path: str):
    if not raw_path:
        return None
    p = Path(str(raw_path).strip())
    if not str(p):
        return None
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def parse_skill_markdown(path: Path):
    raw = load_text(path)
    if not raw:
        return None

    frontmatter = {}
    lines = raw.splitlines()
    body = raw
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                fm_lines = lines[1:i]
                body = "\n".join(lines[i + 1 :]).strip()
                for fm_line in fm_lines:
                    if ":" not in fm_line:
                        continue
                    k, v = fm_line.split(":", 1)
                    frontmatter[k.strip().lower()] = v.strip().strip("\"'")
                break

    if not body:
        body = strip_yaml_frontmatter(raw)
    if not body:
        return None

    name = (frontmatter.get("name") or path.parent.name).strip()
    description = (frontmatter.get("description") or "").strip()

    return {
        "name": name,
        "description": description,
        "path": str(path),
        "body": body,
    }


def discover_llm_skills(cfg):
    roots = cfg.get("llm_skill_roots") or []
    if isinstance(roots, str):
        roots = [roots]
    if not roots and DEFAULT_LLM_SKILL_ROOT.exists():
        roots = [str(DEFAULT_LLM_SKILL_ROOT)]

    out = []
    seen = set()
    for root_item in _dedupe_keep_order([str(x) for x in roots]):
        root = _resolve_project_path(root_item)
        if not root or not root.exists() or not root.is_dir():
            continue
        for skill_file in sorted(root.glob("*/SKILL.md")):
            skill = parse_skill_markdown(skill_file)
            if not skill:
                continue
            key = skill["name"].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(skill)
    return out


def resolve_llm_skill_requirements(cfg, llm_enabled, api_key, llm_model, llm_api_base):
    discovered = discover_llm_skills(cfg)
    discovered_names = [s["name"] for s in discovered]
    route_error = ""
    selected_name = ""

    if llm_enabled and api_key and discovered:
        try:
            selected_name, _ = choose_skill_with_llm(
                task="Rerank arXiv papers by relevance, quality, novelty, and practical impact.",
                skills=discovered,
                model=llm_model,
                api_base=llm_api_base,
                api_key=api_key,
            )
        except Exception as ex:
            route_error = str(ex)

    selected = None
    by_name = {s["name"].lower(): s for s in discovered}
    if selected_name and selected_name.lower() in by_name:
        selected = by_name[selected_name.lower()]
    elif "paper-rerank" in by_name:
        selected = by_name["paper-rerank"]
    elif discovered:
        selected = discovered[0]

    requirements = selected["body"] if selected else ""
    loaded_paths = [selected["path"]] if selected else []
    selected_final = selected["name"] if selected else ""

    return {
        "mode": "router",
        "requirements": requirements,
        "loaded_paths": loaded_paths,
        "discovered_skills": discovered_names,
        "selected_skill": selected_final,
        "route_error": route_error,
    }


def _dedupe_keep_order(items):
    seen = set()
    out = []
    for x in items:
        k = x.strip()
        if not k:
            continue
        low = k.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(k)
    return out


def extract_profile_context(profile, selected_areas=None):
    if not profile:
        return {
            "interest_text": "",
            "keywords_for_arxiv": [],
            "keywords_for_llm": [],
            "priority_preference": [],
            "deprioritize": [],
            "selected_areas": [],
        }

    interests = profile.get("research_interests") or []
    wanted = set(a.strip().lower() for a in (selected_areas or []) if a.strip())

    picked = []
    for item in interests:
        area = (item.get("area") or "").strip()
        if not wanted or area.lower() in wanted:
            picked.append(item)

    if not picked:
        picked = interests

    kw_arxiv = []
    kw_llm = []
    arxiv_queries = []
    area_lines = []
    for item in picked:
        area = item.get("area", "")
        focus = item.get("focus", "")
        if area or focus:
            area_lines.append(f"- {area}: {focus}".strip())
        kw_arxiv.extend(item.get("keywords_for_arxiv") or [])
        kw_llm.extend(item.get("keywords_for_llm") or [])
        q = (item.get("arxiv_query") or "").strip()
        if q:
            arxiv_queries.append(q)

    notes = profile.get("notes_for_search") or {}
    pref = notes.get("priority_preference") or []
    depr = notes.get("deprioritize") or []

    summary = (profile.get("research_interest_summary") or "").strip()
    objective = (profile.get("core_objective") or "").strip()
    interest_parts = []
    if summary:
        interest_parts.append(summary)
    if objective:
        interest_parts.append("Core objective: " + objective)
    if area_lines:
        interest_parts.append("Focus areas:\n" + "\n".join(area_lines))
    if pref:
        interest_parts.append("Prioritize:\n" + "\n".join([f"- {x}" for x in pref]))
    if depr:
        interest_parts.append("Deprioritize:\n" + "\n".join([f"- {x}" for x in depr]))

    return {
        "interest_text": "\n\n".join(interest_parts).strip(),
        "keywords_for_arxiv": _dedupe_keep_order(kw_arxiv),
        "keywords_for_llm": _dedupe_keep_order(kw_llm),
        "arxiv_queries": _dedupe_keep_order(arxiv_queries),
        "priority_preference": pref,
        "deprioritize": depr,
        "selected_areas": [x.get("area", "") for x in picked],
    }


def safe_filename(s: str):
    return re.sub(r"[^a-zA-Z0-9._-]", "_", s)


def extract_pdf_text(pdf_path: Path):
    if not pdf_path or not pdf_path.exists() or PdfReader is None:
        return ""
    try:
        reader = PdfReader(str(pdf_path))
        pages = []
        for page in reader.pages:
            pages.append((page.extract_text() or "").strip())
        return "\n\n".join([p for p in pages if p])
    except Exception:
        return ""


def make_pdf_filename(paper, area_order_map):
    area_name = (paper.get("selected_area") or "").strip()
    area_idx = area_order_map.get(area_name)
    title = (paper.get("title") or paper.get("arxiv_id") or "paper").strip()

    if area_idx is not None:
        base = f"area {area_idx} - {title}"
    else:
        base = title

    # Keep filename reasonably short for Windows path limits.
    safe_base = safe_filename(base)[:160].rstrip("._-") or "paper"
    return f"{safe_base}.pdf"


def dedupe_candidates_by_arxiv_id(candidates):
    out = []
    seen = set()
    for c in candidates or []:
        cid = str(c.get("arxiv_id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(c)
    return out


def download_pdf(url: str, out_path: Path):
    context = ssl.create_default_context()
    with urllib.request.urlopen(url, context=context, timeout=60) as resp:
        out_path.write_bytes(resp.read())


def main():
    p = argparse.ArgumentParser(description="Daily arXiv fetcher with API-recall + optional LLM rerank")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON config file path")
    p.add_argument("--profile", default=str(DEFAULT_PROFILE), help="Research profile JSON path")
    p.add_argument("--keywords-file", default=str(PROJECT_ROOT / "arxiv_keywords.txt"))
    p.add_argument("--interest-file", default=str(PROJECT_ROOT / "arxiv_interest.txt"))
    p.add_argument("--keywords", default="", help="Comma-separated keywords; overrides config")
    p.add_argument("--interest", default="", help="Research interest text; overrides config")
    p.add_argument("--categories", default="", help="Comma-separated categories; overrides config")
    p.add_argument(
        "--category-scope",
        default="",
        help="Category set to use: core | extended | all (uses categories_core/categories_extended)",
    )
    p.add_argument("--top", type=int, default=None)
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--date", default="", help="Exact date filter: YYYY-MM-DD (optional)")
    p.add_argument("--max-results", type=int, default=None)
    p.add_argument("--output-root", default="")
    p.add_argument("--llm-model", default="")
    p.add_argument("--llm-api-base", default="")
    p.add_argument("--no-llm", action="store_true", help="Disable final LLM rerank and keep recent-order fallback")
    p.add_argument("--download", action="store_true", default=True)
    args = p.parse_args()

    cfg = load_json(Path(args.config))
    profile_path = Path(cfg.get("profile_path") or args.profile)
    profile = load_json(profile_path)

    selected_areas = cfg.get("selected_areas") or []
    profile_ctx = extract_profile_context(profile, selected_areas=selected_areas)

    # Default behavior: content intent (keywords/interest) comes from profile.
    use_profile_for_content = bool(cfg.get("use_profile_for_content", True))

    keywords_file = Path(args.keywords_file)
    interest_file = Path(args.interest_file)
    output_root = Path(args.output_root or cfg.get("output_root") or str(PROJECT_ROOT / "arxiv-daily"))
    tracker_root, tracker_file = resolve_tracker_paths(cfg, output_root)

    categories, category_source = resolve_categories(cfg, args.categories, args.category_scope)

    cfg_keywords = cfg.get("keywords") or []
    if isinstance(cfg_keywords, str):
        cfg_keywords = parse_inline_keywords(cfg_keywords)

    profile_keywords = profile_ctx.get("keywords_for_arxiv") or []
    if args.keywords:
        keywords = parse_inline_keywords(args.keywords)
    elif use_profile_for_content:
        keywords = profile_keywords if profile_keywords else load_keywords(keywords_file)
    else:
        keywords = cfg_keywords if cfg_keywords else (profile_keywords if profile_keywords else load_keywords(keywords_file))

    if args.interest:
        interest = args.interest
    elif use_profile_for_content:
        interest = profile_ctx.get("interest_text") or load_text(interest_file)
    else:
        interest = cfg.get("research_interest") or profile_ctx.get("interest_text") or load_text(interest_file)

    llm_keywords = _dedupe_keep_order((cfg.get("llm_keywords") or []) + (profile_ctx.get("keywords_for_llm") or []) + keywords)

    days = args.days if args.days is not None else int(cfg.get("days", 7))
    max_results = args.max_results if args.max_results is not None else int(cfg.get("max_results", 120))
    top_n = args.top if args.top is not None else int(cfg.get("top", 10))
    final_top_n = int(cfg.get("final_top", 5))
    stage1_top_per_area = max(1, int(cfg.get("stage1_top_per_area", 10)))
    stage1_fetch_max_per_area = max(stage1_top_per_area, int(cfg.get("stage1_fetch_max_per_area", 100)))
    stage2_per_area_llm_top = max(1, int(cfg.get("stage2_per_area_llm_top", 3)))
    retain_only_final_files = bool(cfg.get("retain_only_final_files", True))
    dedup_lookback_days = int(cfg.get("dedup_lookback_days", 7))
    cleanup_on_dedup_reset = bool(cfg.get("cleanup_on_dedup_reset", True))
    write_latest_brief_alias = bool(cfg.get("write_latest_brief_alias", False))
    write_latest_digest_alias = bool(cfg.get("write_latest_digest_alias", False))

    now_utc = dt.datetime.now(dt.timezone.utc)
    arxiv_date_clause = build_submitted_date_clause(days, args.date, now_utc)
    history_rows = load_tracker_history(tracker_file)
    dedup_tracker_empty = tracker_is_empty(tracker_file)
    downloaded_recent_ids = recent_download_ids(history_rows, now_utc, dedup_lookback_days)

    llm_enabled = not args.no_llm and bool(cfg.get("llm_enabled", True))
    llm_model = args.llm_model or cfg.get("llm_model", "gpt-4o-mini")
    llm_api_base = args.llm_api_base or cfg.get("llm_api_base", "https://api.openai.com/v1")
    api_key = cfg.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
    llm_runtime_active = bool(llm_enabled and api_key)
    llm_disabled_reason = ""
    if llm_enabled and not api_key:
        llm_disabled_reason = "LLM enabled but OPENAI_API_KEY is missing; metadata scoring and summaries fell back to non-LLM path."
    # Skill selection is deferred to final rerank stage only.
    llm_skill_requirements = ""
    llm_skill_files_loaded = []
    llm_skill_discovered = []
    llm_skill_selected = ""
    llm_skill_mode = "router"
    llm_skill_route_error = ""
    arxiv_ranking_mode, arxiv_sort_by, arxiv_sort_order = resolve_arxiv_sort_config(cfg)

    area_plan = cfg.get("area_search_plan") or []
    area_plan_results = []
    area_recent_map = {}
    area_top3_rows = []
    fetched_count = 0
    recent_count = 0
    entries = []
    recent = []

    if isinstance(area_plan, list) and area_plan:
        top = []
        mode = "recent"
        query = "[area_search_plan]"
        query_strategy = "area_plan"
        categories = []
        category_source = "area_plan"

        for item in area_plan:
            area_name = (item.get("area") or "").strip()
            if not area_name:
                continue
            area_scope = (item.get("category_scope") or "core").strip().lower()
            # Stage-1: fetch a larger relevance-ranked pool, then keep top-N recent items.
            area_stage1_top = stage1_top_per_area
            area_fetch_max = max(area_stage1_top, int(item.get("max_results") or stage1_fetch_max_per_area))

            area_ctx = extract_profile_context(profile, selected_areas=[area_name])
            area_keywords = area_ctx.get("keywords_for_arxiv") or keywords

            fetch_info = fetch_entries_for_scope(
                cfg,
                area_keywords,
                area_ctx.get("arxiv_queries") or [],
                area_fetch_max,
                area_scope,
                date_clause=arxiv_date_clause,
            )
            entries = fetch_info["entries"]
            recent = filter_by_exact_date(entries, args.date) if args.date else filter_recent(entries, days)
            recent = [e for e in recent if e.get("arxiv_id") not in downloaded_recent_ids]
            fetched_count += len(entries)
            recent_count += len(recent)

            area_mode = "recent"
            area_items = []

            # Stage-1: keep arXiv top-N in API order for Stage-2.
            for e in recent[:area_stage1_top]:
                x = dict(e)
                x["selected_area"] = area_name
                x["selected_category_scope"] = area_scope
                area_items.append(x)

            area_recent_map[area_name] = area_items

            area_plan_results.append(
                {
                    "area": area_name,
                    "category_scope": area_scope,
                    "stage1_target_top": area_stage1_top,
                    "max_results": area_fetch_max,
                    "fetched": len(entries),
                    "recent": len(recent),
                    "selected": len(area_items),
                    "query": fetch_info["query"],
                    "query_strategy": fetch_info["query_strategy"],
                    "category_source": fetch_info["category_source"],
                    "categories": fetch_info["categories"],
                    "rerank_mode": area_mode,
                }
            )

            if area_mode == "recent":
                mode = "recent"

        top_n = sum(len(v) for v in area_recent_map.values())
    else:
        query, query_strategy = build_search_query(
            keywords,
            categories,
            profile_arxiv_queries=profile_ctx.get("arxiv_queries") or [],
        )

        fetch_info = fetch_entries_for_scope(
            cfg,
            keywords,
            profile_ctx.get("arxiv_queries") or [],
            max_results,
            (args.category_scope or cfg.get("category_scope") or "core"),
            date_clause=arxiv_date_clause,
        )
        entries = fetch_info["entries"]
        query = fetch_info["query"]
        query_strategy = fetch_info["query_strategy"]
        categories = fetch_info["categories"]
        category_source = fetch_info["category_source"]

        recent = filter_by_exact_date(entries, args.date) if args.date else filter_recent(entries, days)
        recent = [e for e in recent if e.get("arxiv_id") not in downloaded_recent_ids]
        fetched_count = len(entries)
        recent_count = len(recent)

        mode = "recent"
        top = []

        # Stage-1 without area plan: keep newest top_n only.
        for e in recent[:top_n]:
            item = dict(e)
            item["score"] = 0.0
            item["hits"] = ["recent"]
            top.append(item)

    stage1_candidates = dedupe_candidates_by_arxiv_id(top)
    pre_final_count = len(stage1_candidates)
    final_mode = "none"
    final_llm_ids = []
    final_llm_error = llm_disabled_reason

    run_local = dt.datetime.now()
    run_stamp = run_local.strftime("%Y%m%d_%H%M%S_%f")
    day_dir = output_root / run_local.strftime("%Y-%m-%d")
    pdf_dir = day_dir / "pdf"
    day_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    pdf_cleanup_errors = []
    should_cleanup_files = bool(retain_only_final_files and (not cleanup_on_dedup_reset or dedup_tracker_empty))
    pdf_pre_existing = list(pdf_dir.glob("*.pdf"))
    pdf_pre_count = len(pdf_pre_existing)
    if should_cleanup_files:
        for old_pdf in pdf_pre_existing:
            try:
                old_pdf.unlink()
            except Exception as ex:
                pdf_cleanup_errors.append(f"{old_pdf}: {ex}")

    area_order_map = {}
    if isinstance(area_plan, list) and area_plan:
        for idx, item in enumerate(area_plan, start=1):
            area_name = (item.get("area") or "").strip()
            if area_name and area_name not in area_order_map:
                area_order_map[area_name] = idx

    # Stage-2: area-wise batch scoring to select top-3 per area.
    candidates = []
    final_reranked_ids = []
    stage2_top_k = 0
    stage2_top_candidates = []

    if llm_runtime_active:
        try:
            llm_skill_ctx = resolve_llm_skill_requirements(cfg, llm_enabled, api_key, llm_model, llm_api_base)
            llm_skill_requirements = llm_skill_ctx["requirements"]
            llm_skill_files_loaded = llm_skill_ctx["loaded_paths"]
            llm_skill_discovered = llm_skill_ctx["discovered_skills"]
            llm_skill_selected = llm_skill_ctx["selected_skill"]
            llm_skill_mode = llm_skill_ctx["mode"]
            llm_skill_route_error = llm_skill_ctx["route_error"]
        except Exception as ex:
            final_llm_error = str(ex)

    if isinstance(area_plan, list) and area_plan:
        for item in area_plan:
            area_name = (item.get("area") or "").strip()
            if not area_name:
                continue
            area_items = list(area_recent_map.get(area_name, []))
            if not area_items:
                continue

            scored_rows = []
            if llm_runtime_active:
                try:
                    scored_rows = llm_score_candidates_batch(
                        candidates=area_items,
                        interest=interest,
                        model=llm_model,
                        api_base=llm_api_base,
                        api_key=api_key,
                        skill_requirements=llm_skill_requirements,
                        area_order_map=area_order_map,
                        task_label=f"Per-area scoring for {area_name}",
                    )
                except Exception:
                    scored_rows = []

            scored_map = {r.get("arxiv_id", ""): r for r in scored_rows if r.get("arxiv_id")}
            area_ranked = []
            for p in area_items:
                pid = p.get("arxiv_id", "")
                r = scored_map.get(pid, {})
                try:
                    score = float(r.get("score", 0) or 0)
                except Exception:
                    score = 0.0
                area_ranked.append(
                    {
                        "paper": p,
                        "score": score,
                        "reason": str(r.get("reason", "") or ""),
                    }
                )

            area_ranked = sorted(area_ranked, key=lambda x: x.get("score", 0), reverse=True)
            top3 = area_ranked[:stage2_per_area_llm_top]
            for r_item in area_plan_results:
                if r_item.get("area") == area_name:
                    r_item["selected"] = len(top3)
                    r_item["selected_top3_ids"] = [
                        (x.get("paper") or {}).get("arxiv_id", "") for x in top3 if (x.get("paper") or {}).get("arxiv_id")
                    ]
                    break
            for idx, obj in enumerate(top3, start=1):
                p = dict(obj["paper"])
                p["llm_stage2_score"] = float(obj.get("score", 0) or 0)
                p["llm_stage2_reason"] = str(obj.get("reason", "") or "")
                p["selected_area_index"] = area_order_map.get((p.get("selected_area") or "").strip())
                p["area_top3_rank"] = idx
                p["local_pdf"] = ""
                candidates.append(p)
                area_top3_rows.append(
                    {
                        "arxiv_id": p.get("arxiv_id", ""),
                        "selected_area": p.get("selected_area", ""),
                        "title": p.get("title", ""),
                        "score": p.get("llm_stage2_score", 0),
                        "reason": p.get("llm_stage2_reason", ""),
                        "selected": False,
                    }
                )
    else:
        # Fallback when area plan is not enabled.
        candidates = [dict(p) for p in stage1_candidates]
        for p in candidates:
            p["local_pdf"] = ""

    candidates = dedupe_candidates_by_arxiv_id(candidates)
    pre_final_count = len(candidates)

    # Unified global metadata scoring on merged per-area top-3 pool.
    global_scored_rows = []
    if llm_runtime_active and candidates:
        try:
            global_scored_rows = llm_score_candidates_batch(
                candidates=candidates,
                interest=interest,
                model=llm_model,
                api_base=llm_api_base,
                api_key=api_key,
                skill_requirements=llm_skill_requirements,
                area_order_map=area_order_map,
                task_label="Global scoring over merged per-area top-3 candidates",
            )
        except Exception as ex:
            final_llm_error = str(ex)

    global_map = {r.get("arxiv_id", ""): r for r in global_scored_rows if r.get("arxiv_id")}
    for row in area_top3_rows:
        pid = row.get("arxiv_id", "")
        r = global_map.get(pid, {})
        try:
            row["score"] = float(r.get("score", row.get("score", 0)) or 0)
        except Exception:
            row["score"] = float(row.get("score", 0) or 0)
        if str(r.get("reason", "") or "").strip():
            row["reason"] = str(r.get("reason", "") or "")

    merged_rows = []
    for c in candidates:
        cid = c.get("arxiv_id", "")
        r = global_map.get(cid, {})
        try:
            g_score = float(r.get("score", 0) or 0)
        except Exception:
            g_score = 0.0
        c["llm_stage2_score"] = g_score
        c["llm_stage2_reason"] = str(r.get("reason", "") or "")
        merged_rows.append(c)

    merged_rows = sorted(merged_rows, key=lambda x: float(x.get("llm_stage2_score", 0) or 0), reverse=True)
    stage2_top_k = len(merged_rows)
    stage2_top_candidates = merged_rows

    # Final rerank/selection over merged pool metadata, applying skill soft constraints.
    if llm_runtime_active and stage2_top_candidates:
        try:
            final_reranked_ids = llm_rerank_topk_for_final(
                top_candidates=stage2_top_candidates,
                interest=interest,
                model=llm_model,
                api_base=llm_api_base,
                api_key=api_key,
                skill_requirements=llm_skill_requirements,
            )
        except Exception:
            final_reranked_ids = []

    top = []
    if final_top_n > 0:
        stage2_map = {p.get("arxiv_id", ""): p for p in stage2_top_candidates if p.get("arxiv_id")}
        ordered_ids = [pid for pid in final_reranked_ids if pid in stage2_map] if final_reranked_ids else []
        if not ordered_ids:
            ordered_ids = [p.get("arxiv_id", "") for p in stage2_top_candidates if p.get("arxiv_id")]

        for pid in ordered_ids:
            if pid in stage2_map:
                item = dict(stage2_map[pid])
                item["hits"] = ["llm-final-top5"] if final_reranked_ids else ["llm-final-top5-fallback"]
                top.append(item)
            if len(top) >= final_top_n:
                break

        final_llm_ids = [p.get("arxiv_id", "") for p in top if p.get("arxiv_id")]
        if top:
            final_mode = "llm-merged-top3-rerank"

        if not top:
            top = list(stage2_top_candidates[:final_top_n])
            for item in top:
                item["hits"] = ["recent-fallback"]
            final_mode = "recent"
    else:
        top = list(stage2_top_candidates)

    final_ids_set = {p.get("arxiv_id", "") for p in top if p.get("arxiv_id")}
    for row in area_top3_rows:
        row["selected"] = row.get("arxiv_id", "") in final_ids_set

    # Download and deep-read only final selected top N papers.
    for paper in top:
        if args.download and paper.get("pdf_url") and paper.get("arxiv_id"):
            out_pdf = pdf_dir / make_pdf_filename(paper, area_order_map)
            if out_pdf.exists():
                out_pdf = pdf_dir / f"{safe_filename(out_pdf.stem)}_{safe_filename(paper['arxiv_id'])}.pdf"
            try:
                download_pdf(paper["pdf_url"], out_pdf)
                paper["local_pdf"] = str(out_pdf)
            except Exception as ex:
                paper["download_error"] = str(ex)

    pdf_post_download_files = list(pdf_dir.glob("*.pdf"))
    pdf_post_download_count = len(pdf_post_download_files)
    if should_cleanup_files:
        keep_pdf_paths = {str(Path(p.get("local_pdf", ""))) for p in top if p.get("local_pdf")}
        for p in pdf_post_download_files:
            if str(p) in keep_pdf_paths:
                continue
            try:
                p.unlink()
            except Exception as ex:
                pdf_cleanup_errors.append(f"{p}: {ex}")
    pdf_final_count = len(list(pdf_dir.glob("*.pdf")))

    # For final selection, read each full paper and generate personalized summary.
    brief_file = ""
    if llm_runtime_active and top:
        if not llm_skill_requirements:
            llm_skill_ctx = resolve_llm_skill_requirements(cfg, llm_enabled, api_key, llm_model, llm_api_base)
            llm_skill_requirements = llm_skill_ctx["requirements"]
            llm_skill_files_loaded = llm_skill_ctx["loaded_paths"]
            llm_skill_discovered = llm_skill_ctx["discovered_skills"]
            llm_skill_selected = llm_skill_ctx["selected_skill"]
            llm_skill_mode = llm_skill_ctx["mode"]
            llm_skill_route_error = llm_skill_ctx["route_error"]

        for paper in top:
            full_text = ""
            if paper.get("local_pdf"):
                full_text = extract_pdf_text(Path(paper["local_pdf"]))
            try:
                summary_obj = llm_summarize_full_paper(
                    paper=paper,
                    full_text=full_text,
                    interest=interest,
                    model=llm_model,
                    api_base=llm_api_base,
                    api_key=api_key,
                    skill_requirements=llm_skill_requirements,
                )
                paper["llm_relevance_score"] = float(summary_obj.get("relevance_score", 0) or 0)
                paper["llm_summary"] = summary_obj.get("summary", "")
                paper["llm_related_highlights"] = summary_obj.get("related_highlights", [])
            except Exception as ex:
                paper["llm_summary_error"] = str(ex)

        brief_path = day_dir / f"personalized_brief_{run_stamp}.md"
        generate_personalized_brief(top, area_top3_rows, brief_path, interest)
        if write_latest_brief_alias:
            # Optional stable alias path for integrations that expect a fixed filename.
            latest_brief_path = day_dir / "personalized_brief.md"
            latest_brief_path.write_text(brief_path.read_text(encoding="utf-8"), encoding="utf-8")
        brief_file = str(brief_path)

    if not brief_file:
        # Also emit a brief when LLM is disabled/unavailable.
        brief_path = day_dir / f"personalized_brief_{run_stamp}.md"
        generate_personalized_brief(top, area_top3_rows, brief_path, interest)
        if write_latest_brief_alias:
            latest_brief_path = day_dir / "personalized_brief.md"
            latest_brief_path.write_text(brief_path.read_text(encoding="utf-8"), encoding="utf-8")
        brief_file = str(brief_path)

    final_count = len(top)

    tracker_rows = []
    for paper in top:
        if paper.get("local_pdf"):
            tracker_rows.append(
                {
                    "downloaded_at": now_utc.isoformat(),
                    "run_date": dt.datetime.now().strftime("%Y-%m-%d"),
                    "arxiv_id": paper.get("arxiv_id", ""),
                    "title": paper.get("title", ""),
                    "selected_area": paper.get("selected_area", ""),
                    "selected_category_scope": paper.get("selected_category_scope", ""),
                    "local_pdf": paper.get("local_pdf", ""),
                }
            )
    if tracker_rows:
        append_tracker_rows(tracker_file, tracker_rows)

    digest_path = day_dir / f"digest_{run_stamp}.md"
    generate_digest(top, digest_path)
    if write_latest_digest_alias:
        latest_digest_path = day_dir / "digest.md"
        latest_digest_path.write_text(digest_path.read_text(encoding="utf-8"), encoding="utf-8")
    generate_commute_script(top, day_dir / "commute_script.txt")
    (day_dir / "papers.json").write_text(json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8")
    (day_dir / "run_config_snapshot.json").write_text(
        json.dumps(
            {
                "query": query,
                "days": days,
                "date": args.date,
                "max_results": max_results,
                "top": final_count,
                "candidate_top": pre_final_count,
                "categories": categories,
                "category_source": category_source,
                "category_scope": (args.category_scope or cfg.get("category_scope") or "core"),
                "query_strategy": query_strategy,
                "arxiv_ranking_mode": arxiv_ranking_mode,
                "arxiv_sort_by": arxiv_sort_by,
                "arxiv_sort_order": arxiv_sort_order,
                "arxiv_date_clause": arxiv_date_clause,
                "area_plan_results": area_plan_results,
                "keywords": keywords,
                "llm_keywords": llm_keywords,
                "research_interest": interest,
                "profile_path": str(profile_path),
                "selected_areas": profile_ctx.get("selected_areas") or [],
                "use_profile_for_content": use_profile_for_content,
                "rerank_mode": mode,
                "final_top": final_top_n,
                "final_selection_mode": final_mode,
                "final_candidates_before": pre_final_count,
                "stage2_top_k": stage2_top_k,
                "stage2_selected_for_final": len(stage2_top_candidates),
                "stage2_scoring_mode": "batch-llm",
                "stage1_top_per_area": stage1_top_per_area,
                "stage1_fetch_max_per_area": stage1_fetch_max_per_area,
                "stage1_selection_mode": "arxiv-order",
                "stage2_per_area_llm_top": stage2_per_area_llm_top,
                "per_area_top3_rows": len(area_top3_rows),
                "stage2_merged_reranked_ids": final_reranked_ids,
                "final_selected": final_count,
                "final_llm_selected_ids": final_llm_ids,
                "final_llm_error": final_llm_error,
                "llm_enabled_requested": llm_enabled,
                "llm_runtime_active": llm_runtime_active,
                "llm_api_key_present": bool(api_key),
                "retain_only_final_files": retain_only_final_files,
                "cleanup_on_dedup_reset": cleanup_on_dedup_reset,
                "write_latest_brief_alias": write_latest_brief_alias,
                "dedup_tracker_empty_at_start": dedup_tracker_empty,
                "file_cleanup_executed": should_cleanup_files,
                "pdf_pre_count": pdf_pre_count,
                "pdf_post_download_count": pdf_post_download_count,
                "pdf_final_count": pdf_final_count,
                "pdf_cleanup_error_count": len(pdf_cleanup_errors),
                "pdf_cleanup_errors": pdf_cleanup_errors,
                "personalized_brief_file": brief_file,
                "digest_file": str(digest_path),
                "write_latest_digest_alias": write_latest_digest_alias,
                "llm_model": llm_model if llm_enabled else "disabled",
                "llm_skill_mode": llm_skill_mode,
                "llm_skill_files_loaded": llm_skill_files_loaded,
                "llm_skill_discovered": llm_skill_discovered,
                "llm_skill_selected": llm_skill_selected,
                "llm_skill_route_error": llm_skill_route_error,
                "tracker_root": str(tracker_root),
                "tracker_file": str(tracker_file),
                "dedup_lookback_days": dedup_lookback_days,
                "excluded_by_recent_download_ids": len(downloaded_recent_ids),
                "tracker_rows_appended": len(tracker_rows),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Keywords file: {keywords_file}")
    print(f"Config: {args.config}")
    print(f"Category source: {category_source}")
    print(f"Query strategy: {query_strategy}")
    print(f"Query: {query}")
    print(f"Fetched: {fetched_count} | Recent: {recent_count} | Selected: {len(top)} | Rerank: {mode}")
    print(f"Final filter: {final_mode} | Before: {pre_final_count} | Final: {final_count} | Target: {final_top_n}")
    print(f"LLM skill mode: {llm_skill_mode} | Selected: {llm_skill_selected or 'none'}")
    if llm_skill_route_error:
        print(f"LLM skill routing fallback reason: {llm_skill_route_error}")
    if final_llm_error:
        print(f"Final LLM fallback reason: {final_llm_error}")
    print(
        f"PDF cleanup: pre={pdf_pre_count} post_download={pdf_post_download_count} "
        f"final={pdf_final_count} cleanup_errors={len(pdf_cleanup_errors)}"
    )
    print(f"Dedup lookback days: {dedup_lookback_days} | Excluded IDs: {len(downloaded_recent_ids)}")
    print(f"Tracker file: {tracker_file} | Appended: {len(tracker_rows)}")
    print(f"Output: {day_dir}")


if __name__ == "__main__":
    main()
