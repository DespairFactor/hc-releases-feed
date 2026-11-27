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

FORMAT_SUFFIX_RE = re.compile(
    r"\b(?:lp|ep|demo|promo|split|single|full\s*stream|album)\b",
    flags=re.IGNORECASE,
)


def normalize_tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower())
    return {tok for tok in cleaned.split() if len(tok) > 1 and tok not in STOPWORDS}


def normalize_compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def extract_result_artists(item: dict[str, Any]) -> list[str]:
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


def generate_query_variants(artist: str, title: str) -> list[str]:
    artist_clean = str(artist or "").strip()
    title_raw = str(title or "").strip()
    title_stripped = FORMAT_SUFFIX_RE.sub(" ", title_raw)
    title_stripped = re.sub(r"\s+", " ", title_stripped).strip(" -–—/.,")

    candidates: list[str] = []
    for t in (title_stripped, title_raw):
        if not t:
            continue
        candidates.append(f"{artist_clean} {t}".strip())

        if "." in t:
            no_spaces = re.sub(r"\s+", "", t)
            collapsed = re.sub(r"\.+", "", t).strip()
            spaced_letters = re.sub(
                r"\b([a-zA-Z0-9])\.(?=(?:[a-zA-Z0-9]\.){1,}[a-zA-Z0-9]?)",
                r"\1 ",
                t,
            )
            spaced_letters = re.sub(r"\.", "", spaced_letters)
            spaced_letters = re.sub(r"\s+", " ", spaced_letters).strip()

            for variant in (no_spaces, collapsed, spaced_letters):
                if variant:
                    candidates.append(f"{artist_clean} {variant}".strip())

    clean_tokens = normalize_tokens(title_raw)
    if clean_tokens:
        candidates.append(f"{artist_clean} {' '.join(sorted(clean_tokens))}".strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for q in candidates:
        key = q.lower()
        if q and key not in seen:
            seen.add(key)
            deduped.append(q)
    return deduped


def score_candidate(
    release_artist: str,
    release_title: str,
    item: dict[str, Any],
    result_type: str,
) -> float:
    artist_compact = normalize_compact(release_artist)
    artist_tokens = normalize_tokens(release_artist)
    title_tokens = normalize_tokens(release_title)
    title_compact = normalize_compact(release_title)

    cand_title = str(item.get("title") or "").strip()
    cand_title_compact = normalize_compact(cand_title)
    cand_title_tokens = normalize_tokens(cand_title)

    cand_artists = extract_result_artists(item)
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
    token_artist_match = bool(artist_tokens) and len(artist_tokens & cand_artists_tokens) >= max(
        1, len(artist_tokens)
    )

    if exact_artist:
        score += 5.0
    elif token_artist_match:
        score += 3.5
    elif artist_in_title:
        score += 3.0
    else:
        score -= 5.0

    overlap = title_tokens & cand_title_tokens
    compact_title_match = (
        bool(title_compact)
        and len(title_compact) >= 2
        and (
            title_compact in cand_title_compact
            or cand_title_compact in title_compact
        )
    )

    if title_tokens:
        if overlap:
            score += (len(overlap) / len(title_tokens)) * 4.0
        elif compact_title_match:
            score += 3.5
        else:
            score -= 1.5
    elif compact_title_match or exact_artist or token_artist_match or artist_in_title:
        score += 1.5

    if result_type == "album":
        score += 1.5
    elif result_type == "song" and item.get("videoId"):
        score += 0.5
    elif result_type == "video" and item.get("videoId"):
        lower_t = cand_title.lower()
        if any(k in lower_t for k in ("full", "ep", "lp", "demo", "promo")):
            score += 1.0
    elif result_type == "playlist":
        score -= 4.0

    return score


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
            artist_compact in ta or ta in artist_compact
            for ta in track_artists_compact
            if len(ta) >= 2
        ):
            artist_ok = True
    else:
        artist_ok = True

    if not artist_ok:
        return True

    title_tokens = normalize_tokens(title)
    matched_tokens = normalize_tokens(matched_title)
    title_compact = normalize_compact(title)
    if title_tokens and matched_tokens and not (title_tokens & matched_tokens):
        compact_ok = bool(title_compact) and (
            title_compact in matched_compact or matched_compact in title_compact
        )
        if not compact_ok and not (
            ytm_type == "video" and artist_compact and artist_compact in matched_compact
        ):
            return True

    if ytm_type == "album" and not track_list:
        return True

    return False


