#!/usr/bin/env python3
"""Minimal interactive client for the local stateful DeepSeek proxy."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shlex
import sys
import uuid
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


def ask(
    base_url: str,
    conversation_id: str,
    prompt: str,
    model: str,
    search_enabled: bool = False,
    file_ids: list[str] | None = None,
) -> str:
    payload = {
        "model": model,
        "conversation_id": conversation_id,
        "search_enabled": search_enabled,
        "messages": [{"role": "user", "content": prompt}],
    }
    if file_ids is not None:
        payload["file_ids"] = file_ids
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"proxy returned HTTP {exc.code}: {detail}") from exc
    return payload["choices"][0]["message"]["content"].strip()


def upload_file(base_url: str, conversation_id: str, raw_path: str) -> dict:
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise RuntimeError(f"файл не найден: {path}")

    content = path.read_bytes()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    boundary = f"----deepseek-wrapper-{uuid.uuid4().hex}"
    suffix = path.suffix if len(path.suffix) <= 16 else ""
    fallback_name = f"upload{suffix}"
    encoded_name = urllib.parse.quote(path.name, safe="")
    disposition = (
        f'Content-Disposition: form-data; name="file"; filename="{fallback_name}"; '
        f"filename*=UTF-8''{encoded_name}\r\n"
    )
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            disposition.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    query = urllib.parse.urlencode({"conversation_id": conversation_id})
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/files?{query}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"proxy returned HTTP {exc.code}: {detail}") from exc


def list_files(base_url: str, conversation_id: str) -> list[dict]:
    encoded_id = urllib.parse.quote(conversation_id, safe="")
    with urllib.request.urlopen(
        f"{base_url.rstrip('/')}/conversations/{encoded_id}/files", timeout=10
    ) as response:
        payload = json.load(response)
    return payload.get("files", [])


def print_uploaded(metadata: dict) -> None:
    kind = metadata.get("model_kind") or (
        "VISION" if metadata.get("is_image") else "NORMAL"
    )
    print(
        f"Файл загружен: {metadata.get('filename', metadata.get('file_name', '?'))} "
        f"[{kind}], id={metadata.get('id', '?')}"
    )


def reset(base_url: str, conversation_id: str) -> None:
    encoded_id = urllib.parse.quote(conversation_id, safe="")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/conversations/{encoded_id}",
        method="DELETE",
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Chat with the local DeepSeek proxy")
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--conversation-id", default="manual")
    parser.add_argument("--model", default="deepseek-v3")
    parser.add_argument("--search", action="store_true", help="enable web search")
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="PATH",
        help="upload and attach a file before sending the prompt",
    )
    default_base_url = os.environ.get(
        "DEEPSEEK_BASE_URL",
        f"http://127.0.0.1:{os.environ.get('PROXY_PORT', '28080')}/v1",
    )
    parser.add_argument("--base-url", default=default_base_url)
    args = parser.parse_args()

    try:
        uploaded_file_ids = []
        for path in args.file:
            metadata = upload_file(args.base_url, args.conversation_id, path)
            print_uploaded(metadata)
            if metadata.get("id"):
                uploaded_file_ids.append(metadata["id"])
    except Exception as exc:
        print(f"Ошибка загрузки: {exc}", file=sys.stderr)
        return 1

    if args.prompt:
        print(
            ask(
                args.base_url,
                args.conversation_id,
                args.prompt,
                args.model,
                args.search,
                uploaded_file_ids or None,
            )
        )
        return 0

    search_enabled = args.search
    pending_file_ids = uploaded_file_ids
    print(f"DeepSeek conversation: {args.conversation_id}")
    print(
        "Введите сообщение; /file PATH — прикрепить файл; /files — список; "
        "/search on|off — интернет; /reset — новый диалог; /exit — выход."
    )
    while True:
        try:
            prompt = input("Вы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            return 0
        if prompt == "/reset":
            reset(args.base_url, args.conversation_id)
            pending_file_ids.clear()
            print("Контекст сброшен.")
            continue
        if prompt == "/files":
            try:
                files = list_files(args.base_url, args.conversation_id)
                if not files:
                    print("Прикреплённых файлов нет.")
                for item in files:
                    print_uploaded(item)
            except Exception as exc:
                print(f"Ошибка: {exc}", file=sys.stderr)
            continue
        if prompt.startswith("/search"):
            parts = prompt.split()
            if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
                print("Использование: /search on или /search off")
                continue
            search_enabled = parts[1].lower() == "on"
            print(f"Интернет-поиск {'включён' if search_enabled else 'выключен'}.")
            continue
        file_path = None
        if prompt.startswith("/file"):
            try:
                parts = shlex.split(prompt[len("/file") :].strip())
            except ValueError as exc:
                print(f"Ошибка пути: {exc}", file=sys.stderr)
                continue
            if len(parts) != 1:
                print('Использование: /file "/полный/путь/к/файлу.png"')
                continue
            file_path = parts[0]
        else:
            # A quoted path entered by itself is treated as a file attachment.
            try:
                parts = shlex.split(prompt)
            except ValueError:
                parts = []
            if len(parts) == 1 and Path(parts[0]).expanduser().is_file():
                file_path = parts[0]
        if file_path:
            try:
                metadata = upload_file(args.base_url, args.conversation_id, file_path)
                print_uploaded(metadata)
                if metadata.get("id"):
                    pending_file_ids.append(metadata["id"])
                    print("Файл будет прикреплён к следующему сообщению.")
            except Exception as exc:
                print(f"Ошибка загрузки: {exc}", file=sys.stderr)
            continue
        try:
            response = ask(
                args.base_url,
                args.conversation_id,
                prompt,
                args.model,
                search_enabled,
                pending_file_ids or None,
            )
            pending_file_ids.clear()
            print("DeepSeek: " + response)
        except Exception as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
