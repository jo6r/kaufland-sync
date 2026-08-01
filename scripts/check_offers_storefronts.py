#!/usr/bin/env python3
"""Export all CZ Kaufland offers and check their existence in other storefronts."""

from __future__ import annotations

import csv
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

import requests
from dotenv import load_dotenv

SCRIPTS_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPTS_DIR / ".env")

OUTPUT_CSV_PATH = SCRIPTS_DIR / "data" / "offers_storefronts_check.csv"
SOURCE_STOREFRONT = "cz"
TARGET_STOREFRONTS = ["de", "sk", "pl", "es", "fr", "nl", "at", "it"]
UNITS_PAGE_LIMIT = 100
LOG_LEVEL = "INFO"

logger = logging.getLogger(__name__)


class KauflandAPIClient:
    def __init__(self, client_key: str, secret_key: str, base_url: str) -> None:
        self.client_key = client_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")

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

        query_parts = [f"{k}={v}" for k, v in params.items() if v is not None]
        if query_parts:
            uri += "?" + "&".join(query_parts)

        return uri

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        uri = self._build_uri(endpoint, params=params)
        timestamp = int(time.time())
        headers = {
            "Accept": "application/json",
            "Shop-Client-Key": self.client_key,
            "Shop-Timestamp": str(timestamp),
            "Shop-Signature": self._sign_request("GET", uri, "", timestamp),
            "User-Agent": "kaufland-sync-storefront-offer-check",
        }
        return requests.get(uri, headers=headers, timeout=30)


def require_env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if not value or not value.strip():
        raise ValueError(f"Missing required environment variable: {name}")
    return value.strip()


def normalize_ean(raw: Optional[str]) -> str:
    return str(raw).strip().strip('"') if raw else ""


