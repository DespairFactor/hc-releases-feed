#!/usr/bin/env python3
"""Audit and re-resolve YouTube Music matches for a post in releases.json."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from ytmusicapi import YTMusic


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


def normalize_tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return {tok for tok in cleaned.split() if len(tok) > 1 and tok not in STOPWORDS}


def normalize_compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def extract_result_artists(item: dict[str, Any]) -> list[str]:
    artists = item.get("artists")
    if isinstance(artists, list):
        names = [str(a.get("name", "")).strip() for a in artists if isinstance(a, dict) and a.get("name")]
        if names:
            return names
    author = item.get("author")
    if isinstance(author, str) and author.strip():
        return [author.strip()]
    return []


def score_candidate(release_artist: str, release_title: str, item: dict[str, Any], result_type: str) -> tuple[float, str]:
    """Return (score, reason) for a YTM search candidate."""
    artist_compact = normalize_compact(release_artist)
    artist_tokens = normalize_tokens(release_artist)
    title_tokens = normalize_tokens(release_title)

    cand_title = str(item.get("title") or "").strip()
    cand_title_compact = normalize_compact(cand_title)
    cand_title_tokens = normalize_tokens(cand_title)

    cand_artists = extract_result_artists(item)
    cand_artists_compact = [normalize_compact(a) for a in cand_artists if a]
    cand_artists_tokens: set[str] = set()
    for a in cand_artists:
        cand_artists_tokens |= normalize_tokens(a)

    score = 0.0
    notes: list[str] = []

    # 1. Artist alignment check
    exact_artist = bool(artist_compact) and any(
        artist_compact == ca or artist_compact in ca or ca in artist_compact
        for ca in cand_artists_compact
        if len(ca) >= 2
    )
    artist_in_title = bool(artist_compact) and len(artist_compact) >= 3 and artist_compact in cand_title_compact
    token_artist_match = bool(artist_tokens) and len(artist_tokens & cand_artists_tokens) >= max(
        1, len(artist_tokens)
    )

    if exact_artist:
        score += 5.0
        notes.append("artist-exact")
    elif token_artist_match:
        score += 3.5
        notes.append("artist-tokens")
    elif artist_in_title:
        score += 3.0
        notes.append("artist-in-title")
    else:
        # Penalize heavily when the release artist isn't present in result artists or title
        score -= 5.0
        notes.append("artist-mismatch")

    # 2. Title / release alignment check
    overlap = title_tokens & cand_title_tokens
    if title_tokens:
        ratio = len(overlap) / len(title_tokens)
        score += ratio * 4.0
        if overlap:
            notes.append(f"title-overlap({','.join(sorted(overlap))})")
        else:
            score -= 1.5
            notes.append("no-title-overlap")
    else:
        # Generic title like "Demo" or "Promo": rely primarily on artist match
        if exact_artist or token_artist_match or artist_in_title:
            score += 1.5
            notes.append("generic-title+artist")

    # 3. Type preferences & penalties
    if result_type == "album" and item.get("browseId"):
        score += 1.5
    elif result_type == "song" and item.get("videoId"):
        score += 0.5
    elif result_type == "video" and item.get("videoId"):
        if "full" in cand_title.lower() or "ep" in cand_title.lower() or "lp" in cand_title.lower() or "demo" in cand_title.lower():
            score += 1.0
    elif result_type == "playlist":
        # User playlists are frequently noisy compilations
        score -= 4.0
        notes.append("playlist-penalty")

    return score, ", ".join(notes) if notes else "no-signals"


def current_match_is_suspect(rel: dict[str, Any]) -> bool:
    matched_title = str(rel.get("ytm_matched_title") or "").strip()
    ytm_type = str(rel.get("ytm_type") or "").strip()
    tracks = rel.get("tracks")

    if not matched_title and not tracks:
        return True
    if ytm_type == "playlist":
        return True

    artist = str(rel.get("artist") or "").strip()
    title = str(rel.get("title") or "").strip()
    artist_compact = normalize_compact(artist)
    matched_compact = normalize_compact(matched_title)

    track_list = tracks if isinstance(tracks, list) else []
    track_artists_compact = [
        normalize_compact(str(t.get("artist", "")))
        for t in track_list
        if isinstance(t, dict) and t.get("artist")
    ]

    artist_ok = False
    if artist_compact:
        if artist_compact in matched_compact:
            artist_ok = True
        elif track_artists_compact and any(
            artist_compact in ta or ta in artist_compact for ta in track_artists_compact if len(ta) >= 2
        ):
            artist_ok = True
    else:
        artist_ok = True

    if not artist_ok:
        return True

    title_tokens = normalize_tokens(title)
    matched_tokens = normalize_tokens(matched_title)
    if title_tokens and matched_tokens and not (title_tokens & matched_tokens):
        if not (ytm_type == "video" and artist_compact and artist_compact in matched_compact):
            return True

    if ytm_type == "album" and not track_list:
        return True

    return False


def resolve_release(ytm: YTMusic, artist: str, title: str, min_score: float = 3.0) -> dict[str, Any] | None:
    """Search YTM across albums, songs, and videos and return a populated release patch or None."""
    queries = [f"{artist} {title}".strip()]
    title_clean_tokens = normalize_tokens(title)
    if title_clean_tokens and len(title_clean_tokens) < len(title.split()):
        queries.append(f"{artist} {' '.join(sorted(title_clean_tokens))}".strip())

    best: tuple[float, str, str, dict[str, Any]] | None = None
    seen_ids: set[str] = set()

    for q in queries:
        for filter_type, ytm_type in [("albums", "album"), ("songs", "song"), ("videos", "video")]:
            try:
                results = ytm.search(q, filter=filter_type, limit=5)
            except Exception:
                continue
            if not isinstance(results, list):
                continue

            for item in results:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("browseId") or item.get("videoId") or "")
                dedup_key = f"{ytm_type}:{item_id}"
                if not item_id or dedup_key in seen_ids:
                    continue
                seen_ids.add(dedup_key)

                s, _ = score_candidate(artist, title, item, ytm_type)
                if best is None or s > best[0]:
                    best = (s, ytm_type, item_id, item)

        # Stop early if we already found a strong album match
        if best and best[0] >= 6.0 and best[1] == "album":
            break

    if not best or best[0] < min_score:
        return None

    score, ytm_type, item_id, item = best
    matched_title = str(item.get("title") or "").strip()

    if ytm_type == "album":
        browse_id = item.get("browseId")
        tracks: list[dict[str, str]] = []
        if browse_id:
            try:
                album_data = ytm.get_album(browse_id)
                for tr in album_data.get("tracks") or []:
                    if not isinstance(tr, dict):
                        continue
                    vid = tr.get("videoId")
                    if not vid:
                        continue
                    tr_artists = extract_result_artists(tr)
                    tracks.append(
                        {
                            "artist": ", ".join(tr_artists) if tr_artists else artist,
                            "title": str(tr.get("title") or "").strip(),
                            "url": f"https://music.youtube.com/watch?v={vid}",
                        }
                    )
            except Exception:
                tracks = []

        return {
            "browse_id": browse_id,
            "video_id": None,
            "ytm_type": "album",
            "ytm_matched_title": matched_title,
            "tracks": tracks,
            "_score": score,
        }

    vid = item.get("videoId")
    if not vid:
        return None

    tr_artists = extract_result_artists(item)
    return {
        "browse_id": None,
        "video_id": vid,
        "ytm_type": ytm_type,
        "ytm_matched_title": matched_title,
        "tracks": [
            {
                "artist": ", ".join(tr_artists) if tr_artists else artist,
                "title": f"{artist} - {title}".strip(" -") if ytm_type == "video" and len(title.split()) <= 3 else matched_title,
                "url": f"https://music.youtube.com/watch?v={vid}",
            }
        ],
        "_score": score,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and re-resolve YTM matches for a post in releases.json.")
    parser.add_argument("--file", default="releases.json", help="Path to releases.json")
    parser.add_argument("--post-idx", type=int, default=0, help="Post index to audit (default: 0)")
    parser.add_argument("--auth", default=None, help="Optional path to ytmusicapi auth JSON")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay in seconds between YTM lookups")
    parser.add_argument("--min-score", type=float, default=3.0, help="Minimum candidate score to accept")
    parser.add_argument("--apply", action="store_true", help="Write resolved replacements back to releases.json")
    parser.add_argument("--dry-run", action="store_true", help="Preview replacements without writing to disk")
    args = parser.parse_args()

    path = Path(args.file)
    data = json.loads(path.read_text(encoding="utf-8"))
    posts = data.get("posts", [])
    if not isinstance(posts, list) or not posts:
        raise SystemExit("No posts found in releases.json")
    if args.post_idx < -len(posts) or args.post_idx >= len(posts):
        raise SystemExit(f"post_idx {args.post_idx} out of range for {len(posts)} posts")

    post = posts[args.post_idx]
    releases = post.get("releases", [])
    print(f"Auditing post[{args.post_idx}]: '{post.get('post_title')}' ({len(releases)} releases)")

    suspect_indices = [
        idx for idx, rel in enumerate(releases) if isinstance(rel, dict) and current_match_is_suspect(rel)
    ]
    print(f"Found {len(suspect_indices)} suspect/unexpanded entries.\n")

    ytm = YTMusic(args.auth) if args.auth else YTMusic()
    updated_count = 0
    cleared_count = 0

    for idx in suspect_indices:
        rel = releases[idx]
        artist = str(rel.get("artist") or "").strip()
        title = str(rel.get("title") or "").strip()
        old_matched = rel.get("ytm_matched_title")

        candidate = resolve_release(ytm, artist, title, min_score=args.min_score)
        if candidate:
            score = candidate.pop("_score")
            print(
                f"  [{idx}] {artist} — {title} (was {old_matched!r})\n"
                f"       -> NEW ({candidate['ytm_type']}, score={score:.1f}): {candidate['ytm_matched_title']!r} "
                f"({len(candidate['tracks'])} tracks)"
            )
            if args.apply and not args.dry_run:
                rel.update(candidate)
                updated_count += 1
        else:
            print(
                f"  [{idx}] {artist} — {title} (was {old_matched!r})\n"
                f"       -> NO CONFIDENT MATCH (resetting to null/unexpanded)"
            )
            if args.apply and not args.dry_run:
                rel["browse_id"] = None
                rel["video_id"] = None
                rel["ytm_type"] = None
                rel["ytm_matched_title"] = None
                rel.pop("tracks", None)
                cleared_count += 1

        time.sleep(max(0.0, args.delay))

    if args.apply and not args.dry_run and (updated_count or cleared_count):
        post.pop("playlist_id", None)
        post.pop("playlist_url", None)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nSaved {path}: updated={updated_count}, cleared={cleared_count}, playlist metadata cleared.")
    elif args.dry_run:
        print("\nDry run complete: no changes written to disk.")
    else:
        print("\nAudit complete (pass --apply to write changes to releases.json).")


if __name__ == "__main__":
    main()
