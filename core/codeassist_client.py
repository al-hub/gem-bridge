"""
Google Code Assist Direct Client for gem-bridge (Tier-0 Engine).

Connects directly to the Google Cloud Code Assist backend
(daily-cloudcode-pa.googleapis.com) using the local Antigravity 1P OAuth token.
This provides:
- Dedicated TPU clusters isolated from public Google AI Studio free-tier load shedding (503-free).
- Google AI Pro subscription quota (up to 1,500 requests per day).
- Direct access to gemini-3.8-flash-tiered and gemini-3.6-flash-high.
"""

import json
import logging
import os
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("gem_bridge.codeassist_client")

DEFAULT_TOKEN_PATH = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
DEFAULT_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
DEFAULT_PROJECT = "aicode-consumers"

# Canonical internal model identifiers for Code Assist
MODEL_ALIAS_MAP: Dict[str, str] = {
    "gemini-3.8-flash": "gemini-3.8-flash-tiered",
    "gemini-3.8-flash-tiered": "gemini-3.8-flash-tiered",
    "gemini-3.6-flash": "gemini-3.6-flash-high",
    "gemini-3.6-flash-high": "gemini-3.6-flash-high",
    "gemini-3.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3.1-pro": "gemini-3.1-pro-low",
    "gemini-3.1-pro-low": "gemini-3.1-pro-low",
    "gemini-pro": "gemini-3.1-pro-low",
}


class CodeAssistError(Exception):
    """Raised when Code Assist generation fails."""
    pass


class CodeAssistClient:
    """
    Tier-0 High-Throughput Client interfacing with Google Code Assist backend.
    Leverages the local user's Google AI Pro subscription session from Antigravity.
    """

    def __init__(
        self,
        token_path: Optional[Path] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        project: str = DEFAULT_PROJECT
    ):
        if token_path:
            self.token_path = Path(token_path)
        else:
            env_path = os.environ.get("ANTIGRAVITY_TOKEN_PATH")
            self.token_path = Path(env_path) if env_path else DEFAULT_TOKEN_PATH

        self.endpoint = endpoint
        self.project = project

    def is_available(self) -> bool:
        """Checks if the Antigravity OAuth token file is present and readable."""
        if not self.token_path.exists():
            return False
        try:
            token = self._load_access_token()
            return bool(token)
        except Exception:
            return False

    def _load_access_token(self) -> Optional[str]:
        """Reads and extracts the access token from the JSON token file."""
        if not self.token_path.exists():
            return None
        with open(self.token_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        token_info = data.get("token", {})
        return token_info.get("access_token")

    def generate_content(
        self,
        prompt: str,
        model: str = "gemini-3.8-flash-tiered",
        system_instruction: Optional[str] = None,
        timeout: int = 30
    ) -> str:
        """
        Sends generation request to the Code Assist internal SSE endpoint.

        Args:
            prompt: User prompt or text to analyze/synthesize.
            model: Model name (mapped via MODEL_ALIAS_MAP).
            system_instruction: Optional system instruction for the model.
            timeout: HTTP request timeout in seconds.

        Returns:
            The complete response text from the model.

        Raises:
            CodeAssistError: If token is missing, expired, or server returns error.
        """
        access_token = self._load_access_token()
        if not access_token:
            raise CodeAssistError(f"Antigravity OAuth token not found or invalid at: {self.token_path}")

        resolved_model = MODEL_ALIAS_MAP.get(model, model)

        request_payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}]
                }
            ]
        }
        if system_instruction and system_instruction.strip():
            request_payload["systemInstruction"] = {
                "parts": [{"text": system_instruction.strip()}]
            }

        body = {
            "project": self.project,
            "model": resolved_model,
            "request": request_payload
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity/1.0.0"
        }

        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        full_text = []
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for line in resp:
                    decoded = line.decode("utf-8", errors="ignore").strip()
                    if decoded.startswith("data:"):
                        chunk_raw = decoded[5:].strip()
                        if not chunk_raw:
                            continue
                        try:
                            chunk = json.loads(chunk_raw)
                            candidates = chunk.get("response", {}).get("candidates", [])
                            for cand in candidates:
                                parts = cand.get("content", {}).get("parts", [])
                                for p in parts:
                                    text_val = p.get("text")
                                    if text_val:
                                        full_text.append(text_val)
                        except json.JSONDecodeError:
                            continue
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            raise CodeAssistError(f"CodeAssist HTTP {e.code} ({e.reason}): {err_body}") from e
        except urllib.error.URLError as e:
            raise CodeAssistError(f"CodeAssist Network Error: {e.reason}") from e
        except Exception as e:
            raise CodeAssistError(f"CodeAssist Error: {e}") from e

        result = "".join(full_text).strip()
        if not result:
            raise CodeAssistError(f"CodeAssist returned empty response for model {resolved_model}")

        return result
