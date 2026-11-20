#!/usr/bin/env python3
"""Resolve YouTube Music album browse IDs and track video IDs for releases.

Prioritizes official full albums/EPs (browseId starting with MPREb_),
followed by community playlist streams (VL/PL), falling back to individual
videos or tracks if no album exists.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    from ytmusicapi import YTMusic
except ImportError:
    print(
        "Error: 'ytmusicapi' package is required. Install it using:\n"
        "  pip install ytmusicapi>=1.8.0",
        file=sys.stderr,
    )
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent
RELEASES_FILE = REPO_ROOT / "releases.json"
CACHE_FILE = REPO_ROOT / "ytmusic_cache.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ytm_resolver")


def clean_query(artist: str, title: str) -> str:
    """Strip bracketed notes, parentheses, and format suffixes from queries."""
    clean_title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)
    clean_title = re.sub(
        r"\b(LP|EP|Demo|Split|Single|Promo|Reissue|Cassette|Flexi)\b\.?$",
        "",
        clean_title,
        flags=re.IGNORECASE,
    )
    clean_title = re.sub(r"\s+", " ", clean_title).strip(" -–—:/")

    clean_artist = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", artist)
    clean_artist = re.sub(r"\s+", " ", clean_artist).strip(" -–—:/")

    return f"{clean_artist} {clean_title or title}".strip()


def load_cache() -> dict[str, dict[str, Any]]:
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as err:
        logger.warning("Could not load cache file: %s", err)
        return {}


def save_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_FILE.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def pick_best_result(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not results or not isinstance(results, list):
        return None

    # Priority 1: Official Album / EP Browse ID
    for item in results:
        browse_id = item.get("browseId")
        item_type = str(item.get("type", "")).lower()
        if browse_id and (
            browse_id.startswith("MPREb_") or item_type in ("album", "ep", "single")
        ):
            return {
                "browse_id": browse_id,
                "video_id": None,
                "result_type": "album",
                "matched_title": item.get("title", ""),
            }

    # Priority 2: Playlist / Full Stream (VL / PL)
    for item in results:
        playlist_id = item.get("playlistId") or item.get("browseId")
        if playlist_id and (
            playlist_id.startswith("VL") or playlist_id.startswith("PL")
        ):
            return {
                "browse_id": playlist_id,
                "video_id": None,
                "result_type": "playlist",
                "matched_title": item.get("title", ""),
            }

    # Priority 3: Video / Single Track
    for item in results:
        vid = item.get("videoId")
        if vid:
            return {
                "browse_id": None,
                "video_id": vid,
                "result_type": item.get("resultType", "track"),
                "matched_title": item.get("title", ""),
            }

    return None


def search_ytmusic(ytm: YTMusic, query: str) -> dict[str, Any] | None:
    # 1. Search official albums/EPs
    try:
        albums = ytm.search(query, filter="albums", limit=3)
        match = pick_best_result(albums if isinstance(albums, list) else [])
        if match:
            return match
    except Exception as err:
        logger.debug("Album search failed for '%s': %s", query, err)

    # 2. Search community playlists / full stream uploads
    try:
        playlists = ytm.search(query, filter="community_playlists", limit=3)
        match = pick_best_result(playlists if isinstance(playlists, list) else [])
        if match:
            return match
    except Exception as err:
        logger.debug("Playlist search failed for '%s': %s", query, err)

    # 3. Search videos (e.g. full-album rip uploads)
    try:
        videos = ytm.search(f"{query} full album", filter="videos", limit=3)
        match = pick_best_result(videos if isinstance(videos, list) else [])
        if match:
            return match
    except Exception as err:
        logger.debug("Video stream search failed for '%s': %s", query, err)

    # 4. Fall back to individual song or general query
    for filter_opt in ("songs", None):
        try:
            items = ytm.search(query, filter=filter_opt, limit=3)
            match = pick_best_result(items if isinstance(items, list) else [])
            if match:
                return match
        except Exception as err:
            logger.debug("Fallback filter=%s failed for '%s': %s", filter_opt, query, err)

    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve releases in releases.json against YouTube Music."
    )
    parser.add_argument(
        "--max-lookups",
        type=int,
        default=25,
        help="Maximum number of uncached API lookups per run (default: 25).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.6,
        help="Throttle delay in seconds between consecutive API queries (default: 0.6).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-lookup even if an item already has a browse_id or video_id.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not RELEASES_FILE.exists():
        logger.error("File not found: %s", RELEASES_FILE)
        sys.exit(1)

    try:
        store = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    except Exception as err:
        logger.error("Failed to parse releases.json: %s", err)
        sys.exit(1)

    posts = store.get("posts", [])
    if not isinstance(posts, list):
        logger.error("'posts' key in releases.json must be a list.")
        sys.exit(1)

    cache = load_cache()
    ytm = YTMusic()

    lookups_performed = 0
    resolved_count = 0
    cache_hits = 0

    for post in posts:
        releases = post.get("releases", [])
        if not isinstance(releases, list):
            continue

        for rel in releases:
            artist = str(rel.get("artist", "")).strip()
            title = str(rel.get("title", "")).strip()
            if not artist or not title:
                continue

            cache_key = f"{artist.lower()}::{title.lower()}"
            has_id = bool(rel.get("browse_id") or rel.get("video_id"))

            if has_id and not args.force:
                continue

            query = clean_query(artist, title)

            # Check local persistent cache
            if cache_key in cache and not args.force:
                cached_item = cache[cache_key]
                if cached_item:
                    rel["browse_id"] = cached_item.get("browse_id")
                    rel["video_id"] = cached_item.get("video_id")
                    rel["ytm_type"] = cached_item.get("result_type")
                    rel["ytm_matched_title"] = cached_item.get("matched_title")
                    cache_hits += 1
                continue

            if lookups_performed >= args.max_lookups:
                logger.info("Reached maximum lookup limit (%d).", args.max_lookups)
                break

            logger.info("Querying YTM [%d/%d]: '%s'", lookups_performed + 1, args.max_lookups, query)
            match = search_ytmusic(ytm, query)
            lookups_performed += 1

            if match:
                rel["browse_id"] = match.get("browse_id")
                rel["video_id"] = match.get("video_id")
                rel["ytm_type"] = match.get("result_type")
                rel["ytm_matched_title"] = match.get("matched_title")
                cache[cache_key] = match
                resolved_count += 1
                logger.info(
                    " -> Found (%s): %s [ID: %s]",
                    match.get("result_type"),
                    match.get("matched_title"),
                    match.get("browse_id") or match.get("video_id"),
                )
            else:
                # Cache None so repeat runs don't burn calls on missing items
                cache[cache_key] = None
                logger.info(" -> No direct match found.")

            time.sleep(args.delay)

        if lookups_performed >= args.max_lookups:
            break

    # Persist cache and releases
    save_cache(cache)
    RELEASES_FILE.write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    logger.info(
        "Finished. Lookups: %d | Newly resolved: %d | Cache hits: %d",
        lookups_performed,
        resolved_count,
        cache_hits,
    )


if __name__ == "__main__":
    main()
