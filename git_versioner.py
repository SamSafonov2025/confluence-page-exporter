#!/usr/bin/env python3
"""
git_versioner.py — Convert versioned attachment files into git commit history.

Recursively scans a directory tree (main page + all child pages) for files
with version numbers in their names (e.g., "request_config 0.1.5.1.json"),
groups them by base name, and creates sequential git commits — one per
version — with the clean filename and version in the commit message.

Supports incremental mode: when --database-url is given (or "database_url"
is set in config.json), already-committed files are skipped.

Usage:
    python git_versioner.py <source_dir> <target_repo> [options]

Examples:
    # Dry run — see what would happen without making changes:
    python git_versioner.py ./output/MainPage_12345 /path/to/repo --dry-run

    # Initialize a new repo and commit all versions recursively:
    python git_versioner.py ./output/MainPage_12345 /path/to/repo --init

    # Incremental: skip files already committed (reads database_url from config.json):
    python git_versioner.py ./output/MainPage_12345 /path/to/repo

    # Explicit database URL:
    python git_versioner.py ./output/MainPage_12345 /path/to/repo \
        --database-url postgresql://user:pass@localhost/confluence_export
"""

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    import mammoth
    _HAS_MAMMOTH = True
except ImportError:
    _HAS_MAMMOTH = False

try:
    import html2text
    _HAS_HTML2TEXT = True
except ImportError:
    _HAS_HTML2TEXT = False

# Matches: "base_name <version>.ext"
# e.g. "request_config 0.1.5.1.json" → name="request_config", ver="0.1.5.1", ext="json"
# e.g. "ФТ Подсистема обработки запросов к ТА 0.1.0.docx" → name="ФТ ...", ver="0.1.0", ext="docx"
VERSION_PATTERN = re.compile(r'^(.+?)\s+(\d+(?:\.\d+)+)\.(\w+)$')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
)


def parse_version(version_str: str) -> tuple[int, ...]:
    """Parse '0.1.5.1' into (0, 1, 5, 1) for proper numeric sorting."""
    return tuple(int(x) for x in version_str.split('.'))


def _extract_page_id(dir_name: str) -> str | None:
    """Extract page_id from directory name like 'PageTitle_12345'."""
    idx = dir_name.rfind('_')
    if idx >= 0:
        candidate = dir_name[idx + 1:]
        if candidate.isdigit():
            return candidate
    return None


def _extract_confluence_version(version_str: str) -> int | None:
    """Extract Confluence page version from filename version like '3.0' → 3.

    main.py creates versioned markdown files as 'PageTitle N.0.md' where N
    is the Confluence version number.  Returns None for other patterns
    (e.g. '0.1.5.1' for attachments).
    """
    parts = version_str.split('.')
    if len(parts) == 2 and parts[1] == '0' and parts[0].isdigit():
        return int(parts[0])
    return None


def convert_doc_to_md(doc_path: Path) -> str | None:
    """Convert .doc/.docx to markdown text.

    .docx — via mammoth (docx→html) + html2text (html→md).
    .doc  — Confluence exports are HTML-based, converted via html2text directly.
    Returns None if the required libraries are missing or conversion fails.
    """
    suffix = doc_path.suffix.lower()
    try:
        if suffix == '.docx':
            if not _HAS_MAMMOTH or not _HAS_HTML2TEXT:
                return None
            with open(doc_path, 'rb') as f:
                html = mammoth.convert_to_html(f).value
        elif suffix == '.doc':
            if not _HAS_HTML2TEXT:
                return None
            html = doc_path.read_text(encoding='utf-8', errors='replace')
        else:
            return None

        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.body_width = 0
        return converter.handle(html)
    except Exception as e:
        logging.warning('Failed to convert %s to markdown: %s', doc_path.name, e)
        return None


def generate_md_companion(doc_path: Path) -> Path | None:
    """Generate a _doc.md / _docx.md companion file next to the original.

    Example: Document.docx → Document_docx.md
    Returns the companion Path, or None if conversion failed.
    """
    md_content = convert_doc_to_md(doc_path)
    if md_content is None:
        return None
    suffix_tag = doc_path.suffix.lstrip('.').lower()  # "docx" or "doc"
    companion = doc_path.parent / f'{doc_path.stem}_{suffix_tag}.md'
    companion.write_text(md_content, encoding='utf-8')
    return companion


