"""Initialize an empty runtime library from the bundled public caption pilot.

This performs only local file work. Existing libraries, including edited demo
records, are reused without replacement or an unsolicited collection request.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .demo import _dump, _linked, _sanitize
from .library import Library, _creator, _post_id


BUNDLED_RECORDS = Path(__file__).parent / "demo_seed" / "records"


def _existing(root):
    if not root.exists():
        if _linked(root):
            raise ValueError("The runtime library must not be a symbolic link or junction.")
        return False
    if _linked(root) or not root.is_dir():
        raise ValueError("The runtime library must be an ordinary directory.")
    for entry in root.iterdir():
        # Library() can have created these two empty directories before the
        # first bootstrap. Any other existing content belongs to the user.
        if (_linked(entry) or entry.name not in {"records", "notes"}
                or not entry.is_dir() or any(entry.iterdir())):
            return True
    return False


def _preserved(root):
    records = root / "records"
    count = (sum(path.is_file() and not _linked(path) and path.suffix == ".json"
                 for path in records.iterdir())
             if records.is_dir() and not _linked(records) else 0)
    return {"initialized": False, "preserved_existing": True, "posts": count,
            "detail": "Using the existing library; no records were replaced."}


def _read_seed(directory):
    if _linked(directory) or not directory.is_dir():
        raise ValueError("The bundled demo records are unavailable.")
    posts = []
    for path in sorted(directory.iterdir()):
        if _linked(path) or not path.is_file() or path.suffix != ".json":
            raise ValueError("The demo seed must contain ordinary JSON source records only.")
        post = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(post, dict) or _post_id(post.get("id")) != path.stem:
            raise ValueError("A demo source record does not match its filename.")
        posts.append(_sanitize(post, _creator(post.get("creator"))))
    if not posts:
        raise ValueError("The bundled demo records are empty.")
    return posts


def bootstrap_demo(library_root=Path("data/library"), *, seed_records=None):
    """Create the pilot once; never merge it into or overwrite a user library.

    A complete new library is built in a private sibling staging directory,
    then renamed into place. A failed validation/index build leaves the target
    untouched. Repeated invocation preserves canonical and derived files.
    """
    requested = Path(library_root)
    if _linked(requested):
        raise ValueError("The runtime library must not be a symbolic link or junction.")
    root = requested.resolve()
    if _existing(root):
        return _preserved(root)
    posts = _read_seed(Path(seed_records) if seed_records is not None else BUNDLED_RECORDS)
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="instant-demo-bootstrap-", dir=root.parent) as temporary:
        staged = Library(Path(temporary) / "library")
        for post in posts:
            (staged.records_dir / f"{post['id']}.json").write_text(_dump(post), encoding="utf-8")
        index = staged.rebuild()
        # Recheck after staging in case another process prepared the library.
        if _existing(root):
            return _preserved(root)
        if root.exists():
            for entry in root.iterdir():
                entry.rmdir()  # Only the verified empty records/notes folders.
            root.rmdir()
        staged.root.rename(root)
    return {"initialized": True, "preserved_existing": False, "posts": len(posts),
            "segments": index["segments"],
            "captions": sum(any(item["modality"] == "caption" for item in post["segments"]) for post in posts),
            "speech": sum(any(item["modality"] == "speech" for item in post["segments"]) for post in posts),
            "ocr": sum(any(item["modality"] == "ocr" for item in post["segments"]) for post in posts),
            "media_retained": False,
            "detail": "Prepared the bundled partial caption pilot without fetching media or models."}
