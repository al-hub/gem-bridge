"""
RepoPathEngine - Intelligent Multi-Tier Path Resolution Engine for gem-bridge
Resolves files and directories within a repository with fuzzy, recursive,
and extension-tolerant matching while strictly enforcing canonical security boundaries.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Set

logger = logging.getLogger("gem_bridge.path_resolver")


class RepoPathEngine:
    """
    Intelligent Path Resolver for repositories.
    Provides fast, deterministic, multi-tier path resolution:
    - Tier 1: Exact direct resolution (0ms)
    - Tier 2: Git index / pruned file walk (excluding ignored directories)
    - Tier 3: Loose & fuzzy matching (case-insensitive, extension auto-completion, delimiter mapping)
    - Disambiguation: Shallow depth > non-archive > recent mtime
    - Security: Canonical path traversal defense (strictly enclosed within repo)
    """

    IGNORED_DIRS: Set[str] = {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".idea",
        ".vscode",
        "dist",
        "build",
        ".next",
        ".turbo",
    }

    COMMON_EXTENSIONS: List[str] = [
        ".md",
        ".py",
        ".txt",
        ".json",
        ".ts",
        ".js",
        ".yaml",
        ".yml",
        ".toml",
        ".html",
        ".css",
        ".sh",
        ".rst",
    ]

    @classmethod
    def resolve(cls, repo_path: Path, target: str) -> Optional[Path]:
        """
        Resolves a target path string within repo_path to an absolute Path.
        Returns None if no matching file/directory is found or if outside repo_path.
        """
        if not target or not target.strip():
            return None

        clean_target = target.strip().lstrip("/")
        repo_abs = repo_path.resolve()

        # 1. Tier 1: Exact Direct Resolution
        direct_path = (repo_abs / clean_target).resolve()
        if cls._is_safe_and_exists(direct_path, repo_abs):
            return direct_path

        # 2. Tier 2: Git Index Resolution (if git repo)
        git_matches = cls._resolve_via_git_index(repo_abs, clean_target)
        if git_matches:
            best = cls._disambiguate_matches(git_matches)
            if best and cls._is_safe_and_exists(best, repo_abs):
                return best

        # 3. Tier 3: Pruned File Walk & Fuzzy/Loose Matching
        walk_matches = cls._resolve_via_pruned_walk(repo_abs, clean_target)
        if walk_matches:
            best = cls._disambiguate_matches(walk_matches)
            if best and cls._is_safe_and_exists(best, repo_abs):
                return best

        return None

    @classmethod
    def _is_safe_and_exists(cls, path: Path, repo_abs: Path) -> bool:
        """Verifies that path exists and is strictly enclosed within repo_abs."""
        try:
            resolved = path.resolve()
            if not resolved.exists():
                return False
            return resolved.is_relative_to(repo_abs)
        except Exception:
            return False

    @classmethod
    def _resolve_via_git_index(cls, repo_abs: Path, target: str) -> List[Path]:
        """Searches tracked files via git ls-files for sub-millisecond lookups."""
        git_dir = repo_abs / ".git"
        if not git_dir.exists():
            return []

        target_path_obj = Path(target)
        target_name = target_path_obj.name.lower()
        target_stem = target_path_obj.stem.lower()
        has_subdirs = len(target_path_obj.parts) > 1
        target_rel_lower = target_path_obj.as_posix().lower()
        target_dir_and_stem = f"{target_path_obj.parent.as_posix().lower()}/{target_stem}" if has_subdirs else target_stem

        try:
            # Query git tracked files
            proc = subprocess.run(
                ["git", "ls-files"],
                cwd=str(repo_abs),
                capture_output=True,
                text=True,
                timeout=2,
            )
            if proc.returncode != 0 or not proc.stdout:
                return []

            matches: List[Path] = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                p = repo_abs / line
                p_rel_lower = line.lower()
                p_name = p.name.lower()
                p_stem = p.stem.lower()

                if has_subdirs and not p_rel_lower.endswith(target_rel_lower) and not p_rel_lower.endswith(f"{target_dir_and_stem}{p.suffix.lower()}"):
                    continue

                # Exact filename match
                if p_name == target_name:
                    matches.append(p)
                # Extension-omitted match
                elif p_stem == target_stem and p.suffix.lower() in cls.COMMON_EXTENSIONS:
                    matches.append(p)
                # Delimiter normalized match (- vs _)
                elif (
                    p_stem.replace("-", "_") == target_stem.replace("-", "_")
                    and p.suffix.lower() in cls.COMMON_EXTENSIONS
                ):
                    matches.append(p)

            return matches
        except Exception as e:
            logger.debug(f"Git index resolution skipped: {e}")
            return []

    @classmethod
    def _resolve_via_pruned_walk(cls, repo_abs: Path, target: str) -> List[Path]:
        """Performs a fast directory walk pruning heavy/ignored directories."""
        target_path_obj = Path(target)
        target_name = target_path_obj.name.lower()
        target_stem = target_path_obj.stem.lower()
        target_norm = target_stem.replace("-", "_")
        has_subdirs = len(target_path_obj.parts) > 1
        target_rel_lower = target_path_obj.as_posix().lower()
        target_dir_and_stem = f"{target_path_obj.parent.as_posix().lower()}/{target_stem}" if has_subdirs else target_stem

        matches: List[Path] = []
        try:
            for root, dirs, files in os.walk(str(repo_abs)):
                # Prune ignored directories in-place
                dirs[:] = [d for d in dirs if d not in cls.IGNORED_DIRS and not d.startswith(".")]

                root_path = Path(root)
                for f in files:
                    f_path = root_path / f
                    p_rel_lower = f_path.relative_to(repo_abs).as_posix().lower()
                    if has_subdirs and not p_rel_lower.endswith(target_rel_lower) and not p_rel_lower.endswith(f"{target_dir_and_stem}{f_path.suffix.lower()}"):
                        continue

                    f_lower = f.lower()
                    p_stem = f_path.stem.lower()

                    # Exact filename
                    if f_lower == target_name:
                        matches.append(f_path)
                    # Extension auto-completion
                    elif p_stem == target_stem and f_path.suffix.lower() in cls.COMMON_EXTENSIONS:
                        matches.append(f_path)
                    # Delimiter normalization
                    elif (
                        p_stem.replace("-", "_") == target_norm
                        and f_path.suffix.lower() in cls.COMMON_EXTENSIONS
                    ):
                        matches.append(f_path)

        except Exception as e:
            logger.warning(f"Error during pruned walk on {repo_abs}: {e}")

        return matches

    @classmethod
    def _disambiguate_matches(cls, candidates: List[Path]) -> Optional[Path]:
        """
        Picks the best candidate when multiple matches exist:
        1. Fewest path components (closest to root)
        2. Non-archive / non-backup paths prioritized
        3. Most recently modified (mtime)
        """
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        penalized_keywords = {"archive", "backup", "old", "tmp", "temp", "test", "tests"}

        def score(p: Path):
            parts_lower = [part.lower() for part in p.parts]
            depth = len(p.parts)
            has_penalty = 1 if any(k in parts_lower for k in penalized_keywords) else 0
            try:
                mtime = p.stat().st_mtime
            except Exception:
                mtime = 0.0
            # Sort order: penalty ascending (0 first), depth ascending (shallowest first), mtime descending (-mtime)
            return (has_penalty, depth, -mtime)

        sorted_candidates = sorted(candidates, key=score)
        return sorted_candidates[0]
