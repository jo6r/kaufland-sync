#!/usr/bin/env python3
"""
Skript pro hromadné založení nabídek do dalších Kaufland storefronts.
Čte data z CSV 'offers_storefronts_check.csv' a vytváří nabídky 
tam, kde ještě neexistují.
Nastavuje specifikovaný handling_time a automaticky přepočítává cenu.
"""

import csv
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Přidáme cestu pro import sdíleného Kaufland API klienta
sys.path.append(str(Path(__file__).resolve().parent.parent / "jirimodels-offer-stock-updater"))

from dotenv import load_dotenv
import requests

try:
    from kaufland_api_client import KauflandAPIClient
except ImportError:
    print("Chyba: Nepodařilo se importovat KauflandAPIClient.")
    sys.exit(1)

# Načtení stejného .env jako má jirimodels updater pro sdílenou konfiguraci
load_dotenv(Path(__file__).resolve().parent.parent / "jirimodels-offer-stock-updater" / ".env")

logger = logging.getLogger(__name__)

TARGET_STOREFRONTS = ["de", "sk", "pl", "es", "fr", "nl", "at", "it"]
DEFAULT_AMOUNT=1

def calculate_new_price_cents(czk_price_halere: int, target_storefront: str) -> int:
    """
    Vypočítá novou cenu pro cílový storefront (v centech / groszích).
    """
    czk_price = czk_price_halere / 100.0

    # Kurzy: kolik CZK stojí 1 jednotka cizí měny
    exchange_rates = {
        "sk": 24.20,  # EUR
        "de": 24.20,
        "at": 24.20,
        "it": 24.20,
        "es": 24.20,
        "fr": 24.20,
        "nl": 24.20,
        "pl": 5.62,   # PLN
    }

    rate = exchange_rates.get(target_storefront.lower(), 24.20)
    new_price = czk_price / rate

    # Návrat v celých číslech (Kaufland API očekává centy)
    return int(round(new_price * 100))

def create_offers_for_storefront(client: KauflandAPIClient, rows: List[Dict[str, str]], storefront: str):
    created_count = 0
    processed_count = 0
    for row in rows:
        # Přeskočíme nabídky, které nejsou v CZ aktivní
        if row.get("source_status") != "AVAILABLE":
            continue

        # Kontrola z CSV, zda už nabídka neexistuje (1 = existuje)
        exists_key = f"kaufland_offer_exists_{storefront}"
        if row.get(exists_key) == "1":
            continue

        id_product = row.get("source_id_product")
        if not id_product:
            continue

        source_price = int(row.get("source_price", 0))
        if source_price <= 0:
            continue

        new_price = calculate_new_price_cents(source_price, storefront)
        amount = int(DEFAULT_AMOUNT)

        id_offer = row.get("source_id_offer")
        

        # Sestavení payloadu pro POST /v2/units (po jednom)
        unit_payload = {
            "id_product": int(id_product),
            "condition": "NEW",
            "listing_price": new_price,
            "amount": DEFAULT_AMOUNT,
            "handling_time": 6,
            "id_offer": id_offer
        }
        
        if row.get("source_id_offer"):
            unit_payload["id_offer"] = row.get("source_id_offer")

        processed_count += 1

        try:
            response = client.post(
                endpoint="/v2/units",
                data=unit_payload,
                params={"storefront": storefront}
            )
            response.raise_for_status()
            logger.info(f"[{storefront}] Nabídka pro produkt {id_product} úspěšně vytvořena.")
            created_count += 1
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401:
                logger.error("Chyba 401 Unauthorized - přerušuji provádění.")
                raise
            
            error_body = exc.response.text if exc.response is not None else str(exc)
            logger.error(f"[{storefront}] API chyba při vytváření produktu {id_product}: {error_body}\nPayload: {unit_payload}")
        except Exception as exc:
            logger.error(f"[{storefront}] Neočekávaná chyba při vytváření produktu {id_product}: {exc}")
            
        # Pauza, abychom nepřetížili API rate limity (Kaufland doporučuje rozložit zátěž)
        time.sleep(0.5)

    if processed_count == 0:
        logger.info(f"Pro storefront '{storefront}' nejsou žádné nové nabídky k vytvoření.")
    else:
        logger.info(f"[{storefront}] Úspěšně vytvořeno {created_count} z {processed_count} potřebných nabídek.")

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    csv_path = Path(__file__).resolve().parent / "data" / "offers_storefronts_check.csv"
    if not csv_path.exists():
        logger.error(f"Soubor s daty nebyl nalezen: {csv_path}")
        return

    logger.info(f"Načítám data z {csv_path}")
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=",")
        rows = list(reader)

    client = KauflandAPIClient()

    for storefront in TARGET_STOREFRONTS:
        logger.info(f"--- Zpracovávám storefront: {storefront.upper()} ---")
        create_offers_for_storefront(client, rows, storefront)

    logger.info("Vytváření nabídek dokončeno.")

if __name__ == "__main__":
    main()