"""Loopback-only prototype server, with no web-framework dependency."""
from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .answers import answer_question
from .conversation import ConversationUnavailable, conversation_status, converse
from .jobs import CreatorJobs
from .library import Library

WEB = Path(__file__).parent / "web"
VIDEO_TYPES = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}


def create_server(library_root: Path, port=8765, media_root: Path | None = None, *, start_worker=False):
    library = Library(Path(library_root))
    library_root = library.root
    jobs = CreatorJobs(library.root)
    jobs.sync_ready(library.creators())
    allowed_media = (Path(media_root) if media_root else Path(library_root).parent / "live-first-test/media").resolve()
    creator_data = library.root.parent / "creators"
    media_roots = (allowed_media, creator_data.resolve())

    class Handler(BaseHTTPRequestHandler):
        server_version = "InstaNT"

        def log_message(self, *args):
            pass

        def _headers(self, status, content_type, size, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()

        def _json(self, value, status=200):
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(data))
            self.wfile.write(data)

        def _same_origin(self):
            port = self.server.server_address[1]
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host", "") not in allowed:
                self._json({"error": "Use the local app address."}, 403)
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in allowed}:
                self._json({"error": "Cross-origin requests are not accepted."}, 403)
                return False
            return True

        def do_GET(self):
            if not self._same_origin():
                return
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            try:
                if path == "/api/health":
                    return self._json({"status": "ok", "creators": len(library.creators()),
                                       "conversation": conversation_status()})
                if path == "/api/creators":
                    return self._json({"creators": library.creators()})
                if path == "/api/creators/status":
                    creator = jobs.get(query.get("handle", [""])[0])
                    return self._json({"creator": creator}) if creator else self._json({"error": "Creator has not been requested yet."}, 404)
                if path == "/api/creators/jobs":
                    return self._json({"creators": jobs.list()})
                if path == "/api/posts":
                    return self._json({"posts": library.posts(query.get("creator", [""])[0])})
                if path == "/api/search":
                    return self._json({"sources": library.search(query.get("q", [""])[0], query.get("creator", [""])[0], limit=8)})
                if path.startswith("/api/posts/"):
                    post = library.get_post(path.removeprefix("/api/posts/"))
                    return self._json({"post": post}, 200) if post else self._json({"error": "Source not found."}, 404)
                if path.startswith("/media/"):
                    return self._media(path.removeprefix("/media/"))
                static = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}.get(path)
                if static and (WEB / static).is_file():
                    data = (WEB / static).read_bytes()
                    kind = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[Path(static).suffix]
                    self._headers(200, kind + "; charset=utf-8", len(data))
                    self.wfile.write(data)
                    return
                self._json({"error": "Not found."}, 404)
            except (ValueError, TypeError) as exc:
                self._json({"error": str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self._json({"error": "Could not read the evidence library. Check the local library import."}, 500)

        def do_POST(self):
            if not self._same_origin():
                return
            if self.path not in {"/api/chat", "/api/ask", "/api/creators/request", "/api/creators/retry"}:
                return self._json({"error": "Not found."}, 404)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 64000:
                    return self._json({"error": "Request size must be between 1 and 64,000 bytes."}, 413)
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("Send a question and creator as a JSON object.")
                if self.path in {"/api/creators/request", "/api/creators/retry"}:
                    creator = (jobs.retry(body.get("handle", "")) if self.path.endswith("/retry")
                               else jobs.request(body.get("handle", "")))
                    worker = getattr(self.server, "creator_worker", None)
                    if worker:
                        worker.notify()
                    return self._json({"creator": creator, "reused": bool(creator.get("reused", False))},
                                      202 if creator["status"] in {"queued", "processing"} else 200)
                answer = converse if self.path == "/api/chat" else answer_question
                result = answer(library, body.get("question", ""), body.get("creator", ""), body.get("history", []))
                self._json(result)
            except ConversationUnavailable as exc:
                self._json({"error": str(exc), "code": "conversation_unavailable"}, 503)
            except (ValueError, TypeError, UnicodeError) as exc:
                self._json({"error": str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self._json({"error": "Could not answer from the local library."}, 500)

        def _media(self, post_id):
            post = library.get_post(post_id)
            media = post.get("media", {}) if post else {}
            if isinstance(media, list):
                media = media[0] if media else {}
            relative = media.get("path") if isinstance(media, dict) else None
            source = (Path(library_root) / relative).resolve() if relative else None
            if not source or not any(source.is_relative_to(root) for root in media_roots) or not source.is_file() or source.suffix.lower() not in VIDEO_TYPES:
                return self._json({"error": "Local video not available."}, 404)
            total = source.stat().st_size
            start, end, status = 0, total - 1, 200
            range_value = self.headers.get("Range")
            if range_value:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_value)
                if not match or not any(match.groups()):
                    return self._range_error(total)
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), total - 1) if last else total - 1
                else:
                    start = max(0, total - int(last))
                if start > end or start >= total:
                    return self._range_error(total)
                status = 206
            extra = {"Accept-Ranges": "bytes"}
            if status == 206:
                extra["Content-Range"] = f"bytes {start}-{end}/{total}"
            length = end - start + 1
            kind = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            self._headers(status, kind, length, extra)
            with source.open("rb") as handle:
                handle.seek(start)
                while length > 0:
                    chunk = handle.read(min(length, 262144))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    length -= len(chunk)

        def _range_error(self, total):
            self._headers(416, "text/plain", 0, {"Content-Range": f"bytes */{total}"})

    class LocalServer(ThreadingHTTPServer):
        def server_close(self):
            worker = getattr(self, "creator_worker", None)
            if worker:
                worker.stop()
            super().server_close()

    server = LocalServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.creator_jobs = jobs
    server.creator_worker = None
    if start_worker:
        from .worker import CreatorWorker
        server.creator_worker = CreatorWorker(library, jobs, creator_data)
        server.creator_worker.start()
    return server


def serve(library_root: Path, port=8765, media_root: Path | None = None):
    with create_server(library_root, port, media_root, start_worker=True) as server:
        print(f"InstaNT: http://127.0.0.1:{server.server_address[1]}", flush=True)
        print("Local pilot corpus. Press Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