def find_all_files(source_dir: Path):
    """Recursively find all files, separating versioned from plain.

    Returns:
        versioned: {("rel/subdir", "name", "ext"): [("0.1.5.1", Path), ...]}
        plain:     [("rel/subdir", Path), ...]
    """
    versioned = defaultdict(list)
    plain = []

    for entry in sorted(source_dir.rglob('*')):
        if not entry.is_file():
            continue
        rel_dir = str(entry.parent.relative_to(source_dir))
        match = VERSION_PATTERN.match(entry.name)
        if match:
            name, version, ext = match.groups()
            versioned[(rel_dir, name, ext)].append((version, entry))
        else:
            plain.append((rel_dir, entry))

    for key in versioned:
        versioned[key].sort(key=lambda x: parse_version(x[0]))

    return dict(versioned), plain


def git(*args, cwd: Path) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ['git', *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logging.error('git %s failed: %s', ' '.join(args), result.stderr.strip())
        raise RuntimeError(f'git {args[0]} failed: {result.stderr.strip()}')
    return result.stdout.strip()


def commit_all(source_dir: Path, target_repo: Path, *,
               dry_run: bool = False, tracker=None) -> int:
    """Recursively process all files: plain files first, then versioned.

    When tracker is provided, already-committed files are skipped (by source
    path) and newly committed files are recorded in the database.

    Returns the total number of commits created.
    """
    versioned, plain = find_all_files(source_dir)

    if not versioned and not plain:
        logging.warning('No files found in %s', source_dir)
        return 0

    total_commits = 0
    skipped_db = 0

    # 1) Commit plain files (e.g. .md pages, attachments) — one commit per file
    for rel_dir, source_path in plain:
        file_name = source_path.name
        source_rel = str(source_path.relative_to(source_dir))

        # DB check: skip if already committed
        if tracker and tracker.is_file_committed(source_rel):
            logging.info('Skipped (already in DB): %s', source_rel)
            skipped_db += 1
            continue

        if rel_dir == '.':
            target_subdir = target_repo
            git_path = file_name
        else:
            target_subdir = target_repo / rel_dir
            git_path = f'{rel_dir}/{file_name}'

        commit_msg = f'Add {git_path}'

        # Extract page_id for attachment tracking
        page_id = None
        if tracker:
            dir_name = source_dir.name if rel_dir == '.' else Path(rel_dir).name
            page_id = _extract_page_id(dir_name)

        if dry_run:
            logging.info('[DRY RUN] %s  (%d bytes)',
                         commit_msg, source_path.stat().st_size)
            total_commits += 1
            continue

        target_subdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_subdir / file_name)
        git('add', git_path, cwd=target_repo)

        # Generate _doc.md / _docx.md companion for Word files
        companion = generate_md_companion(target_subdir / file_name)
        if companion:
            c_git = companion.name if rel_dir == '.' else f'{rel_dir}/{companion.name}'
            git('add', c_git, cwd=target_repo)
            logging.info('Generated companion: %s', c_git)

        status = git('status', '--porcelain', cwd=target_repo)
        if not status:
            logging.info('Skipped (already exists): %s', git_path)
            if tracker:
                tracker.mark_file_committed(source_rel)
                if page_id:
                    tracker.mark_attachment_committed_by_filename(
                        page_id, file_name)
            continue

        git('commit', '-m', commit_msg, cwd=target_repo)
        logging.info('Committed: %s  (%d bytes)', commit_msg, source_path.stat().st_size)
        total_commits += 1

        if tracker:
            tracker.mark_file_committed(source_rel)
            # Set committed_to_git on ExportedAttachment (checkbox 2)
            if page_id:
                tracker.mark_attachment_committed_by_filename(
                    page_id, file_name)

    # 2) Commit versioned files — one commit per version
    for (rel_dir, name, ext), versions in sorted(versioned.items()):
        target_name = f'{name}.{ext}'

        if rel_dir == '.':
            target_subdir = target_repo
            git_path = target_name
            display_path = target_name
        else:
            target_subdir = target_repo / rel_dir
            git_path = f'{rel_dir}/{target_name}'
            display_path = git_path

        logging.info('--- %s: %d versions ---', display_path, len(versions))

        # Determine page_id for updating ExportedPageVersion flag
        page_id = None
        if tracker:
            dir_name = source_dir.name if rel_dir == '.' else Path(rel_dir).name
            page_id = _extract_page_id(dir_name)

        for version_str, source_path in versions:
            source_rel = str(source_path.relative_to(source_dir))

            # DB check: skip if already committed
            if tracker and tracker.is_file_committed(source_rel):
                logging.info('Skipped (already in DB): %s v%s',
                             display_path, version_str)
                skipped_db += 1
                continue

            commit_msg = f'{display_path} version {version_str}'

            if dry_run:
                logging.info('[DRY RUN] %s  (%s, %d bytes)',
                             commit_msg, source_path.name,
                             source_path.stat().st_size)
                total_commits += 1
                continue

            target_subdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_subdir / target_name)
            git('add', git_path, cwd=target_repo)

            # Generate _doc.md / _docx.md companion for Word files
            companion = generate_md_companion(target_subdir / target_name)
            if companion:
                c_git = companion.name if rel_dir == '.' else f'{rel_dir}/{companion.name}'
                git('add', c_git, cwd=target_repo)
                logging.info('Generated companion: %s', c_git)

            status = git('status', '--porcelain', cwd=target_repo)
            if not status:
                logging.info('Skipped (identical to previous): %s', commit_msg)
                if tracker:
                    tracker.mark_file_committed(source_rel)
                continue

            git('commit', '-m', commit_msg, cwd=target_repo)
            logging.info('Committed: %s  (%d bytes)',
                         commit_msg, source_path.stat().st_size)
            total_commits += 1

            if tracker:
                tracker.mark_file_committed(source_rel)
                # Also set committed_to_git flag on ExportedPageVersion
                if page_id:
                    conf_ver = _extract_confluence_version(version_str)
                    if conf_ver is not None:
                        fmt = 'markdown' if ext == 'md' else ext
                        tracker.mark_version_committed(
                            page_id, conf_ver, fmt)

    if skipped_db:
        logging.info('Skipped %d files (already committed per DB)', skipped_db)

    return total_commits


