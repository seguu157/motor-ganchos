"""Fuente Apify. El cliente existente ya clasifica el 402 como falta de crédito."""

from __future__ import annotations

from typing import Any

from radar.apify_client import ApifyClient


class ApifySource:
    name = "apify"

    def __init__(self, client: ApifyClient) -> None:
        self.client = client

    def fetch_reels(self, usernames: list[str], results_limit: int) -> list[dict[str, Any]]:
        return self.client.fetch_reels(usernames, results_limit)

    def close(self) -> None:
        self.client.close()
