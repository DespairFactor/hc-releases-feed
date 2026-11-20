import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

INPUT_FILE = Path("releases.json")
OUTPUT_FILE = Path("index.html")


def load_data(path: Path) -> dict:
    if not path.exists():
        return {"source_user": "sepdri", "subreddit": "Hardcore", "posts": []}
    return json.loads(path.read_text(encoding="utf-8"))


def clean_search_query(artist: str, title: str) -> str:
    # Remove parenthetical/bracketed notes and trailing standalone format labels
    core_title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)
    core_title = re.sub(r"\b(LP|EP|Demo|Split|Single|Promo|Reissue)\b\.?$", "", core_title, flags=re.IGNORECASE)
    core_title = re.sub(r"\s+", " ", core_title).strip(" -–—:/")
    core_artist = re.sub(r"\s+", " ", artist).strip(" -–—:/")
    return f"{core_artist} {core_title or title}".strip()


def resolve_links(rel: dict) -> tuple[str, str, bool]:
    """Return (ytm_url, yt_url, is_direct_link)."""
    video_id = str(rel.get("video_id", "")).strip()
    ytm_url = str(rel.get("ytm_url", "")).strip()
    yt_url = str(rel.get("yt_url", "")).strip()

    if video_id:
        ytm_url = ytm_url or f"https://music.youtube.com/watch?v={video_id}"
        yt_url = yt_url or f"https://www.youtube.com/watch?v={video_id}"

    if ytm_url or yt_url:
        q = quote_plus(clean_search_query(str(rel.get("artist", "")), str(rel.get("title", ""))))
        return (
            ytm_url or f"https://music.youtube.com/search?q={q}",
            yt_url or f"https://www.youtube.com/results?search_query={q}",
            True,
        )

    q = quote_plus(clean_search_query(str(rel.get("artist", "")), str(rel.get("title", ""))))
    return (
        f"https://music.youtube.com/search?q={q}",
        f"https://www.youtube.com/results?search_query={q}",
        False,
    )


