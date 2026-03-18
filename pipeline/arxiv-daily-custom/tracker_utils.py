import datetime as dt
import json
from pathlib import Path


def resolve_tracker_paths(cfg, output_root: Path):
    tracker_root_raw = cfg.get("tracker_root")
    tracker_file_raw = cfg.get("tracker_file")

    tracker_root = Path(tracker_root_raw) if tracker_root_raw else (output_root.parent / "arxiv-daily-tracker")
    tracker_file = tracker_root / (tracker_file_raw or "download_history.jsonl")
    return tracker_root, tracker_file


def load_tracker_history(tracker_file: Path):
    if not tracker_file.exists():
        return []
    rows = []
    for line in tracker_file.read_text(encoding="utf-8-sig").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rows.append(json.loads(s))
        except Exception:
            continue
    return rows


def tracker_is_empty(tracker_file: Path):
    if not tracker_file.exists():
        return True
    try:
        # Treat BOM/newline-only files as empty as well.
        for line in tracker_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                return False
        return True
    except Exception:
        return False


def _parse_iso_dt(raw: str):
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def recent_download_ids(history_rows, now_utc: dt.datetime, lookback_days: int):
    cutoff = now_utc - dt.timedelta(days=lookback_days)
    ids = set()
    for row in history_rows:
        rid = str(row.get("arxiv_id") or "").strip()
        if not rid:
            continue
        ts = _parse_iso_dt(str(row.get("downloaded_at") or ""))
        if ts and ts >= cutoff:
            ids.add(rid)
    return ids


def append_tracker_rows(tracker_file: Path, rows):
    tracker_file.parent.mkdir(parents=True, exist_ok=True)
    with tracker_file.open("a", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
