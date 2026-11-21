#!/usr/bin/env python3
"""Migrate legacy track-only entries in releases.json and ytmusic_cache.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASES_FILE = REPO_ROOT / "releases.json"
CACHE_FILE = REPO_ROOT / "ytmusic_cache.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate legacy entries and optionally reset playlist IDs in releases.json."
    )
    parser.add_argument(
        "--reset-playlists",
        action="store_true",
        help="Remove ytm_playlist_id and ytm_playlist_url from all posts in releases.json.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not RELEASES_FILE.exists():
        print(f"Error: {RELEASES_FILE} not found.", file=sys.stderr)
        sys.exit(1)

    store = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    posts = store.get("posts", [])

    reset_releases = 0
    kept_albums = 0
    cleared_playlists = 0

    for post in posts:
        if args.reset_playlists:
            if "ytm_playlist_id" in post or "ytm_playlist_url" in post:
                post.pop("ytm_playlist_id", None)
                post.pop("ytm_playlist_url", None)
                cleared_playlists += 1

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
        f"cleared {cleared_playlists} post playlist(s), "
        f"purged {purged_cache} stale cache entry/entries."
    )


if __name__ == "__main__":
    main()
