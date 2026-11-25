#!/usr/bin/env python3
"""Publish public YouTube Music playlists for posts in releases.json using your account.

Supports both browser header auth files (e.g., browser.json) and OAuth token
files (e.g., oauth.json) with OAuthCredentials. Iterates posts starting with
the newest entry, retries rate-limit/transient API errors with backoff, and
writes 'playlist_id' and 'playlist_url' into releases.json.
"""

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

try:
    from ytmusicapi import OAuthCredentials
except ImportError:
    try:
        from ytmusicapi.auth.oauth.credentials import OAuthCredentials  # type: ignore[no-redef]
    except ImportError:
        OAuthCredentials = None  # type: ignore[assignment,misc]

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASES_FILE = REPO_ROOT / "releases.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("publish_playlists")

T = TypeVar("T")

RATE_LIMIT_MARKERS = (
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

HARD_QUOTA_MARKERS = (
    "quotaexceeded",
    "quota exceeded",
    "exceeded your quota",
    "daily limit",
)


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


def init_ytmusic(
    auth_path: Path,
    cli_client_id: str | None = None,
    cli_client_secret: str | None = None,
) -> YTMusic:
    """Initialize YTMusic, attaching OAuthCredentials when OAuth token fields are present."""
    raw_text = auth_path.read_text(encoding="utf-8")
    data = json.loads(raw_text)

    is_oauth_payload = isinstance(data, dict) and (
        "refresh_token" in data or "access_token" in data
    )

    if not is_oauth_payload:
        return YTMusic(str(auth_path))

    client_id = (
        (cli_client_id or "").strip()
        or str(data.get("client_id") or "").strip()
        or os.environ.get("YT_CLIENT_ID", "").strip()
    )
    client_secret = (
        (cli_client_secret or "").strip()
        or str(data.get("client_secret") or "").strip()
        or os.environ.get("YT_CLIENT_SECRET", "").strip()
    )

    if client_id and client_secret and OAuthCredentials is not None:
        creds = OAuthCredentials(client_id=client_id, client_secret=client_secret)
        return YTMusic(str(auth_path), oauth_credentials=creds)

    if not client_id or not client_secret:
        logger.warning(
            "OAuth token file '%s' does not include both client_id and client_secret, "
            "and YT_CLIENT_ID / YT_CLIENT_SECRET were not set; falling back to YTMusic(path).",
            auth_path,
        )
    elif OAuthCredentials is None:
        logger.warning(
            "OAuthCredentials is unavailable in the installed ytmusicapi version; "
            "falling back to YTMusic(path).",
        )

    return YTMusic(str(auth_path))


def is_hard_quota_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(marker in msg for marker in HARD_QUOTA_MARKERS)


def is_retryable_api_error(err: Exception) -> bool:
    if is_hard_quota_error(err):
        return False
    msg = str(err).lower()
    return any(marker in msg for marker in RATE_LIMIT_MARKERS)


def call_with_retry(
    fn: Callable[[], T],
    *,
    label: str,
    max_retries: int = 5,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
) -> T:
    """Execute fn and retry with exponential backoff + jitter on rate/transient limits."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as err:
            if not is_retryable_api_error(err) or attempt >= max_retries:
                raise
            attempt += 1
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            jitter = random.uniform(0.0, min(5.0, delay * 0.25))
            sleep_for = delay + jitter
            logger.warning(
                "API limit/transient error during %s (%s). Retrying %d/%d in %.1fs...",
                label,
                err,
                attempt,
                max_retries,
                sleep_for,
            )
            time.sleep(sleep_for)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create public YTM playlists for posts in releases.json."
    )
    parser.add_argument(
        "--auth",
        type=str,
        default="browser.json",
        help="Path to ytmusicapi credentials file (oauth.json or browser.json). Default: browser.json",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Google OAuth Client ID for TV/Limited Input device client (or set YT_CLIENT_ID).",
    )
    parser.add_argument(
        "--client-secret",
        type=str,
        default=None,
        help="Google OAuth Client Secret for TV/Limited Input device client (or set YT_CLIENT_SECRET).",
    )
    parser.add_argument(
        "--post-idx",
        type=int,
        default=None,
        help="Target a specific post index (default: process newest first).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of playlists to create in this run (default: unlimited).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum retry attempts on rate-limit/transient API errors (default: 5).",
    )
    parser.add_argument(
        "--retry-base-delay",
        type=float,
        default=5.0,
        help="Initial backoff delay in seconds before first retry (default: 5.0).",
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
            "Auth file '%s' not found. Generate oauth.json via 'ytmusicapi oauth' "
            "or browser.json via 'ytmusicapi browser'.",
            auth_path,
        )
        sys.exit(1)

    try:
        ytm = init_ytmusic(
            auth_path,
            cli_client_id=args.client_id,
            cli_client_secret=args.client_secret,
        )
    except Exception as err:
        logger.error("Failed to initialize YTMusic with %s: %s", auth_path, err)
        sys.exit(1)

    store = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    posts = store.get("posts", [])
    updated = False
    created_count = 0

    indices = (
        [args.post_idx]
        if args.post_idx is not None
        else list(range(len(posts) - 1, -1, -1))
    )

    for idx in indices:
        if args.limit is not None and created_count >= args.limit:
            logger.info("Reached --limit=%d; stopping.", args.limit)
            break
        if idx < 0 or idx >= len(posts):
            continue
        post = posts[idx]
        if not isinstance(post, dict):
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
            logger.warning("Post [%d] '%s' has 0 resolved video IDs. Skipping.", idx, title)
            continue

        logger.info("Creating public playlist [%d] '%s' with %d tracks...", idx, title, len(video_ids))
        try:
            pl_id = call_with_retry(
                lambda: ytm.create_playlist(
                    title=title[:150],
                    description=desc[:5000],
                    privacy_status="PUBLIC",
                    video_ids=video_ids,
                ),
                label=f"create_playlist[{idx}]",
                max_retries=max(0, args.max_retries),
                base_delay=max(0.5, args.retry_base_delay),
            )
            if isinstance(pl_id, dict):
                pl_id = pl_id.get("id") or pl_id.get("playlistId")

            pl_url = f"https://music.youtube.com/playlist?list={pl_id}"
            post["playlist_id"] = pl_id
            post["playlist_url"] = pl_url
            updated = True
            created_count += 1
            logger.info("Successfully published [%d]: %s", idx, pl_url)
            time.sleep(1.5)
        except Exception as err:
            if is_hard_quota_error(err):
                logger.error(
                    "Daily/hard API quota hit while creating playlist for [%d] '%s': %s. "
                    "Stopping remaining posts for this run.",
                    idx,
                    title,
                    err,
                )
                break
            logger.error("Failed creating playlist for [%d] '%s': %s", idx, title, err)

    if updated:
        RELEASES_FILE.write_text(
            json.dumps(store, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info("Updated %s with %d new playlist link(s).", RELEASES_FILE, created_count)


if __name__ == "__main__":
    main()
