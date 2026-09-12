"""
gem-bridge Core Architecture Package
Modular Agent Architecture with Dispatcher-Executor Pattern.
"""

from core.__version__ import __version__, __version_info__
from core.intent_analyzer import TaskType, IntentAnalysisResult, IntentAnalyzer
from core.repo_manager import RepoManager, RepoError
from core.executor_read import ReadExecutor
from core.executor_write import WriteExecutor, ProtectedFileError
from core.executor_exec import ExecExecutor

__all__ = [
    "__version__",
    "__version_info__",
    "TaskType",
    "IntentAnalysisResult",
    "IntentAnalyzer",
    "RepoManager",
    "RepoError",
    "ReadExecutor",
    "WriteExecutor",
    "ProtectedFileError",
    "ExecExecutor",
]
