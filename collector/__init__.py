"""Shared collection utilities for Chennai Metro datasets."""

import json
from pathlib import Path
from typing import Any

import requests


def fetch_json(url: str, api_response: str | Path | None = None) -> Any:
    if api_response is not None:
        with open(api_response, encoding="utf-8") as file:
            bundle = json.load(file)
        if not isinstance(bundle, dict) or not isinstance(bundle.get("responses"), dict):
            raise ValueError("API response file must contain a 'responses' object keyed by URL")
        if url not in bundle["responses"]:
            raise ValueError(f"API response file has no response for {url}")
        return bundle["responses"][url]
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()
