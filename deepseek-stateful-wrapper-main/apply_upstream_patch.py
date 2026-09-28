#!/usr/bin/env python3
"""Apply verified compatibility fixes to the pinned smkttl upstream source."""
from __future__ import annotations

import sys
from pathlib import Path


def replace_exact(source: str, old: str, new: str, expected: int = 1) -> str:
    count = source.count(old)
    if count != expected:
        raise RuntimeError(
            f"upstream patch mismatch: expected {expected} occurrence(s), found {count}: {old!r}"
        )
    return source.replace(old, new)


def main() -> None:
    path = Path(sys.argv[1])
    source = path.read_text()

    source = replace_exact(
        source,
        '            "content-type": "application/json",\n'
        '            "priority": "u=1, i",',
        '            "content-type": "application/json",\n'
        '            "origin": "https://chat.deepseek.com",\n'
        '            "referer": "https://chat.deepseek.com/",\n'
        '            "priority": "u=1, i",',
    )
    source = replace_exact(
        source,
        '            "priority": "u=1, i",\n'
        '            "sec-fetch-dest": "empty",',
        '            "priority": "u=1, i",\n'
        '            "sec-ch-ua": \'"Chromium";v="134", "Not:A-Brand";v="24", '
        '"Google Chrome";v="134"\',\n'
        '            "sec-ch-ua-mobile": "?0",\n'
        '            "sec-ch-ua-platform": \'"macOS"\',\n'
        '            "sec-fetch-dest": "empty",',
    )
    source = replace_exact(source, '"x-client-locale": "zh_CN"', '"x-client-locale": "zh-CN"')
    source = replace_exact(
        source,
        '"x-client-version": "1.5.0"',
        '"x-client-version": "2.0.0"',
    )
    source = replace_exact(
        source,
        '"x-app-version": "20241129.1"',
        '"x-app-version": "2.0.0"',
    )
    source = replace_exact(
        source,
        '"user-agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) '
        'Gecko/20100101 Firefox/142.0"',
        '"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"',
    )
    source = replace_exact(source, 'headers["referrer"]', 'headers["referer"]', expected=2)
    source = replace_exact(
        source,
        '                self.chat_session_id=result["data"]["biz_data"]["id"]\n',
        '                biz_data=result["data"]["biz_data"]\n'
        '                self.chat_session_id=biz_data.get("id") or biz_data["chat_session"]["id"]\n',
    )
    source = replace_exact(
        source,
        "    def create_pow_challenge(self):\n",
        '    def create_pow_challenge(self, target_path="/api/v0/chat/completion"):\n',
    )
    source = replace_exact(
        source,
        '        data={"target_path":"/api/v0/chat/completion"}\n',
        '        data={"target_path":target_path}\n',
    )
    source = replace_exact(
        source,
        "    def send_message(self,message,printing=None,thinking_enabled=False,search_enabled=False,model_type=\"default\"):\n",
        "    def send_message(self,message,printing=None,thinking_enabled=False,search_enabled=False,model_type=\"default\",ref_file_ids=None):\n",
    )
    source = replace_exact(
        source,
        """        headers = self.headers.copy()
        headers["x-ds-pow-response"] = pow_response
        headers["accept"] = "text/event-stream"
""",
        """        headers = self.headers.copy()
        headers["x-ds-pow-response"] = pow_response
        headers["accept"] = "text/event-stream"
        headers["x-client-timezone-offset"] = "28800"
        headers["x-thinking-enabled"] = "1" if thinking_enabled else "0"
        headers["x-model-type"] = model_type
""",
    )
    source = replace_exact(
        source,
        """        if model_type not in ("default", "expert"):
            return {"ok": False, "content": "model_type must be 'default' or 'expert'."}
""",
        """        if model_type not in ("default", "expert", "vision"):
            return {"ok": False, "content": "model_type must be 'default', 'expert', or 'vision'."}
""",
    )
    source = replace_exact(source, '            "ref_file_ids": [],', '            "ref_file_ids": list(ref_file_ids or []),')
    source = replace_exact(source, '            "search_enabled": False,', '            "search_enabled": search_enabled,')
    source = replace_exact(source, '                ret["search_enabled"]=False', '                ret["search_enabled"]=search_enabled')
    source = replace_exact(
        source,
        """                            msgid=data.get('message_id',msgid)
                        elif 'updated_at' in data and len(data)==1:
""",
        """                            msgid=data.get('message_id',msgid)
                            # Continuation snapshots carry the first generated
                            # text inside fragments alongside message metadata.
                            if 'fragments' in data:
                                parse_output(data['fragments'],line)
                        elif 'updated_at' in data and len(data)==1:
""",
    )
    source = replace_exact(
        source,
        """                        elif 'p' in data:
                            tp=data['p'].split('/')[-1]
""",
        """                        elif 'p' in data:
                            update_path=data['p']
                            tp=update_path.split('/')[-1]
                            # Continuation turns may omit the type marker and
                            # start directly with response fragment updates.
                            if not generate_mode and update_path.startswith('response/'):
                                generate_mode='RESPONSE'
""",
    )
    source = replace_exact(
        source,
        """                        elif generate_mode=='TIP':
                            pass
                        else:
""",
        """                        elif generate_mode in ('TIP','SEARCH','TOOL_SEARCH','TOOL_OPEN'):
                            pass
                        else:
""",
    )
    source = replace_exact(
        source,
        """                        if generate_mode=='SEARCH' and 'url' in data:
                            citation[data.get('cite_index')]=data
                            if printing:
                                send_to_sd(f"{data.get('cite_index','?')}. [{data.get('title',data.get('site_name','UNKNOWN'))} - {data.get('site_name','UNKNOWN')}]({data['url']})\\n")
                                send_to_sd('> '+data.get('snippet','')+"\\n")
""",
        """                        if 'url' in data and (generate_mode in ('SEARCH','TOOL_SEARCH','TOOL_OPEN') or 'cite_index' in data or 'site_name' in data or 'snippet' in data):
                            cite_index=data.get('cite_index') or data.get('id') or (len(citation)+1)
                            citation[cite_index]=data
                            if printing:
                                send_to_sd(f"{cite_index}. [{data.get('title',data.get('site_name','UNKNOWN'))}]({data['url']})\\n")
                                send_to_sd('> '+data.get('snippet','')+"\\n")
""",
    )
    source = replace_exact(
        source,
        """                            elif tp=='results' and generate_mode=='SEARCH':
                                parse_output(data['v'],line)
                            elif tp=='has_pending_fragment' or tp=='conversation_mode' or tp=='quasi_status':
                                pass
""",
        """                            elif tp=='search_results' or (tp in ('results','result') and generate_mode in ('SEARCH','TOOL_SEARCH','TOOL_OPEN')):
                                parse_output(data['v'],line)
                            elif tp in ('has_pending_fragment','conversation_mode','quasi_status','search_status','references','queries'):
                                pass
""",
    )
    source = replace_exact(
        source,
        """                            if 'content' in data:
                                parse_output(data['content'],line)
""",
        """                            if data.get('content') is not None:
                                parse_output(data['content'],line)
                            if data['type'] in ('SEARCH','TOOL_SEARCH') and 'results' in data:
                                parse_output(data['results'],line)
                            if data['type']=='TOOL_OPEN' and 'result' in data:
                                parse_output(data['result'],line)
""",
    )

    path.write_text(source)


if __name__ == "__main__":
    main()
