import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deepseek_chat


def json_response(payload: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode())


class ClientTest(unittest.TestCase):
    def test_upload_sends_multipart_bytes_and_conversation_id(self):
        response = {
            "id": "file-test",
            "filename": "upload.png",
            "status": "SUCCESS",
            "is_image": True,
            "model_kind": "VISION",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "снимок.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\nimage-data")
            with patch(
                "deepseek_chat.urllib.request.urlopen",
                return_value=json_response(response),
            ) as urlopen:
                result = deepseek_chat.upload_file(
                    "http://127.0.0.1:28080/v1", "manual", str(path)
                )

        request = urlopen.call_args.args[0]
        self.assertIn("conversation_id=manual", request.full_url)
        self.assertTrue(request.headers["Content-type"].startswith("multipart/form-data;"))
        self.assertIn(b"image-data", request.data)
        self.assertEqual(result["id"], "file-test")

    def test_ask_forwards_search_flag(self):
        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch(
            "deepseek_chat.urllib.request.urlopen",
            return_value=json_response(response),
        ) as urlopen:
            result = deepseek_chat.ask(
                "http://127.0.0.1:28080/v1",
                "manual",
                "latest",
                "deepseek-v3",
                search_enabled=True,
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(result, "ok")
        self.assertTrue(payload["search_enabled"])
        self.assertEqual(payload["conversation_id"], "manual")
        self.assertNotIn("file_ids", payload)

    def test_ask_forwards_file_ids_only_when_provided(self):
        response = {"choices": [{"message": {"content": "image ok"}}]}
        with patch(
            "deepseek_chat.urllib.request.urlopen",
            return_value=json_response(response),
        ) as urlopen:
            result = deepseek_chat.ask(
                "http://127.0.0.1:28080/v1",
                "manual",
                "what is on the image?",
                "deepseek-v3",
                file_ids=["file-test"],
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(result, "image ok")
        self.assertEqual(payload["file_ids"], ["file-test"])


if __name__ == "__main__":
    unittest.main()
