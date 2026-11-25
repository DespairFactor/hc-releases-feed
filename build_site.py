#!/usr/bin/env python3
"""Build static index.html, privacy.html, and terms.html from releases.json.

Renders public YouTube Music playlist links published per post, in-page
playback queues, and static documentation pages.
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
OUTPUT_PRIVACY = REPO_ROOT / "privacy.html"
OUTPUT_TERMS = REPO_ROOT / "terms.html"

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

    query = quote_plus(clean_search_query(rel.get("artist", ""), rel.get("title", "")))
    return f"https://music.youtube.com/search?q={query}", "Search"


def build_post_queue_items(releases: list) -> list[dict]:
    """Collect all track/item info for in-page stepping."""
    items: list[dict] = []
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        artist = str(rel.get("artist", "")).strip()
        title = str(rel.get("title", "")).strip()
        ytm_url, badge = get_ytm_url(rel)

        tracks = rel.get("tracks")
        if isinstance(tracks, list) and tracks:
            for tr in tracks:
                if not isinstance(tr, dict):
                    continue
                url = str(tr.get("url") or "")
                items.append({
                    "artist": str(tr.get("artist") or artist),
                    "title": str(tr.get("title") or title),
                    "badge": "Song",
                    "ytm_url": url,
                })
        else:
            items.append({
                "artist": artist,
                "title": title,
                "badge": badge,
                "ytm_url": ytm_url,
            })
    return items


def shared_styles() -> str:
    return """    :root {
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
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      background: var(--bg);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      padding: 2rem 1rem 4rem;
      line-height: 1.5;
    }

    .container {
      max-width: 960px;
      margin: 0 auto;
    }

    header.site-header {
      margin-bottom: 1.25rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.25rem;
    }

    h1 {
      font-size: 1.85rem;
      font-weight: 700;
      margin-bottom: 0.35rem;
    }

    .subtitle {
      color: var(--text-sub);
      font-size: 0.92rem;
    }

    .subtitle a {
      color: var(--accent);
      text-decoration: none;
    }

    footer.site-footer {
      margin-top: 2.5rem;
      padding-top: 1.25rem;
      border-top: 1px solid var(--border);
      color: var(--text-sub);
      font-size: 0.85rem;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 0.75rem;
    }

    footer.site-footer a {
      color: var(--accent);
      text-decoration: none;
      margin-left: 1rem;
    }

    footer.site-footer a:first-child {
      margin-left: 0;
    }

    .doc-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.5rem;
    }

    .doc-card h2 {
      font-size: 1.15rem;
      margin-top: 1.25rem;
      margin-bottom: 0.45rem;
      color: #fff;
    }

    .doc-card h2:first-child {
      margin-top: 0;
    }

    .doc-card p,
    .doc-card ul {
      color: var(--text-sub);
      font-size: 0.94rem;
      margin-bottom: 0.75rem;
    }

    .doc-card ul {
      padding-left: 1.25rem;
    }

    .doc-card li {
      margin-bottom: 0.35rem;
    }

    .doc-card a {
      color: var(--accent);
      text-decoration: none;
    }"""


def render_doc_page(store: dict, page_title: str, heading: str, body_html: str) -> str:
    source_user = html.escape(str(store.get("source_user", "sepdri")))
    subreddit = html.escape(str(store.get("subreddit", "Hardcore")))
    safe_page_title = html.escape(page_title)
    safe_heading = html.escape(heading)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{safe_page_title} — r/{subreddit} Releases</title>
  <style>
{shared_styles()}
  </style>
</head>
<body>
  <div class="container">
    <header class="site-header">
      <h1>{safe_heading}</h1>
      <p class="subtitle">
        <a href="./index.html">← Back to r/{subreddit} Releases (u/{source_user})</a>
      </p>
    </header>

    <main class="doc-card">
{body_html}
    </main>

    <footer class="site-footer">
      <span>r/{subreddit} Releases — u/{source_user}</span>
      <nav aria-label="Legal links">
        <a href="./index.html">Releases</a>
        <a href="./privacy.html">Privacy Policy</a>
        <a href="./terms.html">Terms of Service</a>
      </nav>
    </footer>
  </div>
</body>
</html>
"""


