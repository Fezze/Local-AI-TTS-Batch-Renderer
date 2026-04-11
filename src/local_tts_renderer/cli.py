from __future__ import annotations

import argparse
import atexit
import ctypes
import html
import json
import os
import posixpath
import re
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable
import xml.etree.ElementTree as ET

import numpy as np
import requests
import soundfile as sf
import lameenc
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, COMM, ID3NoHeaderError, TPUB
from local_tts_renderer.providers import parse_provider_priority, resolve_provider


MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
DEFAULT_VOICE = "af_bella"
DEFAULT_LANG = "en-us"
DEFAULT_SPEED = 0.9
DEFAULT_MAX_CHARS = 850
DEFAULT_SILENCE_MS = 250
DEFAULT_MAX_PART_MINUTES = 30
GROUP_PATH_SEPARATOR = " / "
DEFAULT_TRIM_MODE = "off"
DEFAULT_HEARTBEAT_SECONDS = 30.0
_ORT = None
_KOKORO_CLASS = None


@dataclass
class Chunk:
    index: int
    heading: str | None
    text: str


@dataclass
class Chapter:
    title: str
    text: str
    group: str | None = None


@dataclass
class TocNode:
    title: str
    href: str | None = None
    children: list["TocNode"] | None = None


@dataclass
class AudioMetadata:
    source_title: str
    author: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    language: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert English Markdown files to speech with Kokoro ONNX.")
    parser.add_argument("--input", nargs="+", help="Markdown file(s) to process.")
    parser.add_argument("--output-dir", default=".\\out", help="Directory for generated audio and manifests.")
    parser.add_argument("--model-dir", default="models", help="Directory for Kokoro model files.")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="Kokoro voice id, for example af_bella.")
    parser.add_argument("--lang", default=DEFAULT_LANG, help="Language code for Kokoro ONNX.")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED, help="Speech speed multiplier.")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="Max text characters per chunk.")
    parser.add_argument("--silence-ms", type=int, default=DEFAULT_SILENCE_MS, help="Silence inserted between chunks.")
    parser.add_argument("--max-part-minutes", type=float, default=DEFAULT_MAX_PART_MINUTES, help="Maximum duration per output audio file.")
    parser.add_argument("--keep-chunks", action="store_true", help="Write one WAV file per chunk.")
    parser.add_argument("--mp3-only", action="store_true", default=True, help="Write only MP3 output files and skip WAV files on disk.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--wav-to-mp3", help="Convert an existing WAV file to MP3 without rerunning TTS.")
    parser.add_argument("--mp3-bitrate", type=int, default=192, help="MP3 bitrate in kbps for WAV to MP3 conversion.")
    parser.add_argument("--list-chapters", action="store_true", help="Print extracted chapter info and exit without generating audio.")
    parser.add_argument("--chapter-index", type=int, help="Render only one extracted chapter by 1-based index.")
    parser.add_argument("--output-subdir", help="Optional output subdirectory under --output-dir for chapter batch jobs.")
    parser.add_argument("--output-name", help="Optional base output name for chapter batch jobs.")
    parser.add_argument("--trim-mode", choices=["full", "light", "off"], default=DEFAULT_TRIM_MODE, help="Silence trimming mode.")
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS, help="Emit periodic heartbeat lines while rendering.")
    parser.add_argument("--providers", help="Comma-separated ONNX provider priority, for example CUDAExecutionProvider,CPUExecutionProvider.")
    parser.add_argument("--temp-dir", help="Optional temp directory used by runtime dependencies such as phonemizer.")
    return parser.parse_args()


def is_debug_enabled() -> bool:
    value = os.environ.get("LOCAL_TTS_DEBUG", "").strip().lower()
    return value in {"1", "true", "yes", "on", "debug"}


def debug_trace(message: str) -> None:
    if not is_debug_enabled():
        return
    print(f"[run:debug {time.strftime('%H:%M:%S')}] {message}", flush=True)


def configure_runtime_temp_dir(output_dir: Path, temp_dir: str | None = None) -> Path:
    preferred = temp_dir or os.environ.get("LOCAL_TTS_TEMP_DIR") or os.environ.get("TEMP") or os.environ.get("TMP")
    if preferred:
        root = Path(preferred).expanduser().resolve()
    else:
        root = Path(tempfile.gettempdir()).resolve() / "local-tts-runtime"
    root.mkdir(parents=True, exist_ok=True)
    resolved = str(root)
    os.environ["TMPDIR"] = resolved
    os.environ["TEMP"] = resolved
    os.environ["TMP"] = resolved
    tempfile.tempdir = resolved
    return root


def enable_windows_espeak_fallback() -> None:
    if os.name != "nt":
        return
    try:
        from phonemizer.backend.espeak import api as espeak_api
    except Exception:
        return

    if getattr(espeak_api.EspeakAPI, "_local_tts_patch_enabled", False):
        return

    original_init = espeak_api.EspeakAPI.__init__
    original_delete = espeak_api.EspeakAPI._delete

    def patched_init(self, library, data_path):
        try:
            return original_init(self, library, data_path)
        except PermissionError:
            encoded_data_path = None if data_path is None else str(data_path).encode("utf-8")
            try:
                espeak_lib = ctypes.cdll.LoadLibrary(str(library))
                library_path = espeak_api.EspeakAPI._shared_library_path(espeak_lib)
                del espeak_lib
            except OSError as error:
                raise RuntimeError(f"failed to load espeak library: {str(error)}") from None

            self._tempdir = tempfile.mkdtemp()
            atexit.register(self._delete_win32)
            self._library = ctypes.cdll.LoadLibrary(str(library_path))
            try:
                if self._library.espeak_Initialize(0x02, 0, encoded_data_path, 0) <= 0:
                    raise RuntimeError("failed to initialize espeak shared library")
            except AttributeError:
                raise RuntimeError("failed to load espeak library") from None

            self._library_path = library_path
            print(json.dumps({"espeak_copy_fallback": True, "library_path": str(library_path)}), flush=True)

    def patched_delete(library, tempdir):
        try:
            original_delete(library, tempdir)
        except Exception:
            return

    espeak_api.EspeakAPI.__init__ = patched_init
    espeak_api.EspeakAPI._delete = staticmethod(patched_delete)
    espeak_api.EspeakAPI._local_tts_patch_enabled = True


def start_progress_heartbeat(progress_state: dict, interval_seconds: float) -> tuple[threading.Event, threading.Thread | None]:
    stop_event = threading.Event()
    if interval_seconds <= 0:
        return stop_event, None

    def emit() -> None:
        while not stop_event.wait(interval_seconds):
            print(
                json.dumps(
                    {
                        "heartbeat": True,
                        "chapter_index": progress_state.get("chapter_index"),
                        "chapter_title": progress_state.get("chapter_title"),
                        "completed_chunks": progress_state.get("completed_chunks", 0),
                        "total_chunks": progress_state.get("total_chunks", 0),
                    }
                ),
                flush=True,
            )

    thread = threading.Thread(target=emit, name="tts-heartbeat", daemon=True)
    thread.start()
    return stop_event, thread


def ensure_file(path: Path, url: str) -> None:
    if path.exists():
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for part in response.iter_content(chunk_size=1024 * 1024):
                if part:
                    handle.write(part)


def ensure_model_files(model_dir: Path) -> tuple[Path, Path]:
    model_path = model_dir / "kokoro-v1.0.onnx"
    voices_path = model_dir / "voices-v1.0.bin"
    ensure_file(model_path, MODEL_URL)
    ensure_file(voices_path, VOICES_URL)
    return model_path, voices_path


