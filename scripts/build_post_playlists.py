#!/usr/bin/env python3
"""Build and sync YouTube Music playlists per post in releases.json.

Expands albums (MPREb_...) and playlists (VL... / PL...) into playable track
video IDs, creates an unlisted playlist per post via authenticated ytmusicapi,
and records ytm_playlist_id / ytm_playlist_url on the post.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
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

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASES_FILE = REPO_ROOT / "releases.json"
CACHE_FILE = REPO_ROOT / "ytmusic_cache.json"
DEFAULT_AUTH_FILE = REPO_ROOT / "oauth.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("build_post_playlists")


def clean_query(artist: str, title: str) -> str:
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


def get_auth_client(auth_path: Path) -> YTMusic:
    auth_env = os.environ.get("YTM_AUTH_JSON", "").strip()
    if auth_env:
        try:
            return YTMusic(auth_env)
        except Exception as err:
            logger.warning("Could not initialize YTMusic from YTM_AUTH_JSON: %s", err)

    if auth_path.exists():
        return YTMusic(str(auth_path))

    raise RuntimeError(
        f"Missing authentication credentials. Either place '{auth_path.name}' "
        "in the repo root or export the 'YTM_AUTH_JSON' environment variable."
    )


def extract_video_ids_for_release(
    auth_ytm: YTMusic,
    unauth_ytm: YTMusic,
    rel: dict[str, Any],
    delay: float = 0.2,
) -> list[str]:
    video_ids: list[str] = []
    seen: set[str] = set()

    def add_vid(vid: str | None) -> None:
        if vid and vid not in seen:
            seen.add(vid)
            video_ids.append(vid)

    # 1. Direct track / video ID
    if rel.get("video_id"):
        add_vid(str(rel["video_id"]).strip())
        return video_ids

    browse_id = str(rel.get("browse_id") or "").strip()

    # 2. Album (MPREb_...)
    if browse_id.startswith("MPREb_"):
        try:
            album = unauth_ytm.get_album(browse_id)
            for track in album.get("tracks") or []:
                add_vid(track.get("videoId"))
            if video_ids:
                return video_ids
        except Exception as err:
            logger.debug("Album lookup failed for %s: %s", browse_id, err)

    # 3. Playlist / Stream (VL... / PL...)
    elif browse_id.startswith(("VL", "PL")):
        try:
            pl = unauth_ytm.get_playlist(browse_id, limit=100)
            for track in pl.get("tracks") or []:
                add_vid(track.get("videoId"))
            if video_ids:
                return video_ids
        except Exception as err:
            logger.debug("Playlist lookup failed for %s: %s", browse_id, err)

    # 4. Fallback search by artist + title
    time.sleep(delay)
    query = clean_query(str(rel.get("artist", "")), str(rel.get("title", "")))
    if query:
        for flt in ("songs", "videos"):
            try:
                items = unauth_ytm.search(query, filter=flt, limit=1)
                if isinstance(items, list) and items and items[0].get("videoId"):
                    add_vid(items[0]["videoId"])
                    return video_ids
            except Exception as err:
                logger.debug("Fallback query search failed for '%s': %s", query, err)

    return video_ids


def sync_post_playlist(
    auth_ytm: YTMusic,
    unauth_ytm: YTMusic,
    post: dict[str, Any],
    force: bool = False,
    delay: float = 0.25,
) -> bool:
    post_title = str(post.get("post_title") or "Untitled Roundup").strip()
    post_url = str(post.get("post_url") or "").strip()
    releases = post.get("releases") or []

    if not releases:
        logger.info("Post '%s' contains no releases; skipping.", post_title)
        return False

    existing_pl_id = str(post.get("ytm_playlist_id") or "").strip()
    if existing_pl_id and not force:
        logger.info("Playlist already exists for '%s' (%s). Use --force to resync.", post_title, existing_pl_id)
        return False

    logger.info("Resolving playable tracks for '%s' (%d releases)...", post_title, len(releases))

    track_video_ids: list[str] = []
    seen: set[str] = set()

    for idx, rel in enumerate(releases, start=1):
        vids = extract_video_ids_for_release(auth_ytm, unauth_ytm, rel, delay=delay)
        for vid in vids:
            if vid not in seen:
                seen.add(vid)
                track_video_ids.append(vid)
        time.sleep(delay)

    if not track_video_ids:
        logger.warning("No playable track video IDs resolved for post '%s'.", post_title)
        return False

    description = f"Releases from: {post_title}"
    if post_url:
        description += f"\nSource: {post_url}"

    if not existing_pl_id:
        logger.info("Creating new unlisted playlist for '%s' with %d tracks...", post_title, len(track_video_ids))
        playlist_id = auth_ytm.create_playlist(
            title=post_title[:150],
            description=description[:5000],
            privacy_status="UNLISTED",
            video_ids=track_video_ids,
        )
        post["ytm_playlist_id"] = playlist_id
        post["ytm_playlist_url"] = f"https://music.youtube.com/playlist?list={playlist_id}"
        logger.info("Created playlist: %s", post["ytm_playlist_url"])
        return True

    # Update existing playlist
    try:
        pl_data = auth_ytm.get_playlist(existing_pl_id, limit=1000)
        current_vids = {
            t.get("videoId") for t in (pl_data.get("tracks") or []) if t.get("videoId")
        }
        to_add = [v for v in track_video_ids if v not in current_vids]

        if to_add:
            logger.info("Adding %d new track(s) to existing playlist '%s'...", len(to_add), existing_pl_id)
            auth_ytm.add_playlist_items(existing_pl_id, to_add, duplicates=False)
        else:
            logger.info("Playlist '%s' is already up to date (%d tracks).", existing_pl_id, len(current_vids))

        post["ytm_playlist_url"] = f"https://music.youtube.com/playlist?list={existing_pl_id}"
        return bool(to_add)
    except Exception as err:
        logger.warning("Could not sync existing playlist %s: %s", existing_pl_id, err)
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build YouTube Music playlists per post in releases.json."
    )
    parser.add_argument(
        "--auth-file",
        type=Path,
        default=DEFAULT_AUTH_FILE,
        help="Path to ytmusicapi credentials file (default: oauth.json).",
    )
    parser.add_argument(
        "--post-title",
        type=str,
        default="",
        help="Target only a specific post title (case-insensitive substring).",
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Only build a playlist for the most recent post in releases.json.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-verify and append missing tracks even if ytm_playlist_id is set.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Throttle delay between API queries in seconds (default: 0.25).",
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
    if not isinstance(posts, list) or not posts:
        logger.warning("No posts found in %s", RELEASES_FILE)
        sys.exit(0)

    auth_ytm = get_auth_client(args.auth_file)
    unauth_ytm = YTMusic()

    target_posts = posts
    if args.latest_only:
        target_posts = posts[:1]
    elif args.post_title:
        title_needle = args.post_title.strip().lower()
        target_posts = [
            p for p in posts if title_needle in str(p.get("post_title", "")).lower()
        ]

    if not target_posts:
        logger.warning("No matching posts found.")
        sys.exit(0)

    modified = False
    for post in target_posts:
        changed = sync_post_playlist(
            auth_ytm=auth_ytm,
            unauth_ytm=unauth_ytm,
            post=post,
            force=args.force,
            delay=args.delay,
        )
        if changed:
            modified = True

    if modified:
        RELEASES_FILE.write_text(
            json.dumps(store, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info("Updated releases.json with new playlist metadata.")


if __name__ == "__main__":
    main()