def clear_match_fields(rel: dict[str, Any]) -> None:
    rel["browse_id"] = None
    rel["video_id"] = None
    rel["ytm_type"] = None
    rel["ytm_matched_title"] = None
    rel.pop("tracks", None)


def fetch_playlist_tracks(
    ytm: YTMusic,
    playlist_id: str,
    fallback_artist: str,
) -> list[dict[str, str]]:
    tracks: list[dict[str, str]] = []
    try:
        pl_data = ytm.get_playlist(playlist_id, limit=None)
    except Exception:
        return tracks

    for tr in pl_data.get("tracks") or []:
        if not isinstance(tr, dict):
            continue
        vid = tr.get("videoId")
        if not vid:
            continue
        tr_artists = extract_result_artists(tr)
        tracks.append(
            {
                "artist": ", ".join(tr_artists) if tr_artists else fallback_artist,
                "title": str(tr.get("title") or "").strip(),
                "url": f"https://music.youtube.com/watch?v={vid}",
            }
        )
    return tracks


def fetch_album_tracks(
    ytm: YTMusic,
    browse_id: str,
    fallback_artist: str,
) -> list[dict[str, str]]:
    if str(browse_id).startswith("OLAK5uy_"):
        return fetch_playlist_tracks(ytm, browse_id, fallback_artist)

    tracks: list[dict[str, str]] = []
    try:
        album_data = ytm.get_album(browse_id)
    except Exception:
        return tracks

    for tr in album_data.get("tracks") or []:
        if not isinstance(tr, dict):
            continue
        vid = tr.get("videoId")
        if not vid:
            continue
        tr_artists = extract_result_artists(tr)
        tracks.append(
            {
                "artist": ", ".join(tr_artists) if tr_artists else fallback_artist,
                "title": str(tr.get("title") or "").strip(),
                "url": f"https://music.youtube.com/watch?v={vid}",
            }
        )
    return tracks


