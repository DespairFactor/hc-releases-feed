#!/usr/bin/env python3
"""Expand album and playlist releases in releases.json into individual tracks."""

from __future__ import annotations

import argparse
import json
import logging
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

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASES_FILE = REPO_ROOT / "releases.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("expand_tracks")


def format_artists(raw_artists: Any, fallback: str) -> str:
    if isinstance(raw_artists, list):
        names = [
            str(a.get("name", "")).strip()
            for a in raw_artists
            if isinstance(a, dict) and str(a.get("name", "")).strip()
        ]
        if names:
            return ", ".join(names)
    return fallback


def fetch_tracks_for_release(
    ytm: YTMusic, rel: dict[str, Any], playlist_limit: int = 200
) -> list[dict[str, str]]:
    artist = str(rel.get("artist") or "").strip()
    title = str(rel.get("title") or "").strip()
    browse_id = str(rel.get("browse_id") or "").strip()
    video_id = str(rel.get("video_id") or "").strip()

    tracks: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    def add_track(tr_artist: str, tr_title: str, vid: str) -> None:
        clean_vid = vid.strip()
        if not clean_vid:
            return
        url = f"https://music.youtube.com/watch?v={clean_vid}"
        if url in seen_urls:
            return
        seen_urls.add(url)
        tracks.append({
            "artist": tr_artist.strip() or artist,
            "title": tr_title.strip() or title,
            "url": url,
        })

    if browse_id.startswith("MPREb_"):
        album = ytm.get_album(browse_id)
        for tr in album.get("tracks") or []:
            if not isinstance(tr, dict):
                continue
            add_track(
                format_artists(tr.get("artists"), artist),
                str(tr.get("title") or title),
                str(tr.get("videoId") or ""),
            )
    elif browse_id.startswith(("VL", "PL")):
        pl = ytm.get_playlist(browse_id, limit=playlist_limit)
        for tr in pl.get("tracks") or []:
            if not isinstance(tr, dict):
                continue
            add_track(
                format_artists(tr.get("artists"), artist),
                str(tr.get("title") or title),
                str(tr.get("videoId") or ""),
            )
    elif video_id:
        add_track(artist, title, video_id)

    return tracks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Populate per-release track lists in releases.json."
    )
    parser.add_argument(
        "--max-expansions",
        type=int,
        default=40,
        help="Maximum number of releases to expand per run (default: 40).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Delay in seconds between release lookups (default: 0.25).",
    )
    parser.add_argument(
        "--post-title",
        type=str,
        default=None,
        help="Only expand releases in posts matching this title substring.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch tracks even if a release already has a non-empty 'tracks' list.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not RELEASES_FILE.exists():
        logger.error("Missing %s", RELEASES_FILE)
        sys.exit(1)

    store = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    posts = store.get("posts", [])
    if not isinstance(posts, list):
        logger.error("'posts' in releases.json must be a list.")
        sys.exit(1)

    ytm = YTMusic()
    expanded_count = 0
    total_tracks_added = 0

    for post in posts:
        if not isinstance(post, dict):
            continue
        post_title = str(post.get("post_title") or "")
        if args.post_title and args.post_title.lower() not in post_title.lower():
            continue

        for rel in post.get("releases", []):
            if not isinstance(rel, dict):
                continue
            if expanded_count >= args.max_expansions:
                break

            existing_tracks = rel.get("tracks")
            if isinstance(existing_tracks, list) and existing_tracks and not args.force:
                continue

            browse_id = str(rel.get("browse_id") or "").strip()
            video_id = str(rel.get("video_id") or "").strip()
            if not browse_id and not video_id:
                continue

            try:
                tracks = fetch_tracks_for_release(ytm, rel)
            except Exception as err:
                logger.warning(
                    "Failed expanding '%s - %s' (%s): %s",
                    rel.get("artist"),
                    rel.get("title"),
                    browse_id or video_id,
                    err,
                )
                expanded_count += 1
                time.sleep(args.delay)
                continue

            expanded_count += 1
            if tracks:
                rel["tracks"] = tracks
                total_tracks_added += len(tracks)
                logger.info(
                    "Expanded '%s - %s' -> %d track(s)",
                    rel.get("artist"),
                    rel.get("title"),
                    len(tracks),
                )

            time.sleep(args.delay)

        if expanded_count >= args.max_expansions:
            break

    if total_tracks_added > 0 or args.force:
        RELEASES_FILE.write_text(
            json.dumps(store, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info(
            "Updated %s: expanded %d release(s), recorded %d track(s).",
            RELEASES_FILE,
            expanded_count,
            total_tracks_added,
        )
    else:
        logger.info("No new tracks expanded (processed %d release(s)).", expanded_count)


if __name__ == "__main__":
    main()
