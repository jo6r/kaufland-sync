#!/usr/bin/env python3
"""
Skript pro aktualizaci ceny existujících nabídek (jednotek) na Kauflandu.
Čte EAN a price z 'data/prices.csv'.
Pro storefront 'cz' použije přímo zadanou cenu.
Pro ostatní storefronty vynásobí cenu koeficientem 1.15 a převede na příslušnou měnu.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import requests

SCRIPTS_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPTS_DIR / ".env")

KAUFLAND_CLIENT_KEY = os.getenv("KAUFLAND_CLIENT_KEY")
KAUFLAND_SECRET_KEY = os.getenv("KAUFLAND_SECRET_KEY")
KAUFLAND_BASE_URL = os.getenv("KAUFLAND_BASE_URL", "https://sellerapi.kaufland.com/v2")

logger = logging.getLogger(__name__)

CSV_FILE = SCRIPTS_DIR / "data" / "prices.csv"
OUTPUT_CSV_FILE = SCRIPTS_DIR / "data" / "prices_updated.csv"

TARGET_STOREFRONTS = ["cz", "de", "sk", "pl", "es", "fr", "nl", "at", "it"]

MARKUP_MULTIPLIER = Decimal("1.15")
CZK_PER_EUR = Decimal("24.20")
CZK_PER_PLN = Decimal("5.62")


def build_uri(endpoint: str, params: Optional[Dict[str, Any]] = None) -> str:
    uri = f"{KAUFLAND_BASE_URL}{endpoint}"
    if params:
        query_parts = [f"{k}={v}" for k, v in params.items() if v is not None]
        if query_parts:
            uri += "?" + "&".join(query_parts)
    return uri


def sign_request(method: str, uri: str, body: str, timestamp: int) -> str:
    string_to_sign = "\n".join([method.upper(), uri, body or "", str(timestamp)])
    signature = hmac.new(
        KAUFLAND_SECRET_KEY.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature


def make_request(method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, body: str = "") -> Optional[requests.Response]:
    if not KAUFLAND_SECRET_KEY or not KAUFLAND_CLIENT_KEY:
        logger.error("KAUFLAND API keys are missing in .env")
        return None

    url = build_uri(endpoint, params)
    timestamp = int(time.time())
    signature = sign_request(method, url, body, timestamp)
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Shop-Client-Key": KAUFLAND_CLIENT_KEY,
        "Shop-Timestamp": str(timestamp),
        "Shop-Signature": signature,
        "User-Agent": "update_prices_from_csv",
    }
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=10)
        elif method.upper() == "PATCH":
            response = requests.patch(url, headers=headers, data=body, timeout=10)
        else:
            response = requests.request(method, url, headers=headers, data=body, timeout=10)
        
        return response
    except Exception as e:
        logger.error(f"API Request Error: {e}")
        return None


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

    normalized = str(raw_price).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
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
        # CZ: pouzijeme rovnou cenu v CZK ze vstupu
        return _to_minor_units(czk_price)

    if storefront_key == "pl":
        # PL: navyseni o 15 %, prevod CZK -> PLN
        marked_up_czk = czk_price * MARKUP_MULTIPLIER
        pln_price = marked_up_czk / CZK_PER_PLN
        return _to_minor_units(pln_price)

    # Ostatni storefronty: navyseni o 15 %, prevod CZK -> EUR
    marked_up_czk = czk_price * MARKUP_MULTIPLIER
    eur_price = marked_up_czk / CZK_PER_EUR
    return _to_minor_units(eur_price)


def load_prices_from_csv(csv_path: Path) -> Dict[str, int]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Zdrojový CSV soubor nebyl nalezen: {csv_path}")

    eans_data: Dict[str, int] = {}
    with open(csv_path, mode="r", encoding="utf-8", newline="") as file_handle:
        # Detekce oddelovace
        sample = file_handle.read(1024)
        file_handle.seek(0)
        delimiter = ";" if ";" in sample else ","
        reader = csv.DictReader(file_handle, delimiter=delimiter)
        
        if not reader.fieldnames:
            raise ValueError(f"CSV soubor nemá hlavičku: {csv_path}")

        normalized_headers = {header.strip().lower(): header for header in reader.fieldnames if header}
        ean_key = normalized_headers.get("ean")
        price_key = normalized_headers.get("price")

        if not ean_key or not price_key:
            raise ValueError(f"CSV soubor {csv_path} neobsahuje sloupce 'ean' a 'price'")

        for row in reader:
            ean = normalize_ean(row.get(ean_key))
            if not ean:
                continue

            price_halere = parse_czk_price_to_halere(row.get(price_key))
            if price_halere is None:
                continue

            eans_data[ean] = price_halere

    return eans_data


def get_product_by_ean(ean: str, storefront: str) -> Optional[Dict[str, Any]]:
    try:
        response = make_request(
            "GET",
            f"/products/ean/{ean}",
            params={"storefront": storefront},
        )
        if response is None or response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data:
            return data[0]
        return None
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            logger.error("Chyba 401 Unauthorized při lookupu EAN %s.", ean)
            raise
        logger.warning("Nepodařilo se najít produkt pro EAN %s na storefrontu %s: %s", ean, storefront, exc)
    except Exception as exc:
        logger.warning("Neočekávaná chyba při lookupu EAN %s: %s", ean, exc)
    return None


def get_product_units(id_product: int, storefront: str) -> Optional[List[Dict[str, Any]]]:
    try:
        response = make_request(
            "GET",
            "/units",
            params={
                "storefront": storefront,
                "id_product": id_product,
                "fulfillment_type": "fulfilled_by_merchant",
                "limit": 30,
                "offset": 0,
            }
        )
        if response is None or response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if isinstance(data, list):
            return data
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            logger.error("Chyba 401 Unauthorized při načítání jednotek.")
            raise
        logger.warning("Nepodařilo se najít jednotky pro id_product %s na storefrontu %s: %s", id_product, storefront, exc)
    except Exception as exc:
        logger.warning("Neočekávaná chyba při načítání jednotek pro id_product %s: %s", id_product, exc)
    return None


def update_unit_price(id_unit: int, listing_price: int, storefront: str) -> bool:
    payload = {"listing_price": listing_price}
    body = json.dumps(payload)
    try:
        response = make_request(
            "PATCH",
            f"/units/{id_unit}",
            params={"storefront": storefront},
            body=body
        )
        if response is None:
            return False
        response.raise_for_status()
        return True
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            logger.error("Chyba 401 Unauthorized při updatu ceny.")
            raise
        error_body = exc.response.text if exc.response is not None else str(exc)
        logger.error("API chyba při aktualizaci unit_id %s (%s): %s", id_unit, storefront, error_body)
    except Exception as exc:
        logger.error("Neočekávaná chyba při aktualizaci unit_id %s: %s", id_unit, exc)
    return False


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    
    print(f"\n📊 Aktualizace cen z {CSV_FILE.name}")
    print(f"Koeficient: {MARKUP_MULTIPLIER} pro ostatní storefronty (mimo CZ)\n")
    
    if not CSV_FILE.exists():
        logger.error(f"Soubor nenalezen: {CSV_FILE}")
        sys.exit(1)
        
    eans_data = load_prices_from_csv(CSV_FILE)
    print(f"📋 Nalezeno {len(eans_data)} produktů s EAN a cenou v CSV\n")
    
    csv_fieldnames = ['EAN', 'SOURCE_PRICE']
    for sf in TARGET_STOREFRONTS:
        csv_fieldnames.append(f'PRICE_{sf.upper()}')

    try:
        f = open(OUTPUT_CSV_FILE, 'w', newline='', encoding='utf-8')
        writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
        writer.writeheader()
    except Exception as e:
        logger.error(f"Chyba při otevírání výstupního souboru: {e}")
        sys.exit(1)

    updated_count = 0
    not_found_product_count = 0
    not_found_unit_count = 0
    errors = 0
    
    try:
        for ean, price_halere in eans_data.items():
            row_data = {
                'EAN': ean,
                'SOURCE_PRICE': price_halere / 100.0
            }
            
            storefront_prices = {}
            for sf in TARGET_STOREFRONTS:
                listing_price_int = calculate_target_listing_price(price_halere, sf)
                storefront_prices[sf] = listing_price_int
                row_data[f'PRICE_{sf.upper()}'] = listing_price_int / 100.0

            writer.writerow(row_data)
            
            success_for_product = False
            
            for sf in TARGET_STOREFRONTS:
                listing_price_int = storefront_prices[sf]
                new_price_display = listing_price_int / 100.0
                
                product = get_product_by_ean(ean, sf)
                if not product:
                    not_found_product_count += 1
                    time.sleep(0.2)
                    continue
                    
                id_product = product.get('id_product') or product.get('id')
                if not id_product:
                    continue
                
                units = get_product_units(int(id_product), sf)
                if not units:
                    not_found_unit_count += 1
                    time.sleep(0.2)
                    continue
                    
                for unit in units:
                    id_unit_raw = unit.get('id_unit') or unit.get('id')
                    if not id_unit_raw:
                        continue
                        
                    id_unit = int(id_unit_raw)
                    old_listing_price = unit.get('listing_price')
                    
                    old_price_display = old_listing_price / 100.0 if old_listing_price else 'N/A'
                    logger.info(f"🔄 Aktualizuji EAN {ean} na storefrontu {sf.upper()} (unit_id: {id_unit}): cena {old_price_display} -> {new_price_display}")
                    
                    if old_listing_price == listing_price_int:
                        logger.info("  ✅ Cena je již aktuální.")
                        success_for_product = True
                    else:
                        success = update_unit_price(id_unit, listing_price_int, sf)
                        if success:
                            success_for_product = True
                        else:
                            errors += 1
                        
                time.sleep(0.3)
                
            if success_for_product:
                updated_count += 1

    except KeyboardInterrupt:
        print("\n⚠️ Přerušeno uživatelem.")
    except Exception as e:
        logger.error(f"Zpracování selhalo: {e}")
    finally:
        f.close()
        
    print(f"\n{'='*70}")
    print(f"SOUHRN")
    print(f"{'='*70}")
    print(f"Zpracováno položek z CSV: {len(eans_data)}")
    print(f"✅ Úspěšně aktualizovaných produktů (alespoň 1 storefront): {updated_count}")
    print(f"❌ Produkt nenalezen na Kauflandu (počet pokusů): {not_found_product_count}")
    print(f"❌ Prodejní jednotka nenalezena (počet pokusů): {not_found_unit_count}")
    print(f"⚠️ Chyb při updatu (API error): {errors}")
    print(f"📄 Uloženo do: {OUTPUT_CSV_FILE}")


if __name__ == "__main__":
    main()