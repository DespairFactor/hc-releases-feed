import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASES_FILE = REPO_ROOT / "releases.json"


def parse_releases(raw_text: str) -> list[dict]:
    if not raw_text:
        return []

    with_breaks = re.sub(
        r"<(?:/?(?:p|div|br|li|tr|h[1-6]|ul|ol))[^>]*>",
        "\n",
        raw_text,
        flags=re.IGNORECASE,
    )
    plain = html.unescape(re.sub(r"<[^>]+>", " ", with_breaks))
    normalized = re.sub(r"(https?://\S+)", r"\n\1\n", plain)

    items = []
    seen = set()

    ui_noise = {
        "upvote",
        "downvote",
        "go to comments",
        "share",
        "locked post",
        "stickied post",
        "archived post",
        "open",
        "sort by:",
        "best",
        "top",
        "new",
    }

    for raw_line in normalized.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line.startswith(("http://", "https://")):
            continue
        if line.lower() in ui_noise:
            continue

        cleaned = re.sub(r"^[-*•\d\.\)\]]+\s*", "", line).strip()
        cleaned = re.sub(r"\s*https?://\S+", "", cleaned).strip()
        if not cleaned:
            continue

        notes_parts = []
        for match in re.findall(r"\[([^\]]+)\]|\(([^)]+)\)", cleaned):
            note = (match[0] or match[1]).strip()
            if note and not note.lower().startswith("http"):
                notes_parts.append(note)
        notes = " | ".join(notes_parts)

        candidate = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", cleaned)
        candidate = re.sub(r"\s+", " ", candidate).strip(" -–—•*")

        parts = re.split(r"\s+[-–—]+\s+", candidate, maxsplit=1)
        if len(parts) == 2:
            artist, title = parts[0].strip(), parts[1].strip()
        else:
            compact = re.split(r"\s*[-–—]\s*", candidate, maxsplit=1)
            if len(compact) != 2:
                continue
            artist, title = compact[0].strip(), compact[1].strip()

        if 1 <= len(artist) <= 100 and 1 <= len(title) <= 180:
            key = f"{artist.lower()}::{title.lower()}"
            if key not in seen:
                seen.add(key)
                items.append({
                    "artist": artist,
                    "title": title,
                    "notes": notes,
                })

    return items


def main() -> None:
    post_title = (os.environ.get("ISSUE_TITLE") or "").strip()
    post_body = os.environ.get("ISSUE_BODY") or ""
    post_url = (os.environ.get("ISSUE_URL") or "https://www.reddit.com/user/sepdri/").strip()

    parsed_releases = parse_releases(post_body)
    if not parsed_releases:
        raise SystemExit("No 'Artist - Release' entries found in the issue body.")

    if RELEASES_FILE.exists():
        store = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    else:
        store = {
            "source_user": "sepdri",
            "subreddit": "Hardcore",
            "posts": [],
        }

    posts = store.setdefault("posts", [])
    resolved_title = post_title or f"Roundup ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})"

    target_post = next(
        (p for p in posts if str(p.get("post_title", "")).strip().lower() == resolved_title.lower()),
        None,
    )

    if target_post is None:
        target_post = {
            "post_title": resolved_title,
            "post_url": post_url,
            "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "releases": [],
        }
        posts.insert(0, target_post)

    existing_keys = {
        f"{str(r.get('artist', '')).strip().lower()}::{str(r.get('title', '')).strip().lower()}"
        for r in target_post.get("releases", [])
    }

    added = 0
    for rel in parsed_releases:
        key = f"{rel['artist'].lower()}::{rel['title'].lower()}"
        if key not in existing_keys:
            target_post["releases"].append(rel)
            existing_keys.add(key)
            added += 1

    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    RELEASES_FILE.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Merged '{resolved_title}': +{added} new release(s) added.")


if __name__ == "__main__":
    main()
