#!/usr/bin/env python3
"""Publish public YouTube Music playlists for posts in releases.json using your account.

Supports both browser header auth files (e.g., browser.json) and OAuth token
files (e.g., oauth.json) with OAuthCredentials. Iterates posts starting with
the newest entry, logs releases/tracks missing video IDs, adds tracks in
chunks with retry backoff, falls back to single-track inserts if a chunk
fails, and saves 'playlist_id' and 'playlist_url' into releases.json
immediately after each playlist is created.
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


def _extract_vid_from_url(url: Any) -> str:
    text = str(url or "")
    if "v=" not in text:
        return ""
    return text.split("v=", 1)[1].split("&", 1)[0].strip()


def extract_post_video_ids(post: dict[str, Any], post_label: str = "") -> list[str]:
    """Collect unique video IDs for a post while logging missing or duplicate entries."""
    video_ids: list[str] = []
    seen: set[str] = set()
    releases = post.get("releases", [])
    if not isinstance(releases, list):
        return video_ids

    for rel_idx, rel in enumerate(releases):
        if not isinstance(rel, dict):
            logger.warning("[%s] Release [%d] is not a dict; skipping.", post_label, rel_idx)
            continue

        rel_name = (
            str(rel.get("title") or rel.get("release_title") or rel.get("artist") or f"release[{rel_idx}]")
            .strip()
        )
        tracks = rel.get("tracks")
        rel_ids: list[str] = []

        if isinstance(tracks, list) and tracks:
            for tr_idx, tr in enumerate(tracks):
                if not isinstance(tr, dict):
                    logger.warning(
                        "[%s] Release '%s' track [%d] is invalid; skipping.",
                        post_label,
                        rel_name,
                        tr_idx,
                    )
                    continue
                vid = _extract_vid_from_url(tr.get("url")) or str(tr.get("video_id") or "").strip()
                if not vid:
                    tr_title = str(tr.get("title") or tr.get("name") or f"track[{tr_idx}]").strip()
                    logger.warning(
                        "[%s] Missing video ID for '%s' in release '%s' (url=%r).",
                        post_label,
                        tr_title,
                        rel_name,
                        tr.get("url"),
                    )
                    continue
                rel_ids.append(vid)
        else:
            vid = str(rel.get("video_id") or "").strip() or _extract_vid_from_url(rel.get("url"))
            if not vid:
                logger.warning(
                    "[%s] Missing video ID for release '%s' (no tracks with v= and no video_id).",
                    post_label,
                    rel_name,
                )
                continue
            rel_ids.append(vid)

        for vid in rel_ids:
            if vid in seen:
                logger.debug(
                    "[%s] Duplicate video_id '%s' in release '%s'; skipping duplicate.",
                    post_label,
                    vid,
                    rel_name,
                )
                continue
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


def append_chunk_with_fallback(
    ytm: YTMusic,
    playlist_id: str,
    chunk: list[str],
    *,
    post_label: str,
    chunk_num: int,
    max_retries: int,
    base_delay: float,
    single_track_fallback: bool,
) -> tuple[int, bool]:
    """Append a chunk of video IDs, optionally retrying one-by-one if the batch fails."""
    try:
        call_with_retry(
            lambda c=chunk: ytm.add_playlist_items(
                playlistId=playlist_id,
                videoIds=c,
                duplicates=False,
            ),
            label=f"{post_label}[chunk={chunk_num}]",
            max_retries=max_retries,
            base_delay=base_delay,
        )
        return len(chunk), False
    except Exception as chunk_err:
        if is_hard_quota_error(chunk_err):
            logger.warning(
                "[%s] Hard API quota reached on chunk %d (%d track(s)): %s",
                post_label,
                chunk_num,
                len(chunk),
                chunk_err,
            )
            return 0, True

        if not single_track_fallback or len(chunk) <= 1:
            logger.warning(
                "[%s] Failed appending chunk %d (%d track(s)): %s",
                post_label,
                chunk_num,
                len(chunk),
                chunk_err,
            )
            return 0, False

        logger.warning(
            "[%s] Chunk %d (%d track(s)) failed (%s); falling back to one-by-one insert.",
            post_label,
            chunk_num,
            len(chunk),
            chunk_err,
        )
        added_individually = 0
        for vid in chunk:
            try:
                call_with_retry(
                    lambda single_id=vid: ytm.add_playlist_items(
                        playlistId=playlist_id,
                        videoIds=[single_id],
                        duplicates=False,
                    ),
                    label=f"{post_label}[vid={vid}]",
                    max_retries=max_retries,
                    base_delay=base_delay,
                )
                added_individually += 1
                time.sleep(0.4)
            except Exception as track_err:
                logger.warning(
                    "[%s] Skipping video_id '%s' after single-track insert failed: %s",
                    post_label,
                    vid,
                    track_err,
                )
                if is_hard_quota_error(track_err):
                    return added_individually, True

        return added_individually, False


def save_releases_store(releases_path: Path, store: dict[str, Any]) -> None:
    """Persist updated releases.json atomically via a temporary file in the same directory."""
    tmp_path = releases_path.with_suffix(f"{releases_path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, releases_path)


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
        "--chunk-size",
        type=int,
        default=25,
        help="Number of tracks per chunk when creating and appending to a playlist (default: 25).",
    )
    parser.add_argument(
        "--no-single-track-fallback",
        action="store_true",
        help="Disable one-by-one track retry when a batch chunk fails.",
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
    created_count = 0
    chunk_size = max(1, args.chunk_size)
    single_track_fallback = not args.no_single_track_fallback
    retries = max(0, args.max_retries)
    delay = max(0.5, args.retry_base_delay)

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
        post_label = f"post[{idx}] {title}"
        reddit_url = str(post.get("post_url") or "").strip()
        desc = f"Curated hardcore releases from {title} on r/Hardcore."
        if reddit_url:
            desc += f" Reddit thread: {reddit_url}"

        video_ids = extract_post_video_ids(post, post_label=post_label)
        if not video_ids:
            logger.warning("[%s] Has 0 resolved video IDs. Skipping playlist creation.", post_label)
            continue

        initial_ids = video_ids[:chunk_size]
        remaining_ids = video_ids[chunk_size:]

        logger.info(
            "[%s] Creating public playlist with %d initial track(s) (%d total resolved)...",
            post_label,
            len(initial_ids),
            len(video_ids),
        )
        try:
            pl_id = call_with_retry(
                lambda: ytm.create_playlist(
                    title=title[:150],
                    description=desc[:5000],
                    privacy_status="PUBLIC",
                    video_ids=initial_ids,
                ),
                label=f"create_playlist[{idx}]",
                max_retries=retries,
                base_delay=delay,
            )
            if isinstance(pl_id, dict):
                pl_id = pl_id.get("id") or pl_id.get("playlistId")

            if not pl_id:
                raise RuntimeError("create_playlist returned an empty playlist ID")

            added_so_far = len(initial_ids)
            stop_due_to_quota = False

            for chunk_num, offset in enumerate(range(0, len(remaining_ids), chunk_size), start=1):
                chunk = remaining_ids[offset : offset + chunk_size]
                logger.info(
                    "[%s] Appending chunk %d (%d track(s), %d/%d queued)...",
                    post_label,
                    chunk_num,
                    len(chunk),
                    added_so_far + len(chunk),
                    len(video_ids),
                )
                added_in_chunk, hit_quota = append_chunk_with_fallback(
                    ytm,
                    str(pl_id),
                    chunk,
                    post_label=post_label,
                    chunk_num=chunk_num,
                    max_retries=retries,
                    base_delay=delay,
                    single_track_fallback=single_track_fallback,
                )
                added_so_far += added_in_chunk
                if hit_quota:
                    stop_due_to_quota = True
                    break
                time.sleep(1.0)

            pl_url = f"https://music.youtube.com/playlist?list={pl_id}"
            post["playlist_id"] = pl_id
            post["playlist_url"] = pl_url
            save_releases_store(RELEASES_FILE, store)
            created_count += 1
            logger.info(
                "[%s] Published and saved %s (%d/%d resolved tracks added).",
                post_label,
                pl_url,
                added_so_far,
                len(video_ids),
            )
            if stop_due_to_quota:
                logger.error("[%s] Hard API quota reached during chunk append; stopping run.", post_label)
                break
            time.sleep(1.5)
        except Exception as err:
            if is_hard_quota_error(err):
                logger.error(
                    "[%s] Daily/hard API quota hit while creating playlist: %s. "
                    "Stopping remaining posts for this run.",
                    post_label,
                    err,
                )
                break
            logger.error("[%s] Failed creating playlist: %s", post_label, err)

    logger.info("Finished publishing run. Total new playlist(s) created: %d.", created_count)


if __name__ == "__main__":
    main()
