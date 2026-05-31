from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from openai import OpenAI

from realview_chat.config import AppConfig
from realview_chat.utils.rate_limit import RateLimiter
from realview_chat.utils.retry import with_retry

from . import prompts, schemas


class LLMClient(Protocol):
    def pass1(self, image_data_url: str) -> dict[str, Any]: ...
    def pass2(self, image_data_url: str) -> dict[str, Any]: ...
    def pass25(self, room_type: str, image_data_urls: list[str]) -> dict[str, Any]: ...


class OpenAIBackend:
    def __init__(self, config: AppConfig, rate_limiter: RateLimiter) -> None:
        self._client = OpenAI(api_key=config.openai_api_key)
        self._model = config.openai_model
        self._rate_limiter = rate_limiter
        self._max_retries = config.max_retries
        self._retry_backoff_seconds = config.retry_backoff_seconds
        self._logger = logging.getLogger(self.__class__.__name__)

    def _call(self, *, system_prompt: str, schema: dict, input_items: list[dict]) -> dict:
        def execute() -> dict:
            self._rate_limiter.wait()
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *input_items
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": schema,
                },
            )
            choice = response.choices[0]
            output_text = choice.message.content
            if not output_text:
                raise ValueError("Empty response output")
            return json.loads(output_text)

        return with_retry(
            execute,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
            logger=self._logger,
        )

    def pass1(self, image_data_url: str) -> dict[str, Any]:
        input_items = [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_data_url}}]},
        ]
        return self._call(
            system_prompt=prompts.PASS1_SYSTEM,
            schema=schemas.pass1_schema(),
            input_items=input_items,
        )

    def pass2(self, image_data_url: str) -> dict[str, Any]:
        whitelist = ", ".join(schemas.FEATURE_WHITELIST)
        system_prompt = f"{prompts.PASS2_SYSTEM}\nAllowed feature IDs: {whitelist}"
        input_items = [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_data_url}}]},
        ]
        return self._call(
            system_prompt=system_prompt,
            schema=schemas.pass2_schema(),
            input_items=input_items,
        )

    def pass25(self, room_type: str, image_data_urls: list[str]) -> dict[str, Any]:
        content = [{"type": "text", "text": f"Room type to consolidate: {room_type}"}]
        content.extend(
            {"type": "image_url", "image_url": {"url": url}} for url in image_data_urls
        )
        input_items = [{"role": "user", "content": content}]
        return self._call(
            system_prompt=prompts.PASS25_SYSTEM,
            schema=schemas.pass25_schema(),
            input_items=input_items,
        )


def create_client(config: AppConfig) -> LLMClient:
    limiter = RateLimiter(config.requests_per_minute)
    return OpenAIBackend(config, limiter)
