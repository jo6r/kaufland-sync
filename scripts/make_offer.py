#!/usr/bin/env python3
"""Create Kaufland offers for selected products from products_kaufland_check.csv."""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import requests
from dotenv import load_dotenv


SCRIPTS_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPTS_DIR / ".env")

INPUT_CSV_PATH = SCRIPTS_DIR / "data" / "products_kaufland_check.csv"
OUTPUT_CSV_PATH = SCRIPTS_DIR / "data" / "offers_created.csv"

STOREFRONT = os.getenv("KAUFLAND_STOREFRONT", "cz")
HANDLING_TIME = 3
CONDITION = "NEW"
LISTING_PRICE = 0


class KauflandAPIClient:
    def __init__(
        self,
        client_key: str,
        secret_key: str,
        base_url: str,
        storefront: str,
    ) -> None:
        self.client_key = client_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.storefront = storefront

    def _sign_request(self, method: str, uri: str, body: str, timestamp: int) -> str:
        to_sign = "\n".join([method.upper(), uri, body if body else "", str(timestamp)])
        return hmac.new(
            self.secret_key.encode("utf-8"),
            to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _build_uri(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> str:
        uri = f"{self.base_url}{endpoint}"
        if not params:
            return uri

        query_parts = []
        for key, value in params.items():
            if value is None:
                continue
            query_parts.append(f"{key}={value}")

        if query_parts:
            uri += "?" + "&".join(query_parts)

        return uri

    def post(self, endpoint: str, data: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> requests.Response:
        uri = self._build_uri(endpoint, params=params)
        body = json.dumps(data, separators=(",", ":"))
        timestamp = int(time.time())
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Shop-Client-Key": self.client_key,
            "Shop-Timestamp": str(timestamp),
            "Shop-Signature": self._sign_request("POST", uri, body, timestamp),
            "User-Agent": "kaufland-sync-make-offer",
        }
        return requests.post(uri, headers=headers, data=body, timeout=30)


def require_env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None or not value.strip():
        raise ValueError(f"Missing required environment variable: {name}")
    return value.strip()


def is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def normalize_ean(raw: Any) -> str:
    return str(raw or "").strip().strip('"')


def sanitize_offer_part(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")


def generate_id_offer(row: Dict[str, Any]) -> str:
    code = str(row.get("code") or "").strip()
    ean = normalize_ean(row.get("ean"))

    base = sanitize_offer_part(code) or "offer"
    return f"{base}-{ean}"


def should_create_offer(row: Dict[str, Any]) -> bool:
    visibility = str(row.get("productVisibility") or "").strip().lower()
    ean_exists = is_true(row.get("kaufland_ean_exists"))
    offer_exists = is_true(row.get("kaufland_offer_exists"))
    return visibility == "visible" and ean_exists and not offer_exists


def iter_source_rows(input_path: Path) -> Iterable[Dict[str, Any]]:
    with input_path.open("r", encoding="utf-8", newline="") as src_file:
        reader = csv.DictReader(src_file, delimiter=";")
        if reader.fieldnames is None:
            raise ValueError("Input CSV has no header")
        for row in reader:
            yield row


def create_offer(client: KauflandAPIClient, ean: str, id_offer: str) -> bool:
    payload = {
        "ean": ean,
        "id_offer": id_offer,
        "handling_time": HANDLING_TIME,
        "condition": CONDITION,
        "listing_price": LISTING_PRICE,
    }

    response = client.post(
        endpoint="/v2/units",
        data=payload,
        params={"storefront": client.storefront},
    )

    if response.status_code == 200:
        return True

    details = response.text.strip()
    print(f"[WARN] Create offer failed for ean={ean}, id_offer={id_offer}, status={response.status_code}, body={details}")
    return False


def write_created_rows(output_path: Path, created: list[Dict[str, str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as dst_file:
        writer = csv.DictWriter(dst_file, fieldnames=["id_offer", "ean"], delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in created:
            writer.writerow(row)


def build_client() -> KauflandAPIClient:
    return KauflandAPIClient(
        client_key=require_env("KAUFLAND_CLIENT_KEY"),
        secret_key=require_env("KAUFLAND_SECRET_KEY"),
        base_url=require_env("KAUFLAND_BASE_URL", "https://sellerapi.kaufland.com"),
        storefront=STOREFRONT,
    )


def main() -> None:
    client = build_client()
    created: list[Dict[str, str]] = []

    total = 0
    selected = 0

    for row in iter_source_rows(INPUT_CSV_PATH):
        total += 1
        if not should_create_offer(row):
            continue

        ean = normalize_ean(row.get("ean"))
        if not ean:
            continue

        selected += 1
        id_offer = generate_id_offer(row)

        if create_offer(client, ean, id_offer):
            created.append({"id_offer": id_offer, "ean": ean})

    write_created_rows(OUTPUT_CSV_PATH, created)

    print(f"Processed rows: {total}")
    print(f"Selected rows for create: {selected}")
    print(f"Successfully created offers: {len(created)}")
    print(f"Output written to: {OUTPUT_CSV_PATH}")
    print("Note: listing_price is set to 0 as requested; Kaufland API may reject such offers.")


if __name__ == "__main__":
    main()
