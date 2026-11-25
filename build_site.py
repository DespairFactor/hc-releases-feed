#!/usr/bin/env python3
"""Build static index.html, privacy.html, and terms.html from releases.json.

Supports full albums, single tracks, in-page queueing, client-side
Google OAuth to let visitors sign in and export any post directly into
their own YouTube / YouTube Music account as a playlist, and static
Privacy Policy / Terms of Service pages.
"""

from __future__ import annotations

import html
import json
import logging
import os
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
    """Collect all track/item info for queueing and client-side playlist export."""
    items: list[dict] = []
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        artist = str(rel.get("artist", "")).strip()
        title = str(rel.get("title", "")).strip()
        video_id = (rel.get("video_id") or "").strip()
        ytm_url, badge = get_ytm_url(rel)

        tracks = rel.get("tracks")
        if isinstance(tracks, list) and tracks:
            for tr in tracks:
                if not isinstance(tr, dict):
                    continue
                url = str(tr.get("url") or "")
                vid = ""
                if "v=" in url:
                    vid = url.split("v=")[1].split("&")[0]
                items.append({
                    "artist": str(tr.get("artist") or artist),
                    "title": str(tr.get("title") or title),
                    "video_id": vid,
                    "badge": "Song",
                    "ytm_url": url,
                })
        else:
            items.append({
                "artist": artist,
                "title": title,
                "video_id": video_id,
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
      <p>hc-releases-feed is a static web application displaying curated hardcore release roundups and providing tools to open or export releases to YouTube and YouTube Music. The site runs directly in your browser without a custom backend server operated by the developer to store account data.</p>

      <h2>2. How YouTube API Services Are Used</h2>
      <p>When you choose to sign in with Google and export a release roundup into a playlist, this site uses YouTube API Services (YouTube Data API v3) directly from your browser:</p>
      <ul>
        <li><strong>Requested scope:</strong> <code>https://www.googleapis.com/auth/youtube</code> is requested to create a playlist and add video/track entries on your YouTube account.</li>
        <li><strong>Playlist creation:</strong> Clicking <strong>Export to My YTM</strong> sends requests directly from your browser to the YouTube Data API to create a playlist (unlisted by default) and insert resolved video IDs from that roundup.</li>
        <li><strong>No developer backend storage:</strong> Your Google credentials, OAuth tokens, and created playlists are not transmitted to or stored on a backend server controlled by hc-releases-feed.</li>
      </ul>

      <h2>3. Local Browser Storage</h2>
      <p>When you sign in via Google OAuth, your OAuth access token is stored in your browser&rsquo;s <code>sessionStorage</code> for the active session and is removed when you sign out or close your session. Standard web hosting infrastructure (such as GitHub Pages) may log standard HTTP request metadata under its own policies.</p>

      <h2>4. Third-Party Policies &amp; Revoking Access</h2>
      <p>Your use of YouTube API Services is subject to the <a href="https://www.youtube.com/t/terms" target="_blank" rel="noopener noreferrer">YouTube Terms of Service</a> and the <a href="https://policies.google.com/privacy" target="_blank" rel="noopener noreferrer">Google Privacy Policy</a>. You can revoke access at any time in your <a href="https://myaccount.google.com/permissions" target="_blank" rel="noopener noreferrer">Google Account permissions</a>.</p>
    """
    return render_doc_page(store, "Privacy Policy", "Privacy Policy", body)


def render_terms_html(store: dict) -> str:
    updated_at = html.escape(str(store.get("updated_at", "2026-11-25"))[:10])
    body = f"""
      <p><strong>Last updated:</strong> {updated_at} &bull; <strong>Contact:</strong> matthewdalex92@gmail.com</p>

      <h2>1. Acceptance of Terms</h2>
      <p>By accessing hc-releases-feed or using its YouTube playlist export features, you agree to these Terms of Service and the <a href="https://www.youtube.com/t/terms" target="_blank" rel="noopener noreferrer">YouTube Terms of Service</a>.</p>

      <h2>2. Service &amp; YouTube API Usage</h2>
      <p>hc-releases-feed presents curated release entries and links to YouTube and YouTube Music. When you sign in with Google OAuth and export a post, your browser calls YouTube API Services directly to create a playlist on your account and add available track/video IDs associated with that post.</p>
      <p>Release entries may resolve to albums, playlists, single videos, or search queries, and some items may lack resolved video IDs or be unavailable in your region or on YouTube Music. Availability and API behavior depend on third-party services.</p>

      <h2>3. User Responsibilities</h2>
      <p>You are responsible for your Google and YouTube account activity and for using the playlist export tool in compliance with applicable laws and YouTube&rsquo;s Terms of Service.</p>

      <h2>4. Disclaimer &amp; Contact</h2>
      <p>This site is provided &ldquo;as is&rdquo; and &ldquo;as available&rdquo; without warranties of any kind. For questions or support, contact matthewdalex92@gmail.com.</p>
    """
    return render_doc_page(store, "Terms of Service", "Terms of Service", body)


def render_html(store: dict, default_client_id: str = "") -> str:
    source_user = html.escape(str(store.get("source_user", "sepdri")))
    subreddit = html.escape(str(store.get("subreddit", "Hardcore")))
    updated_at = html.escape(str(store.get("updated_at", "Unknown")))
    posts = store.get("posts", [])
    default_client_id_js = json.dumps(default_client_id.strip())

    total_releases = sum(len(p.get("releases", [])) for p in posts if isinstance(p.get("releases"), list))

    post_sections = []
    for post_idx, post in enumerate(posts):
        post_title = html.escape(str(post.get("post_title", "Untitled Roundup")))
        post_url = html.escape(str(post.get("post_url", f"https://www.reddit.com/user/{source_user}/")))
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

        post_sections.append(f"""
      <section class="post-group" data-post-idx="{post_idx}" data-post-title="{post_title}" data-queue="{queue_json}">
        <header class="post-header">
          <h2>{post_title}</h2>
          <div class="post-info">
            <span>{added_at}</span>
            <button type="button" class="btn btn-queue" data-action="toggle-queue">▶ Queue ({len(queue_items)})</button>
            <button type="button" class="btn btn-export-oauth" data-action="export-playlist">Export to My YTM</button>
            <a href="{post_url}" target="_blank" rel="noopener noreferrer" class="thread-link">Reddit Thread ↗</a>
          </div>
        </header>
        <div class="export-status-bar" hidden></div>
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

    .oauth-panel {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem 1.15rem;
      margin-bottom: 1.25rem;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      font-size: 0.85rem;
    }}

    .oauth-copy {{
      color: var(--text-sub);
    }}

    .oauth-copy strong {{
      color: var(--text-main);
    }}

    .oauth-actions {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
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

    .export-status-bar {{
      padding: 0.6rem 1.15rem;
      background: #192231;
      border-bottom: 1px solid var(--border);
      font-size: 0.83rem;
      color: #7dd3fc;
    }}

    .export-status-bar a {{
      color: #38bdf8;
      font-weight: 700;
      text-decoration: underline;
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

    .btn-queue {{
      background: #1e293b;
      color: #f8fafc;
      border-color: #334155;
    }}

    .btn-export-oauth {{
      background: #b91c1c;
      color: #ffffff;
      border-color: #991b1b;
      font-weight: 700;
    }}

    .btn-export-oauth:hover {{
      background: #dc2626;
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

    <section class="oauth-panel">
      <div class="oauth-copy">
        <strong>YouTube Music Playlist Export:</strong> Sign in with Google to export any post into a playlist on your account.
      </div>
      <div class="oauth-actions">
        <span id="oauthStatus" style="color: var(--text-sub);">Signed out</span>
        <button type="button" class="btn btn-ytm" id="loginBtn">Sign in with Google</button>
        <button type="button" class="btn btn-action" id="logoutBtn" style="display: none;">Sign out</button>
      </div>
    </section>

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

    const SESSION_TOKEN = "yt_oauth_access_token";
    const OAUTH_CLIENT_ID = {default_client_id_js};

    const loginBtn = document.getElementById("loginBtn");
    const logoutBtn = document.getElementById("logoutBtn");
    const oauthStatus = document.getElementById("oauthStatus");

    if (window.location.hash) {{
      const params = new URLSearchParams(window.location.hash.substring(1));
      const accessToken = params.get("access_token");
      if (accessToken) {{
        sessionStorage.setItem(SESSION_TOKEN, accessToken);
        history.replaceState(null, "", window.location.pathname + window.location.search);
      }}
    }}

    function updateAuthUi() {{
      const token = sessionStorage.getItem(SESSION_TOKEN);
      if (token) {{
        oauthStatus.textContent = "Signed in ✓";
        oauthStatus.style.color = "#4ade80";
        loginBtn.style.display = "none";
        logoutBtn.style.display = "inline-flex";
      }} else {{
        oauthStatus.textContent = "Signed out";
        oauthStatus.style.color = "var(--text-sub)";
        loginBtn.style.display = OAUTH_CLIENT_ID ? "inline-flex" : "none";
        logoutBtn.style.display = "none";
        if (!OAUTH_CLIENT_ID) {{
          oauthStatus.textContent = "OAuth sign-in not configured (YT_CLIENT_ID missing at build)";
        }}
      }}
    }}
    updateAuthUi();

    function promptGoogleLogin() {{
      if (!OAUTH_CLIENT_ID) {{
        alert("Google OAuth sign-in is not configured on this page because YT_CLIENT_ID was not set when building the site.");
        return;
      }}
      const redirectUri = window.location.origin + window.location.pathname;
      const scope = "https://www.googleapis.com/auth/youtube";
      const authUrl = "https://accounts.google.com/o/oauth2/v2/auth?" + new URLSearchParams({{
        client_id: OAUTH_CLIENT_ID,
        redirect_uri: redirectUri,
        response_type: "token",
        scope: scope,
        include_granted_scopes: "true",
      }}).toString();
      window.location.href = authUrl;
    }}

    loginBtn.addEventListener("click", promptGoogleLogin);

    logoutBtn.addEventListener("click", () => {{
      sessionStorage.removeItem(SESSION_TOKEN);
      updateAuthUi();
    }});

    async function createYouTubePlaylist(token, title, description) {{
      const res = await fetch("https://www.googleapis.com/youtube/v3/playlists?part=snippet,status", {{
        method: "POST",
        headers: {{
          "Authorization": "Bearer " + token,
          "Content-Type": "application/json",
        }},
        body: JSON.stringify({{
          snippet: {{
            title: title.slice(0, 150),
            description: description.slice(0, 5000),
          }},
          status: {{
            privacyStatus: "unlisted",
          }},
        }}),
      }});

      if (!res.ok) {{
        const err = await res.json().catch(() => ({{}}));
        throw new Error(err.error?.message || ("HTTP " + res.status));
      }}
      const data = await res.json();
      return data.id;
    }}

    async function addVideoToPlaylist(token, playlistId, videoId) {{
      const res = await fetch("https://www.googleapis.com/youtube/v3/playlistItems?part=snippet", {{
        method: "POST",
        headers: {{
          "Authorization": "Bearer " + token,
          "Content-Type": "application/json",
        }},
        body: JSON.stringify({{
          snippet: {{
            playlistId: playlistId,
            resourceId: {{
              kind: "youtube#video",
              videoId: videoId,
            }},
          }},
        }}),
      }});
      return res.ok;
    }}

    document.querySelectorAll('[data-action="export-playlist"]').forEach((btn) => {{
      btn.addEventListener("click", async () => {{
        let token = sessionStorage.getItem(SESSION_TOKEN);
        if (!token) {{
          if (!OAUTH_CLIENT_ID) {{
            alert("Google OAuth sign-in is not configured on this page.");
            return;
          }}
          promptGoogleLogin();
          return;
        }}

        const group = btn.closest(".post-group");
        const statusDiv = group.querySelector(".export-status-bar");
        const postTitle = group.getAttribute("data-post-title") || "Roundup";
        let items = [];
        try {{
          items = JSON.parse(group.getAttribute("data-queue") || "[]");
        }} catch {{
          items = [];
        }}

        const videoIds = items.map((i) => i.video_id).filter(Boolean);
        if (!videoIds.length) {{
          alert("No resolved track video IDs found in this post to add to a playlist.");
          return;
        }}

        btn.disabled = true;
        statusDiv.hidden = false;
        statusDiv.textContent = `Creating playlist for "${{postTitle}}"...`;

        try {{
          const playlistId = await createYouTubePlaylist(
            token,
            postTitle,
            `Curated releases from ${{postTitle}} on r/Hardcore.`
          );

          statusDiv.textContent = `Created playlist. Adding 0/${{videoIds.length}} track(s)...`;

          let added = 0;
          for (const vid of videoIds) {{
            const ok = await addVideoToPlaylist(token, playlistId, vid);
            if (ok) added++;
            statusDiv.textContent = `Adding tracks: ${{added}}/${{videoIds.length}}...`;
            await new Promise((r) => setTimeout(r, 120));
          }}

          const ytmLink = `https://music.youtube.com/playlist?list=${{playlistId}}`;
          statusDiv.innerHTML = `✓ Created playlist with ${{added}} track(s)! <a href="${{ytmLink}}" target="_blank" rel="noopener noreferrer">Open "${{postTitle}}" in YouTube Music ↗</a>`;
        }} catch (err) {{
          if (err.message.includes("401") || err.message.toLowerCase().includes("invalid credentials")) {{
            sessionStorage.removeItem(SESSION_TOKEN);
            updateAuthUi();
            promptGoogleLogin();
          }} else {{
            statusDiv.textContent = "Failed to create playlist: " + err.message;
          }}
        }} finally {{
          btn.disabled = false;
        }}
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

    default_client_id = os.environ.get("YT_CLIENT_ID", "")
    OUTPUT_HTML.write_text(render_html(store, default_client_id=default_client_id), encoding="utf-8")
    OUTPUT_PRIVACY.write_text(render_privacy_html(store), encoding="utf-8")
    OUTPUT_TERMS.write_text(render_terms_html(store), encoding="utf-8")
    logger.info("Generated %s, %s, and %s successfully.", OUTPUT_HTML, OUTPUT_PRIVACY, OUTPUT_TERMS)


if __name__ == "__main__":
    main()