def resolve_release(
    ytm: YTMusic,
    artist: str,
    title: str,
    min_score: float = 3.0,
) -> dict[str, Any] | None:
    best: tuple[float, str, str, dict[str, Any]] | None = None
    seen_ids: set[str] = set()

    search_passes: list[tuple[str | None, str]] = [
        ("albums", "album"),
        (None, "general"),
        ("playlists", "playlist"),
        ("songs", "song"),
        ("videos", "video"),
    ]

    for q in generate_query_variants(artist, title):
        for filter_type, fallback_kind in search_passes:
            try:
                results = (
                    ytm.search(q, filter=filter_type, limit=5)
                    if filter_type
                    else ytm.search(q, limit=5)
                )
            except Exception:
                continue
            if not isinstance(results, list):
                continue

            for item in results:
                if not isinstance(item, dict):
                    continue

                raw_pid = item.get("playlistId")
                raw_bid = item.get("browseId")
                raw_vid = item.get("videoId")
                raw_type = str(item.get("resultType") or "").lower()

                is_olak = any(
                    isinstance(x, str) and x.startswith("OLAK5uy_")
                    for x in (raw_pid, raw_bid)
                )

                if is_olak:
                    resolved_type = "album"
                    album_id = str(
                        raw_pid
                        if isinstance(raw_pid, str) and raw_pid.startswith("OLAK5uy_")
                        else raw_bid
                    )
                    item = {**item, "browseId": album_id}
                    item_id = album_id
                elif fallback_kind == "album" or raw_type == "album":
                    resolved_type = "album"
                    item_id = str(raw_bid or raw_pid or "")
                elif fallback_kind in ("playlist", "general") and raw_type == "playlist":
                    resolved_type = "playlist"
                    item_id = str(raw_pid or raw_bid or "")
                elif raw_type == "song" or fallback_kind == "song":
                    resolved_type = "song"
                    item_id = str(raw_vid or "")
                elif raw_type == "video" or fallback_kind == "video":
                    resolved_type = "video"
                    item_id = str(raw_vid or "")
                else:
                    continue

                if not item_id:
                    continue

                dedup_key = f"{resolved_type}:{item_id}"
                if dedup_key in seen_ids:
                    continue
                seen_ids.add(dedup_key)

                s = score_candidate(artist, title, item, resolved_type)
                if best is None or s > best[0]:
                    best = (s, resolved_type, item_id, item)

        if best and best[0] >= 6.0 and best[1] == "album":
            break

    if not best or best[0] < min_score:
        return None

    score, ytm_type, item_id, item = best
    matched_title = str(item.get("title") or "").strip()

    if ytm_type == "album":
        tracks = fetch_album_tracks(ytm, item_id, artist)
        return {
            "browse_id": item_id,
            "video_id": None,
            "ytm_type": "album",
            "ytm_matched_title": matched_title,
            "tracks": tracks,
            "_score": score,
        }

    if ytm_type == "playlist":
        tracks = fetch_playlist_tracks(ytm, item_id, artist)
        return {
            "browse_id": item_id,
            "video_id": None,
            "ytm_type": "playlist",
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
                "title": (
                    f"{artist} - {title}".strip(" -")
                    if ytm_type == "video" and len(title.split()) <= 3
                    else matched_title
                ),
                "url": f"https://music.youtube.com/watch?v={vid}",
            }
        ],
        "_score": score,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit and re-resolve YTM matches for a post in releases.json."
    )
    parser.add_argument("--file", default="releases.json", help="Path to releases.json")
    parser.add_argument("--post-idx", type=int, default=0, help="Post index to audit")
    parser.add_argument("--auth", default=None, help="Path to ytmusicapi auth JSON")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between lookups")
    parser.add_argument("--min-score", type=float, default=3.0, help="Minimum score")
    parser.add_argument("--apply", action="store_true", help="Write updates to releases.json")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    path = Path(args.file)
    data = json.loads(path.read_text(encoding="utf-8"))
    posts = data.get("posts", [])
    if not isinstance(posts, list) or not posts:
        raise SystemExit("No posts found in releases.json")
    if args.post_idx < -len(posts) or args.post_idx >= len(posts):
        raise SystemExit(f"post_idx {args.post_idx} out of range")

    post = posts[args.post_idx]
    releases = post.get("releases", [])
    print(
        f"Auditing post[{args.post_idx}]: '{post.get('post_title')}' ({len(releases)} releases)",
        flush=True,
    )

    suspect_indices = [
        idx
        for idx, rel in enumerate(releases)
        if isinstance(rel, dict) and current_match_is_suspect(rel)
    ]
    print(f"Found {len(suspect_indices)} suspect/unexpanded entries.\n", flush=True)

    ytm = YTMusic(args.auth) if args.auth else YTMusic()
    updated_count = 0
    cleared_count = 0
    post_changed = False

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
                f"       -> NEW ({candidate['ytm_type']}, score={score:.1f}): "
                f"{candidate['ytm_matched_title']!r} ({len(candidate['tracks'])} tracks)",
                flush=True,
            )
            if args.apply and not args.dry_run:
                rel.update(candidate)
                updated_count += 1
                post_changed = True
        else:
            print(
                f"  [{idx}] {artist} — {title} (was {old_matched!r})\n"
                f"       -> NO CONFIDENT MATCH (clearing to null)",
                flush=True,
            )
            if args.apply and not args.dry_run:
                clear_match_fields(rel)
                cleared_count += 1
                post_changed = True

        time.sleep(max(0.0, args.delay))

    if args.apply and not args.dry_run and post_changed:
        post.pop("playlist_id", None)
        post.pop("playlist_url", None)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(
            f"\nSaved {path}: updated={updated_count}, cleared={cleared_count}, "
            f"playlist metadata cleared for re-publish.",
            flush=True,
        )
    elif args.dry_run:
        print("\nDry run complete: no changes written.", flush=True)
    else:
        print("\nAudit complete (pass --apply to write changes).", flush=True)


if __name__ == "__main__":
    main()
