"""Single-run Jiri Models offer stock updater.

Downloads the Jiri Models XML feed, resolves Kaufland units by EAN,
and updates unit stock in one pass without any database dependency.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

import requests

from kaufland_api_client import KauflandAPIClient

load_dotenv(Path(__file__).with_name(".env"))

logger = logging.getLogger(__name__)

AMOUNT_IN_STOCK = 5
AMOUNT_OUT_OF_STOCK = 0
MAX_UNITS_PER_REQUEST = 150


def get_feed_url() -> str:
    """Return feed URL from the environment."""
    url = os.environ.get("FEED_XML_URL")
    if url and url.strip():
        return url.strip()
    raise ValueError("Environment variable FEED_XML_URL must be set")


def fetch_feed_xml(url: str) -> str:
    """Download the XML feed."""
    logger.info("Fetching feed from %s", url)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


def _text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _find_child(parent: ET.Element, tag: str) -> ET.Element | None:
    elem = parent.find(tag)
    if elem is not None:
        return elem
    return parent.find(f"{{*}}{tag}")


def _normalize_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def determine_amount(stock_value: Any) -> int:
    """Convert stock data from the feed into Kaufland amount."""
    if stock_value is None:
        return AMOUNT_OUT_OF_STOCK

    if isinstance(stock_value, bool):
        return AMOUNT_IN_STOCK if stock_value else AMOUNT_OUT_OF_STOCK

    if isinstance(stock_value, (int, float)):
        return max(0, int(stock_value))

    text = _normalize_value(stock_value)
    if not text:
        return AMOUNT_OUT_OF_STOCK

    try:
        return max(0, int(float(text.replace(",", "."))))
    except ValueError:
        pass

    upper_text = text.upper()
    if upper_text in {"ANO", "YES", "TRUE", "AVAILABLE", "IN_STOCK"}:
        return AMOUNT_IN_STOCK
    if upper_text in {"NE", "NO", "FALSE", "UNAVAILABLE", "OUT_OF_STOCK"}:
        return AMOUNT_OUT_OF_STOCK

    return AMOUNT_OUT_OF_STOCK


def parse_products(xml_content: str) -> List[Dict[str, Any]]:
    """Extract normalized feed rows with code, ean, stock and amount."""
    products: List[Dict[str, Any]] = []
    root = ET.fromstring(xml_content)
    feed_products = root.findall(".//product")
    if not feed_products:
        feed_products = root.findall(".//{*}product")

    for index, product in enumerate(feed_products, 1):
        ean = _text(_find_child(product, "EAN"))
        if not ean:
            logger.warning(
                "Skipping item %d with empty EAN: %s",
                index,
                ET.tostring(product, encoding="unicode")[:200],
            )
            continue

        code = _text(_find_child(product, "CODE"))
        stock = _text(_find_child(product, "STOCK"))
        amount = determine_amount(stock)

        products.append(
            {
                "code": code,
                "ean": ean,
                "stock": stock,
                "amount": amount,
            }
        )
        logger.info("Parsed feed item ean=%s amount=%s code=%s", ean, amount, code)

    return products


def fetch_id_unit_for_ean(client: KauflandAPIClient, ean: str, storefront: str = "cz") -> Optional[str]:
    """Resolve Kaufland id_unit for a given EAN."""
    response = client.get(endpoint="/v2/units", params={"storefront": storefront, "ean": ean})
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        return None

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None

    unit = data[0]
    if not isinstance(unit, dict) or "id_unit" not in unit:
        return None

    return str(unit["id_unit"])


def resolve_offers(client: KauflandAPIClient, feed_items: List[Dict[str, Any]], storefront: str = "cz") -> List[Dict[str, Any]]:
    """Filter feed rows to Kaufland offers that exist and add id_unit information."""
    resolved: List[Dict[str, Any]] = []

    for index, item in enumerate(feed_items, 1):
        ean = item["ean"]
        try:
            id_unit = fetch_id_unit_for_ean(client, ean, storefront=storefront)
            if not id_unit:
                logger.warning("Offer not found in Kaufland for EAN %s, skipping", ean)
                continue

            resolved.append({**item, "id_unit": id_unit})
            logger.info(
                "Resolved %d/%d ean=%s id_unit=%s amount=%s",
                index,
                len(feed_items),
                ean,
                id_unit,
                item["amount"],
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401:
                logger.error("Received 401 Unauthorized from Kaufland API. Terminating.")
                raise
            logger.error("Kaufland HTTP error for EAN %s: %s", ean, exc)
        except Exception as exc:
            logger.error("Error resolving EAN %s: %s", ean, exc)

    return resolved


def create_bulk_payload(resolved: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Build Kaufland bulk update batches with deduplicated id_unit values."""
    batches: List[List[Dict[str, Any]]] = []
    current_batch: List[Dict[str, Any]] = []
    seen: set[int] = set()

    for item in resolved:
        id_unit_int = int(item["id_unit"])
        if id_unit_int in seen:
            logger.warning("Skipped duplicate id_unit=%s for ean=%s", id_unit_int, item["ean"])
            continue

        seen.add(id_unit_int)
        current_batch.append(
            {
                "id_unit": id_unit_int,
                "unit_data": {"amount": item["amount"]},
            }
        )
        logger.info(
            "Prepared update ean=%s id_unit=%s amount=%s",
            item["ean"],
            id_unit_int,
            item["amount"],
        )

        if len(current_batch) == MAX_UNITS_PER_REQUEST:
            batches.append(current_batch)
            current_batch = []

    if current_batch:
        batches.append(current_batch)

    return batches


def update_units_bulk(client: KauflandAPIClient, payload: List[Dict[str, Any]], storefront: str = "cz") -> Dict[str, Any]:
    """Send a bulk stock update to Kaufland API."""
    logger.info("Sending bulk update with %d units (storefront=%s)", len(payload), storefront)
    response = client.post(endpoint="/v2/units/bulk", data=payload, params={"storefront": storefront})
    response.raise_for_status()
    return response.json()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    client = KauflandAPIClient()

    logger.info("Starting cycle at %s", datetime.now(timezone.utc))
    xml_content = fetch_feed_xml(get_feed_url())
    feed_items = parse_products(xml_content)

    if not feed_items:
        logger.warning("No feed items found in XML payload")
        return

    logger.info("Parsed %d feed items", len(feed_items))
    resolved = resolve_offers(client, feed_items)

    if not resolved:
        logger.warning("No Kaufland offers were resolved from the feed")
        return

    batches = create_bulk_payload(resolved)
    logger.info("Created %d bulk batch(es)", len(batches))

    for batch_index, batch in enumerate(batches, 1):
        result = update_units_bulk(client, batch)
        logger.info("Bulk batch %d/%d updated successfully: %s", batch_index, len(batches), result)

    logger.info("Jiri Models offer stock updater completed successfully")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as exc:
        logger.error("HTTP error during update: %s", exc, exc_info=True)
        raise
    except Exception as exc:
        logger.error("Error during update: %s", exc, exc_info=True)
        raise