#!/usr/bin/env python3
"""Expand release entries in releases.json with YouTube Music track URLs."""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
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

STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "to",
    "for",
    "and",
    "or",
    "vs",
    "ep",
    "lp",
    "demo",
    "promo",
    "split",
    "vol",
    "volume",
    "pt",
    "part",
    "full",
    "stream",
    "album",
    "single",
    "edition",
    "remastered",
    "live",
    "2020",
    "2021",
    "2022",
    "2023",
    "2024",
    "2025",
    "2026",
}

FORMAT_SUFFIX_RE = re.compile(
    r"\b(?:lp|ep|demo|promo|split|single|full\s*stream|album|reissue|cassette|flexi)\b",
    flags=re.IGNORECASE,
)

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
            logger.warning(
                "Transient error on %s (%s); retry %d/%d in %.1fs",
                label,
                err,
                attempt,
                max_retries,
                delay,
            )
            time.sleep(delay)


def normalize_tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower())
    return {tok for tok in cleaned.split() if len(tok) > 1 and tok not in STOPWORDS}


def normalize_compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def extract_artists(item: dict[str, Any]) -> list[str]:
    artists = item.get("artists")
    if isinstance(artists, list):
        names = [
            str(a.get("name", "")).strip()
            for a in artists
            if isinstance(a, dict) and a.get("name")
        ]
        if names:
            return names
    author = item.get("author")
    if isinstance(author, str) and author.strip():
        return [author.strip()]
    return []


def generate_queries(artist: str, title: str) -> list[str]:
    clean_artist = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", artist)
    clean_artist = re.sub(r"\s+", " ", clean_artist).strip(" -–—:/")

    raw_title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)
    raw_title = re.sub(r"\s+", " ", raw_title).strip(" -–—:/")

    stripped_title = FORMAT_SUFFIX_RE.sub(" ", raw_title)
    stripped_title = re.sub(r"\s+", " ", stripped_title).strip(" -–—:/.,")

    candidates: list[str] = []
    for t in (stripped_title, raw_title):
        if not t:
            continue
        candidates.append(f"{clean_artist} {t}".strip())

        if "." in t:
            no_spaces = re.sub(r"\s+", "", t)
            dotless = re.sub(r"\.+", "", t).strip()
            spaced = re.sub(
                r"\b([a-zA-Z0-9])\.(?=(?:[a-zA-Z0-9]\.){1,}[a-zA-Z0-9]?)",
                r"\1 ",
                t,
            )
            spaced = re.sub(r"\.", "", spaced)
            spaced = re.sub(r"\s+", " ", spaced).strip()
            for v in (no_spaces, dotless, spaced):
                if v:
                    candidates.append(f"{clean_artist} {v}".strip())

    tokens = normalize_tokens(raw_title)
    if tokens:
        candidates.append(f"{clean_artist} {' '.join(sorted(tokens))}".strip())

    out: list[str] = []
    seen: set[str] = set()
    for q in candidates:
        k = q.lower()
        if q and k not in seen:
            seen.add(k)
            out.append(q)
    return out


