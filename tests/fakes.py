"""FakeVisionClient — the test double for the LLMClient seam.

Implements the `LLMClient` Protocol from realview_chat.openai_client.responses
(pass1 / pass2 / pass25, each returning a dict) so the pipeline can be exercised
with zero network, zero API cost, and deterministic output. Responses are keyed
by the image data-URL the pipeline passes in.
"""
from __future__ import annotations

from typing import Any, Callable


class FakeVisionClient:
    def __init__(
        self,
        pass1: dict[str, dict[str, Any]],
        pass2: dict[str, dict[str, Any]] | None = None,
        pass25: dict[str, Any] | Callable[[str, list[str]], dict[str, Any]] | None = None,
    ) -> None:
        self._pass1 = pass1
        self._pass2 = pass2 or {}
        self._pass25 = pass25
        # call log so tests can assert what the pipeline actually invoked
        self.calls: dict[str, list] = {"pass1": [], "pass2": [], "pass25": []}

    def pass1(self, image_data_url: str) -> dict[str, Any]:
        self.calls["pass1"].append(image_data_url)
        return self._pass1[image_data_url]

    def pass2(self, image_data_url: str) -> dict[str, Any]:
        self.calls["pass2"].append(image_data_url)
        return self._pass2.get(
            image_data_url,
            {
                "features": [],
                "condition_score": None,
                "modernity_score": None,
                "material_score": None,
                "functionality_score": None,
            },
        )

    def pass25(self, room_type: str, image_data_urls: list[str]) -> dict[str, Any]:
        self.calls["pass25"].append((room_type, tuple(image_data_urls)))
        if callable(self._pass25):
            return self._pass25(room_type, image_data_urls)
        if self._pass25 is not None:
            return self._pass25
        return {
            "room_type": room_type,
            "confirmed_features": [],
            "room_condition_score": None,
            "room_modernity_score": None,
            "room_material_score": None,
            "room_functionality_score": None,
        }
