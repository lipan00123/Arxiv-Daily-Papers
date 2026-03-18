import datetime as dt
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_API = "https://export.arxiv.org/api/query"


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


def resolve_categories(cfg, args_categories: str, args_category_scope: str, forced_scope: str = ""):
    # CLI explicit categories always take highest priority.
    if args_categories:
        return [c.strip() for c in args_categories.split(",") if c.strip()], "cli"

    legacy = cfg.get("categories") or []
    core = cfg.get("categories_core") or []
    extended = cfg.get("categories_extended") or []

    if isinstance(legacy, str):
        legacy = [c.strip() for c in legacy.split(",") if c.strip()]
    if isinstance(core, str):
        core = [c.strip() for c in core.split(",") if c.strip()]
    if isinstance(extended, str):
        extended = [c.strip() for c in extended.split(",") if c.strip()]

    # Backward compatible: if only legacy categories exist, keep using them.
    if legacy and not core and not extended:
        return legacy, "legacy"

    scope = (forced_scope or args_category_scope or cfg.get("category_scope") or "core").strip().lower()
    if scope in {"all", "both", "core+extended", "extended+core"}:
        return _dedupe_keep_order(core + extended), "core+extended"
    if scope == "extended":
        return _dedupe_keep_order(extended), "extended"

    # Default: core categories for higher precision.
    return _dedupe_keep_order(core if core else legacy), "core"


def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_entry(entry: ET.Element):
    title = normalize_spaces(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
    summary = normalize_spaces(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))
    published = entry.findtext("atom:published", default="", namespaces=ATOM_NS)

    authors = []
    for a in entry.findall("atom:author", ATOM_NS):
        name = a.findtext("atom:name", default="", namespaces=ATOM_NS).strip()
        if name:
            authors.append(name)

    categories = [c.attrib.get("term", "") for c in entry.findall("atom:category", ATOM_NS)]

    entry_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
    arxiv_id = entry_id.rstrip("/").split("/")[-1] if entry_id else ""

    pdf_url = ""
    for link in entry.findall("atom:link", ATOM_NS):
        if link.attrib.get("title") == "pdf":
            pdf_url = link.attrib.get("href", "")
            break
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    return {
        "title": title,
        "summary": summary,
        "published": published,
        "authors": authors,
        "categories": categories,
        "arxiv_id": arxiv_id,
        "pdf_url": pdf_url,
        "entry_url": entry_id,
    }


def fetch_arxiv(max_results: int, query: str, sort_by: str = "relevance", sort_order: str = "descending"):
    sort_by_norm = (sort_by or "relevance").strip()
    if sort_by_norm not in {"relevance", "lastUpdatedDate", "submittedDate"}:
        sort_by_norm = "relevance"

    sort_order_norm = (sort_order or "descending").strip().lower()
    if sort_order_norm not in {"ascending", "descending"}:
        sort_order_norm = "descending"

    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": sort_by_norm,
        "sortOrder": sort_order_norm,
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"

    context = ssl.create_default_context()
    with urllib.request.urlopen(url, context=context, timeout=30) as resp:
        data = resp.read()

    root = ET.fromstring(data)
    entries = [parse_entry(e) for e in root.findall("atom:entry", ATOM_NS)]
    return entries


def build_search_query(keywords, categories, profile_arxiv_queries=None):
    # Strategy 1 (preferred): use curated per-area arXiv boolean query from profile.
    q_list = [q.strip() for q in (profile_arxiv_queries or []) if q and q.strip()]
    if q_list:
        base_clause = " OR ".join([f"({q})" for q in q_list])
        strategy = "profile_arxiv_query"
    else:
        # Strategy 2 (fallback): build broad recall query from keywords.
        kw_parts = []
        for kw in keywords:
            k = kw.strip().lower().replace('"', "")
            if not k:
                continue
            if " " in k:
                kw_parts.append(f'ti:"{k}"')
                kw_parts.append(f'abs:"{k}"')
            for token in re.findall(r"[a-zA-Z0-9]+", k):
                if len(token) >= 3:
                    kw_parts.append(f"all:{token}")
        base_clause = " OR ".join(_dedupe_keep_order(kw_parts)) if kw_parts else "all:machine"
        strategy = "keywords_fallback"

    if categories:
        cat_clause = " OR ".join([f"cat:{c}" for c in categories])
        return f"({base_clause}) AND ({cat_clause})", strategy
    return base_clause, strategy


def build_submitted_date_clause(days: int, date_str: str, now_utc: dt.datetime):
    if date_str:
        target = dt.date.fromisoformat(date_str)
        start_dt = dt.datetime(target.year, target.month, target.day, 0, 0, tzinfo=dt.timezone.utc)
        end_dt = dt.datetime(target.year, target.month, target.day, 23, 59, tzinfo=dt.timezone.utc)
    else:
        start_dt = now_utc - dt.timedelta(days=max(1, int(days)))
        end_dt = now_utc

    start_s = start_dt.strftime("%Y%m%d%H%M")
    end_s = end_dt.strftime("%Y%m%d%H%M")
    return f"submittedDate:[{start_s} TO {end_s}]"


def resolve_arxiv_sort_config(cfg):
    mode = str(cfg.get("arxiv_ranking_mode", "") or "").strip().lower()
    if mode in {"days+relevance", "days_relevance", "relevance"}:
        return "days+relevance", "relevance", "descending"
    if mode in {"days-only", "days_only", "submitteddate", "time"}:
        return "days-only", "submittedDate", "descending"

    # Backward-compatible fallback to explicit sort fields.
    sort_by = str(cfg.get("arxiv_sort_by", "relevance") or "relevance")
    sort_order = str(cfg.get("arxiv_sort_order", "descending") or "descending")
    inferred_mode = "days-only" if str(sort_by) == "submittedDate" else "days+relevance"
    return inferred_mode, sort_by, sort_order


def fetch_entries_for_scope(cfg, keywords, profile_arxiv_queries, max_results, category_scope, date_clause=""):
    categories, category_source = resolve_categories(cfg, "", "", forced_scope=category_scope)
    query, query_strategy = build_search_query(
        keywords,
        categories,
        profile_arxiv_queries=profile_arxiv_queries or [],
    )
    if date_clause:
        query = f"({query}) AND ({date_clause})"
    # For scope=all, categories already contains core + extended as a single OR clause.
    _mode, sort_by, sort_order = resolve_arxiv_sort_config(cfg)
    entries = fetch_arxiv(max_results=max_results, query=query, sort_by=sort_by, sort_order=sort_order)

    return {
        "entries": entries,
        "query": query,
        "query_strategy": query_strategy,
        "categories": categories,
        "category_source": category_source,
    }


def parse_date(s: str):
    # arXiv format: 2026-03-16T01:23:45Z
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_recent(entries, days: int):
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=days)
    out = []
    for e in entries:
        d = parse_date(e["published"])
        if d and d >= cutoff:
            out.append(e)
    return out


def filter_by_exact_date(entries, date_str: str):
    try:
        target = dt.date.fromisoformat(date_str)
    except ValueError:
        raise ValueError("--date must be YYYY-MM-DD")

    out = []
    for e in entries:
        d = parse_date(e["published"])
        if d and d.date() == target:
            out.append(e)
    return out
