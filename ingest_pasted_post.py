import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

RELEASES_FILE = Path("releases.json")


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

    for raw_line in normalized.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line.startswith(("http://", "https://")):
            continue

        if line.lower() in {
            "upvote", "downvote", "go to comments", "share", "locked post",
            "stickied post", "archived post", "open", "sort by:", "best", "top", "new"
        }:
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

        if 1 <= len(artist) <= 100 and 1 <= len(title) <= 160:
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
    post_title = os.environ.get("POST_TITLE", "").strip()
    post_url = os.environ.get("POST_URL", "https://www.reddit.com/user/sepdri/").strip() or "https://www.reddit.com/user/sepdri/"
    raw_text = os.environ.get("RAW_POST_TEXT", "")

    if not post_title:
        raise RuntimeError("Missing POST_TITLE input.")

    new_releases = parse_releases(raw_text)
    if not new_releases:
        raise RuntimeError("No valid 'Artist - Release' entries found in RAW_POST_TEXT.")

    store = (
        json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
        if RELEASES_FILE.exists()
        else {"source_user": "sepdri", "subreddit": "Hardcore", "posts": []}
    )
    posts = store.get("posts", [])

    target_post = None
    for p in posts:
        if str(p.get("post_title", "")).strip().lower() == post_title.lower():
            target_post = p
            break

    if target_post is None:
        target_post = {
            "post_title": post_title,
            "post_url": post_url,
            "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "releases": [],
        }
        posts.insert(0, target_post)
    else:
        if post_url and post_url != "https://www.reddit.com/user/sepdri/":
            target_post["post_url"] = post_url

    existing_keys = {
        f"{str(r.get('artist', '')).strip().lower()}::{str(r.get('title', '')).strip().lower()}"
        for r in target_post.get("releases", [])
    }

    added_count = 0
    for rel in new_releases:
        key = f"{rel['artist'].lower()}::{rel['title'].lower()}"
        if key not in existing_keys:
            target_post["releases"].append(rel)
            existing_keys.add(key)
            added_count += 1

    store["posts"] = posts
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    RELEASES_FILE.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Merged {added_count} new release(s) into '{post_title}' (total in post: {len(target_post['releases'])}).")


if __name__ == "__main__":
    main()
