#!/usr/bin/env python3
"""Migrate legacy track-only entries in releases.json and ytmusic_cache.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASES_FILE = REPO_ROOT / "releases.json"
CACHE_FILE = REPO_ROOT / "ytmusic_cache.json"


def main() -> None:
    if not RELEASES_FILE.exists():
        print(f"Error: {RELEASES_FILE} not found.", file=sys.stderr)
        sys.exit(1)

    store = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    posts = store.get("posts", [])

    reset_releases = 0
    kept_albums = 0

    for post in posts:
        for rel in post.get("releases", []):
            browse_id = (rel.get("browse_id") or "").strip()
            video_id = (rel.get("video_id") or "").strip()

            if browse_id:
                kept_albums += 1
                continue

            if video_id or rel.get("ytm_type") or rel.get("ytm_matched_title"):
                rel.pop("video_id", None)
                rel.pop("browse_id", None)
                rel.pop("ytm_type", None)
                rel.pop("ytm_matched_title", None)
                reset_releases += 1

    RELEASES_FILE.write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    purged_cache = 0
    if CACHE_FILE.exists():
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(cache, dict):
            cleaned_cache = {}
            for key, val in cache.items():
                if val is None:
                    purged_cache += 1
                    continue
                if isinstance(val, dict) and not val.get("browse_id") and val.get("video_id"):
                    purged_cache += 1
                    continue
                cleaned_cache[key] = val

            CACHE_FILE.write_text(
                json.dumps(cleaned_cache, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    print(
        f"Migration complete: reset {reset_releases} track-only release(s), "
        f"kept {kept_albums} album/playlist release(s), "
        f"purged {purged_cache} stale cache entry/entries."
    )


if __name__ == "__main__":
    main()
