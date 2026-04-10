#!/usr/bin/env python3
"""
Dump project source files into a single .txt (paths + contents).
Skips venv, __pycache__, .git, storage, node_modules, and by default tests/.
"""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        "venv",
        ".venv",
        "node_modules",
        "storage",
        ".idea",
        ".vscode",
        "dist",
        "build",
    }
)

DEFAULT_EXTENSIONS = frozenset({".py", ".jsx", ".js", ".html", ".css", ".yml", ".yaml"})

EXTRA_NAMES = frozenset({"Dockerfile", ".dockerignore"})


def _parse_extensions(s: str) -> frozenset[str]:
    parts = [p.strip().lower() for p in s.split(",") if p.strip()]
    out = set()
    for p in parts:
        if not p.startswith("."):
            p = "." + p
        out.add(p)
    return frozenset(out)


def iter_source_files(
    root: Path,
    *,
    extensions: frozenset[str],
    skip_dir_names: frozenset[str],
    include_tests: bool,
) -> list[Path]:
    root = root.resolve()
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(p in skip_dir_names for p in rel.parts):
            continue
        if not include_tests and "tests" in rel.parts:
            continue
        name = path.name
        suf = path.suffix.lower()
        if name in EXTRA_NAMES or suf in extensions:
            files.append(path)
    files.sort(key=lambda p: str(p).lower())
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect source code into one text file for review or sharing."
    )
    parser.add_argument(
        "-r",
        "--root",
        type=Path,
        default=None,
        help="Project root (default: parent of scripts/ or cwd)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("code_dump.txt"),
        help="Output .txt path (default: code_dump.txt in cwd)",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include files under any tests/ directory",
    )
    parser.add_argument(
        "--extensions",
        type=str,
        default=",".join(sorted(DEFAULT_EXTENSIONS)),
        help="Comma-separated extensions, e.g. .py,.jsx (default: common code types)",
    )
    args = parser.parse_args()

    root = args.root
    if root is None:
        here = Path(__file__).resolve().parent
        root = here.parent if (here.parent / "main.py").is_file() else Path.cwd()
    root = root.resolve()

    exts = _parse_extensions(args.extensions)
    paths = iter_source_files(
        root,
        extensions=exts,
        skip_dir_names=DEFAULT_SKIP_DIR_NAMES,
        include_tests=args.include_tests,
    )

    out_path = args.output
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    sep = "=" * 72
    for path in paths:
        rel = path.relative_to(root)
        lines.append(f"{sep}")
        lines.append(f"{rel.as_posix()}")
        lines.append(f"{sep}")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            lines.append(f"<read error: {e}>")
            lines.append("")
            continue
        lines.append(text.rstrip("\n"))
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"Wrote {len(paths)} files to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
