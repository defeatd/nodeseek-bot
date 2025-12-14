from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass

import httpx

from nodeseek_bot.storage.types import SummaryResult
from nodeseek_bot.utils import truncate


logger = logging.getLogger(__name__)


PROMPT_VERSION = "v2-short-zh-longtext"


@dataclass(frozen=True)
class AIConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int
    max_retries: int
    prefer_chat_completions: bool
    fallback_to_responses: bool
    max_input_chars: int
    chunk_chars: int
    chunk_overlap_chars: int


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # best-effort: extract the first JSON object in the response
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _normalize_result(model: str, payload: dict, token_in: int | None, token_out: int | None) -> SummaryResult:
    summary = str(payload.get("summary") or "").strip()
    key_points = payload.get("key_points") or payload.get("points") or []
    actions = payload.get("actions") or payload.get("todos") or []

    if isinstance(key_points, str):
        key_points = [x.strip() for x in key_points.split("\n") if x.strip()]
    if isinstance(actions, str):
        actions = [x.strip() for x in actions.split("\n") if x.strip()]

    key_points = [str(x).strip() for x in key_points if str(x).strip()]
    actions = [str(x).strip() for x in actions if str(x).strip()]

    return SummaryResult(
        model=model,
        prompt_version=PROMPT_VERSION,
        summary_text=summary,
        key_points=key_points[:6],
        actions=actions[:4],
        token_in=token_in,
        token_out=token_out,
    )


class OpenAICompatClient:
    def __init__(self, cfg: AIConfig):
        self._cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url.rstrip("/"),
            timeout=httpx.Timeout(cfg.timeout_seconds),
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict) -> dict:
        # Retry on transient network errors and 429/5xx
        attempts = max(0, int(self._cfg.max_retries)) + 1
        last_exc: Exception | None = None

        for i in range(attempts):
            try:
                resp = await self._client.post(path, json=payload)
                if resp.status_code in {429} or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"retryable status: {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.ReadTimeout, httpx.HTTPStatusError) as e:
                last_exc = e
                if i >= attempts - 1:
                    raise
                # exponential backoff + jitter
                base = min(20.0, (2.0**i))
                await asyncio.sleep(base + random.uniform(0.0, 1.0))

        raise last_exc or RuntimeError("ai request failed")

    async def summarize(self, title: str, url: str, text: str) -> SummaryResult:
        if not self._cfg.base_url or not self._cfg.api_key or not self._cfg.model:
            payload = {
                "summary": truncate(text, 220),
                "key_points": [],
                "actions": [],
            }
            return _normalize_result(model="", payload=payload, token_in=None, token_out=None)

        system = (
            "你是一个中文信息提炼助手。请阅读输入的帖子内容，输出严格的 JSON 对象，不要输出任何额外文本。\n"
            "JSON 字段：\n"
            "- summary: 1-3 句的超短总结（更短风格）\n"
            "- key_points: 最多 6 条要点（每条尽量短）\n"
            "- actions: 最多 4 条可操作建议/结论（没有就空数组）\n"
            "要求：尽量保留具体信息（价格/期限/关键步骤/结论/风险/可操作建议）。"
        )

        # Long-text handling: keep as much as possible, only chunk when necessary.
        max_chars = max(1000, int(self._cfg.max_input_chars or 0))
        if len(text) > max_chars:
            return await self._summarize_long_text(system, title, url, text)

        user = f"标题：{title}\n链接：{url}\n内容：\n{text}"
        return await self._summarize_once(system, user)

    async def _summarize_once(self, system: str, user: str) -> SummaryResult:
        if self._cfg.prefer_chat_completions:
            try:
                return await self._summarize_chat(system, user)
            except Exception:
                if not self._cfg.fallback_to_responses:
                    raise
                return await self._summarize_responses(system, user)

        return await self._summarize_responses(system, user)

    def _chunk_text(self, text: str) -> list[str]:
        chunk_chars = max(2000, int(self._cfg.chunk_chars or 0))
        overlap = max(0, min(chunk_chars - 1, int(self._cfg.chunk_overlap_chars or 0)))

        chunks: list[str] = []
        start = 0
        n = len(text)
        while start < n:
            end = min(n, start + chunk_chars)
            chunks.append(text[start:end])
            if end >= n:
                break
            start = max(0, end - overlap)
        return chunks

    async def _summarize_long_text(self, system: str, title: str, url: str, text: str) -> SummaryResult:
        chunks = self._chunk_text(text)
        logger.info("ai long text: %s chars split into %s chunks", len(text), len(chunks))

        partials: list[SummaryResult] = []
        for idx, chunk in enumerate(chunks, start=1):
            chunk_system = (
                system
                + "\n你将收到长文的一部分。请只总结这一部分的关键信息，保留数字/期限/步骤/风险。"
            )
            user = (
                f"标题：{title}\n链接：{url}\n"
                f"片段：{idx}/{len(chunks)}\n"
                f"内容：\n{chunk}"
            )
            partials.append(await self._summarize_once(chunk_system, user))

        merged_lines: list[str] = []
        for i, p in enumerate(partials, start=1):
            merged_lines.append(f"[片段{i}] {p.summary_text}")
            if p.key_points:
                merged_lines.append("要点：" + " | ".join(p.key_points))
            if p.actions:
                merged_lines.append("可操作：" + " | ".join(p.actions))

        merge_user = (
            f"标题：{title}\n链接：{url}\n"
            "以下是各片段的提炼结果，请你合并成最终结论，去重、保留具体信息，输出同样的 JSON。\n"
            + "\n".join(merged_lines)
        )
        return await self._summarize_once(system, merge_user)

    async def _summarize_chat(self, system: str, user: str) -> SummaryResult:
        payload = {
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 700,
        }

        # Some providers support response_format, but not all.
        try:
            payload_with_format = dict(payload)
            payload_with_format["response_format"] = {"type": "json_object"}
            data = await self._post("/v1/chat/completions", payload_with_format)
        except httpx.HTTPStatusError:
            data = await self._post("/v1/chat/completions", payload)

        content = (
            (((data.get("choices") or [{}])[0]).get("message") or {}).get("content")
            or (((data.get("choices") or [{}])[0]).get("delta") or {}).get("content")
            or ""
        )

        token_in = None
        token_out = None
        usage = data.get("usage") or {}
        if isinstance(usage, dict):
            token_in = usage.get("prompt_tokens")
            token_out = usage.get("completion_tokens")

        payload_obj = _extract_json(content) or {"summary": content.strip(), "key_points": [], "actions": []}
        return _normalize_result(self._cfg.model, payload_obj, token_in, token_out)

    async def _summarize_responses(self, system: str, user: str) -> SummaryResult:
        payload = {
            "model": self._cfg.model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_output_tokens": 900,
        }

        data = await self._post("/v1/responses", payload)

        # Try common fields
        content = data.get("output_text") or ""
        if not content and isinstance(data.get("output"), list):
            for item in data["output"]:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "message" and isinstance(item.get("content"), list):
                    parts = []
                    for part in item["content"]:
                        if isinstance(part, dict) and part.get("type") == "output_text":
                            parts.append(part.get("text") or "")
                    content = "\n".join(parts).strip()
                    if content:
                        break

        token_in = None
        token_out = None
        usage = data.get("usage") or {}
        if isinstance(usage, dict):
            token_in = usage.get("input_tokens") or usage.get("prompt_tokens")
            token_out = usage.get("output_tokens") or usage.get("completion_tokens")

        payload_obj = _extract_json(content) or {"summary": content.strip(), "key_points": [], "actions": []}
        return _normalize_result(self._cfg.model, payload_obj, token_in, token_out)
