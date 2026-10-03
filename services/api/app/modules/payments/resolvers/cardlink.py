"""
Async SBP link extractor for Cardlink payment gateway (cardlink.link).
Extracts clean https://qr.nspk.ru/... from https://cardlink.link/transfer/...
"""

import base64
import json
import re
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.core.logging import get_logger

logger = get_logger(__name__)


def _unwrap_livewire(value: Any) -> Any:
    """Unwrap Livewire array wrappers [value, {'s': 'arr'}]."""
    if (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[1], dict)
        and value[1].get("s") == "arr"
    ):
        return _unwrap_livewire(value[0])

    if isinstance(value, dict):
        return {key: _unwrap_livewire(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_unwrap_livewire(item) for item in value]

    return value


def _find_payment_urls(text: str) -> list[str]:
    """Find valid https://qr.nspk.ru/... URLs in text."""
    clean_text = unescape(text).replace("\\/", "/")
    matches = re.findall(
        r"""https://qr\.nspk\.ru/[^\s<>"'\\)`]+""",
        clean_text,
    )
    results: list[str] = []
    for match in matches:
        parsed = urlsplit(match)
        if (
            parsed.scheme == "https"
            and parsed.netloc == "qr.nspk.ru"
            and match not in results
        ):
            results.append(match)
    return results


def _remember_snapshot(raw_str: str, snapshots: dict[str, str]) -> None:
    try:
        data = json.loads(raw_str)
        name = data.get("memo", {}).get("name")
        if name:
            snapshots[name] = raw_str
    except Exception:
        pass


def _inspect_html(html: str, snapshots: dict[str, str]) -> list[str]:
    urls = _find_payment_urls(html)
    try:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup.select("[wire\\:snapshot]"):
            raw = element.get("wire:snapshot")
            if raw and isinstance(raw, str):
                _remember_snapshot(raw, snapshots)
    except Exception:
        pass
    return urls


class CardlinkSBPResolver:
    """Extracts clean NSPK SBP link from Cardlink Livewire transfer pages."""

    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    )

    @classmethod
    def can_handle(cls, url: str) -> bool:
        """Check if URL is a Cardlink transfer link."""
        try:
            parsed = urlsplit(url)
            return parsed.netloc == "cardlink.link" and bool(
                re.fullmatch(r"/transfer/[A-Za-z0-9]+/?", parsed.path)
            )
        except Exception:
            return False

    @classmethod
    async def _call_livewire(
        cls,
        client: httpx.AsyncClient,
        endpoint: str,
        page_url: str,
        csrf: str,
        snapshots: dict[str, str],
        component_name: str,
        method: str,
        params: list[Any],
        payment_urls: list[str],
    ) -> list[dict[str, Any]]:
        if component_name not in snapshots:
            return []

        payload = {
            "_token": csrf,
            "components": [
                {
                    "snapshot": snapshots[component_name],
                    "updates": {},
                    "calls": [
                        {
                            "method": method,
                            "params": params,
                            "metadata": {},
                        }
                    ],
                }
            ],
        }

        response = await client.post(
            endpoint,
            json=payload,
            headers={
                "Accept": "application/json",
                "X-Livewire": "1",
                "Origin": "https://cardlink.link",
                "Referer": page_url,
            },
        )

        if response.status_code != 200:
            return []

        try:
            result = response.json()
        except Exception:
            return []

        if not isinstance(result, dict):
            return []

        events: list[dict[str, Any]] = []
        for comp in result.get("components", []):
            effects = comp.get("effects") or {}

            html = effects.get("html")
            if isinstance(html, str):
                new_urls = _inspect_html(html, snapshots)
                for u in new_urls:
                    if u not in payment_urls:
                        payment_urls.append(u)

            raw = comp.get("snapshot")
            if isinstance(raw, str):
                _remember_snapshot(raw, snapshots)

            dispatches = effects.get("dispatches") or []
            events.extend(dispatches)

            redirect = effects.get("redirect")
            if isinstance(redirect, str):
                for u in _find_payment_urls(redirect):
                    if u not in payment_urls:
                        payment_urls.append(u)

        return events

    @classmethod
    async def resolve(cls, url: str, timeout: float = 20.0) -> str | None:
        """
        Execute Livewire interaction flow to extract direct qr.nspk.ru URL.
        """
        if not cls.can_handle(url):
            return None

        snapshots: dict[str, str] = {}
        payment_urls: list[str] = []

        headers = {
            "User-Agent": cls.USER_AGENT,
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

        try:
            async with httpx.AsyncClient(
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                # Step 1: Initial page GET
                res = await client.get(url)
                if res.status_code != 200:
                    logger.warning(
                        "cardlink_initial_page_failed",
                        status_code=res.status_code,
                        url=url,
                    )
                    return None

                page_url = str(res.url)
                new_urls = _inspect_html(res.text, snapshots)
                payment_urls.extend(new_urls)
                if payment_urls:
                    return payment_urls[0]

                soup = BeautifulSoup(res.text, "html.parser")
                token_elem = soup.select_one('meta[name="csrf-token"]')
                update_elem = soup.select_one("[data-update-uri]")

                if not token_elem or not update_elem:
                    logger.warning("cardlink_missing_csrf_or_update_uri", url=url)
                    return None

                csrf = token_elem.get("content")
                update_path = update_elem.get("data-update-uri")
                if (
                    not isinstance(csrf, str)
                    or not csrf
                    or not isinstance(update_path, str)
                    or not update_path
                ):
                    return None

                endpoint = urljoin(page_url, update_path)

                collect_comp = "payment-send-form.collect-data"
                main_comp = "payment-send-form.main"
                sbp_comp = "payment-send-form.methods.sbp"

                # Step 2: collect-data component initialization
                if collect_comp in snapshots:
                    offset = datetime.now().astimezone().utcoffset()
                    offset_minutes = (
                        int(offset.total_seconds() / 60) if offset else -180
                    )
                    browser_data = {
                        "screenWidth": 1920,
                        "screenHeight": 1080,
                        "colorDepth": 24,
                        "userAgent": cls.USER_AGENT,
                        "timeZoneOffset": -offset_minutes,
                        "language": "ru-RU",
                        "javaEnabled": False,
                        "location": page_url,
                    }
                    encoded = base64.b64encode(
                        json.dumps(
                            browser_data,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).decode("ascii")

                    events = await cls._call_livewire(
                        client=client,
                        endpoint=endpoint,
                        page_url=page_url,
                        csrf=csrf,
                        snapshots=snapshots,
                        component_name=collect_comp,
                        method="componentInit",
                        params=[encoded],
                        payment_urls=payment_urls,
                    )

                    event = next(
                        (ev for ev in events if ev.get("name") == "dataCollected"),
                        None,
                    )
                    if event is None:
                        events = await cls._call_livewire(
                            client=client,
                            endpoint=endpoint,
                            page_url=page_url,
                            csrf=csrf,
                            snapshots=snapshots,
                            component_name=collect_comp,
                            method="submit",
                            params=[],
                            payment_urls=payment_urls,
                        )
                        event = next(
                            (ev for ev in events if ev.get("name") == "dataCollected"),
                            None,
                        )

                    if event and isinstance(event.get("params"), dict):
                        await cls._call_livewire(
                            client=client,
                            endpoint=endpoint,
                            page_url=page_url,
                            csrf=csrf,
                            snapshots=snapshots,
                            component_name=main_comp,
                            method="__dispatch",
                            params=["dataCollected", event["params"]],
                            payment_urls=payment_urls,
                        )

                if payment_urls:
                    return payment_urls[0]

                # Step 3: SBP component initialization & submission
                if sbp_comp in snapshots:
                    sbp_raw = snapshots[sbp_comp]
                    sbp_data = _unwrap_livewire(json.loads(sbp_raw).get("data", {}))

                    if not sbp_data.get("componentInited"):
                        await cls._call_livewire(
                            client=client,
                            endpoint=endpoint,
                            page_url=page_url,
                            csrf=csrf,
                            snapshots=snapshots,
                            component_name=sbp_comp,
                            method="componentInit",
                            params=[None],
                            payment_urls=payment_urls,
                        )
                        sbp_raw = snapshots.get(sbp_comp, sbp_raw)
                        sbp_data = _unwrap_livewire(json.loads(sbp_raw).get("data", {}))

                    if (
                        not payment_urls
                        and not sbp_data.get("autopostData")
                        and not sbp_data.get("waitingForActivePayment")
                    ):
                        await cls._call_livewire(
                            client=client,
                            endpoint=endpoint,
                            page_url=page_url,
                            csrf=csrf,
                            snapshots=snapshots,
                            component_name=sbp_comp,
                            method="submit",
                            params=[],
                            payment_urls=payment_urls,
                        )
                        sbp_raw = snapshots.get(sbp_comp, sbp_raw)
                        sbp_data = _unwrap_livewire(json.loads(sbp_raw).get("data", {}))

                    autopost = sbp_data.get("autopostData") or {}
                    if (
                        not payment_urls
                        and isinstance(autopost, dict)
                        and autopost.get("url")
                    ):
                        target = urljoin(page_url, autopost["url"])
                        res_req = await client.get(
                            target,
                            params=autopost.get("params") or None,
                            headers={"Referer": page_url},
                        )
                        if 300 <= res_req.status_code < 400:
                            loc = res_req.headers.get("Location", "")
                            for u in _find_payment_urls(loc):
                                if u not in payment_urls:
                                    payment_urls.append(u)
                        elif res_req.status_code == 200:
                            for u in _inspect_html(res_req.text, snapshots):
                                if u not in payment_urls:
                                    payment_urls.append(u)

                if payment_urls:
                    return payment_urls[0]

                return None

        except Exception as exc:
            logger.warning(
                "cardlink_resolve_exception",
                url=url,
                error=str(exc),
            )
            return None
