import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from ytmusicapi import YTMusic

DEFAULT_RELEASES_FILE = Path("releases.json")
DEFAULT_CACHE_FILE = Path("ytmusic_cache.json")


def normalize_release_query(artist: str, title: str) -> str:
    core_title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)
    core_title = re.sub(
        r"\b(LP|EP|Demo|Split|Single|Promo|Reissue)\b\.?$",
        "",
        core_title,
        flags=re.IGNORECASE,
    )
    core_title = re.sub(r"\s+", " ", core_title).strip(" -–—:/")
    core_artist = re.sub(r"\s+", " ", artist).strip(" -–—:/")
    cleaned = f"{core_artist} {core_title or title}".strip()
    return f"{cleaned} hardcore"


def pick_best_result(results: list[dict]) -> dict | None:
    for item in results:
        vid = item.get("videoId")
        if vid:
            return {
                "video_id": vid,
                "result_type": item.get("resultType", "unknown"),
                "matched_title": item.get("title", ""),
            }
    return None


def search_ytmusic(ytm: YTMusic, query: str) -> dict | None:
    # Prefer songs/videos first, then fall back to unfiltered search
    for filter_type in ("songs", "videos", None):
        try:
            if filter_type:
                res = ytm.search(query, filter=filter_type, limit=5)
            else:
                res = ytm.search(query, limit=5)
            picked = pick_best_result(res if isinstance(res, list) else [])
            if picked:
                return picked
        except Exception:
            continue
    return None


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve YouTube Music video_id values for releases.json using ytmusicapi."
    )
    parser.add_argument("--releases", type=Path, default=DEFAULT_RELEASES_FILE, help="Path to releases.json")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_FILE, help="Path to cache file")
    parser.add_argument("--max-lookups", type=int, default=30, help="Max new ytmusicapi queries per run")
    parser.add_argument("--delay", type=float, default=0.6, help="Delay in seconds between queries")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without modifying files")
    args = parser.parse_args()

    ytm = YTMusic()
    store = load_json(args.releases, {"source_user": "sepdri", "subreddit": "Hardcore", "posts": []})
    cache = load_json(args.cache, {"entries": {}})
    cache_entries: dict = cache.setdefault("entries", {})

    lookups_used = 0
    resolved_count = 0
    stop_early = False

    for post in store.get("posts", []):
        if stop_early:
            break
        for rel in post.get("releases", []):
            if rel.get("video_id") or rel.get("ytm_url"):
                continue

            artist = str(rel.get("artist", "")).strip()
            title = str(rel.get("title", "")).strip()
            if not artist or not title:
                continue

            query = normalize_release_query(artist, title)
            cache_key = query.lower()

            if cache_key in cache_entries:
                cached = cache_entries[cache_key]
                vid = cached.get("video_id")
                if vid:
                    rel["video_id"] = vid
                    resolved_count += 1
                    print(f"[CACHE HIT] {artist} — {title} -> {vid}")
                else:
                    print(f"[CACHE MISS] {artist} — {title} (no prior match)")
                continue

            if lookups_used >= args.max_lookups:
                print(f"Reached --max-lookups ({args.max_lookups}); stopping.")
                stop_early = True
                break

            lookups_used += 1
            match = search_ytmusic(ytm, query)
            if match:
                vid = match["video_id"]
                rel["video_id"] = vid
                resolved_count += 1
                cache_entries[cache_key] = {
                    "video_id": vid,
                    "result_type": match["result_type"],
                    "matched_title": match["matched_title"],
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
                print(f"[MATCHED] {artist} — {title} -> {match['matched_title']} ({vid})")
            else:
                cache_entries[cache_key] = {
                    "video_id": None,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
                print(f"[NO MATCH] {artist} — {title}")

            if args.delay > 0:
                time.sleep(args.delay)

    if args.dry_run:
        print(f"Dry run complete: {resolved_count} match(es) resolved, {lookups_used} ytmusicapi lookup(s) made.")
        return

    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(args.releases, store)
    save_json(args.cache, cache)
    print(f"Updated {args.releases}: {resolved_count} release(s) linked, {lookups_used} ytmusicapi lookup(s) made.")


if __name__ == "__main__":
    main()
