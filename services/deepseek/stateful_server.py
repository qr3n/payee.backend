#!/usr/bin/env python3
"""Stateful OpenAI-compatible facade for smkttl/deepseek-api.

The upstream ``DeepSeekChat`` object already knows how to continue a web chat
through ``chat_session_id`` and ``parent_message_id``. The upstream HTTP server
creates a fresh object per request, losing both values. This facade keeps one
object per explicit ``conversation_id`` and serializes access to it.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from curl_cffi import CurlMime, requests as curl_requests
from flask import Flask, Response, jsonify, request

from DeepSeekAPI import DeepSeekChat

app = Flask(__name__)

DS_SESSION_ID = os.environ.get("DS_SESSION_ID", "")
AUTHORIZATION_TOKEN = os.environ.get("AUTHORIZATION_TOKEN", "")
CONVERSATION_TTL_SECONDS = int(os.environ.get("CONVERSATION_TTL_SECONDS", "3600"))
MAX_CONVERSATIONS = int(os.environ.get("MAX_CONVERSATIONS", "100"))
UPSTREAM_RETRIES = max(1, int(os.environ.get("UPSTREAM_RETRIES", "3")))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
FILE_PROCESS_TIMEOUT_SECONDS = int(
    os.environ.get("FILE_PROCESS_TIMEOUT_SECONDS", "180")
)
FILE_POLL_INTERVAL_SECONDS = float(os.environ.get("FILE_POLL_INTERVAL_SECONDS", "1"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024 * 1024


@dataclass
class ConversationState:
    chat: DeepSeekChat
    last_used: float
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_model_type: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_conversations: dict[str, ConversationState] = {}
_conversations_lock = threading.Lock()


def _model_config(model: str) -> tuple[str, bool]:
    model_lower = model.lower()
    model_type = (
        "expert"
        if "v4" in model_lower or "r4" in model_lower or "expert" in model_lower
        else "default"
    )
    thinking_enabled = any(
        marker in model_lower
        for marker in ("r1", "r4", "reasoning", "reasoner")
    )
    return model_type, thinking_enabled


def _conversation_id(data: dict[str, Any] | None = None) -> str:
    value = (
        (data or {}).get("conversation_id")
        or request.args.get("conversation_id")
        or request.form.get("conversation_id")
        or request.headers.get("X-Conversation-ID")
    )
    conversation_id = str(value).strip() if value else str(uuid.uuid4())
    if not conversation_id or len(conversation_id) > 128:
        raise ValueError("conversation_id must contain 1-128 characters")
    return conversation_id


def _prune_conversations(now: float) -> None:
    expired = [
        key
        for key, state in _conversations.items()
        if now - state.last_used > CONVERSATION_TTL_SECONDS
    ]
    for key in expired:
        _conversations.pop(key, None)

    while len(_conversations) >= MAX_CONVERSATIONS:
        oldest = min(_conversations, key=lambda key: _conversations[key].last_used)
        _conversations.pop(oldest, None)


def _get_conversation(conversation_id: str) -> ConversationState:
    now = time.monotonic()
    with _conversations_lock:
        _prune_conversations(now)
        state = _conversations.get(conversation_id)
        if state is None:
            state = ConversationState(
                chat=DeepSeekChat(DS_SESSION_ID, AUTHORIZATION_TOKEN),
                last_used=now,
            )
            _conversations[conversation_id] = state
        else:
            state.last_used = now
        return state


def _extract_content(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or not result.get("ok"):
        detail = result.get("content") if isinstance(result, dict) else result
        raise RuntimeError(f"DeepSeek request failed: {detail}")
    content = result.get("content")
    if not isinstance(content, dict):
        raise RuntimeError("DeepSeek returned an invalid content object")
    return content


def _extract_response(content: dict[str, Any]) -> str:
    response_text = content.get("response")
    if not isinstance(response_text, str) or not response_text.strip():
        raise RuntimeError("DeepSeek returned an empty response")
    return response_text.strip()


def _completion_payload(
    model: str,
    conversation_id: str,
    response_text: str,
    search_enabled: bool,
    file_ids: list[str],
    citations: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "conversation_id": conversation_id,
        "search_enabled": search_enabled,
        "file_ids": file_ids,
        "citations": citations,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(response_text.split()),
            "total_tokens": len(response_text.split()),
        },
    }


def _upstream_data(response: Any) -> dict[str, Any]:
    if response.status_code != 200:
        raise RuntimeError(
            f"DeepSeek file request returned HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"DeepSeek file request failed: {payload}")
    data = payload.get("data") or {}
    if data.get("biz_code") != 0:
        raise RuntimeError(
            f"DeepSeek file request failed: {data.get('biz_msg') or data}"
        )
    biz_data = data.get("biz_data")
    if not isinstance(biz_data, dict):
        raise RuntimeError("DeepSeek file request returned invalid data")
    return biz_data


def _upload_file(
    chat: DeepSeekChat,
    filename: str,
    content_type: str,
    content: bytes,
) -> dict[str, Any]:
    target_path = "/api/v0/file/upload_file"
    challenge = chat.create_pow_challenge(target_path)
    if not challenge:
        raise RuntimeError("Can't create file upload PoW challenge")
    solved, pow_response = chat.solve_pow_challenge(challenge)
    if not solved:
        raise RuntimeError("Can't solve file upload PoW challenge")
    # The current file endpoint requires standard padded base64. The pinned
    # upstream strips padding because completion accepts both forms.
    pow_response += "=" * (-len(pow_response) % 4)

    # The file endpoint applies a stricter browser check than completion and
    # validates the TLS/browser fingerprint. curl_cffi impersonates Chrome;
    # requests.Session remains in use for the upstream chat stream.
    base_headers = {
        "accept": "*/*",
        "authorization": chat.authorization,
        "origin": "https://chat.deepseek.com",
        "referer": "https://chat.deepseek.com/",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "x-app-version": "2.0.0",
        "x-client-locale": "zh_CN",
        "x-client-platform": "web",
        "x-client-timezone-offset": "28800",
        "x-client-version": "2.0.0",
    }
    upload_headers = {
        **base_headers,
        "x-ds-pow-response": pow_response,
        "x-file-size": str(len(content)),
        "x-thinking-enabled": "1",
        "x-model-type": "vision" if content_type.startswith("image/") else "default",
    }
    cookies = chat.session.cookies.get_dict()
    multipart = CurlMime()
    multipart.addpart(
        name="file",
        content_type=content_type,
        filename=filename,
        data=content,
    )
    try:
        with curl_requests.Session(impersonate="chrome136") as file_client:
            response = file_client.post(
                f"{chat.base_url}{target_path}",
                headers=upload_headers,
                cookies=cookies,
                multipart=multipart,
                timeout=45,
            )
            uploaded = _upstream_data(response)
            file_id = uploaded.get("id")
            if not isinstance(file_id, str) or not file_id:
                raise RuntimeError("DeepSeek file upload returned no file id")

            deadline = time.monotonic() + FILE_PROCESS_TIMEOUT_SECONDS
            status_data = uploaded
            while time.monotonic() < deadline:
                response = file_client.get(
                    f"{chat.base_url}/api/v0/file/fetch_files",
                    headers=base_headers,
                    cookies=cookies,
                    params={"file_ids": file_id},
                    timeout=30,
                )
                fetched = _upstream_data(response)
                files = fetched.get("files") or []
                match = next(
                    (
                        item
                        for item in files
                        if isinstance(item, dict) and item.get("id") == file_id
                    ),
                    None,
                )
                if match:
                    status_data = match
                    status = str(match.get("status", "")).upper()
                    if status in {
                        "SUCCESS",
                        "PROCESSED",
                        "READY",
                        "DONE",
                        "AVAILABLE",
                        "COMPLETED",
                        "FINISHED",
                        "CONTENT_EMPTY",
                    }:
                        return match
                    if status in {"FAILED", "ERROR"}:
                        raise RuntimeError(
                            f"DeepSeek could not process the file: "
                            f"{match.get('error_code') or 'unknown error'}"
                        )
                time.sleep(FILE_POLL_INTERVAL_SECONDS)
    finally:
        multipart.close()
    raise RuntimeError(
        f"DeepSeek file processing timed out (last status: "
        f"{status_data.get('status', 'unknown')})"
    )


def _citations(content: dict[str, Any]) -> list[dict[str, Any]]:
    raw = content.get("citation")
    if isinstance(raw, dict):
        return [item for item in raw.values() if isinstance(item, dict)]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


@app.get("/health")
def health() -> Response:
    with _conversations_lock:
        count = len(_conversations)
    return jsonify({"status": "ok", "active_conversations": count})


@app.get("/v1/models")
def list_models() -> Response:
    return jsonify(
        {
            "object": "list",
            "data": [
                {"id": "deepseek-v3", "object": "model", "owned_by": "deepseek"},
                {"id": "deepseek-r1", "object": "model", "owned_by": "deepseek"},
                {"id": "deepseek-v4", "object": "model", "owned_by": "deepseek"},
                {"id": "deepseek-r4", "object": "model", "owned_by": "deepseek"},
            ],
        }
    )


@app.post("/v1/files")
def upload_file() -> Response:
    incoming = request.files.get("file")
    if incoming is None or not incoming.filename:
        return jsonify({"error": "multipart field 'file' is required"}), 400
    try:
        conversation_id = _conversation_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    filename = os.path.basename(incoming.filename.replace("\x00", ""))[:255]
    content = incoming.stream.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        return jsonify({"error": "file is empty"}), 400
    if len(content) > MAX_UPLOAD_BYTES:
        return jsonify({"error": f"file exceeds {MAX_UPLOAD_BYTES} bytes"}), 413
    content_type = (
        incoming.mimetype
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )

    state = _get_conversation(conversation_id)
    try:
        with state.lock:
            metadata = _upload_file(state.chat, filename, content_type, content)
            file_id = metadata["id"]
            state.files[file_id] = metadata
            state.last_used = time.monotonic()
    except Exception as exc:
        app.logger.exception("DeepSeek file upload failed")
        return jsonify({"error": str(exc), "conversation_id": conversation_id}), 502

    return jsonify(
        {
            "object": "file",
            "conversation_id": conversation_id,
            "id": file_id,
            "filename": metadata.get("file_name", filename),
            "status": metadata.get("status", "SUCCESS"),
            "is_image": bool(metadata.get("is_image")),
            "model_kind": metadata.get("model_kind"),
            "size": metadata.get("file_size", len(content)),
        }
    )


@app.get("/v1/conversations/<conversation_id>/files")
def list_conversation_files(conversation_id: str) -> Response:
    with _conversations_lock:
        state = _conversations.get(conversation_id)
        files = list(state.files.values()) if state else []
    return jsonify({"conversation_id": conversation_id, "files": files})


@app.post("/v1/chat/completions")
def chat_completions() -> Response:
    data = request.get_json(silent=True) or {}
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "messages must be a non-empty list"}), 400
    user_message = messages[-1].get("content")
    if not isinstance(user_message, str) or not user_message.strip():
        return jsonify({"error": "the last message must contain text"}), 400

    try:
        conversation_id = _conversation_id(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    model = str(data.get("model", "deepseek-v3"))
    model_type, thinking_enabled = _model_config(model)
    search_enabled = data.get("search_enabled", "search" in model.lower())
    if not isinstance(search_enabled, bool):
        return jsonify({"error": "search_enabled must be true or false"}), 400
    state = _get_conversation(conversation_id)
    requested_file_ids = data.get("file_ids")
    if requested_file_ids is not None and not (
        isinstance(requested_file_ids, list)
        and all(isinstance(item, str) and item for item in requested_file_ids)
    ):
        return jsonify({"error": "file_ids must be an array of strings"}), 400
    file_ids = (
        list(dict.fromkeys(requested_file_ids))
        if requested_file_ids is not None
        else []
    )
    completion_model_type = (
        "vision"
        if any(state.files.get(file_id, {}).get("is_image") for file_id in file_ids)
        else model_type
    )

    try:
        # DeepSeekChat is mutable: its parent_message_id advances after every
        # response. A per-conversation lock prevents branching by concurrent calls.
        with state.lock:
            if state.last_model_type and state.last_model_type != completion_model_type:
                state.chat = DeepSeekChat(DS_SESSION_ID, AUTHORIZATION_TOKEN)
                state.last_model_type = None
            for attempt in range(UPSTREAM_RETRIES):
                result = state.chat.send_message(
                    user_message.strip(),
                    printing=False,
                    thinking_enabled=thinking_enabled,
                    search_enabled=search_enabled,
                    model_type=completion_model_type,
                    ref_file_ids=file_ids,
                )
                try:
                    result_content = _extract_content(result)
                    response_text = _extract_response(result_content)
                    state.last_model_type = completion_model_type
                    break
                except RuntimeError as exc:
                    retryable = "MISSING_HEADER" in str(exc)
                    if not retryable or attempt + 1 >= UPSTREAM_RETRIES:
                        raise
                    time.sleep(1)
            state.last_used = time.monotonic()
    except Exception as exc:
        app.logger.exception("DeepSeek completion failed")
        return jsonify({"error": str(exc), "conversation_id": conversation_id}), 502

    payload = _completion_payload(
        model,
        conversation_id,
        response_text,
        search_enabled,
        file_ids,
        _citations(result_content),
    )
    response = jsonify(payload)
    response.headers["X-Conversation-ID"] = conversation_id
    return response


@app.delete("/v1/conversations/<conversation_id>")
def reset_conversation(conversation_id: str) -> Response:
    with _conversations_lock:
        removed = _conversations.pop(conversation_id, None) is not None
    return jsonify({"conversation_id": conversation_id, "reset": removed})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stateful DeepSeek API server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, threaded=True)
