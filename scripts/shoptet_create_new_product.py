#!/usr/bin/env python3
"""
Zalozeni novych produktu v Kaufland katalogu.

- Nacte EANy z data/shoptet_new_product.csv
- Krizi-referenci nazev produktu z data/shoptet.csv
- Pro kazdy EAN overi, ze produkt v Kaufland NEEXISTUJE
- Pokud neexistuje, vytvori jej pres PUT /v2/product-data
- Defaultni kategorie: "jine kreativni hracky"
- Vysledky uklada do data/shoptet_create_new_product_results.csv
"""

from __future__ import annotations

import csv
import io
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dotenv import load_dotenv
import requests

sys.path.append(str(Path(__file__).resolve().parent.parent / "jirimodels-offer-stock-updater"))

try:
    from kaufland_api_client import KauflandAPIClient
except ImportError:
    print("Chyba: Nepodarilo se importovat KauflandAPIClient.")
    sys.exit(1)

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

DEFAULT_CATEGORY = "jine kreativni hracky"
REQUEST_DELAY_SECONDS = 0.5
STOREFRONT = "cz"

SCRIPTS_DIR = Path(__file__).resolve().parent
NEW_PRODUCT_CSV = SCRIPTS_DIR / "data" / "shoptet_new_product.csv"
SHOPTET_SOURCE_CSV = SCRIPTS_DIR / "data" / "shoptet.csv"
RESULTS_CSV = SCRIPTS_DIR / "data" / "shoptet_create_new_product_results.csv"


def normalize_ean(raw: Optional[str]) -> str:
    if not raw:
        return ""
    ean = raw.strip().replace("\xa0", "").replace(" ", "")
    if ean.endswith(".0"):
        ean = ean[:-2]
    return ean


def load_target_eans(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Soubor s EANy nebyl nalezen: {path}")
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"CSV soubor nema hlavicku: {path}")
        headers = {h.strip().lower(): h for h in reader.fieldnames if h}
        ean_key = headers.get("ean")
        if not ean_key:
            raise ValueError(f"CSV {path} neobsahuje sloupec 'ean'")
        eans: List[str] = []
        seen: Set[str] = set()
        for row in reader:
            ean = normalize_ean(row.get(ean_key))
            if ean and ean not in seen:
                seen.add(ean)
                eans.append(ean)
    return eans


def load_shoptet_data(path: Path) -> Dict[str, Dict[str, str]]:
    """Vrati slovnik EAN -> {'title': nazev, 'image': obrazek} ze Shoptet fedu."""
    if not path.exists():
        logger.warning("Shoptet zdrojovy CSV nebyl nalezen: %s — nazvy nebudou doplneny", path)
        return {}
    data: Dict[str, Dict[str, str]] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        if not reader.fieldnames:
            return data
        headers = {(h or "").strip().lower(): h for h in reader.fieldnames if h}
        ean_key = headers.get("ean")
        name_key = headers.get("name")
        image_key = headers.get("image")
        if not ean_key or not name_key:
            logger.warning("shoptet.csv nema sloupce 'ean' a/nebo 'name'")
            return data
        for row in reader:
            ean = normalize_ean(row.get(ean_key, ""))
            name = (row.get(name_key) or "").strip()
            image = (row.get(image_key) or "").strip() if image_key else ""
            manufacturer = (row.get("manufacturer") or "").strip()
            data[ean] = {"title": name, "image": image, "manufacturer": manufacturer}
    return data


