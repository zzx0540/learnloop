"""Package allowlisted project source and notices, never runtime data or credentials."""

import argparse
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

DIRECTORIES = {"backend", "frontend", "deploy", "tools", "docs", "examples", "specs", ".github", "LICENSES"}
ROOT_FILES = {
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
    "README.en.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "Dockerfile",
    ".dockerignore",
    ".gitignore",
    ".gitattributes",
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "docker-compose.demo.yml",
    ".env.example",
}
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "data",
    "backups",
    "private-data",
    "secrets",
    "private",
    "uploads",
    "storage",
    ".legal",
    ".qa",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pem", ".key", ".dump", ".log", ".zip", ".sql", ".db", ".sqlite"}


def source_files(root):
    root = root.resolve()
    for name in sorted(ROOT_FILES):
        path = root / name
        if path.is_file() and not path.is_symlink():
            yield path
    for name in sorted(DIRECTORIES):
        base = root / name
        if not base.is_dir() or base.is_symlink():
            continue
        for directory, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in EXCLUDED_DIRS
                and not d.startswith(".env")
                and not (Path(directory) / d).is_symlink()
            )
            for filename in sorted(files):
                path = Path(directory) / filename
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    continue
                if filename.startswith(".env") or path.suffix.lower() in EXCLUDED_SUFFIXES:
                    continue
                if filename in {"instructions.md", "credentials.json"}:
                    continue
                yield path


def package(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or any(output.is_relative_to(root / d) for d in DIRECTORIES):
        raise ValueError("Output must be outside source directories (for example root/.legal).")
    output.mkdir(parents=True, exist_ok=True)
    files = list(source_files(root))
    digest = hashlib.sha256()
    manifest = {}
    with zipfile.ZipFile(output / "learnloop-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            content = path.read_bytes()
            manifest[relative] = hashlib.sha256(content).hexdigest()
            digest.update(relative.encode() + b"\0" + content + b"\0")
            item = zipfile.ZipInfo("learnloop/" + relative)
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = (0o755 if relative.endswith(".sh") else 0o644) << 16
            archive.writestr(item, content)
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(root / name, output / name)
    with (output / "third-party-notices.txt").open("w", encoding="utf-8") as combined:
        combined.write((root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8"))
        for path in sorted((root / "LICENSES").glob("*.txt")):
            combined.write("\n\n===== " + path.name + " =====\n")
            combined.write(path.read_text(encoding="utf-8"))
    (output / "manifest.json").write_text(
        json.dumps({"source_sha256": digest.hexdigest(), "files": manifest}, indent=2), encoding="utf-8"
    )
    return {"source_sha256": digest.hexdigest(), "files": len(manifest)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.root, args.output)))
