#!/usr/bin/env python3
"""Expand release entries in releases.json with YouTube Music track URLs."""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

try:
    from ytmusicapi import YTMusic
except ImportError:
    print(
        "Error: 'ytmusicapi' package is required. Install it using:\n"
        "  pip install ytmusicapi>=1.8.0",
        file=sys.stderr,
    )
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASES_FILE = REPO_ROOT / "releases.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("expand_release_tracks")

T = TypeVar("T")

RETRYABLE_MARKERS = (
    "429",
    "rate limit",
    "ratelimit",
    "too many requests",
    "resource_exhausted",
    "resource exhausted",
    "backenderror",
    "backend error",
    "internal error",
    "temporarily unavailable",
    "service unavailable",
    "500",
    "502",
    "503",
    "504",
)


def call_with_retry(
    fn: Callable[[], T],
    *,
    label: str,
    max_retries: int = 4,
    base_delay: float = 3.0,
) -> T:
    """Retry transient or rate-limit YouTube Music API errors with backoff."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as err:
            msg = str(err).lower()
            is_retryable = any(marker in msg for marker in RETRYABLE_MARKERS)
            if not is_retryable or attempt >= max_retries:
                raise
            attempt += 1
            delay = min(60.0, base_delay * (2 ** (attempt - 1))) + random.uniform(0.0, 2.0)
            logger.warning("Transient error on %s (%s); retry %d/%d in %.1fs", label, err, attempt, max_retries, delay)
            time.sleep(delay)


def reset_release_match(rel: dict[str, Any]) -> None:
    """Clear prior YTM match metadata and expanded tracks before full re-expansion."""
    rel["browse_id"] = None
    rel["video_id"] = None
    rel["ytm_type"] = None
    rel["ytm_matched_title"] = None
    rel.pop("tracks", None)


def resolve_release(ytm: YTMusic, rel: dict[str, Any]) -> bool:
    """Search YTM for a release and populate browse_id/video_id and tracks."""
    artist = str(rel.get("artist") or "").strip()
    title = str(rel.get("title") or "").strip()
    query = f"{artist} {title}".strip()
    if not query:
        return False

    results = call_with_retry(
        lambda: ytm.search(query),
        label=f"search:{query}",
    )
    if not isinstance(results, list) or not results:
        return False

    # Prefer album/EP browseId first, then playlist/video results.
    for item in results:
        if not isinstance(item, dict):
            continue
        browse_id = item.get("browseId")
        result_type = str(item.get("resultType") or "").lower()
        if browse_id and str(browse_id).startswith("MPREb_"):
            album = call_with_retry(
                lambda bid=str(browse_id): ytm.get_album(bid),
                label=f"album:{browse_id}",
            )
            tracks_out: list[dict[str, str]] = []
            for tr in album.get("tracks") or []:
                if not isinstance(tr, dict):
                    continue
                vid = str(tr.get("videoId") or "").strip()
                if not vid:
                    continue
                tr_artists = tr.get("artists")
                tr_artist = (
                    ", ".join(
                        str(a.get("name") or "").strip()
                        for a in tr_artists
                        if isinstance(a, dict) and a.get("name")
                    )
                    if isinstance(tr_artists, list)
                    else artist
                ) or artist
                tracks_out.append(
                    {
                        "artist": tr_artist,
                        "title": str(tr.get("title") or title).strip(),
                        "url": f"https://music.youtube.com/watch?v={vid}",
                    }
                )
            rel["browse_id"] = str(browse_id)
            rel["video_id"] = None
            rel["ytm_type"] = result_type or "album"
            rel["ytm_matched_title"] = str(album.get("title") or item.get("title") or "").strip() or None
            if tracks_out:
                rel["tracks"] = tracks_out
            return True

    for item in results:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("videoId") or "").strip()
        if vid:
            rel["browse_id"] = None
            rel["video_id"] = vid
            rel["ytm_type"] = str(item.get("resultType") or "video").lower()
            rel["ytm_matched_title"] = str(item.get("title") or "").strip() or None
            rel["tracks"] = [
                {
                    "artist": artist,
                    "title": title,
                    "url": f"https://music.youtube.com/watch?v={vid}",
                }
            ]
            return True

    return False


def save_releases_store(path: Path, store: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expand release track URLs in releases.json.")
    parser.add_argument(
        "--max-expansions",
        type=int,
        default=40,
        help="Maximum number of releases to expand in this run (default: 40).",
    )
    parser.add_argument(
        "--post-idx",
        type=int,
        default=None,
        help="Target a specific post index (default: process newest first).",
    )
    parser.add_argument(
        "--reprocess-existing",
        action="store_true",
        help="Reset and re-expand releases that already have tracks or match metadata.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not RELEASES_FILE.exists():
        logger.error("Missing %s", RELEASES_FILE)
        sys.exit(1)

    store = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    posts = store.get("posts", [])
    ytm = YTMusic()

    expanded_count = 0
    changed = False
    max_expansions = max(0, args.max_expansions)

    indices = (
        [args.post_idx]
        if args.post_idx is not None
        else list(range(len(posts) - 1, -1, -1))
    )

    for post_idx in indices:
        if expanded_count >= max_expansions:
            break
        if post_idx < 0 or post_idx >= len(posts):
            continue
        post = posts[post_idx]
        if not isinstance(post, dict):
            continue

        releases = post.get("releases", [])
        if not isinstance(releases, list):
            continue

        for rel in releases:
            if expanded_count >= max_expansions:
                break
            if not isinstance(rel, dict):
                continue

            has_tracks = isinstance(rel.get("tracks"), list) and len(rel["tracks"]) > 0
            if has_tracks and not args.reprocess_existing:
                continue

            if args.reprocess_existing:
                reset_release_match(rel)
                changed = True

            try:
                ok = resolve_release(ytm, rel)
                if ok:
                    expanded_count += 1
                    changed = True
                    save_releases_store(RELEASES_FILE, store)
                    time.sleep(0.8)
            except Exception as err:
                logger.warning("Failed expanding '%s - %s': %s", rel.get("artist"), rel.get("title"), err)

    if changed:
        save_releases_store(RELEASES_FILE, store)
    logger.info("Expansion run finished. Releases expanded: %d", expanded_count)


if __name__ == "__main__":
    main()