def get_onnxruntime():
    global _ORT
    if _ORT is None:
        print("[run:bootstrap] loading onnxruntime...", flush=True)
        import onnxruntime as ort  # type: ignore

        _ORT = ort
    return _ORT


def get_kokoro_class():
    global _KOKORO_CLASS
    if _KOKORO_CLASS is None:
        print("[run:bootstrap] loading kokoro_onnx...", flush=True)
        from kokoro_onnx import Kokoro as KokoroClass  # type: ignore

        _KOKORO_CLASS = KokoroClass
    return _KOKORO_CLASS


def configure_onnx_provider(provider_priority: list[str] | None = None) -> str:
    ort = get_onnxruntime()
    available = ort.get_available_providers()
    requested = os.environ.get("ONNX_PROVIDER")
    requested_list = [requested] if requested else []
    resolution = resolve_provider(available=available, requested=requested_list, fallback=provider_priority)
    os.environ["ONNX_PROVIDER"] = resolution.selected
    return resolution.selected


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return slug or "document"


def sanitize_filename_component(value: str) -> str:
    value = html.unescape(value).replace("\r\n", "\n")
    value = re.sub(r"\s+", " ", value.replace("\n", " ")).strip()
    value = value.replace(":", " -")
    value = re.sub(r'[<>:"/\\\\|?*]', "-", value)
    value = re.sub(r"\s*-\s*", " - ", value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    return value or "Document"


def strip_front_matter(text: str) -> str:
    return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)


