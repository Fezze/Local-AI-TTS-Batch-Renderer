from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


def normalize_title(value: str) -> str:
    value = value.replace("’", "'").replace("“", '"').replace("”", '"')
    value = re.sub(r"^\d+\s*[-.]\s*", "", value)
    value = re.sub(r"^(chapter|part|epilogue|prologue)\s+\w+\s*[-:]\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(chapter|part|epilogue|prologue)\s+\w+\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(title page|table of contents|maps|cover|dedication|copyright|acknowledgements|about the author|also by the author|about the publisher|continue the adventure)\s*[-:]\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[^a-z0-9]+", "", value.lower())
    return value


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_root_audio(root_dir: Path) -> list[Path]:
    return sorted(
        [path for path in root_dir.glob("*.mp3") if path.is_file()],
        key=lambda path: path.name.lower(),
    )


def iter_tree_audio(root_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in root_dir.rglob("*.mp3")
            if path.is_file() and path.parent != root_dir
        ],
        key=lambda path: str(path.relative_to(root_dir)).lower(),
    )


def build_tree_index(tree_audio: list[Path], root_dir: Path) -> list[tuple[Path, str]]:
    indexed: list[tuple[Path, str]] = []
    for path in tree_audio:
        indexed.append((path, normalize_title(path.stem)))
    return indexed


def choose_target(root_title: str, tree_index: list[tuple[Path, str]], used: set[Path]) -> Path | None:
    normalized = normalize_title(root_title)
    if not normalized:
        return None

    exact_matches = [path for path, title in tree_index if path not in used and title == normalized]
    if exact_matches:
        return exact_matches[0]

    contains_matches = [path for path, title in tree_index if path not in used and normalized in title]
    if contains_matches:
        return contains_matches[0]

    return None


def safe_move_replace(src: Path, dst: Path, quarantine_root: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    quarantine_root.mkdir(parents=True, exist_ok=True)

    if dst.exists():
        relative = dst.relative_to(dst.anchor)
        quarantine_target = quarantine_root / relative.as_posix().replace("/", "__")
        quarantine_target.parent.mkdir(parents=True, exist_ok=True)
        if quarantine_target.exists():
            quarantine_target.unlink()
        dst.replace(quarantine_target)

    src.replace(dst)


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair EPUB output layout by moving root audio into canonical tree paths.")
    parser.add_argument("--source-dir", required=True, help="Path to out/mp3/<source> directory.")
    parser.add_argument("--quarantine-dir", help="Directory to store replaced files.")
    parser.add_argument("--apply", action="store_true", help="Perform moves instead of dry-run.")
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    if not source_dir.exists():
        raise FileNotFoundError(source_dir)

    quarantine_dir = Path(args.quarantine_dir).resolve() if args.quarantine_dir else source_dir.parent.parent / "_quarantine" / source_dir.name
    root_audio = iter_root_audio(source_dir)
    tree_audio = iter_tree_audio(source_dir)
    tree_index = build_tree_index(tree_audio, source_dir)
    used_targets: set[Path] = set()
    mappings: list[tuple[Path, Path]] = []
    unmatched: list[Path] = []

    for root_file in root_audio:
        source_json = root_file.with_suffix(".json")
        title = root_file.stem
        if source_json.exists():
            try:
                manifest = load_json(source_json)
                chapter_titles = manifest.get("parts", [{}])[0].get("chapter_titles") or []
                if chapter_titles:
                    title = chapter_titles[0]
            except Exception:
                pass

        target = choose_target(title, tree_index, used_targets)
        if target is None:
            unmatched.append(root_file)
            continue
        used_targets.add(target)
        mappings.append((root_file, target))

    print(json.dumps({
        "source_dir": str(source_dir),
        "root_audio": len(root_audio),
        "tree_audio": len(tree_audio),
        "mappings": len(mappings),
        "unmatched": len(unmatched),
        "apply": args.apply,
    }, ensure_ascii=False))

    for root_file, target in mappings[:10]:
        print(json.dumps({"root": root_file.name, "target": str(target.relative_to(source_dir))}, ensure_ascii=False))

    if unmatched:
        print(json.dumps({"unmatched_preview": [path.name for path in unmatched[:10]]}, ensure_ascii=False))

    if not args.apply:
        return 0

    for root_file, target in mappings:
        safe_move_replace(root_file, target, quarantine_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
