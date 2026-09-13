import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

from core.codeassist_client import CodeAssistClient, CodeAssistError, MODEL_ALIAS_MAP


class TestCodeAssistClient(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_file = Path(self.temp_dir.name) / "test-token"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_is_available_false_when_missing(self):
        client = CodeAssistClient(token_path=self.token_file)
        self.assertFalse(client.is_available())

    def test_is_available_true_when_token_present(self):
        self.token_file.write_text(json.dumps({"token": {"access_token": "ya29.test_token"}}), encoding="utf-8")
        client = CodeAssistClient(token_path=self.token_file)
        self.assertTrue(client.is_available())

    def test_is_available_false_when_malformed_json(self):
        self.token_file.write_text("invalid json", encoding="utf-8")
        client = CodeAssistClient(token_path=self.token_file)
        self.assertFalse(client.is_available())

    def test_model_alias_mapping(self):
        self.assertEqual(MODEL_ALIAS_MAP.get("gemini-3.8-flash"), "gemini-3.8-flash-tiered")
        self.assertEqual(MODEL_ALIAS_MAP.get("gemini-3.6-flash"), "gemini-3.6-flash-high")
        self.assertEqual(MODEL_ALIAS_MAP.get("gemini-3.5-flash-lite"), "gemini-3.5-flash-lite")

    @patch("urllib.request.urlopen")
    def test_generate_content_success(self, mock_urlopen):
        self.token_file.write_text(json.dumps({"token": {"access_token": "ya29.test_token"}}), encoding="utf-8")
        client = CodeAssistClient(token_path=self.token_file)

        sse_chunks = [
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello "}]}}]}}\n',
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "world!"}]}}]}}\n',
            b'\n'
        ]
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = sse_chunks
        mock_resp.__exit__.return_value = None
        mock_urlopen.return_value = mock_resp

        result = client.generate_content("Say hello", model="gemini-3.8-flash")
        self.assertEqual(result, "Hello world!")

        # Verify request parameters
        req_sent = mock_urlopen.call_args[0][0]
        self.assertEqual(req_sent.get_header("Authorization"), "Bearer ya29.test_token")
        sent_body = json.loads(req_sent.data.decode("utf-8"))
        self.assertEqual(sent_body["model"], "gemini-3.8-flash-tiered")
        self.assertEqual(sent_body["request"]["contents"][0]["parts"][0]["text"], "Say hello")

    @patch("urllib.request.urlopen")
    def test_generate_content_with_system_instruction(self, mock_urlopen):
        self.token_file.write_text(json.dumps({"token": {"access_token": "ya29.test_token"}}), encoding="utf-8")
        client = CodeAssistClient(token_path=self.token_file)

        sse_chunks = [
            b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Synthesized code"}]}}]}}\n'
        ]
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = sse_chunks
        mock_resp.__exit__.return_value = None
        mock_urlopen.return_value = mock_resp

        result = client.generate_content(
            prompt="def add(a, b): pass",
            model="gemini-3.8-flash-tiered",
            system_instruction="You are an expert coder."
        )
        self.assertEqual(result, "Synthesized code")

        req_sent = mock_urlopen.call_args[0][0]
        sent_body = json.loads(req_sent.data.decode("utf-8"))
        self.assertEqual(sent_body["request"]["systemInstruction"]["parts"][0]["text"], "You are an expert coder.")

    @patch("urllib.request.urlopen")
    def test_generate_content_http_error_raises_codeassist_error(self, mock_urlopen):
        self.token_file.write_text(json.dumps({"token": {"access_token": "ya29.test_token"}}), encoding="utf-8")
        client = CodeAssistClient(token_path=self.token_file)

        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://test",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=io.BytesIO(b"Overloaded")
        )

        with self.assertRaises(CodeAssistError) as ctx:
            client.generate_content("test prompt")
        self.assertIn("503", str(ctx.exception))

    def test_generate_content_missing_token_raises_error(self):
        client = CodeAssistClient(token_path=self.token_file)
        with self.assertRaises(CodeAssistError):
            client.generate_content("test prompt")


if __name__ == "__main__":
    unittest.main()