def clean_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = strip_front_matter(text)
    text = re.sub(r"```.*?```", "\n", text, flags=re.DOTALL)
    text = re.sub(r"~~~.*?~~~", "\n", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue

        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*\d+\.\s+", "", line)

        if re.fullmatch(r"[-|:\s]+", line):
            continue

        line = line.replace("|", " ")
        line = line.replace("*", "")
        line = line.replace("_", " ")
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_plain_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_markdown_chapters(text: str, fallback_title: str) -> list[Chapter]:
    lines = text.replace("\r\n", "\n").splitlines()
    chapters: list[Chapter] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in lines:
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            if current_lines:
                chapter_text = clean_markdown("\n".join(current_lines))
                if chapter_text:
                    chapters.append(Chapter(title=current_title or fallback_title, text=chapter_text))
            current_title = match.group(1).strip()
            current_lines = []
            continue
        current_lines.append(line)

    if current_lines:
        chapter_text = clean_markdown("\n".join(current_lines))
        if chapter_text:
            chapters.append(Chapter(title=current_title or fallback_title, text=chapter_text))

    return chapters or [Chapter(title=fallback_title, text=clean_markdown(text))]


def normalize_epub_path(base_path: str, href: str) -> str:
    base_dir = str(PurePosixPath(base_path).parent)
    if base_dir == ".":
        return posixpath.normpath(href)
    return posixpath.normpath(posixpath.join(base_dir, href))


def strip_href_fragment(href: str) -> str:
    return href.split("#", 1)[0]


def parse_ncx_navpoints(navpoints: list[ET.Element], package_path: str) -> list[TocNode]:
    nodes: list[TocNode] = []
    for navpoint in navpoints:
        text_node = navpoint.find(".//{*}navLabel/{*}text")
        content_node = navpoint.find("{*}content")
        title = clean_plain_text("".join(text_node.itertext())) if text_node is not None else "Untitled"
        href = None
        if content_node is not None and "src" in content_node.attrib:
            href = normalize_epub_path(package_path, strip_href_fragment(content_node.attrib["src"]))
        children = parse_ncx_navpoints(navpoint.findall("{*}navPoint"), package_path)
        nodes.append(TocNode(title=title, href=href, children=children))
    return nodes


def load_epub_toc(archive: zipfile.ZipFile, package_path: str, package_xml: ET.Element) -> list[TocNode]:
    manifest_items = package_xml.findall(".//{*}manifest/{*}item")
    ncx_href = None
    for item in manifest_items:
        media_type = item.attrib.get("media-type", "")
        if media_type == "application/x-dtbncx+xml":
            ncx_href = normalize_epub_path(package_path, item.attrib["href"])
            break

    if not ncx_href:
        return []

    ncx_xml = ET.fromstring(archive.read(ncx_href))
    nav_map = ncx_xml.find(".//{*}navMap")
    if nav_map is None:
        return []
    return parse_ncx_navpoints(nav_map.findall("{*}navPoint"), package_path)


def load_epub_toc_from_path(path: Path) -> list[TocNode]:
    with zipfile.ZipFile(path) as archive:
        container_xml = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container_xml.find(".//{*}rootfile")
        if rootfile is None:
            raise RuntimeError(f"EPUB container missing rootfile: {path}")
        package_path = rootfile.attrib["full-path"]
        package_xml = ET.fromstring(archive.read(package_path))
        return load_epub_toc(archive, package_path, package_xml)


def extract_epub_metadata(path: Path) -> AudioMetadata:
    with zipfile.ZipFile(path) as archive:
        container_xml = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container_xml.find(".//{*}rootfile")
        if rootfile is None:
            raise RuntimeError(f"EPUB container missing rootfile: {path}")

        package_path = rootfile.attrib["full-path"]
        package_xml = ET.fromstring(archive.read(package_path))
        metadata = package_xml.find(".//{*}metadata")

        def first_text(pattern: str) -> str | None:
            if metadata is None:
                return None
            node = metadata.find(pattern)
            if node is None or node.text is None:
                return None
            text = clean_plain_text(node.text)
            return text or None

        title = first_text(".//{*}title") or path.stem
        return AudioMetadata(
            source_title=title,
            author=first_text(".//{*}creator"),
            publisher=first_text(".//{*}publisher"),
            published_date=first_text(".//{*}date"),
            language=first_text(".//{*}language"),
        )


def build_toc_lookup(nodes: list[TocNode], path: list[str] | None = None, lookup: dict[str, tuple[list[str], bool]] | None = None) -> dict[str, tuple[list[str], bool]]:
    path = path or []
    lookup = lookup or {}
    for node in nodes:
        current_path = [*path, node.title]
        has_children = bool(node.children)
        if node.href:
            lookup[node.href] = (current_path, has_children)
        if node.children:
            build_toc_lookup(node.children, current_path, lookup)
    return lookup


def print_toc_tree(nodes: list[TocNode], depth: int = 0) -> None:
    indent = "  " * depth
    for node in nodes:
        print(f"{indent}{node.title}")
        if node.children:
            print_toc_tree(node.children, depth + 1)


def build_group_directory_map_from_toc(nodes: list[TocNode], selected_groups: set[str]) -> dict[str, Path]:
    mapping: dict[str, Path] = {}

    def walk(level_nodes: list[TocNode], parent_titles: list[str], parent_dirs: list[str]) -> None:
        for index, node in enumerate(level_nodes, start=1):
            current_titles = [*parent_titles, node.title]
            current_group = join_group_path(current_titles)
            current_dir = f"{index:02d}-{sanitize_filename_component(node.title)}"
            current_dirs = [*parent_dirs, current_dir]
            if current_group in selected_groups:
                mapping[current_group] = Path(*(current_dirs if node.children else parent_dirs))
            if node.children:
                walk(node.children, current_titles, current_dirs)

    walk(nodes, [], [])
    return mapping


def get_group_leaf_title(group: str | None) -> str:
    parts = split_group_path(group)
    return parts[-1] if parts else "part"


def extract_epub_chapters(path: Path) -> list[Chapter]:
    chapters: list[Chapter] = []
    current_group: str | None = None
    candidate_book_title: str | None = None
    with zipfile.ZipFile(path) as archive:
        container_xml = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container_xml.find(".//{*}rootfile")
        if rootfile is None:
            raise RuntimeError(f"EPUB container missing rootfile: {path}")

        package_path = rootfile.attrib["full-path"]
        package_xml = ET.fromstring(archive.read(package_path))
        manifest = {
            item.attrib["id"]: normalize_epub_path(package_path, item.attrib["href"])
            for item in package_xml.findall(".//{*}manifest/{*}item")
            if "id" in item.attrib and "href" in item.attrib
        }
        spine_ids = [item.attrib["idref"] for item in package_xml.findall(".//{*}spine/{*}itemref") if "idref" in item.attrib]

        for spine_id in spine_ids:
            item_path = manifest.get(spine_id)
            if not item_path:
                continue
            if not item_path.lower().endswith((".xhtml", ".html", ".htm", ".xml")):
                continue

            try:
                doc = ET.fromstring(archive.read(item_path))
            except ET.ParseError:
                continue

            body = doc.find(".//{*}body")
            if body is None:
                continue

            title_node = body.find(".//{*}h1")
            if title_node is None:
                title_node = body.find(".//{*}h2")
            if title_node is None:
                title_node = doc.find(".//{*}title")
            title = clean_plain_text(" ".join(title_node.itertext())) if title_node is not None else PurePosixPath(item_path).stem
            text = clean_plain_text("\n".join(body.itertext()))
            if text:
                normalized_title = title or PurePosixPath(item_path).stem
                short_text = re.sub(r"\s+", " ", text[:400]).strip()
                if re.fullmatch(r"(book|volume)\s+[a-z0-9ivx\- ].*", normalized_title.strip(), flags=re.IGNORECASE):
                    current_group = candidate_book_title or current_group or normalized_title.strip()
                elif looks_like_book_title(normalized_title, text):
                    current_group = normalized_title.strip()
                    candidate_book_title = normalized_title.strip()
                elif (
                    len(normalized_title) <= 80
                    and re.fullmatch(r"[A-Z][A-Za-z0-9'’&,:;\- ]+", normalized_title)
                    and len(text) <= 120
                ):
                    candidate_book_title = normalized_title.strip()
                elif candidate_book_title and candidate_book_title.lower() in short_text.lower():
                    current_group = candidate_book_title
                    candidate_book_title = None

                chapters.append(Chapter(title=normalized_title, text=text, group=current_group))

    if not chapters:
        raise RuntimeError(f"No readable spine chapters found in EPUB: {path}")
    return chapters


def extract_epub_chapters_dynamic(path: Path) -> list[Chapter]:
    chapters: list[Chapter] = []
    debug_trace(f"epub_dynamic:start path={path}")
    with zipfile.ZipFile(path) as archive:
        debug_trace(f"epub_dynamic:zip_opened entries={len(archive.namelist())}")
        container_xml = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container_xml.find(".//{*}rootfile")
        if rootfile is None:
            raise RuntimeError(f"EPUB container missing rootfile: {path}")

        package_path = rootfile.attrib["full-path"]
        debug_trace(f"epub_dynamic:package_path={package_path}")
        package_xml = ET.fromstring(archive.read(package_path))
        manifest = {
            item.attrib["id"]: normalize_epub_path(package_path, item.attrib["href"])
            for item in package_xml.findall(".//{*}manifest/{*}item")
            if "id" in item.attrib and "href" in item.attrib
        }
        debug_trace(f"epub_dynamic:manifest_items={len(manifest)}")
        spine_ids = [item.attrib["idref"] for item in package_xml.findall(".//{*}spine/{*}itemref") if "idref" in item.attrib]
        debug_trace(f"epub_dynamic:spine_items={len(spine_ids)}")
        toc_lookup = build_toc_lookup(load_epub_toc(archive, package_path, package_xml))
        debug_trace(f"epub_dynamic:toc_lookup={len(toc_lookup)}")

        for idx, spine_id in enumerate(spine_ids, start=1):
            if idx == 1 or idx % 20 == 0:
                debug_trace(f"epub_dynamic:spine_progress idx={idx}/{len(spine_ids)} spine_id={spine_id}")
            item_path = manifest.get(spine_id)
            if not item_path or not item_path.lower().endswith((".xhtml", ".html", ".htm", ".xml")):
                continue

            try:
                doc = ET.fromstring(archive.read(item_path))
            except ET.ParseError:
                continue

            body = doc.find(".//{*}body")
            if body is None:
                continue

            title_node = body.find(".//{*}h1")
            if title_node is None:
                title_node = body.find(".//{*}h2")
            if title_node is None:
                title_node = doc.find(".//{*}title")

            title = clean_plain_text(" ".join(title_node.itertext())) if title_node is not None else PurePosixPath(item_path).stem
            text = clean_plain_text("\n".join(body.itertext()))
            if not text:
                continue

            normalized_title = title or PurePosixPath(item_path).stem
            toc_entry = toc_lookup.get(item_path)
            group = None
            if toc_entry:
                toc_path, has_children = toc_entry
                group_parts = toc_path if has_children else toc_path[:-1]
                group = join_group_path(group_parts)
                if toc_path:
                    normalized_title = toc_path[-1]

            chapters.append(Chapter(title=normalized_title, text=text, group=group))

    if not chapters:
        raise RuntimeError(f"No readable spine chapters found in EPUB: {path}")
    debug_trace(f"epub_dynamic:done chapters={len(chapters)}")
    return chapters


def load_chapters(source_path: Path) -> list[Chapter]:
    suffix = source_path.suffix.lower()
    debug_trace(f"load_chapters:start path={source_path} suffix={suffix}")
    if suffix == ".epub":
        chapters = extract_epub_chapters_dynamic(source_path)
        debug_trace(f"load_chapters:epub_done chapters={len(chapters)}")
        return chapters

    raw_text = source_path.read_text(encoding="utf-8")
    chapters = split_markdown_chapters(raw_text, fallback_title=source_path.stem)
    debug_trace(f"load_chapters:markdown_done chapters={len(chapters)}")
    return chapters


def is_short_structure_marker(title: str, text: str) -> bool:
    normalized_title = re.sub(r"\s+", " ", title).strip()
    normalized_text = re.sub(r"\s+", " ", text).strip()
    if not normalized_title:
        return False
    if len(normalized_title) > 100 or len(normalized_text) > 400:
        return False
    if re.fullmatch(r"(chapter|prologue|epilogue)\b.*", normalized_title, flags=re.IGNORECASE):
        return False
    return True


def is_content_chapter(title: str, text: str) -> bool:
    normalized_title = re.sub(r"\s+", " ", title).strip()
    normalized_text = re.sub(r"\s+", " ", text).strip()
    if len(normalized_text) > 2000:
        return True
    return bool(re.fullmatch(r"(chapter|prologue|epilogue)\b.*", normalized_title, flags=re.IGNORECASE))


def is_sublevel_marker(title: str) -> bool:
    normalized_title = re.sub(r"\s+", " ", title).strip()
    return bool(re.fullmatch(r"(book|part|volume|section)\b.*", normalized_title, flags=re.IGNORECASE))


def join_group_path(parts: list[str]) -> str | None:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return GROUP_PATH_SEPARATOR.join(cleaned) if cleaned else None


def split_group_path(group: str | None) -> list[str]:
    if not group:
        return []
    return [part.strip() for part in group.split(GROUP_PATH_SEPARATOR) if part.strip()]


def build_group_directory_map(chapters: list[Chapter]) -> dict[str, Path]:
    counters_by_parent: dict[str, dict[str, int]] = {}
    mapping: dict[str, Path] = {}
    for chapter in chapters:
        if not chapter.group or chapter.group in mapping:
            continue

        raw_parts = split_group_path(chapter.group)
        parent_parts: list[str] = []
        numbered_parts: list[str] = []
        for raw_part in raw_parts:
            parent_key = join_group_path(parent_parts) or ""
            sibling_map = counters_by_parent.setdefault(parent_key, {})
            if raw_part not in sibling_map:
                sibling_map[raw_part] = len(sibling_map) + 1
            numbered_parts.append(f"{sibling_map[raw_part]:02d}-{slugify(raw_part)}")
            parent_parts.append(raw_part)

        mapping[chapter.group] = Path(*numbered_parts)
    return mapping


def summarize_chapters(chapters: list[Chapter]) -> list[dict]:
    summary: list[dict] = []
    for index, chapter in enumerate(chapters, start=1):
        text = chapter.text.strip()
        if not text:
            continue
        summary.append(
            {
                "index": index,
                "group": chapter.group,
                "title": chapter.title,
                "chars": len(text),
                "words": len(text.split()),
                "preview": text[:160],
            }
        )
    return summary


def print_chapter_summary(source_path: Path, chapters: list[Chapter]) -> None:
    print(f"Source: {source_path}")
    current_group: str | None = None
    for summary in summarize_chapters(chapters):
        group = summary["group"]
        if group and group != current_group:
            current_group = group
            print(f"\n[{current_group}]")
        print(f"{summary['index']:03d}. {summary['title']}")


def print_output_structure_preview(source_path: Path, chapters: list[Chapter]) -> None:
    output_root_name = slugify(source_path.stem)
    groups = [chapter.group for chapter in chapters if chapter.group and chapter.text.strip()]
    if source_path.suffix.lower() == ".epub":
        toc_nodes = load_epub_toc_from_path(source_path)
        group_dir_map = build_group_directory_map_from_toc(toc_nodes, set(groups))
    else:
        group_dir_map = build_group_directory_map(chapters)

    print("\nPlanned output structure:")
    if groups:
        grouped_titles: dict[str, list[str]] = {}
        for chapter in chapters:
            if not chapter.group or not chapter.text.strip():
                continue
            grouped_titles.setdefault(chapter.group, [])
            title_slug = slugify(chapter.title)
            title_slug = sanitize_filename_component(chapter.title)
            if title_slug not in grouped_titles[chapter.group]:
                grouped_titles[chapter.group].append(title_slug)

        seen_dirs: set[Path] = set()
        print(f"out/{output_root_name}/")
        for group in dict.fromkeys(groups):
            group_dir = group_dir_map[group]
            path_parts = group_dir.parts
            for depth in range(len(path_parts)):
                prefix = Path(*path_parts[: depth + 1])
                if prefix in seen_dirs:
                    continue
                seen_dirs.add(prefix)
                indent = "  " * (depth + 1)
                print(f"{indent}{path_parts[depth]}/")
            file_indent = "  " * (len(path_parts) + 1)
            for title_slug in grouped_titles.get(group, []):
                print(f"{file_indent}{title_slug}")
    else:
        print(f"out/{output_root_name}/")


def build_chapter_number_map(chapters: list[Chapter]) -> dict[int, int]:
    counters: dict[str | None, int] = {}
    mapping: dict[int, int] = {}
    for index, chapter in enumerate(chapters, start=1):
        key = chapter.group
        counters[key] = counters.get(key, 0) + 1
        mapping[index] = counters[key]
    return mapping


def iter_sections(text: str) -> Iterable[tuple[str | None, str]]:
    heading: str | None = None
    body: list[str] = []

    for line in text.splitlines():
        if not line.strip():
            if body and body[-1] != "":
                body.append("")
            continue

        if len(line) < 120 and re.match(r"^[A-Z0-9][A-Za-z0-9 ,:'\"()/-]{0,118}$", line):
            if body:
                yield heading, "\n".join(body).strip()
                body = []
            heading = line
            continue

        body.append(line)

    if body:
        yield heading, "\n".join(body).strip()


def split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", normalized)
    return [part.strip() for part in parts if part.strip()]


def split_text_for_retry(text: str) -> list[str]:
    sentences = split_sentences(text)
    if len(sentences) > 1:
        midpoint = max(1, len(sentences) // 2)
        return [" ".join(sentences[:midpoint]).strip(), " ".join(sentences[midpoint:]).strip()]

    words = text.split()
    if len(words) > 1:
        midpoint = max(1, len(words) // 2)
        return [" ".join(words[:midpoint]).strip(), " ".join(words[midpoint:]).strip()]

    midpoint = max(1, len(text) // 2)
    return [text[:midpoint].strip(), text[midpoint:].strip()]


def light_trim_audio(samples: np.ndarray, sample_rate: int, threshold: float = 0.003, padding_ms: int = 40) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.size == 0:
        return audio
    non_silent = np.flatnonzero(np.abs(audio) > threshold)
    if non_silent.size == 0:
        return audio
    padding = int(sample_rate * padding_ms / 1000)
    start = max(int(non_silent[0]) - padding, 0)
    end = min(int(non_silent[-1]) + padding + 1, len(audio))
    return audio[start:end]


def create_audio_with_retry(
    kokoro: Kokoro,
    text: str,
    voice: str,
    speed: float,
    lang: str,
    trim_mode: str,
    depth: int = 0,
) -> tuple[list[np.ndarray], int]:
    try:
        use_full_trim = trim_mode == "full"
        samples, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang, trim=use_full_trim)
        normalized = np.asarray(samples, dtype=np.float32)
        if trim_mode == "light":
            normalized = light_trim_audio(normalized, sample_rate)
        return [normalized], sample_rate
    except Exception as exc:
        message = str(exc).lower()
        exc_name = type(exc).__name__.lower()
        retryable = (
            isinstance(exc, IndexError)
            or "bad allocation" in message
            or "onnxruntimeerror" in message
            or "arraymemoryerror" in exc_name
            or "unable to allocate" in message
        )
        if not retryable or depth >= 8 or len(text.strip()) < 20:
            raise

        parts = [part for part in split_text_for_retry(text) if part]
        if len(parts) < 2:
            raise

        combined_parts: list[np.ndarray] = []
        sample_rate: int | None = None
        for part in parts:
            sub_parts, current_rate = create_audio_with_retry(
                kokoro=kokoro,
                text=part,
                voice=voice,
                speed=speed,
                lang=lang,
                trim_mode=trim_mode,
                depth=depth + 1,
            )
            if sample_rate is None:
                sample_rate = current_rate
            elif current_rate != sample_rate:
                raise RuntimeError(f"Sample rate changed from {sample_rate} to {current_rate}.")
            combined_parts.extend(sub_parts)

        if sample_rate is None:
            raise RuntimeError("Retry split produced no audio.")
        return combined_parts, sample_rate


def load_resume_state(checkpoint_path: Path) -> dict | None:
    if not checkpoint_path.exists():
        return None
    return json.loads(checkpoint_path.read_text(encoding="utf-8"))


def save_resume_state(checkpoint_path: Path, state: dict) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def remove_output_files(part: dict) -> None:
    for key in ("wav_path", "mp3_path"):
        value = part.get(key)
        if not value:
            continue
        path = Path(value)
        if path.exists():
            path.unlink()


def compute_part_output_paths(
    output_root: Path,
    base_output_dir: Path,
    part_index: int,
    multi_part: bool,
    base_name: str,
    group_name: str | None,
    final_stem_override: str | None = None,
) -> tuple[Path, Path]:
    relative_root = output_root.relative_to(base_output_dir)
    wav_dir = base_output_dir / "wav" / relative_root
    mp3_dir = base_output_dir / "mp3" / relative_root

    if not multi_part and part_index == 1:
        final_name = final_stem_override or (relative_root.name if group_name is None else base_name)
        return wav_dir / f"{final_name}.wav", mp3_dir / f"{final_name}.mp3"
    return wav_dir / f"{part_index:02d}-{base_name}.wav", mp3_dir / f"{part_index:02d}-{base_name}.mp3"


def split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]


def flush_chunk_buffer(chunks: list[Chunk], chunk_index: int, heading: str | None, buffer: list[str]) -> int:
    text = " ".join(part.strip() for part in buffer if part.strip()).strip()
    if text:
        chunks.append(Chunk(index=chunk_index, heading=heading, text=text))
        return chunk_index + 1
    return chunk_index


def chunk_paragraph_text(chunks: list[Chunk], heading: str | None, paragraph: str, max_chars: int, chunk_index: int) -> int:
    sentences = split_sentences(paragraph)
    if not sentences:
        return chunk_index

    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        sentence_len = len(sentence)
        if current and current_len + 1 + sentence_len > max_chars:
            chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, current)
            current = [sentence]
            current_len = sentence_len
            continue

        if sentence_len > max_chars:
            if current:
                chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, current)
                current = []
                current_len = 0
            words = sentence.split()
            overflow: list[str] = []
            overflow_len = 0
            for word in words:
                add_len = len(word) if not overflow else len(word) + 1
                if overflow and overflow_len + add_len > max_chars:
                    chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, overflow)
                    overflow = [word]
                    overflow_len = len(word)
                else:
                    overflow.append(word)
                    overflow_len += add_len
            if overflow:
                chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, overflow)
            continue

        current.append(sentence)
        current_len += sentence_len if len(current) == 1 else sentence_len + 1

    if current:
        chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, current)
    return chunk_index


