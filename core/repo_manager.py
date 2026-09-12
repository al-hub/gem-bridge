import os
import re
import subprocess
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("gem_bridge.repo_manager")


class RepoError(Exception):
    """Exception raised for repository management and preparation errors."""
    pass


class RepoManager:
    """
    Manages repository resolution, validation, and dynamic cloning/updating.
    - Public repositories: Shallow cloned/pulled under ~/workspace/repos/
    - Private/Local repositories: Validated against configured paths and permissions
    """

    PUBLIC_URL_PATTERNS = [
        re.compile(r"^https?://", re.IGNORECASE),
        re.compile(r"^git@", re.IGNORECASE),
        re.compile(r"^ssh://", re.IGNORECASE),
        re.compile(r"github\.com[/:]", re.IGNORECASE),
        re.compile(r"gitlab\.com[/:]", re.IGNORECASE),
    ]

    def __init__(
        self,
        repos_base_dir: Optional[Path] = None,
        repo_mapping: Optional[Dict[str, str]] = None
    ):
        if repos_base_dir is None:
            self.repos_base_dir = Path.home() / "workspace" / "repos"
        else:
            self.repos_base_dir = Path(repos_base_dir)

        self.repo_mapping: Dict[str, Path] = {}
        if repo_mapping:
            for k, v in repo_mapping.items():
                self.repo_mapping[k.strip()] = Path(v).resolve()

    def is_public_url(self, target_repo: str) -> bool:
        """Determines whether the given repo identifier is a remote public git URL."""
        if not target_repo:
            return False
        clean_target = target_repo.strip()
        return any(pattern.search(clean_target) for pattern in self.PUBLIC_URL_PATTERNS)

    def extract_repo_name_from_url(self, url: str) -> str:
        """Extracts a clean repository folder name from a git URL."""
        clean = url.strip().rstrip("/")
        if clean.endswith(".git"):
            clean = clean[:-4]
        # Match the last path component
        parts = re.split(r"[/:]", clean)
        repo_name = parts[-1] if parts else "cloned_repo"
        # Sanitize safe folder name
        repo_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", repo_name)
        return repo_name or "cloned_repo"

    def prepare_repo(self, target_repo: str) -> Path:
        """
        Dynamically prepares and returns the target repository path.
        Dispatches to public shallow clone/pull or local validation.
        """
        if not target_repo or not target_repo.strip():
            raise RepoError("Empty target repository specified.")

        target_repo = target_repo.strip()

        if self.is_public_url(target_repo):
            return self._prepare_public_repo(target_repo)
        else:
            return self._prepare_local_repo(target_repo)

    def _prepare_public_repo(self, public_url: str) -> Path:
        """Shallow clones or pulls a public repository under ~/workspace/repos/."""
        self.repos_base_dir.mkdir(parents=True, exist_ok=True)
        repo_name = self.extract_repo_name_from_url(public_url)
        target_dir = (self.repos_base_dir / repo_name).resolve()

        if not target_dir.exists():
            logger.info(f"Cloning public repository (shallow): {public_url} -> {target_dir}")
            try:
                res = subprocess.run(
                    ["git", "clone", "--depth", "1", public_url, str(target_dir)],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=120
                )
                logger.info(f"Successfully cloned {public_url}: {res.stdout.strip()}")
            except subprocess.CalledProcessError as e:
                err_msg = f"Failed to clone public repository '{public_url}': {e.stderr.strip()}"
                logger.error(err_msg)
                raise RepoError(err_msg) from e
            except Exception as e:
                err_msg = f"Unexpected error while cloning '{public_url}': {e}"
                logger.error(err_msg)
                raise RepoError(err_msg) from e
        else:
            # Check if it is a valid git repository
            git_dir = target_dir / ".git"
            if not git_dir.exists():
                raise RepoError(
                    f"Directory '{target_dir}' exists but is not a valid git repository."
                )

            logger.info(f"Pulling latest changes (shallow) for {target_dir}")
            try:
                # Try shallow pull or fetch & reset
                res = subprocess.run(
                    ["git", "pull", "--depth", "1"],
                    cwd=target_dir,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=60
                )
                logger.info(f"Successfully updated {repo_name}: {res.stdout.strip()}")
            except subprocess.CalledProcessError as e:
                logger.warning(f"git pull failed ({e.stderr.strip()}), attempting git fetch and reset")
                try:
                    subprocess.run(
                        ["git", "fetch", "--depth", "1"],
                        cwd=target_dir,
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=60
                    )
                    subprocess.run(
                        ["git", "reset", "--hard", "FETCH_HEAD"],
                        cwd=target_dir,
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=30
                    )
                except Exception as fallback_err:
                    err_msg = f"Failed to pull/fetch public repo '{repo_name}': {fallback_err}"
                    logger.error(err_msg)
                    raise RepoError(err_msg) from fallback_err

        return target_dir

    def _prepare_local_repo(self, local_identifier: str) -> Path:
        """Validates and returns the path for a configured or local repository."""
        resolved_path: Optional[Path] = None

        # 1. Check in configured repo mapping
        if local_identifier in self.repo_mapping:
            resolved_path = self.repo_mapping[local_identifier]
        else:
            # Check case-insensitive or partial key
            for k, v in self.repo_mapping.items():
                if k.lower() == local_identifier.lower():
                    resolved_path = v
                    break

        # 2. Check if local_identifier is already an absolute or relative directory path
        if not resolved_path:
            direct_path = Path(local_identifier).expanduser().resolve()
            if direct_path.exists() and direct_path.is_dir():
                resolved_path = direct_path

        # 3. Fallback to default 'gem-bridge' if available
        if not resolved_path:
            if "gem-bridge" in self.repo_mapping:
                logger.warning(
                    f"Unknown repository '{local_identifier}'. Falling back to default 'gem-bridge'."
                )
                resolved_path = self.repo_mapping["gem-bridge"]
            else:
                available = list(self.repo_mapping.keys())
                raise RepoError(
                    f"Repository '{local_identifier}' not found in configuration or filesystem. Available: {available}"
                )

        # Validate existence & permissions
        if not resolved_path.exists():
            raise RepoError(f"Repository path does not exist: {resolved_path}")

        if not resolved_path.is_dir():
            raise RepoError(f"Repository path is not a directory: {resolved_path}")

        if not os.access(resolved_path, os.R_OK):
            raise RepoError(f"Read permission denied for repository path: {resolved_path}")

        return resolved_path