def score_candidate(
    artist: str,
    title: str,
    item: dict[str, Any],
    kind: str,
) -> float:
    artist_compact = normalize_compact(artist)
    artist_tokens = normalize_tokens(artist)
    title_tokens = normalize_tokens(title)
    title_compact = normalize_compact(title)

    cand_title = str(item.get("title") or "").strip()
    cand_title_compact = normalize_compact(cand_title)
    cand_title_tokens = normalize_tokens(cand_title)

    cand_artists = extract_artists(item)
    cand_artists_compact = [normalize_compact(a) for a in cand_artists if a]
    cand_artists_tokens: set[str] = set()
    for a in cand_artists:
        cand_artists_tokens |= normalize_tokens(a)

    score = 0.0

    exact_artist = bool(artist_compact) and any(
        artist_compact == ca or artist_compact in ca or ca in artist_compact
        for ca in cand_artists_compact
        if len(ca) >= 2
    )
    artist_in_title = (
        bool(artist_compact)
        and len(artist_compact) >= 3
        and artist_compact in cand_title_compact
    )
    token_artist = bool(artist_tokens) and len(artist_tokens & cand_artists_tokens) >= max(
        1, len(artist_tokens)
    )

    if exact_artist:
        score += 5.0
    elif token_artist:
        score += 3.5
    elif artist_in_title:
        score += 3.0
    else:
        score -= 5.0

    overlap = title_tokens & cand_title_tokens
    compact_match = (
        bool(title_compact)
        and len(title_compact) >= 2
        and (title_compact in cand_title_compact or cand_title_compact in title_compact)
    )

    if title_tokens:
        if overlap:
            score += (len(overlap) / len(title_tokens)) * 4.0
        elif compact_match:
            score += 3.5
        else:
            score -= 1.5
    elif compact_match or exact_artist or token_artist or artist_in_title:
        score += 1.5

    if kind == "album":
        score += 1.5
    elif kind in ("song", "video") and item.get("videoId"):
        score += 0.5
    elif kind == "playlist":
        score -= 4.0

    return score


def reset_release_match(rel: dict[str, Any]) -> None:
    """Clear prior YTM match metadata and expanded tracks before full re-expansion."""
    rel["browse_id"] = None
    rel["video_id"] = None
    rel["ytm_type"] = None
    rel["ytm_matched_title"] = None
    rel.pop("tracks", None)


def fetch_tracks_for_collection(
    ytm: YTMusic,
    browse_id: str,
    fallback_artist: str,
    fallback_title: str,
    use_playlist_api: bool,
) -> tuple[str | None, list[dict[str, str]]]:
    if use_playlist_api or str(browse_id).startswith("OLAK5uy_"):
        data = call_with_retry(
            lambda bid=browse_id: ytm.get_playlist(bid, limit=None),
            label=f"playlist:{browse_id}",
        )
    else:
        data = call_with_retry(
            lambda bid=browse_id: ytm.get_album(bid),
            label=f"album:{browse_id}",
        )

    matched_title = str(data.get("title") or "").strip() or None
    tracks_out: list[dict[str, str]] = []
    for tr in data.get("tracks") or []:
        if not isinstance(tr, dict):
            continue
        vid = str(tr.get("videoId") or "").strip()
        if not vid:
            continue
        tr_artists = extract_artists(tr)
        tracks_out.append(
            {
                "artist": ", ".join(tr_artists) if tr_artists else fallback_artist,
                "title": str(tr.get("title") or fallback_title).strip(),
                "url": f"https://music.youtube.com/watch?v={vid}",
            }
        )
    return matched_title, tracks_out