def chunk_section(heading: str | None, text: str, max_chars: int, start_index: int) -> list[Chunk]:
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []
    chunks: list[Chunk] = []
    chunk_index = start_index
    paragraph_buffer: list[str] = []
    paragraph_buffer_len = 0

    for paragraph in paragraphs:
        paragraph_len = len(paragraph)
        if paragraph_len > max_chars:
            if paragraph_buffer:
                chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, paragraph_buffer)
                paragraph_buffer = []
                paragraph_buffer_len = 0
            chunk_index = chunk_paragraph_text(chunks, heading, paragraph, max_chars, chunk_index)
            continue

        add_len = paragraph_len if not paragraph_buffer else paragraph_len + 2
        if paragraph_buffer and paragraph_buffer_len + add_len > max_chars:
            chunk_index = flush_chunk_buffer(chunks, chunk_index, heading, paragraph_buffer)
            paragraph_buffer = [paragraph]
            paragraph_buffer_len = paragraph_len
            continue

        paragraph_buffer.append(paragraph)
        paragraph_buffer_len += add_len if len(paragraph_buffer) > 1 else paragraph_len

    if paragraph_buffer:
        flush_chunk_buffer(chunks, chunk_index, heading, paragraph_buffer)
    return chunks


def build_chunks(cleaned_text: str, max_chars: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    chunk_index = 1
    for heading, section_text in iter_sections(cleaned_text):
        section_chunks = chunk_section(heading, section_text, max_chars=max_chars, start_index=chunk_index)
        chunks.extend(section_chunks)
        if section_chunks:
            chunk_index = section_chunks[-1].index + 1
    return chunks


def write_mp3_from_wav(wav_path: Path, mp3_path: Path, bitrate_kbps: int, force: bool) -> Path:
    if not wav_path.exists():
        raise FileNotFoundError(f"Missing WAV input: {wav_path}")
    if mp3_path.exists() and not force:
        raise FileExistsError(f"Output already exists: {mp3_path}. Use --force to overwrite.")

    audio, sample_rate = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if audio.ndim == 1:
        channels = 1
        pcm = np.clip(audio, -1.0, 1.0)
        interleaved = (pcm * 32767.0).astype(np.int16)
    else:
        channels = audio.shape[1]
        pcm = np.clip(audio, -1.0, 1.0)
        interleaved = (pcm * 32767.0).astype(np.int16).reshape(-1)

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate_kbps)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)

    mp3_bytes = encoder.encode(interleaved.tobytes())
    mp3_bytes += encoder.flush()
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    mp3_path.write_bytes(mp3_bytes)
    return mp3_path