def render_privacy_html(store: dict) -> str:
    updated_at = html.escape(str(store.get("updated_at", "2026-11-25"))[:10])
    body = f"""
      <p><strong>Last updated:</strong> {updated_at} &bull; <strong>Contact:</strong> matthewdalex92@gmail.com</p>

      <h2>1. Overview</h2>
      <p>hc-releases-feed is a static web application displaying curated hardcore release roundups and providing links to listen to full playlists and tracks on YouTube Music. No personal account credentials or tracking cookies are collected from visitors.</p>

      <h2>2. YouTube API Services &amp; Links</h2>
      <p>Public playlists displayed on this site are published using YouTube API Services. Visitors can click playlist links directly to listen on YouTube Music under YouTube&rsquo;s standard platform terms.</p>

      <h2>3. Third-Party Terms</h2>
      <p>By using links to YouTube Music on this site, you are subject to the <a href="https://www.youtube.com/t/terms" target="_blank" rel="noopener noreferrer">YouTube Terms of Service</a> and the <a href="https://policies.google.com/privacy" target="_blank" rel="noopener noreferrer">Google Privacy Policy</a>.</p>
    """
    return render_doc_page(store, "Privacy Policy", "Privacy Policy", body)


def render_terms_html(store: dict) -> str:
    updated_at = html.escape(str(store.get("updated_at", "2026-11-25"))[:10])
    body = f"""
      <p><strong>Last updated:</strong> {updated_at} &bull; <strong>Contact:</strong> matthewdalex92@gmail.com</p>

      <h2>1. Acceptance of Terms</h2>
      <p>By accessing hc-releases-feed, you agree to these Terms of Service and the <a href="https://www.youtube.com/t/terms" target="_blank" rel="noopener noreferrer">YouTube Terms of Service</a>.</p>

      <h2>2. Content &amp; Availability</h2>
      <p>Curated releases and playlists are provided for discovery purposes. Track availability, regional restrictions, and audio playback are subject to YouTube&rsquo;s platform availability.</p>

      <h2>3. Disclaimer</h2>
      <p>This service is provided &ldquo;as is&rdquo; without warranties of any kind.</p>
    """
    return render_doc_page(store, "Terms of Service", "Terms of Service", body)


