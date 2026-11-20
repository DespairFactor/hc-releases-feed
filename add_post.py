import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RELEASES_FILE = Path("releases.json")


def parse_lines(raw_text: str) -> list[dict]:
    items = []
    seen = set()

    for raw_line in raw_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        cleaned = re.sub(r"^[-*•\d\.\)\]]+\s*", "", line).strip()
        if not cleaned or cleaned.startswith(("http://", "https://")):
            continue

        notes_match = re.search(r"\(([^)]+)\)", cleaned)
        notes = notes_match.group(1).strip() if notes_match else ""

        candidate = re.sub(r"\s*\([^)]*\)\s*", " ", cleaned)
        candidate = re.sub(r"\s+", " ", candidate).strip(" -–—•*")

        parts = re.split(r"\s*[-–—]+\s*", candidate, maxsplit=1)
        if len(parts) == 2:
            artist, title = parts[0].strip(), parts[1].strip()
            if 2 <= len(artist) <= 80 and 2 <= len(title) <= 120:
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

    store["posts"].insert(0, {
        "post_title": post_title,
        "post_url": post_url,
        "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "releases": releases,
    })
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    RELEASES_FILE.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Added {len(releases)} releases under '{post_title}' to {RELEASES_FILE}.")


if __name__ == "__main__":
    main()
