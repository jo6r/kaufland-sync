#!/usr/bin/env python3
"""Export all CZ Kaufland offers and check their existence in other storefronts."""

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

OUTPUT_CSV_PATH = SCRIPTS_DIR / "data" / "offers_storefronts_check.csv"
SOURCE_STOREFRONT = "cz"
TARGET_STOREFRONTS = ["de", "sk", "pl", "es", "fr", "nl", "at", "it"]
UNITS_PAGE_LIMIT = 100
FLUSH_EVERY_ROWS = max(1, int(os.getenv("OUTPUT_FLUSH_EVERY_ROWS", "1")))
ENABLE_FSYNC = os.getenv("OUTPUT_ENABLE_FSYNC", "0").strip().lower() in {"1", "true", "yes"}
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

        query_parts = []
        for key, value in params.items():
            if value is None:
                continue
            query_parts.append(f"{key}={value}")

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
    if value is None or not value.strip():
        raise ValueError(f"Missing required environment variable: {name}")
    return value.strip()


def normalize_ean(raw: Optional[str]) -> str:
    if raw is None:
        return ""
    return str(raw).strip().strip('"')



def check_offer_exists_in_storefront(
    client: KauflandAPIClient,
    ean: str,
    storefront: str,
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    response = client.get("/units", params={"storefront": storefront, "ean": ean, "limit": 1})

    if response.status_code != 200:
        logger.warning(
            "Units endpoint returned HTTP %s for EAN %s in storefront %s",
            response.status_code,
            ean,
            storefront,
        )
        return False, None, None, f"units_http_{response.status_code}"

    payload = response.json()
    if not isinstance(payload, dict):
        return False, None, None, "units_invalid_payload"

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return False, None, None, None

    first = data[0]
    if not isinstance(first, dict):
        return True, None, None, "units_invalid_data_row"

    unit_id = first.get("id_unit")
    price = first.get("price")
    price_value = str(price) if price is not None else None

    if unit_id is None:
        return True, None, price_value, "units_missing_id_unit"

    return True, str(unit_id), price_value, None


def fetch_units_page(
    client: KauflandAPIClient,
    storefront: str,
    offset: int,
    limit: int,
) -> Tuple[list[Dict[str, Any]], int, Optional[str]]:
    response = client.get(
        "/units",
        params={"storefront": storefront, "offset": offset, "limit": limit},
    )

    if response.status_code != 200:
        return [], 0, f"units_list_http_{response.status_code}"

    payload = response.json()
    if not isinstance(payload, dict):
        return [], 0, "units_list_invalid_payload"

    data = payload.get("data")
    pagination = payload.get("pagination")
    if not isinstance(data, list):
        return [], 0, "units_list_invalid_data"
    if not isinstance(pagination, dict):
        return data, 0, "units_list_missing_pagination"

    total = int(pagination.get("total") or 0)
    return data, total, None


def fetch_all_units(client: KauflandAPIClient, storefront: str) -> list[Dict[str, Any]]:
    offset = 0
    total = None
    result: list[Dict[str, Any]] = []

    while total is None or offset < total:
        page, page_total, error = fetch_units_page(
            client=client,
            storefront=storefront,
            offset=offset,
            limit=UNITS_PAGE_LIMIT,
        )
        if error:
            raise RuntimeError(f"Failed to fetch units page at offset={offset}: {error}")

        if total is None:
            total = page_total
            logger.info("Storefront %s has %s offers", storefront, total)

        if not page:
            break

        result.extend(page)
        offset += len(page)
        logger.info("Fetched %s/%s offers from storefront %s", len(result), total, storefront)

    return result


def fetch_product_ean(client: KauflandAPIClient, product_id: int, storefront: str) -> Tuple[str, Optional[str]]:
    response = client.get(f"/products/{product_id}", params={"storefront": storefront})

    if response.status_code != 200:
        return "", f"product_http_{response.status_code}"

    payload = response.json()
    if not isinstance(payload, dict):
        return "", "product_invalid_payload"

    data = payload.get("data")
    if not isinstance(data, dict):
        return "", "product_invalid_data"

    eans = data.get("eans")
    if not isinstance(eans, list) or not eans:
        return "", "product_missing_ean"

    ean = normalize_ean(str(eans[0]))
    if not ean:
        return "", "product_missing_ean"

    return ean, None


def export_offers_storefronts_check(
    client: KauflandAPIClient,
    output_path: Path,
    source_storefront: str,
    target_storefronts: list[str],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    offer_cache: Dict[str, Dict[str, Tuple[bool, Optional[str], Optional[str], Optional[str]]]] = {}
    ean_by_product_cache: Dict[int, Tuple[str, Optional[str]]] = {}

    source_units = fetch_all_units(client, source_storefront)

    total_rows = len(source_units)
    written_rows = 0
    missing_product_id_rows = 0
    missing_ean_rows = 0
    cache_hits = 0
    cache_misses = 0

    logger.info(
        "Exporting storefront offer check from source storefront %s to %s",
        source_storefront,
        output_path,
    )

    fieldnames = [
        "source_storefront",
        "source_id_unit",
        "source_id_product",
        "source_id_offer",
        "source_status",
        "source_amount",
        "source_price",
        "source_currency",
        "source_date_inserted_iso",
        "source_date_lastchange_iso",
        "ean",
        "ean_error",
    ]
    for storefront in target_storefronts:
        fieldnames.extend(
            [
                f"kaufland_offer_exists_{storefront}",
                f"kaufland_id_unit_{storefront}",
                f"kaufland_price_{storefront}",
                f"kaufland_error_{storefront}",
            ]
        )
    fieldnames.extend(
        [
            "kaufland_offer_exists_in_any_target",
            "kaufland_offer_exists_in_all_targets",
            "kaufland_missing_target_storefronts",
            "kaufland_checked_target_storefronts",
        ]
    )

    with output_path.open("w", encoding="utf-8", newline="") as dst_file:
        writer = csv.DictWriter(dst_file, fieldnames=fieldnames, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        dst_file.flush()
        if ENABLE_FSYNC:
            os.fsync(dst_file.fileno())

        for index, unit in enumerate(source_units, start=1):
            product_id_raw = unit.get("id_product")
            try:
                product_id = int(str(product_id_raw))
            except (TypeError, ValueError):
                product_id = -1

            ean = ""
            ean_error = ""
            if product_id < 0:
                missing_product_id_rows += 1
                ean_error = "missing_product_id"
            else:
                if product_id not in ean_by_product_cache:
                    ean_by_product_cache[product_id] = fetch_product_ean(client, product_id, source_storefront)
                ean, ean_error_opt = ean_by_product_cache[product_id]
                ean_error = str(ean_error_opt or "")

            if ean:
                logger.info("Processing %s/%s: EAN=%s", index, total_rows, ean)
            else:
                logger.info(
                    "Processing %s/%s: EAN missing (id_product=%s, ean_error=%s)",
                    index,
                    total_rows,
                    str(product_id_raw or ""),
                    ean_error,
                )

            row: Dict[str, Any] = {
                "source_storefront": source_storefront,
                "source_id_unit": str(unit.get("id_unit") or ""),
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
                missing_ean_rows += 1
                missing_all_targets = ",".join(target_storefronts)
                for storefront in target_storefronts:
                    row[f"kaufland_offer_exists_{storefront}"] = "0"
                    row[f"kaufland_id_unit_{storefront}"] = ""
                    row[f"kaufland_price_{storefront}"] = ""
                    row[f"kaufland_error_{storefront}"] = "missing_ean"
                row["kaufland_offer_exists_in_any_target"] = "0"
                row["kaufland_offer_exists_in_all_targets"] = "0"
                row["kaufland_missing_target_storefronts"] = missing_all_targets
                row["kaufland_checked_target_storefronts"] = ",".join(target_storefronts)
                writer.writerow(row)
                written_rows += 1
                if written_rows % FLUSH_EVERY_ROWS == 0:
                    dst_file.flush()
                    if ENABLE_FSYNC:
                        os.fsync(dst_file.fileno())
                continue

            if ean not in offer_cache:
                cache_misses += 1
                offer_cache[ean] = {}
                for storefront in target_storefronts:
                    offer_cache[ean][storefront] = check_offer_exists_in_storefront(client, ean, storefront)
            else:
                cache_hits += 1

            offer_exists_values: list[bool] = []
            missing_storefronts: list[str] = []

            for storefront in target_storefronts:
                offer_exists, unit_id, price, error = offer_cache[ean][storefront]
                offer_exists_values.append(offer_exists)
                if not offer_exists:
                    missing_storefronts.append(storefront)

                row[f"kaufland_offer_exists_{storefront}"] = "1" if offer_exists else "0"
                row[f"kaufland_id_unit_{storefront}"] = str(unit_id or "")
                row[f"kaufland_price_{storefront}"] = str(price or "")
                row[f"kaufland_error_{storefront}"] = str(error or "")

            row["kaufland_offer_exists_in_any_target"] = "1" if any(offer_exists_values) else "0"
            row["kaufland_offer_exists_in_all_targets"] = "1" if all(offer_exists_values) else "0"
            row["kaufland_missing_target_storefronts"] = ",".join(missing_storefronts)
            row["kaufland_checked_target_storefronts"] = ",".join(target_storefronts)

            writer.writerow(row)
            written_rows += 1
            if written_rows % FLUSH_EVERY_ROWS == 0:
                dst_file.flush()
                if ENABLE_FSYNC:
                    os.fsync(dst_file.fileno())

        dst_file.flush()
        if ENABLE_FSYNC:
            os.fsync(dst_file.fileno())

    logger.info(
        "Finished export: total=%s written=%s missing_product_id=%s missing_ean=%s unique_eans=%s cache_hits=%s cache_misses=%s",
        total_rows,
        written_rows,
        missing_product_id_rows,
        missing_ean_rows,
        len(offer_cache),
        cache_hits,
        cache_misses,
    )


def configure_logging() -> None:
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_client() -> KauflandAPIClient:
    return KauflandAPIClient(
        client_key=require_env("KAUFLAND_CLIENT_KEY"),
        secret_key=require_env("KAUFLAND_SECRET_KEY"),
        base_url=require_env("KAUFLAND_BASE_URL", "https://sellerapi.kaufland.com/v2"),
    )


def main() -> None:
    configure_logging()

    source_storefront = SOURCE_STOREFRONT
    target_storefronts = TARGET_STOREFRONTS
    output_path = OUTPUT_CSV_PATH

    if source_storefront in target_storefronts:
        target_storefronts = [s for s in target_storefronts if s != source_storefront]

    if not target_storefronts:
        raise ValueError("No target storefronts to check after removing source storefront")

    client = build_client()
    export_offers_storefronts_check(
        client=client,
        output_path=output_path,
        source_storefront=source_storefront,
        target_storefronts=target_storefronts,
    )

    logger.info("Done. Output written to: %s", output_path)


if __name__ == "__main__":
    main()
