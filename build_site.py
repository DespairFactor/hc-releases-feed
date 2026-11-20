#!/usr/bin/env python3
"""Build a static index.html from releases.json for GitHub Pages.

Supports full albums (browse_id -> /browse/MPREb_...), single tracks
(video_id -> /watch?v=...), and query search fallbacks.
"""

from __future__ import annotations

import html
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

REPO_ROOT = Path(__file__).resolve().parent
RELEASES_FILE = REPO_ROOT / "releases.json"
OUTPUT_HTML = REPO_ROOT / "index.html"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("build_site")


def clean_search_query(artist: str, title: str) -> str:
    """Strip bracketed notes and format suffixes for cleaner query links."""
    clean_title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)
    clean_title = re.sub(
        r"\b(LP|EP|Demo|Split|Single|Promo|Reissue|Cassette|Flexi)\b\.?$",
        "",
        clean_title,
        flags=re.IGNORECASE,
    )
    clean_title = re.sub(r"\s+", " ", clean_title).strip(" -–—:/")

    clean_artist = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", artist)
    clean_artist = re.sub(r"\s+", " ", clean_artist).strip(" -–—:/")

    return f"{clean_artist} {clean_title or title}".strip()


def get_ytm_url(rel: dict) -> tuple[str, str]:
    """Return the destination URL and a badge/label based on the resolved type."""
    browse_id = (rel.get("browse_id") or "").strip()
    video_id = (rel.get("video_id") or "").strip()
    res_type = (rel.get("ytm_type") or "").strip().lower()

    if browse_id:
        url = f"https://music.youtube.com/browse/{browse_id}"
        label = "Album" if "album" in res_type or browse_id.startswith("MPREb_") else "Playlist"
        return url, label

    if video_id:
        url = f"https://music.youtube.com/watch?v={video_id}"
        return url, "Track"

    # Search fallback if unresolved
    query = quote_plus(clean_search_query(rel.get("artist", ""), rel.get("title", "")))
    return f"https://music.youtube.com/search?q={query}", "Search"