def build_html(data: dict) -> str:
    user = html.escape(str(data.get("source_user", "sepdri")))
    subreddit = html.escape(str(data.get("subreddit", "Hardcore")))
    posts = data.get("posts", [])
    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_releases = sum(len(p.get("releases", [])) for p in posts)

    sections = []
    for post in posts:
        post_title = html.escape(str(post.get("post_title", "Untitled Roundup")))
        post_url = html.escape(str(post.get("post_url", f"https://www.reddit.com/user/{user}/")))
        added_at = html.escape(str(post.get("added_at", "")))

        cards = []
        for rel in post.get("releases", []):
            artist = str(rel.get("artist", "")).strip()
            title = str(rel.get("title", "")).strip()
            notes = str(rel.get("notes", "")).strip()
            if not artist or not title:
                continue

            ytm_url, yt_url, is_direct = resolve_links(rel)
            search_blob = html.escape(f"{artist} {title} {notes} {post_title}".lower())
            badge = f'<span class="badge">{html.escape(notes)}</span>' if notes else ""
            link_tag = '<span class="badge badge-direct">Direct Link</span>' if is_direct else '<span class="badge badge-search">Search</span>'

            cards.append(f"""
            <div class="card" data-search="{search_blob}">
                <div class="card-info">
                    <span class="artist">{html.escape(artist)}</span>
                    <span class="divider">—</span>
                    <span class="title">{html.escape(title)}</span>
                    {badge}
                    {link_tag}
                </div>
                <div class="card-links">
                    <a href="{html.escape(ytm_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-ytm">YouTube Music</a>
                    <a href="{html.escape(yt_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-yt">YouTube</a>
                </div>
            </div>
            """)

        sections.append(f"""
        <section class="post-group">
            <div class="post-header">
                <h2>{post_title}</h2>
                <div class="post-meta">
                    <span>{added_at}</span>
                    <a href="{post_url}" target="_blank" rel="noopener noreferrer">Reddit Thread ↗</a>
                </div>
            </div>
            <div class="releases-list">
                {"".join(cards)}
            </div>
        </section>
        """)

    main_content = "".join(sections) if sections else """
    <div class="empty-state">
        <h2>No releases tracked yet</h2>
        <p>Add entries to <code>releases.json</code> and push to populate this list.</p>
    </div>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>u/{user} — r/{subreddit} New Releases</title>
    <style>
        :root {{
            --bg: #0f1115;
            --surface: #181b22;
            --surface-alt: #13161c;
            --border: #272c38;
            --text-main: #f0f2f5;
            --text-sub: #9ca3af;
            --accent: #e11d48;
            --badge-bg: #222734;
            --badge-text: #cbd5e1;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg);
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            padding: 2rem 1rem;
            line-height: 1.5;
        }}
        .container {{ max-width: 920px; margin: 0 auto; }}
        header {{
            margin-bottom: 1.75rem;
            border-bottom: 1px solid var(--border);
            padding-bottom: 1.25rem;
        }}
        h1 {{ font-size: 1.75rem; font-weight: 700; margin-bottom: 0.5rem; }}
        .meta {{
            display: flex;
            gap: 1.25rem;
            flex-wrap: wrap;
            color: var(--text-sub);
            font-size: 0.875rem;
            margin-bottom: 1rem;
        }}
        .meta a {{ color: var(--text-sub); text-decoration: none; }}
        .meta a:hover {{ color: #fff; }}
        .filter-input {{
            width: 100%;
            padding: 0.65rem 0.85rem;
            border-radius: 6px;
            border: 1px solid var(--border);
            background: var(--surface);
            color: var(--text-main);
            font-size: 0.95rem;
        }}
        .filter-input:focus {{
            outline: none;
            border-color: var(--accent);
        }}
        .post-group {{
            margin-bottom: 2rem;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
        }}
        .post-header {{
            padding: 0.95rem 1.25rem;
            background: var(--surface-alt);
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 0.5rem;
        }}
        .post-header h2 {{ font-size: 1.05rem; font-weight: 600; }}
        .post-meta {{ display: flex; gap: 1rem; font-size: 0.85rem; color: var(--text-sub); }}
        .post-meta a {{ color: var(--accent); text-decoration: none; font-weight: 500; }}
        .card {{
            padding: 0.85rem 1.25rem;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 1rem;
        }}
        .card:last-child {{ border-bottom: none; }}
        .card.hidden {{ display: none; }}
        .card-info {{ display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }}
        .artist {{ font-weight: 600; color: #fff; }}
        .divider {{ color: var(--text-sub); }}
        .badge {{
            background: var(--badge-bg);
            color: var(--badge-text);
            font-size: 0.75rem;
            padding: 0.15rem 0.5rem;
            border-radius: 4px;
            border: 1px solid var(--border);
        }}
        .badge-direct {{
            border-color: #15803d;
            color: #86efac;
        }}
        .badge-search {{
            color: var(--text-sub);
        }}
        .card-links {{ display: flex; gap: 0.5rem; flex-shrink: 0; }}
        .btn {{
            font-size: 0.75rem;
            font-weight: 600;
            padding: 0.35rem 0.65rem;
            border-radius: 4px;
            text-decoration: none;
        }}
        .btn-ytm {{ background: #ff0000; color: #fff; }}
        .btn-yt {{ background: #27272a; color: #e4e4e7; border: 1px solid #3f3f46; }}
        .empty-state {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.5rem;
        }}
        .empty-state p {{ color: var(--text-sub); margin-top: 0.4rem; }}
        @media (max-width: 600px) {{
            .card {{ flex-direction: column; align-items: flex-start; }}
            .card-links {{ width: 100%; justify-content: flex-end; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>r/{subreddit} New Releases</h1>
            <div class="meta">
                <span>Source: <a href="https://www.reddit.com/user/{user}/" target="_blank" rel="noopener noreferrer">u/{user}</a></span>
                <span>Tracked Releases: {total_releases}</span>
                <span>Last Built: {built_at}</span>
            </div>
            <input type="search" id="releaseFilter" class="filter-input" placeholder="Filter by artist, release, or format (e.g., LP, Disfear)..." aria-label="Filter releases">
        </header>
        <main id="releasesMain">
            {main_content}
        </main>
    </div>
    <script>
        const input = document.getElementById("releaseFilter");
        input.addEventListener("input", () => {{
            const term = input.value.trim().toLowerCase();
            document.querySelectorAll(".post-group").forEach((group) => {{
                let visibleInGroup = 0;
                group.querySelectorAll(".card").forEach((card) => {{
                    const hay = card.getAttribute("data-search") || "";
                    const match = !term || hay.includes(term);
                    card.classList.toggle("hidden", !match);
                    if (match) visibleInGroup++;
                }});
                group.style.display = visibleInGroup > 0 || !term ? "" : "none";
            }});
        }});
    </script>
</body>
</html>
"""


def main() -> None:
    data = load_data(INPUT_FILE)
    OUTPUT_FILE.write_text(build_html(data), encoding="utf-8")
    count = sum(len(p.get("releases", [])) for p in data.get("posts", []))
    print(f"Built {OUTPUT_FILE} with {count} releases.")


if __name__ == "__main__":
    main()