def write_mp3_from_audio(audio: np.ndarray, sample_rate: int, mp3_path: Path, bitrate_kbps: int, force: bool) -> Path:
    if mp3_path.exists() and not force:
        raise FileExistsError(f"Output already exists: {mp3_path}. Use --force to overwrite.")

    if audio.ndim == 1:
        channels = 1
        pcm = np.clip(audio, -1.0, 1.0)
        interleaved = (pcm * 32767.0).astype(np.int16)
    else:
        channels = audio.shape[1]
        pcm = np.clip(audio, -1.0, 1.0)
        interleaved = (pcm * 32767.0).astype(np.int16).reshape(-1)

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate_kbps)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)

    mp3_bytes = encoder.encode(interleaved.tobytes())
    mp3_bytes += encoder.flush()
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    mp3_path.write_bytes(mp3_bytes)
    return mp3_path


def write_mp3_tags(mp3_path: Path, title: str, track_number: int, metadata: AudioMetadata, album_title: str | None = None) -> None:
    try:
        tags = EasyID3(str(mp3_path))
    except ID3NoHeaderError:
        tags = EasyID3()
    tags["title"] = [title]
    tags["tracknumber"] = [str(track_number)]
    tags["album"] = [album_title or metadata.source_title]
    if metadata.author:
        tags["artist"] = [metadata.author]
        tags["albumartist"] = [metadata.author]
    if metadata.published_date:
        tags["date"] = [metadata.published_date]
    tags.save(str(mp3_path))

    id3 = ID3(str(mp3_path))
    if metadata.source_title:
        id3.delall("COMM")
        id3.add(COMM(encoding=3, lang="eng", desc="source", text=metadata.source_title))
    if metadata.publisher:
        id3.delall("TPUB")
        id3.add(TPUB(encoding=3, text=metadata.publisher))
    id3.save(v2_version=3)


def extract_track_number(stem: str, fallback: int) -> int:
    match = re.match(r"^(\d+)", stem)
    if match:
        return int(match.group(1))
    return fallback


def build_output_paths(output_root: Path, part_count: int) -> list[tuple[Path, Path]]:
    if part_count <= 1:
        return [(output_root.with_suffix(".wav"), output_root.with_suffix(".mp3"))]
    return [
        (
            output_root.parent / f"{output_root.name}-part{part_index:02d}.wav",
            output_root.parent / f"{output_root.name}-part{part_index:02d}.mp3",
        )
        for part_index in range(1, part_count + 1)
    ]


