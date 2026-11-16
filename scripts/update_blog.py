"""Render the latest three public Ameblo posts without an external widget."""

import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


BLOG_URL = "https://ameblo.jp/machinoeki-now/"
FEED_URL = "https://rssblog.ameba.jp/machinoeki-now/rss20.xml"
START = "    <!-- AMEBLO_POSTS_START -->"
END = "    <!-- AMEBLO_POSTS_END -->"
MAX_FEED_BYTES = 4 * 1024 * 1024


def safe_url(value, image=False):
    value = html.unescape(value or "").strip()
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or parts.username or parts.password:
            return None
        if parts.port not in (None, 80, 443):
            return None
        if image:
            valid = re.fullmatch(r"stat\d*\.ameba\.jp", parts.hostname or "")
        else:
            valid = parts.hostname == "ameblo.jp" and re.fullmatch(
                r"/machinoeki-now/entry-\d+\.html", parts.path
            )
        if not valid:
            return None
        return urlunsplit(("https", parts.hostname, parts.path, parts.query, ""))
    except ValueError:
        return None


class ArticleImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag != "img":
            return
        attrs = dict(attrs)
        for dimension in ("width", "height"):
            if attrs.get(dimension, "").isdigit() and int(attrs[dimension]) <= 2:
                return
        for key in ("data-src", "src"):
            candidate = safe_url(attrs.get(key), image=True)
            if candidate:
                if "/user_images/" in candidate:
                    self.images.append(candidate)
                return


def parse_feed(raw):
    if len(raw) > MAX_FEED_BYTES or re.search(br"<!\s*(DOCTYPE|ENTITY)\b", raw, re.I):
        raise ValueError("Unexpected RSS document")
    root = ET.fromstring(raw)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError("Expected an RSS channel")
    if (channel.findtext("link") or "").strip().rstrip("/") != BLOG_URL.rstrip("/"):
        raise ValueError("RSS belongs to a different blog")

    posts = []
    seen = set()
    for item in channel.findall("item"):
        link = safe_url(item.findtext("link"))
        title = html.unescape(item.findtext("title") or "").strip()
        if not link or not title or link in seen:
            continue
        seen.add(link)
        images = ArticleImages()
        for tag in ("{http://purl.org/rss/1.0/modules/content/}encoded", "description"):
            images.feed(item.findtext(tag) or "")
        image = images.images[0] if images.images else None
        if image is None:
            thumbnail = item.find("{http://search.yahoo.com/mrss/}thumbnail")
            if thumbnail is not None:
                image = safe_url(thumbnail.get("url"), image=True)
        published = item.findtext("pubDate") or ""
        try:
            date = parsedate_to_datetime(published)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            published = date.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, OverflowError):
            published = ""
        posts.append({"title": title, "url": link, "image": image, "published": published})
    if channel.findall("item") and not posts:
        raise ValueError("RSS items were present but none were valid; preserving previous posts")
    posts.sort(key=lambda post: post["published"], reverse=True)
    return posts[:3]


def render_posts(posts):
    if not posts:
        return ""
    lines = ['    <div class="home-blog-grid">']
    for post in posts:
        image = post["image"] or "assets/logo-mark.png"
        lines.extend([
            '      <a class="home-blog-article" href="' + html.escape(post["url"], quote=True)
            + '" target="_blank" rel="noopener">',
            '        <img src="' + html.escape(image, quote=True)
            + '" alt="" loading="lazy" decoding="async" onerror="this.onerror=null;this.src=\'assets/logo-mark.png\'">',
            '        <h3>' + html.escape(post["title"]) + '</h3>',
            '      </a>',
        ])
    lines.append('    </div>')
    return "\n".join(lines) + "\n"


def update_site(root, raw, now=None):
    posts = parse_feed(raw)
    rendered = render_posts(posts)
    pages = {}
    for name in ("index.html", "index.dc.html"):
        file = root / name
        text = file.read_text(encoding="utf-8")
        if text.count(START) != 1 or text.count(END) != 1:
            raise ValueError(f"Missing or duplicate blog markers in {name}")
        before, rest = text.split(START, 1)
        _, after = rest.split(END, 1)
        pages[file] = before + START + "\n" + rendered + END + after
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    week = (now.date() - timedelta(days=now.weekday())).isoformat()
    snapshot = {"blog": BLOG_URL, "checkedWeek": week, "posts": posts}
    pages[root / "data" / "blog.json"] = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    changed = []
    for file, content in pages.items():
        if not file.exists() or file.read_text(encoding="utf-8") != content:
            file.parent.mkdir(parents=True, exist_ok=True)
            temporary = file.with_name(file.name + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(file)
            changed.append(str(file.relative_to(root)))
    return posts, changed


def fetch_feed():
    for attempt in range(3):
        try:
            request = Request(FEED_URL, headers={"User-Agent": "MachinoekiHomepage/1.0", "Accept": "application/rss+xml, application/xml"})
            with urlopen(request, timeout=20) as response:
                if urlsplit(response.url).hostname != "rssblog.ameba.jp":
                    raise ValueError("Unexpected RSS redirect")
                return response.read(MAX_FEED_BYTES + 1)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--feed-file", type=Path)
    parser.add_argument("--keep-on-error", action="store_true")
    args = parser.parse_args()
    try:
        raw = args.feed_file.read_bytes() if args.feed_file else fetch_feed()
        posts, changed = update_site(args.root, raw)
        print(f"Official Ameblo posts: {len(posts)}; changed: {', '.join(changed) or 'none'}")
    except Exception as error:
        if not args.keep_on_error:
            raise
        print(f"::warning::Blog refresh failed; keeping the published posts ({type(error).__name__}).", file=sys.stderr)


if __name__ == "__main__":
    main()
