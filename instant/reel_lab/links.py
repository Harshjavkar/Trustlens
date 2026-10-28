"""Discover public reel anchors; URL discovery never claims video access."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .collector import access_blocked, clean_error, save_json, utc_now


def normalize_link(value, profile):
    if not isinstance(value, str):
        return None
    parsed = urlparse(urljoin("https://www.instagram.com/", value.strip()))
    try:
        invalid_port = parsed.port is not None
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.hostname not in {"instagram.com", "www.instagram.com"}
            or parsed.username or invalid_port):
        return None
    match = re.fullmatch(r"/(?:(?P<owner>[A-Za-z0-9_.]+)/)?reel/(?P<code>[A-Za-z0-9_-]+)/?", parsed.path)
    if not match or (match["owner"] and match["owner"].lower() != profile.lower()):
        return None
    return f"https://www.instagram.com/reel/{match['code']}/"


class ReelAnchors(HTMLParser):
    def __init__(self, profile):
        super().__init__(convert_charrefs=True)
        self.profile, self.urls = profile, []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            for key, value in attrs:
                if key.lower() == "href":
                    url = normalize_link(value, self.profile)
                    if url and url not in self.urls:
                        self.urls.append(url)


def extract_reel_links(html, profile):
    parser = ReelAnchors(profile)
    parser.feed(html)
    return parser.urls


def _public_html(profile):
    request = Request(f"https://www.instagram.com/{profile}/reels/", headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"})
    with urlopen(request, timeout=15) as response:
        if any(marker in response.geturl() for marker in ("/accounts/login", "/challenge/", "/checkpoint/")):
            raise RuntimeError("Login or checkpoint restriction: discovery stopped.")
        content = response.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise RuntimeError("Profile HTML exceeds the 8 MiB limit.")
    html = content.decode("utf-8", errors="replace")
    if re.search(r"(?i)(this account is private|too many requests|challenge_required|checkpoint_required)", html):
        raise RuntimeError("Access restriction in profile response: discovery stopped.")
    return html


def _import_links(path, profile):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    values = data.get("urls") if isinstance(data, dict) else data
    if not isinstance(values, list):
        raise ValueError("Import JSON must be a URL list or an object containing urls.")
    provenance = {}
    if isinstance(data, dict):
        if data.get("source_url"):
            source = urlparse(data["source_url"])
            if (source.scheme != "https" or source.netloc not in {"instagram.com", "www.instagram.com"}
                    or source.path.rstrip("/").lower() not in {f"/{profile.lower()}", f"/{profile.lower()}/reels"}):
                raise ValueError("Imported source_url must identify the target Instagram profile.")
            provenance["source_url"] = f"https://www.instagram.com{source.path}"
        if data.get("collected_at"):
            from datetime import datetime
            datetime.fromisoformat(data["collected_at"].replace("Z", "+00:00"))
            provenance["collected_at"] = data["collected_at"]
    return list(dict.fromkeys(url for value in values if (url := normalize_link(value, profile)))), provenance


def discover_links(args):
    profile = args.profile.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", profile):
        raise ValueError("Use a valid Instagram username.")
    limit = getattr(args, "limit", 20)
    if limit < 1:
        raise ValueError("Limit must be positive.")
    output = Path(args.output)
    report = output.with_suffix(output.suffix + ".json")
    summary = {"profile": profile, "source_url": f"https://www.instagram.com/{profile}/reels/",
        "started_at": utc_now(), "status": "failed", "discovered": 0,
        "source": None, "errors": [], "stop_reason": None,
        "completeness": "unverified; not a full profile", "ordering": "source order; may include pinned posts"}
    urls = []
    try:
        if getattr(args, "import_json", None):
            summary["source"] = "imported browser-observed links"
            urls, provenance = _import_links(args.import_json, profile)
            summary.update(provenance)
        elif getattr(args, "html", None):
            summary["source"] = "offline public HTML anchors"
            urls = extract_reel_links(Path(args.html).read_text(encoding="utf-8-sig"), profile)
        else:
            summary["source"] = "public profile HTML anchors"
            urls = extract_reel_links(_public_html(profile), profile)
        urls = urls[:limit]
        if urls:
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(output.suffix + ".tmp")
            temporary.write_text("\n".join(urls) + "\n", encoding="utf-8")
            temporary.replace(output)
            summary.update(status="ok", discovered=len(urls), stop_reason="snapshot_complete")
        else:
            summary["stop_reason"] = "needs_rendered_browser_snapshot"
            summary["errors"].append("No public reel anchors found. Import observed links from a rendered browser snapshot.")
    except Exception as exc:
        summary["errors"].append(clean_error(exc))
        summary["stop_reason"] = "access_restricted" if (access_blocked(str(exc)) or "restriction" in str(exc).lower() or "private" in str(exc).lower()) else "discovery_failed"
    summary["finished_at"] = utc_now()
    save_json(report, summary)
    return summary, report