class OutputPartWriter:
    def __init__(self, output_root: Path, base_output_dir: Path, part_index: int, multi_part: bool, sample_rate: int, force: bool, group_name: str | None = None, audio_metadata: AudioMetadata | None = None, mp3_only: bool = False, final_stem_override: str | None = None):
        self.part_index = part_index
        self.sample_rate = sample_rate
        self.group_name = group_name
        self.output_root = output_root
        self.base_output_dir = base_output_dir
        self.multi_part = multi_part
        self.audio_metadata = audio_metadata
        self.mp3_only = mp3_only
        self.final_stem_override = final_stem_override
        self.base_name = f"_part-{part_index:02d}"
        self.wav_path, self.mp3_path = compute_part_output_paths(output_root, base_output_dir, part_index, multi_part, self.base_name, group_name, final_stem_override)

        if not force and (self.wav_path.exists() or self.mp3_path.exists()):
            raise FileExistsError(f"Output already exists for {self.wav_path.stem}. Use --force to overwrite.")

        if not self.mp3_only:
            self.wav_path.parent.mkdir(parents=True, exist_ok=True)
        self.mp3_path.parent.mkdir(parents=True, exist_ok=True)
        self.wav_file = None if self.mp3_only else sf.SoundFile(str(self.wav_path), mode="w", samplerate=sample_rate, channels=1, subtype="PCM_16")
        self.encoder = lameenc.Encoder()
        self.encoder.set_bit_rate(192)
        self.encoder.set_in_sample_rate(sample_rate)
        self.encoder.set_channels(1)
        self.encoder.set_quality(2)
        self.mp3_handle = self.mp3_path.open("wb")
        self.chapter_titles: list[str] = []
        self.start_chunk: int | None = None
        self.end_chunk: int | None = None
        self.samples_written = 0
        print(
            json.dumps(
                {
                    "part_open": True,
                    "part": self.part_index,
                    "group": self.group_name,
                    "mp3_path": str(self.mp3_path),
                }
            ),
            flush=True,
        )

    def write_audio(self, audio: np.ndarray) -> None:
        mono_audio = np.asarray(audio, dtype=np.float32)
        if self.wav_file is not None:
            self.wav_file.write(mono_audio)
        pcm = (np.clip(mono_audio, -1.0, 1.0) * 32767.0).astype(np.int16)
        self.mp3_handle.write(self.encoder.encode(pcm.tobytes()))
        self.samples_written += len(mono_audio)

    def close(self) -> dict:
        self.mp3_handle.write(self.encoder.flush())
        self.mp3_handle.close()
        if self.wav_file is not None:
            self.wav_file.close()
        final_title = self.chapter_titles[0] if self.chapter_titles else get_group_leaf_title(self.group_name)
        final_base_name = sanitize_filename_component(self.final_stem_override or final_title)
        final_wav_path, final_mp3_path = compute_part_output_paths(
            self.output_root,
            self.base_output_dir,
            self.part_index,
            self.multi_part,
            final_base_name,
            self.group_name,
            self.final_stem_override,
        )
        if not self.mp3_only and self.wav_path != final_wav_path:
            if final_wav_path.exists():
                final_wav_path.unlink()
            self.wav_path.replace(final_wav_path)
        if self.mp3_path != final_mp3_path:
            if final_mp3_path.exists():
                final_mp3_path.unlink()
            self.mp3_path.replace(final_mp3_path)
        self.wav_path = final_wav_path
        self.mp3_path = final_mp3_path
        if self.audio_metadata is not None:
            album_title = get_group_leaf_title(self.group_name) if self.group_name else self.audio_metadata.source_title
            track_number = extract_track_number(self.mp3_path.stem, self.part_index)
            write_mp3_tags(self.mp3_path, final_title, track_number, self.audio_metadata, album_title=album_title)
        part_payload = {
            "part": self.part_index,
            "wav_path": None if self.mp3_only else str(self.wav_path),
            "mp3_path": str(self.mp3_path),
            "duration_seconds": self.samples_written / self.sample_rate,
            "group": self.group_name,
            "chapter_titles": self.chapter_titles,
            "start_chunk": self.start_chunk,
            "end_chunk": self.end_chunk,
        }
        print(
            json.dumps(
                {
                    "part_close": True,
                    "part": self.part_index,
                    "group": self.group_name,
                    "start_chunk": self.start_chunk,
                    "end_chunk": self.end_chunk,
                    "duration_seconds": part_payload["duration_seconds"],
                    "mp3_path": str(self.mp3_path),
                }
            ),
            flush=True,
        )
        return part_payload


def render_chunk_audio(
    kokoro,
    chunk: Chunk,
    chapter: Chapter,
    chapter_index: int,
    total_chapters: int,
    position_in_chapter: int,
    total_chapter_chunks: int,
    voice: str,
    lang: str,
    trim_mode: str,
    speed: float,
    silence_ms: int,
    keep_chunks: bool,
    chunk_dir: Path,
    progress_state: dict,
    expected_sample_rate: int | None,
) -> tuple[np.ndarray, int, dict]:
    progress_state["completed_chunks"] += 1
    completed_chunks = progress_state["completed_chunks"]
    total_chunks = progress_state["total_chunks"]
    chunk_started_at = time.time()

    audio_parts, current_rate = create_audio_with_retry(kokoro=kokoro, text=chunk.text, voice=voice, speed=speed, lang=lang, trim_mode=trim_mode)
    audio = np.concatenate(audio_parts)
    if expected_sample_rate is not None and current_rate != expected_sample_rate:
        raise RuntimeError(f"Sample rate changed from {expected_sample_rate} to {current_rate}.")

    if keep_chunks:
        chunk_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = chunk_dir / f"{chunk.index:04d}.wav"
        sf.write(chunk_path, audio, current_rate)

    if silence_ms > 0 and position_in_chapter < total_chapter_chunks:
        silence = np.zeros(int(current_rate * silence_ms / 1000), dtype=np.float32)
        audio = np.concatenate([audio, silence])

    manifest_chunk = {
        "index": chunk.index,
        "heading": chunk.heading,
        "chapter": chapter.title,
        "chars": len(chunk.text),
        "text": chunk.text,
    }

    elapsed = progress_state["elapsed_offset"] + (time.time() - progress_state["started_at"])
    display_completed = min(completed_chunks, total_chunks) if total_chunks else completed_chunks
    avg_per_chunk = elapsed / completed_chunks if completed_chunks else 0.0
    eta_seconds = avg_per_chunk * max(total_chunks - display_completed, 0)
    chunk_elapsed = time.time() - chunk_started_at
    percent = (display_completed / total_chunks) * 100 if total_chunks else 100.0
    should_log = total_chunks <= 3 or position_in_chapter == 1 or position_in_chapter == total_chapter_chunks or chunk_elapsed >= 1.0
    if should_log:
        print(
            f"[{display_completed}/{total_chunks}] {percent:5.1f}% "
            f"chapter={chapter_index}/{total_chapters} chunk={chunk.index} chars={len(chunk.text)} "
            f"chunk_time={chunk_elapsed:.1f}s elapsed={elapsed:.1f}s eta={eta_seconds:.1f}s",
            flush=True,
        )
    return audio, current_rate, manifest_chunk


def save_safe_checkpoint(
    checkpoint_path: Path,
    next_chapter_index: int,
    next_chunk_index: int,
    completed_chunks: int,
    elapsed_seconds: float,
    sample_rate: int | None,
    output_parts: list[dict],
    manifest_chunks: list[dict],
    next_group: str | None,
    next_part_index: int,
) -> None:
    save_resume_state(
        checkpoint_path,
        {
            "next_chapter_index": next_chapter_index,
            "next_chunk_index": next_chunk_index,
            "completed_chunks": completed_chunks,
            "elapsed_seconds": elapsed_seconds,
            "sample_rate": sample_rate,
            "output_parts": output_parts,
            "manifest_chunks": manifest_chunks,
            "next_group": next_group,
            "next_part_index": next_part_index,
        },
    )


