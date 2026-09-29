"""Import bookmarks from Chrome / Firefox / Safari JSON exports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from .bookmarks import BookmarkStore
from .db import BrowserDB


def _chrome_walk(node: dict, folder_id_lookup) -> Iterable[tuple[str, str, int]]:
    """Yield (type, url, folder_id) for Chrome bookmarks JSON node.

    Chrome's format:
        {"type": "url",    "url": "...", "name": "..."}
        {"type": "folder", "children": [...]}
    """
    node_type = node.get("type")
    if node_type == "url":
        yield ("url", node.get("url", ""), node.get("name", node.get("url", "")))
    elif node_type == "folder":
        folder_id = folder_id_lookup(node.get("name", ""))
        for child in node.get("children", []) or []:
            if child.get("type") == "url":
                yield ("url", child.get("url", ""), child.get("name", child.get("url", "")))
            elif child.get("type") == "folder":
                # Recurse with the new folder as the parent
                sub_id = folder_id_lookup(child.get("name", ""))
                for grand in child.get("children", []) or []:
                    yield from _chrome_walk(grand, lambda _n: sub_id)


def _firefox_walk(node: dict, folder_id_lookup) -> Iterable[tuple[str, str, str, int]]:
    """Yield (type, url, name, folder_id) for Firefox bookmarks HTML/JSON.

    Firefox JSON (places export) uses this shape:
        {"guid": "...", "type": "bookmark", "title": "...", "url": "...", "children": []}
        {"type": "folder", "title": "...", "children": [...]}
    """
    node_type = node.get("type")
    title = node.get("title", node.get("name", ""))
    if node_type in ("bookmark", "url"):
        url = node.get("url") or node.get("uri", "")
        yield ("url", url, title, node.get("parent_folder_id", 1))
    elif node_type == "folder":
        folder_name = title or "Imported"
        folder_id = folder_id_lookup(folder_name)
        for child in node.get("children", []) or []:
            yield from _firefox_walk(child, folder_id_lookup)


def import_chrome_json(path: str | Path, store: Optional[BookmarkStore] = None) -> int:
    """Import Chrome bookmarks from JSON export. Returns count imported."""
    store = store or BookmarkStore()
    p = Path(path)
    if not p.exists():
        return 0
    data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    # Chrome root has "roots": {"bookmark_bar": {...}, "other": {...}, "synced": {...}}
    roots = data.get("roots", {})

    # Map of folder name -> id; create on demand
    folder_cache: dict[str, int] = {"": 1}

    def get_folder_id(name: str) -> int:
        if name in folder_cache:
            return folder_cache[name]
        folder_cache[name] = store.create_folder(name)
        return folder_cache[name]

    count = 0
    for root_name in ("bookmark_bar", "other", "synced"):
        root = roots.get(root_name)
        if not root:
            continue
        # The root name maps to a folder; create folders under it
        root_folder_name = root_name.replace("_", " ").title()
        root_id = get_folder_id(root_folder_name)
        for child in root.get("children", []) or []:
            if child.get("type") == "url":
                url = child.get("url", "")
                title = child.get("name", url)
                if url:
                    store.add(url, title, folder_id=root_id)
                    count += 1
            elif child.get("type") == "folder":
                folder_name = child.get("name") or "Imported Folder"
                folder_id = get_folder_id(folder_name)
                for grand in child.get("children", []) or []:
                    if grand.get("type") == "url" and grand.get("url"):
                        store.add(grand["url"], grand.get("name", grand["url"]),
                                   folder_id=folder_id)
                        count += 1
    return count


def import_firefox_json(path: str | Path, store: Optional[BookmarkStore] = None) -> int:
    """Import Firefox bookmarks from JSON.

    Handles two common shapes:
      1. Firefox places export: {"children": [...]}
      2. Third-party exports that wrap it: {"root": {"children": [...]}}
    """
    store = store or BookmarkStore()
    p = Path(path)
    if not p.exists():
        return 0
    data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    # Resolve the root containing children
    if isinstance(data, dict):
        if "children" in data and isinstance(data["children"], list):
            children_root = data
        elif "root" in data and isinstance(data["root"], dict):
            children_root = data["root"]
        else:
            return 0
    elif isinstance(data, list):
        # Bare list of bookmarks
        children_root = {"children": data}
    else:
        return 0

    folder_cache = {"Bookmarks Bar": 1}

    def get_folder_id(name: str) -> int:
        if name in folder_cache:
            return folder_cache[name]
        folder_cache[name] = store.create_folder(name or "Imported")
        return folder_cache[name]

    count = 0
    def walk(node: dict, parent_folder_id: int = 1) -> None:
        nonlocal count
        kind = node.get("type")
        title = node.get("title") or node.get("name") or ""
        url = node.get("url") or node.get("uri")
        # Many bookmark export formats use different type keys. Treat any node
        # with a URL as a leaf bookmark regardless of its declared type.
        if url and isinstance(url, str) and url.startswith(("http://", "https://", "ftp://")):
            store.add(url, title or url, folder_id=parent_folder_id)
            count += 1
        # Recurse into anything that has children (folder or container)
        children = node.get("children")
        if isinstance(children, list):
            new_folder_id = parent_folder_id
            if kind in ("text/x-moz-place-container", "folder"):
                new_folder_id = get_folder_id(title or "Imported")
            for grand in children:
                walk(grand, new_folder_id)

    for child in children_root.get("children", []) or []:
        walk(child, 1)
    return count