def resolve_release(
    ytm: YTMusic,
    rel: dict[str, Any],
    *,
    min_score: float = 3.0,
    delay: float = 0.6,
) -> bool:
    """Search YTM with candidate scoring and populate verified IDs and tracks."""
    artist = str(rel.get("artist") or "").strip()
    title = str(rel.get("title") or "").strip()
    if not artist or not title:
        return False

    best: tuple[float, str, str, dict[str, Any], bool] | None = None
    seen: set[str] = set()

    passes: list[tuple[str | None, str]] = [
        ("albums", "album"),
        (None, "general"),
        ("songs", "song"),
        ("videos", "video"),
    ]

    for query in generate_queries(artist, title):
        for filter_arg, mode in passes:
            results = call_with_retry(
                lambda q=query, flt=filter_arg: (
                    ytm.search(q, filter=flt, limit=5) if flt else ytm.search(q, limit=5)
                ),
                label=f"search:{query}:{filter_arg or 'all'}",
            )
            if not isinstance(results, list):
                continue

            for item in results:
                if not isinstance(item, dict):
                    continue

                pid = item.get("playlistId")
                bid = item.get("browseId")
                vid = item.get("videoId")
                rtype = str(item.get("resultType") or "").lower()

                is_olak = any(
                    isinstance(x, str) and x.startswith("OLAK5uy_") for x in (pid, bid)
                )
                if is_olak:
                    kind = "album"
                    item_id = str(
                        pid
                        if isinstance(pid, str) and pid.startswith("OLAK5uy_")
                        else bid
                    )
                    item = {**item, "browseId": item_id}
                    use_playlist_api = True
                elif mode == "album" or rtype == "album":
                    kind = "album"
                    item_id = str(bid or pid or "")
                    use_playlist_api = str(item_id).startswith("OLAK5uy_")
                elif rtype == "playlist":
                    kind = "playlist"
                    item_id = str(pid or bid or "")
                    use_playlist_api = True
                elif rtype in ("song", "video") or mode in ("song", "video"):
                    kind = "song" if (rtype == "song" or mode == "song") else "video"
                    item_id = str(vid or "")
                    use_playlist_api = False
                else:
                    continue

                if not item_id:
                    continue
                dedup_key = f"{kind}:{item_id}"
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                s = score_candidate(artist, title, item, kind)
                if best is None or s > best[0]:
                    best = (s, kind, item_id, item, use_playlist_api)

        time.sleep(max(0.0, delay))
        if best and best[0] >= 6.0 and best[1] == "album":
            break

    if not best or best[0] < min_score:
        reset_release_match(rel)
        return False

    score, kind, item_id, item, use_playlist_api = best
    fallback_matched = str(item.get("title") or "").strip() or None

    if kind in ("album", "playlist"):
        matched_title, tracks_out = fetch_tracks_for_collection(
            ytm,
            item_id,
            artist,
            title,
            use_playlist_api=use_playlist_api,
        )
        if not tracks_out:
            reset_release_match(rel)
            return False

        rel["browse_id"] = item_id
        rel["video_id"] = None
        rel["ytm_type"] = kind
        rel["ytm_matched_title"] = matched_title or fallback_matched
        rel["tracks"] = tracks_out
        logger.info(
            "Resolved '%s - %s' -> %s (score=%.1f, tracks=%d)",
            artist,
            title,
            rel["ytm_matched_title"],
            score,
            len(tracks_out),
        )
        return True

    vid = str(item.get("videoId") or "").strip()
    if not vid:
        reset_release_match(rel)
        return False

    tr_artists = extract_artists(item)
    rel["browse_id"] = None
    rel["video_id"] = vid
    rel["ytm_type"] = kind
    rel["ytm_matched_title"] = fallback_matched
    rel["tracks"] = [
        {
            "artist": ", ".join(tr_artists) if tr_artists else artist,
            "title": fallback_matched or title,
            "url": f"https://music.youtube.com/watch?v={vid}",
        }
    ]
    logger.info(
        "Resolved '%s - %s' -> %s (score=%.1f, 1 track)",
        artist,
        title,
        rel["ytm_matched_title"],
        score,
    )
    return True


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
        "--min-score",
        type=float,
        default=3.0,
        help="Minimum candidate match score required to accept a match (default: 3.0).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.6,
        help="Delay in seconds between query variant batches (default: 0.6).",
    )
    parser.add_argument(
        "--auth",
        type=str,
        default=None,
        help="Optional path to ytmusicapi auth JSON.",
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
    ytm = YTMusic(args.auth) if args.auth else YTMusic()

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
                ok = resolve_release(
                    ytm,
                    rel,
                    min_score=args.min_score,
                    delay=args.delay,
                )
                if ok:
                    expanded_count += 1
                    changed = True
                    save_releases_store(RELEASES_FILE, store)
                    time.sleep(0.8)
            except Exception as err:
                logger.warning(
                    "Failed expanding '%s - %s': %s",
                    rel.get("artist"),
                    rel.get("title"),
                    err,
                )

    if changed:
        save_releases_store(RELEASES_FILE, store)
    logger.info("Expansion run finished. Releases expanded: %d", expanded_count)


if __name__ == "__main__":
    main()