def _load_database_url() -> str | None:
    """Try to read database_url from config.json (same as main.py uses)."""
    config_path = Path(__file__).parent / 'config.json'
    if not config_path.exists():
        return None
    try:
        with open(config_path, encoding='utf-8') as f:
            config = json.load(f)
        return config.get('database_url')
    except (json.JSONDecodeError, OSError):
        return None


def main():
    parser = argparse.ArgumentParser(
        description='Convert versioned attachment files into git commit history')
    parser.add_argument('source_dir',
                        help='Root directory with exported pages (scanned recursively)')
    parser.add_argument('target_repo',
                        help='Target git repository path')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('--init', action='store_true',
                        help='Initialize a new git repo at target path')
    parser.add_argument('--database-url',
                        help='PostgreSQL URL for incremental tracking '
                             '(default: read from config.json)')

    args = parser.parse_args()

    source = Path(args.source_dir)
    target = Path(args.target_repo)

    if not source.is_dir():
        sys.exit(f'Source directory not found: {source}')

    if args.init:
        target.mkdir(parents=True, exist_ok=True)
        git('init', cwd=target)
        logging.info('Initialized git repo at %s', target)
    elif not (target / '.git').is_dir():
        sys.exit(f'Not a git repo: {target}  (use --init to create one)')

    # Set up incremental tracking
    tracker = None
    database_url = args.database_url or _load_database_url()
    if database_url:
        from models import init_tracker
        tracker = init_tracker(database_url)
        logging.info('Incremental mode: skipping already-committed files')
    else:
        logging.info('Full mode (no database_url — all files will be processed)')

    total = commit_all(source, target, dry_run=args.dry_run, tracker=tracker)

    if args.dry_run:
        logging.info('Dry run complete — no commits were made')
    else:
        logging.info('Done: %d commits created', total)


if __name__ == '__main__':
    main()
