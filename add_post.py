import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RELEASES_FILE = Path("releases.json")


def parse_lines(raw_text: str) -> list[dict]:
    items = []
    seen = set()

    # If lines lack newlines (e.g., flattened copy-paste), split before URLs or bullet markers
    normalized_text = re.sub(r"(https?://\S+)", r"\n\1\n", raw_text)

    for raw_line in normalized_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line.startswith(("http://", "https://")):
            continue

        # Skip Reddit UI boilerplate lines if copied from a webpage
        if line.lower() in {
            "upvote", "downvote", "go to comments", "share", "locked post",
            "stickied post", "archived post", "open", "sort by:", "best", "top", "new"
        }:
            continue

        cleaned = re.sub(r"^[-*•\d\.\)\]]+\s*", "", line).strip()
        cleaned = re.sub(r"\s*https?://\S+", "", cleaned).strip()
        if not cleaned:
            continue

        # Capture bracketed [location/notes] or parenthetical (notes)
        notes_parts = []
        for match in re.findall(r"\[([^\]]+)\]|\(([^)]+)\)", cleaned):
            note = (match[0] or match[1]).strip()
            if note and not note.lower().startswith("http"):
                notes_parts.append(note)
        notes = " | ".join(notes_parts)

        candidate = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", cleaned)
        candidate = re.sub(r"\s+", " ", candidate).strip(" -–—•*")

        # Split on the first dash separator to preserve titles/labels containing extra hyphens
        parts = re.split(r"\s+[-–—]+\s+", candidate, maxsplit=1)
        if len(parts) == 2:
            artist, title = parts[0].strip(), parts[1].strip()
        else:
            # Fallback for entries with compact "Artist - Title" spacing
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
    if len(sys.argv) < 3:
        print('Usage: python add_post.py <paste.txt> "<Post Title>" ["<Reddit Thread URL>"]')
        sys.exit(1)

    source_path = Path(sys.argv[1])
    post_title = sys.argv[2].strip()
    post_url = sys.argv[3].strip() if len(sys.argv) > 3 else "https://www.reddit.com/user/sepdri/"

    releases = parse_lines(source_path.read_text(encoding="utf-8"))
    store = (
        json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
        if RELEASES_FILE.exists()
        else {"source_user": "sepdri", "subreddit": "Hardcore", "posts": []}
    )

    # Replace existing post with the same title so re-ingesting updates it cleanly instead of skipping
    store["posts"] = [
        p for p in store.get("posts", [])
        if str(p.get("post_title", "")).strip().lower() != post_title.lower()
    ]

    store["posts"].insert(0, {
        "post_title": post_title,
        "post_url": post_url,
        "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "releases": releases,
    })
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    RELEASES_FILE.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(releases)} releases under '{post_title}' into {RELEASES_FILE}.")


if __name__ == "__main__":
    main()