def render_html(store: dict) -> str:
    source_user = html.escape(str(store.get("source_user", "sepdri")))
    subreddit = html.escape(str(store.get("subreddit", "Hardcore")))
    updated_at = html.escape(str(store.get("updated_at", "Unknown")))
    posts = store.get("posts", [])

    total_releases = sum(len(p.get("releases", [])) for p in posts if isinstance(p.get("releases"), list))

    post_sections = []
    for post_idx, post in enumerate(posts):
        post_title = html.escape(str(post.get("post_title", "Untitled Roundup")))
        post_url = html.escape(str(post.get("post_url", f"https://www.reddit.com/user/{source_user}/")))
        playlist_url = (post.get("playlist_url") or "").strip()
        added_at = html.escape(str(post.get("added_at", "")))
        releases = post.get("releases", [])

        queue_items = build_post_queue_items(releases)
        queue_json = html.escape(json.dumps(queue_items, ensure_ascii=False))

        cards = []
        for rel in releases:
            artist_raw = str(rel.get("artist", ""))
            title_raw = str(rel.get("title", ""))
            notes_raw = str(rel.get("notes", ""))

            artist = html.escape(artist_raw)
            title = html.escape(title_raw)
            notes = html.escape(notes_raw)

            ytm_url, ytm_badge = get_ytm_url(rel)
            yt_query = quote_plus(clean_search_query(artist_raw, title_raw))
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

        playlist_btn_html = ""
        if playlist_url:
            playlist_btn_html = (
                f'<a href="{html.escape(playlist_url)}" target="_blank" rel="noopener noreferrer" '
                f'class="btn btn-playlist">▶ Listen to Full Playlist</a>'
            )

        post_sections.append(f"""
      <section class="post-group" data-post-idx="{post_idx}" data-queue="{queue_json}">
        <header class="post-header">
          <h2>{post_title}</h2>
          <div class="post-info">
            <span>{added_at}</span>
            {playlist_btn_html}
            <button type="button" class="btn btn-queue" data-action="toggle-queue">Queue ({len(queue_items)})</button>
            <a href="{post_url}" target="_blank" rel="noopener noreferrer" class="thread-link">Reddit Thread ↗</a>
          </div>
        </header>
        <div class="queue-panel" hidden></div>
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
{shared_styles()}

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
      gap: 0.65rem;
      font-size: 0.83rem;
      color: var(--text-sub);
      flex-wrap: wrap;
    }}

    .thread-link {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 500;
    }}

    .queue-panel {{
      background: #141820;
      border-bottom: 1px solid var(--border);
      padding: 0.9rem 1.15rem;
    }}

    .queue-controls {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 0.6rem;
      margin-bottom: 0.75rem;
    }}

    .queue-nav {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.45rem;
    }}

    .queue-current {{
      font-size: 0.86rem;
      color: var(--text-sub);
      margin-bottom: 0.65rem;
    }}

    .queue-current strong {{
      color: #fff;
    }}

    .queue-list {{
      max-height: 240px;
      overflow-y: auto;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface-alt);
    }}

    .queue-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
      padding: 0.45rem 0.75rem;
      border-bottom: 1px solid var(--border);
      font-size: 0.82rem;
    }}

    .queue-row:last-child {{
      border-bottom: none;
    }}

    .queue-row.active {{
      background: #1e2433;
      border-left: 3px solid var(--accent);
    }}

    .queue-row.played .queue-row-title {{
      color: var(--text-sub);
      text-decoration: line-through;
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
      padding: 0.35rem 0.7rem;
      border-radius: 4px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      cursor: pointer;
      border: 1px solid transparent;
    }}

    .btn-playlist {{
      background: #e11d48;
      color: #ffffff;
      border-color: #be123c;
      font-weight: 700;
    }}

    .btn-playlist:hover {{
      background: #f43f5e;
    }}

    .btn-queue {{
      background: #1e293b;
      color: #f8fafc;
      border-color: #334155;
    }}

    .btn-action {{
      background: #222734;
      color: #f0f2f5;
      border-color: #363d4f;
      padding: 0.32rem 0.65rem;
      font-size: 0.75rem;
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

    <footer class="site-footer">
      <span>r/{subreddit} Releases — u/{source_user}</span>
      <nav aria-label="Legal links">
        <a href="./privacy.html">Privacy Policy</a>
        <a href="./terms.html">Terms of Service</a>
      </nav>
    </footer>
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

    const queueState = new Map();

    function getState(group) {{
      const idx = group.getAttribute("data-post-idx");
      if (!queueState.has(idx)) {{
        let items = [];
        try {{
          items = JSON.parse(group.getAttribute("data-queue") || "[]");
        }} catch {{
          items = [];
        }}
        queueState.set(idx, {{
          items,
          currentIdx: 0,
          played: new Set(),
        }});
      }}
      return queueState.get(idx);
    }}

    function escapeText(s) {{
      return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }}

    function openInYtmTab(url) {{
      window.open(url, "ytm_queue_player", "noopener,noreferrer");
    }}

    function renderQueuePanel(group) {{
      const panel = group.querySelector(".queue-panel");
      const state = getState(group);
      if (!panel || !state.items.length) return;

      const cur = state.items[state.currentIdx] || state.items[0];
      const rowsHtml = state.items.map((item, i) => {{
        const isActive = i === state.currentIdx;
        const isPlayed = state.played.has(i);
        const cls = ["queue-row", isActive ? "active" : "", isPlayed ? "played" : ""].filter(Boolean).join(" ");
        return `
          <div class="${{cls}}" data-row-idx="${{i}}">
            <span class="queue-row-title">${{i + 1}}. <strong>${{escapeText(item.artist)}}</strong> — ${{escapeText(item.title)}} <em>(${{escapeText(item.badge)}})</em></span>
            <a href="${{escapeText(item.ytm_url)}}" target="ytm_queue_player" rel="noopener noreferrer" class="btn btn-ytm" data-play-idx="${{i}}">Open in YTM</a>
          </div>
        `;
      }}).join("");

      panel.innerHTML = `
        <div class="queue-controls">
          <div class="queue-nav">
            <button type="button" class="btn btn-action" data-q-action="prev">← Prev</button>
            <button type="button" class="btn btn-ytm" data-q-action="open-current">Open Current in YTM</button>
            <button type="button" class="btn btn-action" data-q-action="next">Next &amp; Open →</button>
            <button type="button" class="btn btn-action" data-q-action="copy-urls">Copy All YTM URLs</button>
          </div>
          <button type="button" class="btn btn-action" data-q-action="close">Hide Queue</button>
        </div>
        <div class="queue-current">
          Up next (${{state.currentIdx + 1}} / ${{state.items.length}}): <strong>${{escapeText(cur.artist)}} — ${{escapeText(cur.title)}}</strong>
        </div>
        <div class="queue-list">${{rowsHtml}}</div>
      `;

      panel.querySelectorAll("[data-play-idx]").forEach((link) => {{
        link.addEventListener("click", () => {{
          const i = Number(link.getAttribute("data-play-idx"));
          state.currentIdx = i;
          state.played.add(i);
          renderQueuePanel(group);
        }});
      }});

      panel.querySelectorAll("[data-q-action]").forEach((btn) => {{
        btn.addEventListener("click", () => {{
          const action = btn.getAttribute("data-q-action");
          if (action === "close") {{
            panel.hidden = true;
            return;
          }}
          if (action === "prev") {{
            state.currentIdx = (state.currentIdx - 1 + state.items.length) % state.items.length;
            renderQueuePanel(group);
            return;
          }}
          if (action === "open-current") {{
            const item = state.items[state.currentIdx];
            state.played.add(state.currentIdx);
            openInYtmTab(item.ytm_url);
            renderQueuePanel(group);
            return;
          }}
          if (action === "next") {{
            state.played.add(state.currentIdx);
            state.currentIdx = (state.currentIdx + 1) % state.items.length;
            const nextItem = state.items[state.currentIdx];
            openInYtmTab(nextItem.ytm_url);
            renderQueuePanel(group);
            return;
          }}
          if (action === "copy-urls") {{
            const urls = state.items.map((it) => it.ytm_url).join("\\n");
            if (navigator.clipboard && navigator.clipboard.writeText) {{
              navigator.clipboard.writeText(urls).catch(() => {{}});
            }}
          }}
        }});
      }});
    }}

    document.querySelectorAll('[data-action="toggle-queue"]').forEach((btn) => {{
      btn.addEventListener("click", () => {{
        const group = btn.closest(".post-group");
        if (!group) return;
        const panel = group.querySelector(".queue-panel");
        if (!panel) return;
        panel.hidden = !panel.hidden;
        if (!panel.hidden) {{
          renderQueuePanel(group);
        }}
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

    OUTPUT_HTML.write_text(render_html(store), encoding="utf-8")
    OUTPUT_PRIVACY.write_text(render_privacy_html(store), encoding="utf-8")
    OUTPUT_TERMS.write_text(render_terms_html(store), encoding="utf-8")
    logger.info("Generated %s, %s, and %s successfully.", OUTPUT_HTML, OUTPUT_PRIVACY, OUTPUT_TERMS)


if __name__ == "__main__":
    main()