def check_offer_exists_in_storefront(
    client: KauflandAPIClient,
    id_product: int,
    storefront: str,
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    response = client.get(
        "/units",
        params={
            "storefront": storefront,
            "id_product": id_product,
            "fulfillment_type": "fulfilled_by_merchant",
            "limit": 30,
            "offset": 0,
        },
    )

    if response.status_code != 200:
        logger.warning(
            "Units endpoint returned HTTP %s for id_product %s in storefront %s",
            response.status_code, id_product, storefront,
        )
        return False, None, None, f"units_http_{response.status_code}"

    payload = response.json()
    if not isinstance(payload, dict):
        return False, None, None, "units_invalid_payload"

    data = payload.get("data")
    if not isinstance(data, list):
        return False, None, None, "units_missing_data"
        
    if not data:
        return False, None, None, "units_not_found"

    first_unit = data[0]
    unit_id = first_unit.get("id_unit")
    price = first_unit.get("price")
    
    if unit_id is None:
        return True, None, str(price) if price is not None else None, "units_missing_id_unit"

    return True, str(unit_id), str(price) if price is not None else None, None


def fetch_units_page(client: KauflandAPIClient, storefront: str, offset: int, limit: int) -> Tuple[list[Dict[str, Any]], int, Optional[str]]:
    response = client.get("/units", params={"storefront": storefront, "offset": offset, "limit": limit})
    if response.status_code != 200:
        return [], 0, f"units_list_http_{response.status_code}"

    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not isinstance(payload.get("pagination"), dict):
        return [], 0, "units_list_invalid_payload"

    return payload["data"], int(payload["pagination"].get("total") or 0), None


def iter_all_units(client: KauflandAPIClient, storefront: str) -> Iterator[Tuple[Dict[str, Any], int]]:
    offset, total = 0, None

    while total is None or offset < total:
        page, page_total, error = fetch_units_page(client, storefront, offset, UNITS_PAGE_LIMIT)
        if error:
            raise RuntimeError(f"Failed to fetch units page at offset={offset}: {error}")

        if total is None:
            total = page_total
            logger.info("Storefront %s has %s offers", storefront, total)

        if not page:
            break

        for unit in page:
            yield unit, total

        offset += len(page)
        logger.info("Fetched %s/%s offers from storefront %s", offset, total, storefront)


def fetch_product_ean(client: KauflandAPIClient, product_id: int, storefront: str) -> Tuple[str, Optional[str]]:
    response = client.get(f"/products/{product_id}", params={"storefront": storefront})
    if response.status_code != 200:
        return "", f"product_http_{response.status_code}"

    payload = response.json()
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    eans = data.get("eans") if isinstance(data, dict) else None

    if not isinstance(eans, list) or not eans:
        return "", "product_missing_ean"

    ean = normalize_ean(str(eans[0]))
    return (ean, None) if ean else ("", "product_missing_ean")


def get_csv_fieldnames(target_storefronts: list[str]) -> list[str]:
    fields = [
        "source_storefront", "source_id_unit", "source_id_product", "source_id_offer",
        "source_status", "source_amount", "source_price", "source_currency",
        "source_date_inserted_iso", "source_date_lastchange_iso", "ean", "ean_error",
    ]
    for sf in target_storefronts:
        fields.extend([f"kaufland_offer_exists_{sf}", f"kaufland_id_unit_{sf}", f"kaufland_price_{sf}", f"kaufland_error_{sf}"])
    
    return fields


@dataclass
class ExportStats:
    total_rows: int = 0
    written_rows: int = 0
    missing_product_id: int = 0
    missing_ean: int = 0
    cache_hits: int = 0
    cache_misses: int = 0


def populate_missing_targets(row: dict, target_storefronts: list[str], error_reason: str) -> dict:
    for sf in target_storefronts:
        row.update({
            f"kaufland_offer_exists_{sf}": "0",
            f"kaufland_id_unit_{sf}": "",
            f"kaufland_price_{sf}": "",
            f"kaufland_error_{sf}": error_reason,
        })
    return row


def process_unit_row(
    client: KauflandAPIClient, unit: dict, source_storefront: str, target_storefronts: list[str],
    ean_cache: dict, offer_cache: dict, stats: ExportStats,
) -> dict:
    product_id_raw = unit.get("id_product")
    try:
        product_id = int(str(product_id_raw))
    except (TypeError, ValueError):
        product_id = -1

    if product_id < 0:
        stats.missing_product_id += 1
        ean, ean_error = "", "missing_product_id"
    else:
        if product_id not in ean_cache:
            ean_cache[product_id] = fetch_product_ean(client, product_id, source_storefront)
        ean, ean_error_opt = ean_cache[product_id]
        ean_error = str(ean_error_opt or "")

    id_unit = str(unit.get("id_unit") or "")
    
    row = {
        "source_storefront": source_storefront,
        "source_id_unit": id_unit,
        "source_id_product": str(product_id_raw or ""),
        "source_id_offer": str(unit.get("id_offer") or ""),
        "source_status": str(unit.get("status") or ""),
        "source_amount": str(unit.get("amount") or ""),
        "source_price": str(unit.get("price") or ""),
        "source_currency": str(unit.get("currency") or ""),
        "source_date_inserted_iso": str(unit.get("date_inserted_iso") or ""),
        "source_date_lastchange_iso": str(unit.get("date_lastchange_iso") or ""),
        "ean": ean,
        "ean_error": ean_error,
    }

    if not ean:
        stats.missing_ean += 1
        return populate_missing_targets(row, target_storefronts, "missing_ean")

    if product_id < 0:
        return populate_missing_targets(row, target_storefronts, "missing_product_id")

    if product_id not in offer_cache:
        stats.cache_misses += 1
        offer_cache[product_id] = {
            sf: check_offer_exists_in_storefront(client, product_id, sf) for sf in target_storefronts
        }
    else:
        stats.cache_hits += 1

    for sf in target_storefronts:
        exists, target_unit_id, price, error = offer_cache[product_id][sf]

        row.update({
            f"kaufland_offer_exists_{sf}": "1" if exists else "0",
            f"kaufland_id_unit_{sf}": str(target_unit_id or ""),
            f"kaufland_price_{sf}": str(price or ""),
            f"kaufland_error_{sf}": str(error or ""),
        })

    return row


def export_offers_storefronts_check(
    client: KauflandAPIClient, output_path: Path, source_storefront: str, target_storefronts: list[str],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Exporting storefront offer check from %s to %s", source_storefront, output_path)

    stats = ExportStats()
    ean_cache: dict[int, Tuple[str, Optional[str]]] = {}
    offer_cache: dict[int, dict[str, Tuple[bool, Optional[str], Optional[str], Optional[str]]]] = {}

    with output_path.open("w", encoding="utf-8", newline="") as dst_file:
        writer = csv.DictWriter(dst_file, fieldnames=get_csv_fieldnames(target_storefronts), delimiter=",", quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()

        for index, (unit, total) in enumerate(iter_all_units(client, source_storefront), start=1):
            if stats.total_rows == 0:
                stats.total_rows = total

            row = process_unit_row(client, unit, source_storefront, target_storefronts, ean_cache, offer_cache, stats)
            
            if row["ean"]:
                logger.info("Processing %s/%s: EAN=%s", index, stats.total_rows, row["ean"])
            else:
                logger.info("Processing %s/%s: EAN missing (id_product=%s, ean_error=%s)", index, stats.total_rows, row["source_id_product"], row["ean_error"])

            writer.writerow(row)
            stats.written_rows += 1
            if index % 10 == 0:
                dst_file.flush()

    logger.info(
        "Finished export: total=%s written=%s missing_product_id=%s missing_ean=%s unique_eans=%s cache_hits=%s cache_misses=%s",
        stats.total_rows, stats.written_rows, stats.missing_product_id, stats.missing_ean, len(offer_cache), stats.cache_hits, stats.cache_misses,
    )


def configure_logging() -> None:
    logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_client() -> KauflandAPIClient:
    return KauflandAPIClient(
        client_key=require_env("KAUFLAND_CLIENT_KEY"),
        secret_key=require_env("KAUFLAND_SECRET_KEY"),
        base_url=require_env("KAUFLAND_BASE_URL", "https://sellerapi.kaufland.com/v2"),
    )


def main() -> None:
    configure_logging()

    targets = [s for s in TARGET_STOREFRONTS if s != SOURCE_STOREFRONT]
    if not targets:
        raise ValueError("No target storefronts to check after removing source storefront")

    export_offers_storefronts_check(build_client(), OUTPUT_CSV_PATH, SOURCE_STOREFRONT, targets)
    logger.info("Done. Output written to: %s", OUTPUT_CSV_PATH)


if __name__ == "__main__":
    main()