def render_audio(
    kokoro,
    chapters: list[Chapter],
    base_output_dir: Path,
    output_root: Path,
    group_dir_map: dict[str, Path],
    voice: str,
    lang: str,
    trim_mode: str,
    speed: float,
    max_chars: int,
    silence_ms: int,
    max_part_minutes: float,
    keep_chunks: bool,
    mp3_only: bool,
    force: bool,
    audio_metadata: AudioMetadata | None = None,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    final_stem_override: str | None = None,
) -> dict:
    manifest_root = output_root / final_stem_override if final_stem_override else output_root
    manifest_path = manifest_root.with_suffix(".json")
    checkpoint_path = manifest_root.with_suffix(".resume.json")
    if not force and manifest_path.exists():
        raise FileExistsError(f"Output already exists for {output_root.name}. Use --force to overwrite.")

    chunk_dir = manifest_root.parent / f"{manifest_root.name}-chunks"
    keep_chunks = keep_chunks and not mp3_only
    resume_state = None if force else load_resume_state(checkpoint_path)
    manifest_chunks: list[dict] = resume_state.get("manifest_chunks", []) if resume_state else []
    chapter_chunk_counts = [len(chunk_section(chapter.title, chapter.text, max_chars=max_chars, start_index=1)) for chapter in chapters]
    chapter_start_indices: list[int] = []
    next_start_index = 1
    for chunk_count in chapter_chunk_counts:
        chapter_start_indices.append(next_start_index)
        next_start_index += chunk_count
    total_chunks = next_start_index - 1
    normalized_next_chapter_index = resume_state.get("next_chapter_index", 1) if resume_state else 1
    default_next_chunk_index = chapter_start_indices[normalized_next_chapter_index - 1] if chapters and 1 <= normalized_next_chapter_index <= len(chapters) else 1
    normalized_next_chunk_index = resume_state.get("next_chunk_index", default_next_chunk_index) if resume_state else 1
    normalized_completed_chunks = int(resume_state.get("completed_chunks", max(normalized_next_chunk_index - 1, 0))) if resume_state else 0
    progress_state = {
        "completed_chunks": min(normalized_completed_chunks, total_chunks),
        "total_chunks": total_chunks,
        "started_at": time.time(),
        "elapsed_offset": (resume_state.get("elapsed_seconds") or 0.0) if resume_state else 0.0,
        "chapter_index": None,
        "chapter_title": None,
    }
    heartbeat_stop, heartbeat_thread = start_progress_heartbeat(progress_state, heartbeat_seconds)
    sample_rate: int | None = resume_state.get("sample_rate") if resume_state else None
    max_part_samples: int | None = None
    current_writer: OutputPartWriter | None = None
    output_parts: list[dict] = resume_state.get("output_parts", []) if resume_state else []
    next_chunk_index = normalized_next_chunk_index if resume_state else 1
    part_index = resume_state.get("next_part_index", 1) if resume_state else 1
    total_chapters = len(chapters)
    multi_part = total_chapters > 1
    source_groups = [chapter.group for chapter in chapters if chapter.group]
    grouped_output = bool(source_groups)
    current_group: str | None = resume_state.get("next_group") if resume_state else None
    next_chapter_index = normalized_next_chapter_index if resume_state else 1
    if sample_rate is not None:
        max_part_samples = max(1, int(sample_rate * max_part_minutes * 60))
    if resume_state:
        print(json.dumps({"resume": True, "next_chapter_index": next_chapter_index, "next_part_index": part_index, "next_group": current_group}), flush=True)
        resume_output_root = output_root
        if grouped_output and current_group:
            resume_output_root = output_root / group_dir_map.get(current_group, Path(slugify(current_group)))
        stale_wav, stale_mp3 = compute_part_output_paths(resume_output_root, base_output_dir, part_index, multi_part, f"_part-{part_index:02d}", current_group)
        for stale_path in (stale_wav, stale_mp3):
            if stale_path.exists():
                stale_path.unlink()
    try:
        for chapter_index, chapter in enumerate(chapters, start=1):
            if chapter_index < next_chapter_index:
                continue
            chapter_start_index = chapter_start_indices[chapter_index - 1]
            chapter_end_index = chapter_start_index + chapter_chunk_counts[chapter_index - 1] - 1
            chapter_chunks = chunk_section(chapter.title, chapter.text, max_chars=max_chars, start_index=chapter_start_index)
            chapter_chunks = [chunk for chunk in chapter_chunks if chunk.index >= next_chunk_index]
            if not chapter_chunks:
                next_chunk_index = chapter_end_index + 1
                next_chapter_index = chapter_index + 1
                continue
            print(
                json.dumps(
                    {
                        "chapter_dispatch": True,
                        "chapter_index": chapter_index,
                        "chapter_title": chapter.title,
                        "group": chapter.group,
                    }
                ),
                flush=True,
            )
            progress_state["chapter_index"] = chapter_index
            progress_state["chapter_title"] = chapter.title
            if grouped_output and chapter.group != current_group:
                if current_writer is not None:
                    output_parts.append(current_writer.close())
                    save_safe_checkpoint(
                        checkpoint_path,
                        next_chapter_index=chapter_index,
                        next_chunk_index=chapter_chunks[0].index,
                        completed_chunks=progress_state["completed_chunks"],
                        elapsed_seconds=progress_state["elapsed_offset"] + (time.time() - progress_state["started_at"]),
                        sample_rate=sample_rate,
                        output_parts=output_parts,
                        manifest_chunks=manifest_chunks,
                        next_group=chapter.group,
                        next_part_index=1,
                    )
                current_group = chapter.group
                part_index = 1
                current_writer = None
            print(
                json.dumps(
                    {
                        "chapter_start": True,
                        "chapter_index": chapter_index,
                        "chapter_title": chapter.title,
                        "chunk_count": len(chapter_chunks),
                    }
                ),
                flush=True,
            )
            for position_in_chapter, chunk in enumerate(chapter_chunks, start=1):
                audio, current_rate, manifest_chunk = render_chunk_audio(
                    kokoro=kokoro,
                    chunk=chunk,
                    chapter=chapter,
                    chapter_index=chapter_index,
                    total_chapters=total_chapters,
                    position_in_chapter=position_in_chapter,
                    total_chapter_chunks=len(chapter_chunks),
                    voice=voice,
                    lang=lang,
                    trim_mode=trim_mode,
                    speed=speed,
                    silence_ms=silence_ms,
                    keep_chunks=keep_chunks,
                    chunk_dir=chunk_dir,
                    progress_state=progress_state,
                    expected_sample_rate=sample_rate,
                )
                if sample_rate is None:
                    sample_rate = current_rate
                    max_part_samples = max(1, int(sample_rate * max_part_minutes * 60))
                if current_writer is None:
                    current_output_root = output_root
                    if grouped_output and chapter.group:
                        current_output_root = output_root / group_dir_map.get(chapter.group, Path(slugify(chapter.group)))
                    current_writer = OutputPartWriter(
                        current_output_root,
                        base_output_dir,
                        part_index,
                        multi_part,
                        sample_rate,
                        force,
                        group_name=chapter.group,
                        audio_metadata=audio_metadata,
                        mp3_only=mp3_only,
                        final_stem_override=final_stem_override,
                    )
                if current_writer.start_chunk is None:
                    current_writer.start_chunk = chunk.index
                current_writer.end_chunk = chunk.index
                if not current_writer.chapter_titles or current_writer.chapter_titles[-1] != chapter.title:
                    current_writer.chapter_titles.append(chapter.title)
                current_writer.write_audio(audio)
                manifest_chunks.append(manifest_chunk)
                next_chunk_index = chunk.index + 1

                if max_part_samples is not None and current_writer.samples_written >= max_part_samples:
                    output_parts.append(current_writer.close())
                    part_index += 1
                    save_safe_checkpoint(
                        checkpoint_path,
                        next_chapter_index=chapter_index if next_chunk_index <= chapter_end_index else chapter_index + 1,
                        next_chunk_index=next_chunk_index,
                        completed_chunks=progress_state["completed_chunks"],
                        elapsed_seconds=progress_state["elapsed_offset"] + (time.time() - progress_state["started_at"]),
                        sample_rate=sample_rate,
                        output_parts=output_parts,
                        manifest_chunks=manifest_chunks,
                        next_group=chapter.group,
                        next_part_index=part_index,
                    )
                    current_writer = None

            next_chapter_index = chapter_index + 1

        if current_writer is not None:
            output_parts.append(current_writer.close())
    finally:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)
    if sample_rate is None:
        raise RuntimeError("No audio was rendered.")

    manifest = {
        "source": str(output_root.name),
        "voice": voice,
        "lang": lang,
        "speed": speed,
        "sample_rate": sample_rate,
        "chapter_count": len(chapters),
        "chunk_count": len(manifest_chunks),
        "max_part_minutes": max_part_minutes,
        "parts": output_parts,
        "chunks": manifest_chunks,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if checkpoint_path.exists():
        checkpoint_path.unlink()
    return manifest


def expand_inputs(paths: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for item in paths:
        matches = [Path(p) for p in sorted(Path().glob(item))] if any(ch in item for ch in "*?[]") else [Path(item)]
        expanded.extend(matches)
    unique = []
    seen: set[Path] = set()
    for path in expanded:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def main() -> int:
    args = parse_args()
    if args.wav_to_mp3:
        wav_path = Path(args.wav_to_mp3).resolve()
        mp3_path = wav_path.with_suffix(".mp3")
        converted = write_mp3_from_wav(
            wav_path=wav_path,
            mp3_path=mp3_path,
            bitrate_kbps=args.mp3_bitrate,
            force=args.force,
        )
        print(json.dumps({"input_wav": str(wav_path), "output_mp3": str(converted), "bitrate_kbps": args.mp3_bitrate}), flush=True)
        return 0

    if not args.input or not args.output_dir:
        if not args.list_chapters:
            print("--input and --output-dir are required unless --wav-to-mp3 or --list-chapters is used.", file=sys.stderr)
            return 2

    if not args.input:
        print("--input is required.", file=sys.stderr)
        return 2

    inputs = expand_inputs(args.input)
    if not inputs:
        print("No input files found.", file=sys.stderr)
        return 2

    missing = [path for path in inputs if not path.exists()]
    if missing:
        for path in missing:
            print(f"Missing input: {path}", file=sys.stderr)
        return 2

    if args.list_chapters:
        for source_path in inputs:
            if source_path.suffix.lower() == ".epub":
                print(f"Source: {source_path}")
                print_toc_tree(load_epub_toc_from_path(source_path))
            else:
                chapters = [chapter for chapter in load_chapters(source_path) if chapter.text and chapter.text.strip()]
                print_chapter_summary(source_path, chapters)
            chapters = [chapter for chapter in load_chapters(source_path) if chapter.text and chapter.text.strip()]
            print_output_structure_preview(source_path, chapters)
        return 0

    output_dir = Path(args.output_dir).resolve()
    print(
        f"[run:init] inputs={len(inputs)} output_dir={output_dir} model_dir={Path(args.model_dir).resolve()}",
        flush=True,
    )
    print(
        "[run:config] "
        f"voice={args.voice} speed={args.speed} max_chars={args.max_chars} "
        f"trim_mode={args.trim_mode} mp3_only={args.mp3_only} force={args.force}",
        flush=True,
    )
    runtime_temp_dir = configure_runtime_temp_dir(output_dir=output_dir, temp_dir=args.temp_dir)
    enable_windows_espeak_fallback()
    model_dir = Path(args.model_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path, voices_path = ensure_model_files(model_dir)
    provider_priority = parse_provider_priority(args.providers)
    provider = configure_onnx_provider(provider_priority=provider_priority)
    ort = get_onnxruntime()
    print(json.dumps({"onnx_provider_preference": provider, "available_providers": ort.get_available_providers(), "runtime_temp_dir": str(runtime_temp_dir)}), flush=True)
    KokoroClass = get_kokoro_class()
    print("[run:bootstrap] creating kokoro session...", flush=True)
    kokoro = KokoroClass(str(model_path), str(voices_path))
    print(json.dumps({"session_providers": kokoro.sess.get_providers()}), flush=True)

    for source_path in inputs:
        chapters = load_chapters(source_path)
        chapters = [chapter for chapter in chapters if chapter.text and chapter.text.strip()]
        if not chapters:
            print(f"Skipped {source_path}: no readable chapters after cleaning.", file=sys.stderr)
            continue

        audio_metadata = AudioMetadata(source_title=source_path.stem)
        if source_path.suffix.lower() == ".epub":
            audio_metadata = extract_epub_metadata(source_path)

        if source_path.suffix.lower() == ".epub":
            group_dir_map = build_group_directory_map_from_toc(
                load_epub_toc_from_path(source_path),
                {chapter.group for chapter in chapters if chapter.group},
            )
        else:
            group_dir_map = build_group_directory_map(chapters)

        output_root_base = output_dir / slugify(source_path.stem)
        if args.chapter_index is not None:
            if args.chapter_index < 1 or args.chapter_index > len(chapters):
                print(f"Invalid --chapter-index {args.chapter_index} for {source_path}.", file=sys.stderr)
                return 2
            original_chapter = chapters[args.chapter_index - 1]
            chapter_group = original_chapter.group
            chapter_position = args.chapter_index if source_path.suffix.lower() == ".epub" else build_chapter_number_map(chapters)[args.chapter_index]
            chapter_title = original_chapter.title
            output_name = args.output_name or f"{chapter_position:02d}-{sanitize_filename_component(chapter_title)}"
            chapter_subdir = Path(args.output_subdir) if args.output_subdir else Path()
            if not args.output_subdir and chapter_group:
                chapter_subdir = Path(slugify(source_path.stem)) / group_dir_map.get(chapter_group, Path(sanitize_filename_component(chapter_group)))
            elif not args.output_subdir:
                chapter_subdir = Path(slugify(source_path.stem))

            chapter_output_root = output_dir / chapter_subdir
            chapter_for_render = Chapter(title=chapter_title, text=original_chapter.text, group=None)
            manifest = render_audio(
                kokoro=kokoro,
                chapters=[chapter_for_render],
                base_output_dir=output_dir,
                output_root=chapter_output_root,
                group_dir_map={},
                voice=args.voice,
                lang=args.lang,
                trim_mode=args.trim_mode,
                speed=args.speed,
                max_chars=args.max_chars,
                silence_ms=args.silence_ms,
                max_part_minutes=args.max_part_minutes,
                keep_chunks=args.keep_chunks,
                mp3_only=args.mp3_only,
                force=args.force,
                audio_metadata=audio_metadata,
                heartbeat_seconds=args.heartbeat_seconds,
                final_stem_override=output_name,
            )
            print(
                json.dumps(
                    {
                        "source": str(source_path),
                        "chapter_index": args.chapter_index,
                        "chapter_title": chapter_title,
                        "output_parts": manifest["parts"],
                        "chunks": manifest["chunk_count"],
                        "voice": manifest["voice"],
                    }
                ),
                flush=True,
            )
            continue

        output_root = output_root_base
        manifest = render_audio(
            kokoro=kokoro,
            chapters=chapters,
            base_output_dir=output_dir,
            output_root=output_root,
            group_dir_map=group_dir_map,
            voice=args.voice,
            lang=args.lang,
            trim_mode=args.trim_mode,
            speed=args.speed,
            max_chars=args.max_chars,
            silence_ms=args.silence_ms,
            max_part_minutes=args.max_part_minutes,
            keep_chunks=args.keep_chunks,
            mp3_only=args.mp3_only,
            force=args.force,
            audio_metadata=audio_metadata,
            heartbeat_seconds=args.heartbeat_seconds,
        )
        print(
            json.dumps(
                {
                    "source": str(source_path),
                    "output_parts": manifest["parts"],
                    "chunks": manifest["chunk_count"],
                    "voice": manifest["voice"],
                }
            ),
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
