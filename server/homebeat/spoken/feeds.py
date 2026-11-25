"""RSS/Atom podcast feed parsing (stdlib XML; expat >= 2.4 refuses entity-expansion bombs).

Understands RSS 2.0 + the iTunes namespace, Podcasting 2.0 (<podcast:chapters>), Podlove Simple Chapters
(<psc:chapters>) and a minimal Atom fallback (enclosure links)."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "podcast": "https://podcastindex.org/namespace/1.0",
    "psc": "http://podlove.org/simple-chapters",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
}
MAX_TEXT = 20_000
AUDIO_EXT = (".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav", ".m4b")


class FeedError(Exception):
    pass


@dataclass
class Episode:
    guid: str
    title: str
    enclosure_url: str
    enclosure_type: str | None = None
    enclosure_bytes: int | None = None
    description: str | None = None
    link: str | None = None
    published_at: str | None = None
    duration_s: float | None = None
    image_url: str | None = None
    explicit: bool = False
    season: int | None = None
    episode: int | None = None
    chapters_url: str | None = None
    chapters: list[dict] | None = None


@dataclass
class Feed:
    title: str
    author: str | None = None
    description: str | None = None
    link: str | None = None
    language: str | None = None
    image_url: str | None = None
    explicit: bool = False
    new_feed_url: str | None = None
    episodes: list[Episode] = field(default_factory=list)


def _q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t[:MAX_TEXT] if t else None


def _find_text(parent: ET.Element, *tags: str) -> str | None:
    for t in tags:
        v = _text(parent.find(t))
        if v:
            return v
    return None


def parse_explicit(v: str | None) -> bool:
    return (v or "").strip().lower() in ("yes", "true", "explicit", "1")


def parse_duration(v: str | None) -> float | None:
    """itunes:duration: seconds ("3605"), "MM:SS" or "HH:MM:SS" (fractions allowed)."""
    if not v:
        return None
    v = v.strip()
    try:
        parts = [float(p) for p in v.split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3 or any(p < 0 for p in parts):
        return None
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s or None


def parse_npt(v: str | None) -> float | None:
    """Podlove chapter start: normal play time "HH:MM:SS.mmm", "MM:SS" or seconds (0 allowed)."""
    if not v:
        return None
    try:
        parts = [float(p) for p in v.strip().split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3 or any(p < 0 for p in parts):
        return None
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def parse_date(v: str | None) -> str | None:
    if not v:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(v.strip())
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(v: str | None) -> int | None:
    try:
        return int(str(v).strip()) if v not in (None, "") else None
    except ValueError:
        return None


def _psc_chapters(item: ET.Element) -> list[dict] | None:
    box = item.find(_q("psc", "chapters"))
    if box is None:
        return None
    out = []
    for ch in box.findall(_q("psc", "chapter")):
        start = parse_npt(ch.get("start"))
        if start is None:
            continue
        c = {"start": start, "title": (ch.get("title") or "").strip()[:500]}
        if ch.get("href"):
            c["url"] = ch.get("href")
        if ch.get("image"):
            c["img"] = ch.get("image")
        out.append(c)
    out.sort(key=lambda c: c["start"])
    return out or None


def parse_json_chapters(data: bytes | str) -> list[dict]:
    """Podcasting 2.0 JSON chapters: {"version": "1.2.0", "chapters": [{"startTime", "title", "url", "img",
    "toc"}]}. Chapters with toc=false are hidden markers (skipped)."""
    try:
        doc = json.loads(data)
    except (ValueError, TypeError) as e:
        raise FeedError(f"bad chapters JSON: {e}") from e
    out = []
    for ch in (doc.get("chapters") if isinstance(doc, dict) else None) or []:
        if not isinstance(ch, dict) or ch.get("toc") is False:
            continue
        try:
            start = float(ch.get("startTime"))
        except (TypeError, ValueError):
            continue
        c = {"start": max(0.0, start), "title": str(ch.get("title") or "")[:500]}
        for k in ("url", "img"):
            if isinstance(ch.get(k), str) and ch[k].startswith(("http://", "https://")):
                c[k] = ch[k]
        out.append(c)
    out.sort(key=lambda c: c["start"])
    return out


def _strip_html(s: str | None) -> str | None:
    if not s:
        return None
    return s.strip()[:MAX_TEXT] or None


def _image(parent: ET.Element) -> str | None:
    el = parent.find(_q("itunes", "image"))
    if el is not None and el.get("href"):
        return el.get("href").strip()
    img = parent.find("image")
    if img is not None:
        return _find_text(img, "url")
    el = parent.find(_q("podcast", "images"))
    if el is not None and el.get("srcset"):
        return el.get("srcset").split(",")[0].strip().split(" ")[0]
    return None


def _rss_item(item: ET.Element) -> Episode | None:
    enc = item.find("enclosure")
    url = enc.get("url", "").strip() if enc is not None else ""
    etype = enc.get("type") if enc is not None else None
    if not url:
        mc = item.find(_q("media", "content"))
        if mc is not None and mc.get("url"):
            url, etype = mc.get("url").strip(), mc.get("type")
    if not url.startswith(("http://", "https://")):
        return None
    if etype and not (etype.startswith(("audio/", "video/")) or etype in ("application/octet-stream",)):
        if not url.lower().split("?")[0].endswith(AUDIO_EXT):
            return None
    title = _find_text(item, "title", _q("itunes", "title")) or "Untitled episode"
    guid = _find_text(item, "guid") or url
    ch = item.find(_q("podcast", "chapters"))
    return Episode(
        guid=guid[:1000],
        title=title[:1000],
        enclosure_url=url,
        enclosure_type=etype,
        enclosure_bytes=_int(enc.get("length")) if enc is not None else None,
        description=_strip_html(_find_text(item, _q("content", "encoded"), "description", _q("itunes", "summary"))),
        link=_find_text(item, "link"),
        published_at=parse_date(_find_text(item, "pubDate", _q("atom", "updated"))),
        duration_s=parse_duration(_find_text(item, _q("itunes", "duration"))),
        image_url=_image(item),
        explicit=parse_explicit(_find_text(item, _q("itunes", "explicit"))),
        season=_int(_find_text(item, _q("itunes", "season"))),
        episode=_int(_find_text(item, _q("itunes", "episode"))),
        chapters_url=ch.get("url") if ch is not None and (ch.get("url") or "").startswith("http") else None,
        chapters=_psc_chapters(item),
    )


def _atom_entry(e: ET.Element) -> Episode | None:
    a = NS["atom"]
    url = etype = None
    size = None
    for link in e.findall(f"{{{a}}}link"):
        if link.get("rel") == "enclosure" and link.get("href"):
            url, etype, size = link.get("href"), link.get("type"), _int(link.get("length"))
            break
    if not url:
        return None
    alt = next((x.get("href") for x in e.findall(f"{{{a}}}link") if x.get("rel") in (None, "alternate")), None)
    return Episode(
        guid=(_find_text(e, f"{{{a}}}id") or url)[:1000],
        title=(_find_text(e, f"{{{a}}}title") or "Untitled episode")[:1000],
        enclosure_url=url, enclosure_type=etype, enclosure_bytes=size,
        description=_strip_html(_find_text(e, f"{{{a}}}summary", f"{{{a}}}content")),
        link=alt,
        published_at=parse_date(_find_text(e, f"{{{a}}}published", f"{{{a}}}updated")),
    )


def parse(data: bytes) -> Feed:
    """Parse a feed document. Raises FeedError if it is not an RSS/Atom podcast feed."""
    if not data or not data.strip():
        raise FeedError("empty feed")
    if b"<!ENTITY" in data[:4096]:
        raise FeedError("feeds with DTD entities are not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise FeedError(f"not XML: {e}") from e
    if root.tag == f"{{{NS['atom']}}}feed":
        a = NS["atom"]
        feed = Feed(title=_find_text(root, f"{{{a}}}title") or "Untitled podcast",
                    author=_find_text(au, f"{{{a}}}name") if (au := root.find(f"{{{a}}}author")) is not None else None,
                    description=_strip_html(_find_text(root, f"{{{a}}}subtitle")),
                    image_url=_find_text(root, f"{{{a}}}logo", f"{{{a}}}icon"))
        feed.episodes = [ep for ep in (_atom_entry(e) for e in root.findall(f"{{{a}}}entry")) if ep]
        return feed
    ch = root.find("channel") if root.tag == "rss" else None
    if ch is None:
        raise FeedError("not an RSS or Atom feed")
    feed = Feed(
        title=(_find_text(ch, "title", _q("itunes", "title")) or "Untitled podcast")[:1000],
        author=_find_text(ch, _q("itunes", "author"), "managingEditor"),
        description=_strip_html(_find_text(ch, "description", _q("itunes", "summary"))),
        link=_find_text(ch, "link"),
        language=_find_text(ch, "language"),
        image_url=_image(ch),
        explicit=parse_explicit(_find_text(ch, _q("itunes", "explicit"))),
        new_feed_url=_find_text(ch, _q("itunes", "new-feed-url")),
    )
    seen: set[str] = set()
    for item in ch.findall("item"):
        ep = _rss_item(item)
        if ep is None or ep.guid in seen:
            continue
        seen.add(ep.guid)
        ep.explicit = ep.explicit or feed.explicit
        feed.episodes.append(ep)
    return feed


_WS = re.compile(r"\s+")


def plain_text(html: str | None, limit: int = 300) -> str | None:
    """A short plain-text summary of an HTML description (for list rows)."""
    if not html:
        return None
    t = _WS.sub(" ", re.sub(r"<[^>]+>", " ", html)).strip()
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        t = t.replace(a, b)
    return (t[: limit - 1] + "…") if len(t) > limit else (t or None)
