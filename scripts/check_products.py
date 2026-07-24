#!/usr/bin/env python3
"""Check EAN and offer existence in Kaufland API and export results to CSV."""

from __future__ import annotations

import csv
import hashlib
import hmac
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from dotenv import load_dotenv


SCRIPTS_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPTS_DIR / ".env")

INPUT_CSV_PATH = SCRIPTS_DIR / "data" / "products.csv"
OUTPUT_CSV_PATH = SCRIPTS_DIR / "data" / "products_check.csv"
STOREFRONT = os.getenv("KAUFLAND_STOREFRONT", "cz")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = [
    "kaufland_ean_exists",
    "kaufland_offer_exists",
    "kaufland_product_id",
    "kaufland_id_unit",
    "kaufland_error",
]


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

        parts = []
        for key, value in params.items():
            if value is None:
                continue
            parts.append(f"{key}={value}")

        if parts:
            uri += "?" + "&".join(parts)

        return uri

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        uri = self._build_uri(endpoint, params=params)
        timestamp = int(time.time())
        headers = {
            "Accept": "application/json",
            "Shop-Client-Key": self.client_key,
            "Shop-Timestamp": str(timestamp),
            "Shop-Signature": self._sign_request("GET", uri, "", timestamp),
            "User-Agent": "kaufland-sync-ean-check",
        }
        return requests.get(uri, headers=headers, timeout=30)


def require_env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None or not value.strip():
        raise ValueError(f"Missing required environment variable: {name}")
    return value.strip()


def normalize_ean(raw: Optional[str]) -> str:
    if raw is None:
        return ""
    return str(raw).strip().strip('"')


def is_hidden_row(row: Dict[str, Any]) -> bool:
    visibility = row.get("productVisibility")
    if visibility is None:
        visibility = row.get("productvisibility")
    return str(visibility or "").strip().lower() == "hidden"


def check_ean_exists(client: KauflandAPIClient, ean: str) -> Tuple[bool, Optional[str], Optional[str]]:
    response = client.get(f"/v2/products/ean/{ean}", params={"storefront": client.storefront})

    if response.status_code == 404:
        logger.debug("EAN %s was not found in products endpoint", ean)
        return False, None, None

    if response.status_code != 200:
        logger.warning("Products endpoint returned HTTP %s for EAN %s", response.status_code, ean)
        return False, None, f"products_ean_http_{response.status_code}"

    payload = response.json()
    product_id = None

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and "id_product" in data:
            product_id = str(data["id_product"])
        elif "id_product" in payload:
            product_id = str(payload["id_product"])

    return True, product_id, None


def check_offer_exists(client: KauflandAPIClient, ean: str) -> Tuple[bool, Optional[str], Optional[str]]:
    response = client.get("/v2/units", params={"storefront": client.storefront, "ean": ean, "limit": 1})

    if response.status_code != 200:
        logger.warning("Units endpoint returned HTTP %s for EAN %s", response.status_code, ean)
        return False, None, f"units_http_{response.status_code}"

    payload = response.json()
    if not isinstance(payload, dict):
        return False, None, "units_invalid_payload"

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return False, None, None

    first = data[0]
    if not isinstance(first, dict):
        return True, None, "units_invalid_data_row"

    unit_id = first.get("id_unit")
    if unit_id is None:
        return True, None, "units_missing_id_unit"

    return True, str(unit_id), None


def resolve_ean_status(client: KauflandAPIClient, ean: str) -> Dict[str, Optional[str] | bool]:
    ean_exists, product_id, ean_error = check_ean_exists(client, ean)

    offer_exists = False
    unit_id = None
    offer_error = None

    if ean_exists:
        offer_exists, unit_id, offer_error = check_offer_exists(client, ean)

    return {
        "ean_exists": ean_exists,
        "offer_exists": offer_exists,
        "product_id": product_id,
        "unit_id": unit_id,
        "error": ean_error or offer_error,
    }


def enrich_row_with_result(row: Dict[str, Any], result: Dict[str, Optional[str] | bool]) -> Dict[str, Any]:
    row["kaufland_ean_exists"] = "1" if result["ean_exists"] else "0"
    row["kaufland_offer_exists"] = "1" if result["offer_exists"] else "0"
    row["kaufland_product_id"] = str(result["product_id"] or "")
    row["kaufland_id_unit"] = str(result["unit_id"] or "")
    row["kaufland_error"] = str(result["error"] or "")
    return row


def missing_ean_result() -> Dict[str, Optional[str] | bool]:
    return {
        "ean_exists": False,
        "offer_exists": False,
        "product_id": None,
        "unit_id": None,
        "error": "missing_ean",
    }


def process_csv(client: KauflandAPIClient, input_path: Path, output_path: Path) -> None:
    cache: Dict[str, Dict[str, Optional[str] | bool]] = {}
    total_rows = 0
    written_rows = 0
    hidden_rows = 0
    missing_ean_rows = 0
    cache_hits = 0
    cache_misses = 0

    logger.info("Processing CSV from %s to %s", input_path, output_path)

    with input_path.open("r", encoding="utf-8", newline="") as src_file:
        reader = csv.DictReader(src_file, delimiter=";")
        if reader.fieldnames is None:
            raise ValueError("Input CSV has no header")

        fieldnames = list(reader.fieldnames) + OUTPUT_COLUMNS

        with output_path.open("w", encoding="utf-8", newline="") as dst_file:
            writer = csv.DictWriter(dst_file, fieldnames=fieldnames, delimiter=";", quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()

            for row_number, row in enumerate(reader, start=2):
                total_rows += 1

                if is_hidden_row(row):
                    hidden_rows += 1
                    continue

                ean = normalize_ean(row.get("ean"))
                if not ean:
                    missing_ean_rows += 1
                    logger.warning("Row %s is missing EAN", row_number)
                    writer.writerow(enrich_row_with_result(row, missing_ean_result()))
                    written_rows += 1
                    continue

                if ean not in cache:
                    cache_misses += 1
                    cache[ean] = resolve_ean_status(client, ean)
                else:
                    cache_hits += 1

                writer.writerow(enrich_row_with_result(row, cache[ean]))
                written_rows += 1

    logger.info(
        "Finished CSV processing: total=%s written=%s hidden=%s missing_ean=%s unique_eans=%s cache_hits=%s",
        total_rows,
        written_rows,
        hidden_rows,
        missing_ean_rows,
        len(cache),
        cache_hits,
    )


def configure_logging() -> None:
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_client() -> KauflandAPIClient:
    return KauflandAPIClient(
        client_key=require_env("KAUFLAND_CLIENT_KEY"),
        secret_key=require_env("KAUFLAND_SECRET_KEY"),
        base_url=require_env("KAUFLAND_BASE_URL", "https://sellerapi.kaufland.com"),
        storefront=STOREFRONT,
    )


def main() -> None:
    configure_logging()
    client = build_client()
    OUTPUT_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    process_csv(client, INPUT_CSV_PATH, OUTPUT_CSV_PATH)
    logger.info("Done. Output written to: %s", OUTPUT_CSV_PATH)


if __name__ == "__main__":
    main()
