#!/usr/bin/env python3
"""
Skript pro zalozeni novych Kaufland nabidek z listu EANu s cenou.

- Nacte EANy a ceny ze souboru scripts/data/shoptet_new_offer_with_price.csv
- Cenu v CZK prevede:
  - CZ storefront zustava v CZK (halere)
  - PL storefront do PLN (grosze)
  - Ostatni storefronty do EUR (centy)
- Pro kazdy storefront vytvori nabidku pres POST /v2/units
"""

from __future__ import annotations

import csv
import logging
import sys
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dotenv import load_dotenv
import requests

# Pridame cestu pro import sdileneho Kaufland API klienta
sys.path.append(str(Path(__file__).resolve().parent.parent / "jirimodels-offer-stock-updater"))

try:
    from kaufland_api_client import KauflandAPIClient
except ImportError:
    print("Chyba: Nepodarilo se importovat KauflandAPIClient.")
    sys.exit(1)

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

TARGET_STOREFRONTS = ["cz", "de", "sk", "pl", "es", "fr", "nl", "at", "it"]

CZK_PER_EUR = Decimal("24.20")
CZK_PER_PLN = Decimal("5.62")

DEFAULT_AMOUNT = 0
HANDLING_TIME_DAYS = 1
REQUEST_DELAY_SECONDS = 0.5


def normalize_ean(raw: Optional[str]) -> str:
    if not raw:
        return ""

    ean = raw.strip().replace("\xa0", "").replace(" ", "")
    if ean.endswith(".0"):
        ean = ean[:-2]
    return ean


def parse_czk_price_to_halere(raw_price: Optional[str]) -> Optional[int]:
    if not raw_price:
        return None

    normalized = raw_price.strip().replace("\xa0", "").replace(" ", "")
    normalized = normalized.replace(",", ".")
    if not normalized:
        return None

    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None

    if value <= 0:
        return None

    halere = (value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(halere)


def _to_minor_units(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_target_listing_price(czk_price_halere: int, storefront: str) -> int:
    storefront_key = storefront.lower()
    czk_price = Decimal(czk_price_halere) / Decimal("100")

    if storefront_key == "cz":
        return _to_minor_units(czk_price)

    if storefront_key == "pl":
        return _to_minor_units(czk_price / CZK_PER_PLN)

    return _to_minor_units(czk_price / CZK_PER_EUR)


def load_offers_with_price(csv_path: Path) -> List[Dict[str, Any]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Soubor s EANy a cenami nebyl nalezen: {csv_path}")

    with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as file_handle:
        reader = csv.DictReader(file_handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV soubor nema hlavicku: {csv_path}")

        normalized_headers = {header.strip().lower(): header for header in reader.fieldnames if header}
        ean_key = normalized_headers.get("ean")
        price_key = normalized_headers.get("price_czk")
        if not ean_key or not price_key:
            raise ValueError(f"CSV soubor {csv_path} neobsahuje sloupce 'ean' a 'price_czk'")

        offers: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for row in reader:
            ean = normalize_ean(row.get(ean_key))
            if not ean or ean in seen:
                continue

            price_halere = parse_czk_price_to_halere(row.get(price_key))
            if price_halere is None:
                logger.warning("EAN %s ma nevalidni nebo chybejici price_czk - preskakuji.", ean)
                continue

            seen.add(ean)
            offers.append({"ean": ean, "price_halere": price_halere})

    return offers


def _extract_product_object(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return first
    return None


def resolve_product_id_by_ean(client: KauflandAPIClient, ean: str) -> Optional[int]:
    try:
        response = client.get(
            endpoint=f"/products/ean/{ean}",
            params={"storefront": "cz", "embedded": "units"},
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            logger.error("Chyba 401 Unauthorized pri lookupu EAN %s - prerusuji provadeni.", ean)
            raise
        logger.warning("Nepodarilo se najit produkt pro EAN %s: %s", ean, exc)
        return None
    except Exception as exc:
        logger.warning("Neocekavana chyba pri lookupu EAN %s: %s", ean, exc)
        return None

    try:
        payload = response.json()
    except ValueError:
        logger.warning("EAN %s vratil nevalidni JSON odpoved.", ean)
        return None

    product = _extract_product_object(payload)
    if not product:
        logger.warning("Produkt pro EAN %s nebyl v odpovedi nalezen.", ean)
        return None

    product_id_raw = product.get("id_product") or product.get("id")
    if product_id_raw is None:
        logger.warning("EAN %s nema v odpovedi id_product/id.", ean)
        return None

    try:
        return int(product_id_raw)
    except (TypeError, ValueError):
        logger.warning("EAN %s ma nevalidni id produktu: %r", ean, product_id_raw)
        return None


def create_offer(
    client: KauflandAPIClient,
    id_product: int,
    ean: str,
    storefront: str,
    listing_price: int,
    id_offer: str,
) -> bool:
    payload = {
        "id_product": id_product,
        "condition": "NEW",
        "listing_price": listing_price,
        "amount": DEFAULT_AMOUNT,
        "handling_time": HANDLING_TIME_DAYS,
        "id_offer": id_offer,
    }

    try:
        response = client.post(
            endpoint="/units",
            data=payload,
            params={"storefront": storefront},
        )
        response.raise_for_status()
        logger.info(
            "[%s] Nabidka zalozena pro EAN %s (id_product=%s, listing_price=%s)",
            storefront,
            ean,
            id_product,
            listing_price,
        )
        return True
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            logger.error("Chyba 401 Unauthorized pri vytvareni nabidky - prerusuji provadeni.")
            raise

        error_body = exc.response.text if exc.response is not None else str(exc)
        logger.error(
            "[%s] API chyba pri vytvareni nabidky pro EAN %s (id_product=%s): %s; payload=%s",
            storefront,
            ean,
            id_product,
            error_body,
            payload,
        )
    except Exception as exc:
        logger.error(
            "[%s] Neocekavana chyba pri vytvareni nabidky pro EAN %s (id_product=%s): %s",
            storefront,
            ean,
            id_product,
            exc,
        )

    return False


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    script_dir = Path(__file__).resolve().parent
    input_path = script_dir / "data" / "shoptet_new_offer_with_price.csv"

    logger.info("Nacitam EANy a ceny z %s", input_path)
    offers = load_offers_with_price(input_path)
    logger.info("Nacteno %s unikatnich EANu pro zalozeni nabidek", len(offers))

    client = KauflandAPIClient()

    missing_product_count = 0
    created_count = 0
    failed_count = 0

    for index, offer in enumerate(offers, start=1):
        ean = str(offer["ean"])
        price_halere = int(offer["price_halere"])
        logger.info("Zpracovavam EAN %s/%s: %s", index, len(offers), ean)

        id_product = resolve_product_id_by_ean(client, ean)
        if id_product is None:
            missing_product_count += 1
            continue

        for storefront in TARGET_STOREFRONTS:
            listing_price = calculate_target_listing_price(price_halere, storefront)
            is_created = create_offer(
                client=client,
                id_product=id_product,
                ean=ean,
                storefront=storefront,
                listing_price=listing_price,
                id_offer=ean,
            )
            if is_created:
                created_count += 1
            else:
                failed_count += 1

            # Kratka pauza proti API rate limitum
            time.sleep(REQUEST_DELAY_SECONDS)

    logger.info(
        "Hotovo. Vytvoreno: %s, selhalo: %s, nenalezen produkt: %s",
        created_count,
        failed_count,
        missing_product_count,
    )


if __name__ == "__main__":
    main()
