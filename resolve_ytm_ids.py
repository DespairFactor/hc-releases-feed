#!/usr/bin/env python3
"""Resolve YouTube Music releases, expand tracks, and filter out mismatches.

Supports official albums/EPs (MPREb_ and OLAK5uy_ album playlists),
acronym query variants (e.g., S.N.L.M.), artist/title candidate scoring,
and tracklist expansion into releases.json.
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
logger = logging.getLogger("ytm_resolver")


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


def fetch_tracks(
    ytm: YTMusic,
    ytm_type: str,
    item_id: str,
    fallback_artist: str,
    fallback_title: str,
    matched_title: str,
    video_id: str | None,
) -> list[dict[str, str]]:
    if ytm_type in ("album", "playlist") and item_id:
        try:
            if str(item_id).startswith("OLAK5uy_") or ytm_type == "playlist":
                data = ytm.get_playlist(item_id, limit=None)
            else:
                data = ytm.get_album(item_id)
            tracks: list[dict[str, str]] = []
            for tr in data.get("tracks") or []:
                if not isinstance(tr, dict):
                    continue
                vid = tr.get("videoId")
                if not vid:
                    continue
                tr_artists = extract_artists(tr)
                tracks.append({
                    "artist": ", ".join(tr_artists) if tr_artists else fallback_artist,
                    "title": str(tr.get("title") or "").strip(),
                    "url": f"https://music.youtube.com/watch?v={vid}",
                })
            if tracks:
                return tracks
        except Exception as err:
            logger.debug("Track expansion failed for %s: %s", item_id, err)

    if video_id:
        return [{
            "artist": fallback_artist,
            "title": matched_title or fallback_title,
            "url": f"https://music.youtube.com/watch?v={video_id}",
        }]

    return []


def resolve_one(
    ytm: YTMusic,
    artist: str,
    title: str,
    min_score: float,
    delay: float,
) -> dict[str, Any] | None:
    best: tuple[float, str, str, dict[str, Any]] | None = None
    seen: set[str] = set()

    passes: list[tuple[str | None, str]] = [
        ("albums", "album"),
        (None, "general"),
        ("songs", "song"),
        ("videos", "video"),
    ]

    for q in generate_queries(artist, title):
        for filter_arg, mode in passes:
            try:
                raw = ytm.search(q, filter=filter_arg, limit=5) if filter_arg else ytm.search(q, limit=5)
            except Exception:
                continue
            if not isinstance(raw, list):
                continue

            for item in raw:
                if not isinstance(item, dict):
                    continue

                pid = item.get("playlistId")
                bid = item.get("browseId")
                vid = item.get("videoId")
                rtype = str(item.get("resultType") or "").lower()

                is_olak = any(isinstance(x, str) and x.startswith("OLAK5uy_") for x in (pid, bid))
                if is_olak:
                    kind = "album"
                    item_id = str(pid if isinstance(pid, str) and pid.startswith("OLAK5uy_") else bid)
                    item = {**item, "browseId": item_id}
                elif mode == "album" or rtype == "album":
                    kind = "album"
                    item_id = str(bid or pid or "")
                elif rtype == "playlist":
                    kind = "playlist"
                    item_id = str(pid or bid or "")
                elif rtype in ("song", "video") or mode in ("song", "video"):
                    kind = "song" if (rtype == "song" or mode == "song") else "video"
                    item_id = str(vid or "")
                else:
                    continue

                if not item_id:
                    continue
                key = f"{kind}:{item_id}"
                if key in seen:
                    continue
                seen.add(key)

                s = score_candidate(artist, title, item, kind)
                if best is None or s > best[0]:
                    best = (s, kind, item_id, item)

        time.sleep(delay)
        if best and best[0] >= 6.0 and best[1] == "album":
            break

    if not best or best[0] < min_score:
        return None

    score, kind, item_id, item = best
    matched_title = str(item.get("title") or "").strip()

    if kind in ("album", "playlist"):
        browse_id = item_id
        video_id = None
    else:
        browse_id = None
        video_id = str(item.get("videoId") or "")
        if not video_id:
            return None

    tracks = fetch_tracks(ytm, kind, browse_id or "", artist, title, matched_title, video_id)
    return {
        "browse_id": browse_id,
        "video_id": video_id,
        "ytm_type": kind,
        "ytm_matched_title": matched_title,
        "tracks": tracks,
        "_score": score,
    }


def load_cache() -> dict[str, Any]:
    if not CACHE_FILE.exists():
        return {}
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        merged: dict[str, Any] = {}
        if isinstance(raw.get("entries"), dict):
            merged.update(raw["entries"])
        for k, v in raw.items():
            if k != "entries":
                merged[k] = v
        return merged
    except Exception:
        return {}


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_FILE.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve and expand YTM releases in releases.json."
    )
    parser.add_argument("--max-lookups", type=int, default=50, help="Max uncached lookups per run.")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay in seconds between queries.")
    parser.add_argument("--min-score", type=float, default=3.0, help="Minimum candidate score.")
    parser.add_argument("--post-idx", type=int, default=None, help="Optional single post index to process.")
    parser.add_argument("--auth", default=None, help="Optional ytmusicapi auth JSON path.")
    parser.add_argument("--force", action="store_true", help="Re-resolve even if already resolved.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not RELEASES_FILE.exists():
        logger.error("Missing %s", RELEASES_FILE)
        sys.exit(1)

    store = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    posts = store.get("posts", [])
    if not isinstance(posts, list):
        logger.error("'posts' must be a list.")
        sys.exit(1)

    target_posts = [posts[args.post_idx]] if args.post_idx is not None else posts
    cache = load_cache()
    ytm = YTMusic(args.auth) if args.auth else YTMusic()

    lookups = 0
    resolved = 0
    cache_hits = 0

    for post in target_posts:
        for rel in post.get("releases", []):
            if not isinstance(rel, dict):
                continue
            artist = str(rel.get("artist") or "").strip()
            title = str(rel.get("title") or "").strip()
            if not artist or not title:
                continue

            has_match = bool(rel.get("browse_id") or rel.get("video_id")) and bool(rel.get("tracks"))
            if has_match and not args.force:
                continue

            cache_key = f"{artist.lower()}::{title.lower()}"
            cached = cache.get(cache_key)
            if isinstance(cached, dict) and not args.force:
                c_type = str(cached.get("ytm_type") or cached.get("result_type") or "")
                c_bid = cached.get("browse_id")
                c_vid = cached.get("video_id")
                c_title = str(cached.get("ytm_matched_title") or cached.get("matched_title") or "")
                if c_type != "playlist" and (c_bid or c_vid):
                    rel["browse_id"] = c_bid
                    rel["video_id"] = c_vid
                    rel["ytm_type"] = c_type
                    rel["ytm_matched_title"] = c_title
                    rel["tracks"] = fetch_tracks(
                        ytm, c_type, str(c_bid or ""), artist, title, c_title, c_vid
                    )
                    cache_hits += 1
                    continue

            if lookups >= args.max_lookups:
                logger.info("Reached --max-lookups (%d).", args.max_lookups)
                break

            logger.info("Resolving [%d/%d]: %s — %s", lookups + 1, args.max_lookups, artist, title)
            found = resolve_one(ytm, artist, title, min_score=args.min_score, delay=args.delay)
            lookups += 1

            if found:
                score = found.pop("_score")
                rel.update(found)
                cache[cache_key] = {
                    "browse_id": found["browse_id"],
                    "video_id": found["video_id"],
                    "ytm_type": found["ytm_type"],
                    "ytm_matched_title": found["ytm_matched_title"],
                }
                resolved += 1
                logger.info(
                    "  -> %s (score=%.1f): %s [%d tracks]",
                    found["ytm_type"],
                    score,
                    found["ytm_matched_title"],
                    len(found["tracks"]),
                )
            else:
                rel["browse_id"] = None
                rel["video_id"] = None
                rel["ytm_type"] = None
                rel["ytm_matched_title"] = None
                rel.pop("tracks", None)
                logger.info("  -> No confident match.")

        if lookups >= args.max_lookups:
            break

    save_cache(cache)
    RELEASES_FILE.write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Done. Lookups=%d | Resolved=%d | Cache hits=%d", lookups, resolved, cache_hits)


if __name__ == "__main__":
    main()
