#!/usr/bin/env python3
"""Publish public YouTube Music playlists for each post in releases.json using your account.

Writes 'playlist_id' and 'playlist_url' into releases.json so build_site.py can
render permanent playlist links on the static site.
"""

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
logger = logging.getLogger("publish_playlists")


def extract_post_video_ids(post: dict[str, Any]) -> list[str]:
    """Collect all valid track video IDs for a post."""
    video_ids: list[str] = []
    seen: set[str] = set()

    for rel in post.get("releases", []):
        if not isinstance(rel, dict):
            continue

        tracks = rel.get("tracks")
        if isinstance(tracks, list) and tracks:
            for tr in tracks:
                if not isinstance(tr, dict):
                    continue
                url = str(tr.get("url") or "")
                if "v=" in url:
                    vid = url.split("v=")[1].split("&")[0].strip()
                    if vid and vid not in seen:
                        seen.add(vid)
                        video_ids.append(vid)
        else:
            vid = str(rel.get("video_id") or "").strip()
            if vid and vid not in seen:
                seen.add(vid)
                video_ids.append(vid)

    return video_ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create public YTM playlists for posts in releases.json."
    )
    parser.add_argument(
        "--auth",
        type=str,
        default="browser.json",
        help="Path to ytmusicapi credentials file (browser.json or oauth.json). Default: browser.json",
    )
    parser.add_argument(
        "--post-idx",
        type=int,
        default=None,
        help="Target a specific post index (default: process all missing).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recreate playlists even if 'playlist_id' is already present.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not RELEASES_FILE.exists():
        logger.error("Missing %s", RELEASES_FILE)
        sys.exit(1)

    auth_path = Path(args.auth)
    if not auth_path.exists():
        logger.error(
            "Auth file '%s' not found. Set up ytmusicapi authentication "
            "(e.g. ytmusicapi oauth or headers_auth.json/browser.json).",
            auth_path,
        )
        sys.exit(1)

    try:
        ytm = YTMusic(str(auth_path))
    except Exception as err:
        logger.error("Failed to initialize YTMusic with %s: %s", auth_path, err)
        sys.exit(1)

    store = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    posts = store.get("posts", [])
    updated = False

    for idx, post in enumerate(posts):
        if not isinstance(post, dict):
            continue
        if args.post_idx is not None and idx != args.post_idx:
            continue

        existing_pl = post.get("playlist_id")
        if existing_pl and not args.force:
            continue

        title = str(post.get("post_title") or f"Hardcore Releases {post.get('added_at', '')}").strip()
        reddit_url = str(post.get("post_url") or "").strip()
        desc = f"Curated hardcore releases from {title} on r/Hardcore."
        if reddit_url:
            desc += f" Reddit thread: {reddit_url}"

        video_ids = extract_post_video_ids(post)
        if not video_ids:
            logger.warning("Post '%s' has 0 resolved video IDs. Skipping playlist creation.", title)
            continue

        logger.info("Creating public playlist '%s' with %d tracks...", title, len(video_ids))
        try:
            pl_id = ytm.create_playlist(
                title=title[:150],
                description=desc[:5000],
                privacy_status="PUBLIC",
                video_ids=video_ids,
            )
            if isinstance(pl_id, dict):
                pl_id = pl_id.get("id") or pl_id.get("playlistId")

            pl_url = f"https://music.youtube.com/playlist?list={pl_id}"
            post["playlist_id"] = pl_id
            post["playlist_url"] = pl_url
            updated = True
            logger.info("Successfully published: %s", pl_url)
            time.sleep(1.0)
        except Exception as err:
            logger.error("Failed creating playlist for '%s': %s", title, err)

    if updated:
        RELEASES_FILE.write_text(
            json.dumps(store, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info("Updated %s with new playlist links.", RELEASES_FILE)


if __name__ == "__main__":
    main()
