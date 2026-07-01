
from __future__ import annotations

import csv
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import requests

from kaufland_api_client import KauflandAPIClient

load_dotenv(Path(__file__).with_name(".env"))

logger = logging.getLogger(__name__)

MAX_UNITS_PER_REQUEST = 150


def get_csv_url() -> str:
    """Return CSV URL from environment."""
    url = os.environ.get("FEED_CSV_URL")
    if url and url.strip():
        return url.strip()
    raise ValueError("Environment variable FEED_CSV_URL must be set")


def get_ignore_codes() -> set[str]:
    """Return normalized set of ignored product codes from env."""
    raw = os.environ.get("IGNORE_CODES", "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def download_csv(url: str, timeout: int = 60) -> str:
    """Download CSV and decode with Windows-1250."""
    logger.info("Downloading CSV from %s", url)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    content = response.content.decode("windows-1250")
    logger.info("Downloaded %s bytes", len(response.content))
    return content


def parse_feed_rows(csv_content: str, ignore_codes: set[str]) -> List[Dict[str, Any]]:
    """Parse and validate CSV rows from Shoptet feed."""
    rows: List[Dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(csv_content), delimiter=";")

    for row_num, raw_row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items()}
        if not row:
            continue

        ean = row.get("ean", "")
        code = row.get("code", "")
        stock = row.get("stock", "")
        visibility = row.get("productvisibility", "")

        if not ean or not stock:
            logger.debug("Skipping row %s without EAN or stock", row_num)
            continue

        if visibility.lower() == "hidden":
            logger.info("Skipping hidden product on row %s ean=%s", row_num, ean)
            continue

        if code and code.lower() in ignore_codes:
            logger.info("Skipping ignored code on row %s code=%s ean=%s", row_num, code, ean)
            continue

        try:
            quantity = int(float(stock.replace(",", ".")))
        except ValueError:
            logger.warning("Skipping row %s with invalid stock value '%s'", row_num, stock)
            continue

        if quantity < 0:
            logger.warning("Negative stock on row %s ean=%s changed to 0", row_num, ean)
            quantity = 0

        rows.append({"ean": ean, "code": code, "quantity": quantity})

    logger.info("Parsed %s valid feed rows", len(rows))
    return rows


def fetch_id_unit_for_offer(client: KauflandAPIClient, ean: str, storefront: str = "cz") -> Optional[str]:
    """Return Kaufland id_unit if offer exists for EAN, otherwise None."""
    response = client.get(endpoint="/v2/units", params={"storefront": storefront, "ean": ean})
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        return None

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None

    first = data[0]
    if not isinstance(first, dict) or "id_unit" not in first:
        return None

    return str(first["id_unit"])


def resolve_existing_offers(
    client: KauflandAPIClient,
    feed_rows: List[Dict[str, Any]],
    storefront: str = "cz",
) -> List[Dict[str, Any]]:
    """Resolve feed rows to existing Kaufland offers."""
    resolved: List[Dict[str, Any]] = []

    for index, item in enumerate(feed_rows, start=1):
        ean = item["ean"]
        try:
            id_unit = fetch_id_unit_for_offer(client, ean, storefront=storefront)
            if not id_unit:
                logger.info("Offer does not exist in Kaufland for ean=%s", ean)
                continue

            resolved.append({**item, "id_unit": id_unit})
            logger.info(
                "Resolved offer %s/%s ean=%s id_unit=%s quantity=%s",
                index,
                len(feed_rows),
                ean,
                id_unit,
                item["quantity"],
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401:
                logger.error("Received 401 Unauthorized from Kaufland API. Terminating.")
                raise
            logger.error("Kaufland HTTP error for ean=%s: %s", ean, exc)
        except Exception as exc:
            logger.error("Unexpected error resolving ean=%s: %s", ean, exc)

    logger.info("Resolved %s/%s rows to existing offers", len(resolved), len(feed_rows))
    return resolved


def build_bulk_batches(resolved_rows: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Create deduplicated /v2/units/bulk payload split by API limit."""
    batches: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    seen_id_units: set[int] = set()

    for row in resolved_rows:
        id_unit_int = int(row["id_unit"])
        if id_unit_int in seen_id_units:
            logger.warning("Skipping duplicate id_unit=%s ean=%s", id_unit_int, row["ean"])
            continue

        seen_id_units.add(id_unit_int)
        current.append({"id_unit": id_unit_int, "unit_data": {"amount": row["quantity"]}})

        if len(current) >= MAX_UNITS_PER_REQUEST:
            batches.append(current)
            current = []

    if current:
        batches.append(current)

    return batches


def update_bulk_quantities(
    client: KauflandAPIClient,
    payload: List[Dict[str, Any]],
    storefront: str = "cz",
) -> Dict[str, Any]:
    """Send one bulk quantity update request."""
    logger.info("Updating %s units in Kaufland", len(payload))
    response = client.post(endpoint="/v2/units/bulk", data=payload, params={"storefront": storefront})
    response.raise_for_status()
    return response.json()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("Starting Shoptet offer stock updater at %s", datetime.now(timezone.utc))

    client = KauflandAPIClient()
    csv_content = download_csv(get_csv_url())
    feed_rows = parse_feed_rows(csv_content, get_ignore_codes())

    if not feed_rows:
        logger.warning("No valid rows in feed")
        return

    resolved_rows = resolve_existing_offers(client, feed_rows)
    if not resolved_rows:
        logger.warning("No existing Kaufland offers found for feed rows")
        return

    batches = build_bulk_batches(resolved_rows)
    logger.info("Prepared %s bulk batch(es)", len(batches))

    for batch_idx, batch in enumerate(batches, start=1):
        result = update_bulk_quantities(client, batch)
        logger.info("Bulk batch %s/%s successful: %s", batch_idx, len(batches), result)

    logger.info("Shoptet offer stock updater completed successfully")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as exc:
        logger.error("HTTP error during processing: %s", exc, exc_info=True)
        raise
    except Exception as exc:
        logger.error("Processing failed: %s", exc, exc_info=True)
        raise
