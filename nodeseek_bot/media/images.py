from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import mimetypes
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


logger = logging.getLogger(__name__)


_DISALLOWED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
}


@dataclass(frozen=True)
class ImageData:
    url: str
    mime_type: str
    data_url: str
    size_bytes: int


def _is_ip_literal(hostname: str | None) -> bool:
    if not hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
        return True
    except Exception:
        return False


def _is_private_ip(ip_str: str | None) -> bool:
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except Exception:
        return False

    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _should_send_cookie(hostname: str | None, host_suffixes: list[str]) -> bool:
    if not hostname:
        return False
    host = hostname.strip(".").lower()
    if not host:
        return False

    for suffix in host_suffixes:
        s = (suffix or "").strip(".").lower()
        if not s:
            continue
        if host == s or host.endswith("." + s):
            return True
    return False


def _guess_mime_type(url: str, content_type: str | None) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct.startswith("image/"):
        return ct

    guess, _ = mimetypes.guess_type(url)
    if guess and guess.startswith("image/"):
        return guess

    # Fallback: let the provider decide; still encode as octet-stream.
    return "application/octet-stream"


def _to_data_url(mime_type: str, content: bytes) -> str:
    b64 = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


def _resolve_to_ips(hostname: str) -> list[str]:
    """Resolve hostname to IPs (best-effort).

    We use this to prevent obvious SSRF to private networks for hostname-based URLs.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return []

    ips: list[str] = []
    for family, _, _, _, sockaddr in infos:
        if family == socket.AF_INET:
            ip = sockaddr[0]
        elif family == socket.AF_INET6:
            ip = sockaddr[0]
        else:
            continue
        if ip and ip not in ips:
            ips.append(ip)
    return ips


async def download_images_as_data_urls(
    urls: list[str],
    *,
    cookie_header: str,
    user_agent: str,
    timeout_seconds: int,
    max_count: int,
    max_bytes_per_image: int,
    max_total_bytes: int,
    concurrency: int,
    cookie_host_suffixes: list[str],
) -> list[ImageData]:
    """Download images and convert to data URLs (base64).

    Security / stability:
    - Only http/https.
    - Blocks localhost and IP-literal private/reserved ranges.
    - Blocks hostname that resolves to private/reserved IPs (best-effort, via DNS).
    - Enforces per-image and total byte limits.
    - Sends Cookie only to whitelisted host suffixes.
    """

    cleaned: list[str] = []
    seen: set[str] = set()
    for u in urls or []:
        u = (u or "").strip()
        if not u:
            continue
        if u in seen:
            continue
        seen.add(u)
        cleaned.append(u)
        if len(cleaned) >= int(max_count):
            break

    if not cleaned:
        return []

    timeout = httpx.Timeout(timeout_seconds)
    limits = httpx.Limits(max_connections=max(1, int(concurrency)), max_keepalive_connections=10)

    sem = asyncio.Semaphore(max(1, int(concurrency)))
    total_bytes = 0
    out: list[ImageData] = []

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, limits=limits) as client:
        async def fetch_one(url: str) -> None:
            nonlocal total_bytes

            parsed = urlparse(url)
            scheme = (parsed.scheme or "").lower()
            if scheme not in {"http", "https"}:
                return

            hostname = (parsed.hostname or "").lower()
            if not hostname:
                return
            if hostname in _DISALLOWED_HOSTS:
                return

            if _is_ip_literal(hostname):
                if _is_private_ip(hostname):
                    return
            else:
                # Best-effort DNS defense
                for ip in _resolve_to_ips(hostname):
                    if _is_private_ip(ip):
                        return

            headers = {"User-Agent": user_agent}
            if cookie_header and _should_send_cookie(hostname, cookie_host_suffixes):
                headers["Cookie"] = cookie_header

            async with sem:
                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code >= 400:
                        return

                    mime_type = _guess_mime_type(url, resp.headers.get("Content-Type"))

                    # Stream to enforce byte limits.
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue

                        if len(buf) + len(chunk) > int(max_bytes_per_image):
                            return
                        if total_bytes + len(buf) + len(chunk) > int(max_total_bytes):
                            return

                        buf.extend(chunk)

                    content = bytes(buf)
                    if not content:
                        return

                    # Update total after successful download
                    total_bytes += len(content)

                    out.append(
                        ImageData(
                            url=url,
                            mime_type=mime_type,
                            data_url=_to_data_url(mime_type, content),
                            size_bytes=len(content),
                        )
                    )
                except Exception as e:
                    logger.debug("image download failed url=%s err=%s", url, e)
                    return

        await asyncio.gather(*(fetch_one(u) for u in cleaned))

    return out