def product_exists_in_kaufland(client: KauflandAPIClient, ean: str) -> bool:
    """Vrati True pokud produkt s danym EAN existuje v Kauflandu."""
    try:
        response = client.get(
            endpoint=f"/products/ean/{ean}",
            params={"storefront": STOREFRONT},
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        payload = response.json()
        
        data = payload.get("data")
        if isinstance(data, dict):
            return "id_product" in data
        if isinstance(data, list) and len(data) > 0:
            return "id_product" in data[0]
        return False
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            logger.error("Chyba 401 Unauthorized pri kontrole EAN %s — prerusuji.", ean)
            raise
        if exc.response is not None and exc.response.status_code == 404:
            return False
        raise


def create_product(client: KauflandAPIClient, ean: str, title: str, image: str, manufacturer:str) -> bool:
    """Vytvori novy produkt pres PUT /v2/product-data. Vrati True pri uspechu."""
    payload: Dict[str, Any] = {
        "ean": [ean],
        "attributes": {
            "title": [title],
            "description": [title],
            "category": [DEFAULT_CATEGORY],
            "picture": [image],
            "manufacturer": [manufacturer]
        },
    }
        
    try:
        response = client.put(endpoint="/product-data?locale=cs-CZ", data=payload)
        response.raise_for_status()
        logger.info("Produkt vytvoren: EAN=%s title=%s status_code=%s response=%s", ean, title, response.status_code, response.text)
        return True
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            logger.error("Chyba 401 Unauthorized pri vytvareni EAN %s — prerusuji.", ean)
            raise
        body = exc.response.text if exc.response is not None else str(exc)
        logger.error("API chyba pri vytvareni EAN=%s: %s | payload=%s", ean, body, payload)
    except Exception as exc:
        logger.error("Neocekavana chyba pri vytvareni EAN=%s: %s", ean, exc)
    return False


def get_product_status(client: KauflandAPIClient, ean: str) -> str:
    """Vrati update_status z /v2/product-data/status/{ean} nebo 'UNKNOWN'."""
    try:
        response = client.get(endpoint=f"/v2/product-data/status/{ean}")
        if response.status_code == 404:
            return "NOT_FOUND"
        response.raise_for_status()
        payload = response.json()
        return str((payload.get("data") or {}).get("update_status", "UNKNOWN"))
    except Exception as exc:
        logger.debug("Nelze nacist status pro EAN=%s: %s", ean, exc)
        return "UNKNOWN"


def write_results(results: List[Dict[str, Any]], path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ean", "title", "action", "status", "note"])
        writer.writeheader()
        writer.writerows(results)
    logger.info("Vysledky ulozeny do: %s", path)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    logger.info("Nacitam EANy z %s", NEW_PRODUCT_CSV)
    target_eans = load_target_eans(NEW_PRODUCT_CSV)
    logger.info("Nacteno %s unikatnich EANu", len(target_eans))

    logger.info("Nacitam data z %s", SHOPTET_SOURCE_CSV)
    shoptet_data = load_shoptet_data(SHOPTET_SOURCE_CSV)
    logger.info("Nacteno %s zaznamu ze Shoptet fedu", len(shoptet_data))

    client = KauflandAPIClient()

    results: List[Dict[str, Any]] = []
    already_exists = 0
    created = 0
    failed = 0

    for index, ean in enumerate(target_eans, start=1):
        product_info = shoptet_data.get(ean, {})
        title = product_info.get("title", "")
        image = product_info.get("image", "")
        manufacturer = product_info.get("manufacturer", "")


        logger.info("[%s/%s] EAN=%s title=%s manufacturer=%s", index, len(target_eans), ean, title, manufacturer)

        try:
            exists = product_exists_in_kaufland(client, ean)
        except requests.HTTPError:
            results.append({"ean": ean, "title": title, "action": "ERROR", "status": "", "note": "HTTP chyba pri kontrole"})
            failed += 1
            continue

        if exists:
            logger.info("Produkt jiz existuje v Kaufland pro EAN=%s — preskakuji", ean)
            results.append({"ean": ean, "title": title, "action": "SKIP", "status": "", "note": "jiz existuje"})
            already_exists += 1
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        ok = create_product(client, ean, title, image, manufacturer)
        if ok:
            time.sleep(REQUEST_DELAY_SECONDS)
            import_status = get_product_status(client, ean)
            results.append({"ean": ean, "title": title, "action": "CREATED", "status": import_status, "note": ""})
            created += 1
        else:
            results.append({"ean": ean, "title": title, "action": "ERROR", "status": "", "note": "vytvoreni selhalo"})
            failed += 1

        time.sleep(REQUEST_DELAY_SECONDS)

    write_results(results, RESULTS_CSV)

    print("=" * 70)
    print("SOUHRN")
    print("=" * 70)
    print(f"EANy zpracovany:    {len(target_eans)}")
    print(f"Vytvoreno:          {created}")
    print(f"Jiz existovalo:     {already_exists}")
    print(f"Chyby / preskoceno: {failed + (len(target_eans) - created - already_exists - failed)}")
    print(f"Vysledky:           {RESULTS_CSV}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as exc:
        logger.error("HTTP chyba: %s", exc, exc_info=True)
        raise
    except Exception as exc:
        logger.error("Selhani: %s", exc, exc_info=True)
        raise