def render_html(store: dict) -> str:
    source_user = html.escape(str(store.get("source_user", "sepdri")))
    subreddit = html.escape(str(store.get("subreddit", "Hardcore")))
    updated_at = html.escape(str(store.get("updated_at", "Unknown")))
    posts = store.get("posts", [])

    total_releases = sum(len(p.get("releases", [])) for p in posts if isinstance(p.get("releases"), list))

    post_sections = []
    for post in posts:
        post_title = html.escape(str(post.get("post_title", "Untitled Roundup")))
        post_url = html.escape(str(post.get("post_url", f"https://www.reddit.com/user/{source_user}/")))
        added_at = html.escape(str(post.get("added_at", "")))
        releases = post.get("releases", [])

        cards = []
        for rel in releases:
            artist = html.escape(str(rel.get("artist", "")))
            title = html.escape(str(rel.get("title", "")))
            notes = html.escape(str(rel.get("notes", "")))

            ytm_url, ytm_badge = get_ytm_url(rel)
            yt_query = quote_plus(clean_search_query(rel.get("artist", ""), rel.get("title", "")))
            yt_url = f"https://www.youtube.com/results?search_query={yt_query}"

            notes_markup = f'<span class="badge notes-badge">{notes}</span>' if notes else ""
            type_markup = f'<span class="badge type-badge">{ytm_badge}</span>'

            cards.append(f"""
        <article class="release-card" data-search="{artist.lower()} {title.lower()} {notes.lower()} {post_title.lower()}">
          <div class="card-meta">
            <span class="artist">{artist}</span>
            <span class="divider">—</span>
            <span class="title">{title}</span>
            {notes_markup}
            {type_markup}
          </div>
          <div class="card-links">
            <a href="{html.escape(ytm_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-ytm">
              YouTube Music
            </a>
            <a href="{html.escape(yt_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-yt">
              YouTube
            </a>
          </div>
        </article>
""")

        cards_html = "\n".join(cards) if cards else '<p class="empty-text">No releases tracked in this post.</p>'

        post_sections.append(f"""
      <section class="post-group">
        <header class="post-header">
          <h2>{post_title}</h2>
          <div class="post-info">
            <span>{added_at}</span>
            <a href="{post_url}" target="_blank" rel="noopener noreferrer">Reddit Thread ↗</a>
          </div>
        </header>
        <div class="cards-list">
          {cards_html}
        </div>
      </section>
""")

    sections_html = "\n".join(post_sections) if post_sections else '<p class="empty-text">No releases found.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>r/{subreddit} Releases — u/{source_user}</title>
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
      --type-badge-bg: #1e293b;
      --type-badge-text: #38bdf8;
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      background: var(--bg);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      padding: 2rem 1rem 4rem;
      line-height: 1.5;
    }}

    .container {{
      max-width: 940px;
      margin: 0 auto;
    }}

    header.site-header {{
      margin-bottom: 1.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.25rem;
    }}

    h1 {{
      font-size: 1.85rem;
      font-weight: 700;
      margin-bottom: 0.35rem;
    }}

    .subtitle {{
      color: var(--text-sub);
      font-size: 0.92rem;
    }}

    .subtitle a {{
      color: var(--accent);
      text-decoration: none;
    }}

    .filter-panel {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem 1.15rem;
      margin-bottom: 1.75rem;
    }}

    input[type="search"] {{
      width: 100%;
      background: var(--surface-alt);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text-main);
      font-size: 0.95rem;
      padding: 0.65rem 0.85rem;
      outline: none;
    }}

    input[type="search"]:focus {{
      border-color: var(--accent);
    }}

    .post-group {{
      margin-bottom: 1.75rem;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }}

    .post-header {{
      padding: 0.9rem 1.15rem;
      background: var(--surface-alt);
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
    }}

    .post-header h2 {{
      font-size: 1.05rem;
      font-weight: 600;
    }}

    .post-info {{
      display: flex;
      align-items: center;
      gap: 0.85rem;
      font-size: 0.83rem;
      color: var(--text-sub);
    }}

    .post-info a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 500;
    }}

    .release-card {{
      padding: 0.85rem 1.15rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
    }}

    .release-card:last-child {{
      border-bottom: none;
    }}

    .card-meta {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}

    .artist {{
      font-weight: 600;
      color: #fff;
    }}

    .divider {{
      color: var(--text-sub);
    }}

    .badge {{
      font-size: 0.74rem;
      padding: 0.15rem 0.5rem;
      border-radius: 4px;
      border: 1px solid var(--border);
    }}

    .notes-badge {{
      background: var(--badge-bg);
      color: var(--badge-text);
    }}

    .type-badge {{
      background: var(--type-badge-bg);
      color: var(--type-badge-text);
      font-weight: 500;
    }}

    .card-links {{
      display: flex;
      gap: 0.5rem;
      flex-shrink: 0;
    }}

    .btn {{
      font-size: 0.78rem;
      font-weight: 600;
      padding: 0.4rem 0.75rem;
      border-radius: 4px;
      text-decoration: none;
    }}

    .btn-ytm {{
      background: #ff0000;
      color: #fff;
    }}

    .btn-yt {{
      background: #27272a;
      color: #e4e4e7;
      border: 1px solid #3f3f46;
    }}

    .empty-text {{
      padding: 1.25rem;
      color: var(--text-sub);
    }}

    @media (max-width: 640px) {{
      .release-card {{
        flex-direction: column;
        align-items: flex-start;
      }}
      .card-links {{
        width: 100%;
        justify-content: flex-end;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header class="site-header">
      <h1>r/{subreddit} Releases</h1>
      <p class="subtitle">
        Curated from <a href="https://www.reddit.com/user/{source_user}/" target="_blank" rel="noopener noreferrer">u/{source_user}</a> • {len(posts)} posts • {total_releases} releases • Updated {updated_at[:10]}
      </p>
    </header>

    <div class="filter-panel">
      <input type="search" id="searchInput" placeholder="Filter releases by artist, title, notes, or roundup title..." aria-label="Filter releases" />
    </div>

    <main id="content">
{sections_html}
    </main>
  </div>

  <script>
    const searchInput = document.getElementById("searchInput");
    searchInput.addEventListener("input", () => {{
      const query = searchInput.value.trim().toLowerCase();
      const groups = document.querySelectorAll(".post-group");

      groups.forEach((group) => {{
        const cards = group.querySelectorAll(".release-card");
        let visibleCount = 0;

        cards.forEach((card) => {{
          const text = card.getAttribute("data-search") || "";
          const match = !query || text.includes(query);
          card.style.display = match ? "flex" : "none";
          if (match) visibleCount++;
        }});

        group.style.display = (visibleCount > 0 || !query) ? "block" : "none";
      }});
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    if not RELEASES_FILE.exists():
        logger.error("Missing %s", RELEASES_FILE)
        sys.exit(1)

    try:
        store = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    except Exception as err:
        logger.error("Failed to read JSON: %s", err)
        sys.exit(1)

    html_content = render_html(store)
    OUTPUT_HTML.write_text(html_content, encoding="utf-8")
    logger.info("Generated %s successfully.", OUTPUT_HTML)


if __name__ == "__main__":
    main()
