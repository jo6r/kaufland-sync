"""Kaufland REST API client used by the Jiri Models stock updater."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

import requests


class KauflandAPIClient:
    """Client for Kaufland REST API."""

    def __init__(
        self,
        client_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        storefront: Optional[str] = None,
    ):
        self.client_key = client_key or os.getenv("KAUFLAND_CLIENT_KEY")
        self.secret_key = secret_key or os.getenv("KAUFLAND_SECRET_KEY")
        self.base_url = base_url or os.getenv("KAUFLAND_BASE_URL")
        self.storefront = storefront or os.getenv("KAUFLAND_STOREFRONT", "cz")

        if not self.client_key:
            raise ValueError("KAUFLAND_CLIENT_KEY must be provided or set as environment variable")
        if not self.secret_key:
            raise ValueError("KAUFLAND_SECRET_KEY must be provided or set as environment variable")
        if not self.base_url:
            raise ValueError("KAUFLAND_BASE_URL must be provided or set as environment variable")

    def _sign_request(self, method: str, uri: str, body: str, timestamp: int) -> str:
        string_to_sign = "\n".join([method.upper(), uri, body if body else "", str(timestamp)])
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _build_uri(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> str:
        uri = f"{self.base_url}{endpoint}"
        if params:
            query_parts = []
            for key, value in params.items():
                if value is not None:
                    query_parts.append(f"{key}={value}")
            if query_parts:
                uri += "?" + "&".join(query_parts)
        return uri

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        uri = self._build_uri(endpoint, params=params)
        timestamp = int(time.time())
        signature = self._sign_request("GET", uri, "", timestamp)
        headers = {
            "Accept": "application/json",
            "Shop-Client-Key": self.client_key,
            "Shop-Timestamp": str(timestamp),
            "Shop-Signature": signature,
            "User-Agent": "mamitocz_development",
        }
        return requests.get(url=uri, headers=headers)

    def post(self, endpoint: str, data: Any, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        uri = self._build_uri(endpoint, params=params)
        body = json.dumps(data) if data else ""
        timestamp = int(time.time())
        signature = self._sign_request("POST", uri, body, timestamp)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Shop-Client-Key": self.client_key,
            "Shop-Timestamp": str(timestamp),
            "Shop-Signature": signature,
            "User-Agent": "mamitocz_development",
        }
        return requests.post(url=uri, headers=headers, data=body)