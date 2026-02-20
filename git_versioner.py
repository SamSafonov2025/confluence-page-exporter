#!/usr/bin/env python3
"""
git_versioner.py — Convert versioned attachment files into git commit history.

Recursively scans a directory tree (main page + all child pages) for files
with version numbers in their names (e.g., "request_config 0.1.5.1.json"),
groups them by base name, and creates sequential git commits — one per
version — with the clean filename and version in the commit message.

Usage:
    python git_versioner.py <source_dir> <target_repo> [options]

Examples:
    # Dry run — see what would happen without making changes:
    python git_versioner.py ./output/MainPage_12345 /path/to/repo --dry-run

    # Initialize a new repo and commit all versions recursively:
    python git_versioner.py ./output/MainPage_12345 /path/to/repo --init

    # Commit to an existing repo:
    python git_versioner.py ./output/MainPage_12345 /path/to/repo
"""

import argparse
import logging
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

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


def find_versioned_files(source_dir: Path) -> dict[tuple[str, str, str], list[tuple[str, Path]]]:
    """Recursively find files with version patterns.

    Returns:
        {("rel/subdir", "request_config", "json"): [("0.1.5.1", Path), ...]}
        Each group is sorted by version number.
    """
    groups = defaultdict(list)

    for entry in sorted(source_dir.rglob('*')):
        if not entry.is_file():
            continue
        match = VERSION_PATTERN.match(entry.name)
        if match:
            name, version, ext = match.groups()
            rel_dir = str(entry.parent.relative_to(source_dir))
            groups[(rel_dir, name, ext)].append((version, entry))

    for key in groups:
        groups[key].sort(key=lambda x: parse_version(x[0]))

    return dict(groups)


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


def commit_versions(source_dir: Path, target_repo: Path, *, dry_run: bool = False) -> int:
    """Recursively process versioned files and create one git commit per version.

    Returns the total number of commits created.
    """
    groups = find_versioned_files(source_dir)

    if not groups:
        logging.warning('No versioned files found in %s', source_dir)
        return 0

    total_commits = 0

    for (rel_dir, name, ext), versions in sorted(groups.items()):
        target_name = f'{name}.{ext}'

        # Build target path preserving directory structure
        if rel_dir == '.':
            target_subdir = target_repo
            git_path = target_name
            display_path = target_name
        else:
            target_subdir = target_repo / rel_dir
            git_path = f'{rel_dir}/{target_name}'
            display_path = git_path

        logging.info('--- %s: %d versions ---', display_path, len(versions))

        for version_str, source_path in versions:
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

            # Skip if file content is identical to previous version
            status = git('status', '--porcelain', cwd=target_repo)
            if not status:
                logging.info('Skipped (identical to previous): %s', commit_msg)
                continue

            git('commit', '-m', commit_msg, cwd=target_repo)
            logging.info('Committed: %s  (%d bytes)',
                         commit_msg, source_path.stat().st_size)
            total_commits += 1

    return total_commits


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

    total = commit_versions(source, target, dry_run=args.dry_run)

    if args.dry_run:
        logging.info('Dry run complete — no commits were made')
    else:
        logging.info('Done: %d commits created', total)


if __name__ == '__main__':
    main()
