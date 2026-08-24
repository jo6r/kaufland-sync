#!/usr/bin/env python3
"""
Skript pro aktualizaci ceny existujících nabídek (jednotek) na Kauflandu pro Jiri Models.
Čte EAN a PRICE_RCMD z 'jirimodels.xml'.
Koeficient pro výpočet finální ceny je PRICE_RCMD * 2.3.
Podle EAN najde id_product a unit_id a updatuje cenu (listing_price) na unit_id.
"""

import os
import sys
import json
import hashlib
import hmac
import time
import csv
import xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv
import requests

SCRIPTS_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPTS_DIR / ".env")

KAUFLAND_CLIENT_KEY = os.getenv("KAUFLAND_CLIENT_KEY")
KAUFLAND_SECRET_KEY = os.getenv("KAUFLAND_SECRET_KEY")
KAUFLAND_BASE_URL = os.getenv("KAUFLAND_BASE_URL", "https://sellerapi.kaufland.com/v2")
KAUFLAND_STOREFRONT = os.getenv("KAUFLAND_STOREFRONT", "cz")

XML_FILE = SCRIPTS_DIR / "data" / "jirimodels.xml"
OUTPUT_CSV_FILE = SCRIPTS_DIR / "data" / "jirimodels_updated_prices.csv"
PRICE_MULTIPLIER = 2.3

TARGET_STOREFRONTS = ["de", "sk", "pl", "es", "fr", "nl", "at", "it", "cz"]

# Kurzy: kolik CZK stojí 1 jednotka cizí měny
EXCHANGE_RATES = {
    "sk": 24.20,  # EUR
    "de": 24.20,
    "at": 24.20,
    "it": 24.20,
    "es": 24.20,
    "fr": 24.20,
    "nl": 24.20,
    "pl": 5.62,   # PLN
    "cz": 1.0     # CZK
}

def build_uri(endpoint, params=None):
    uri = f"{KAUFLAND_BASE_URL}{endpoint}"
    if params:
        query_parts = [f"{k}={v}" for k, v in params.items() if v is not None]
        if query_parts:
            uri += "?" + "&".join(query_parts)
    return uri

def sign_request(method, uri, body, timestamp):
    string_to_sign = "\n".join([method.upper(), uri, body or "", str(timestamp)])
    signature = hmac.new(
        KAUFLAND_SECRET_KEY.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

def make_request(method, endpoint, params=None, body=""):
    if not KAUFLAND_SECRET_KEY or not KAUFLAND_CLIENT_KEY:
        print("❌ KAUFLAND API keys are missing in .env")
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
        "User-Agent": "update_prices_script",
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
        print(f"❌ API Request Error: {e}")
        return None

def extract_prices_from_xml(xml_file):
    eans_data = {}
    try:
        tree = ET.parse(xml_file)
        root = tree.getroot()
        for product in root.findall('.//product'):
            ean_elem = product.find('EAN')
            price_elem = product.find('PRICE_RCMD')
            
            if ean_elem is not None and ean_elem.text and price_elem is not None and price_elem.text:
                ean = ean_elem.text.strip()
                price_str = price_elem.text.strip().replace(',', '.')
                try:
                    price_float = float(price_str)
                    eans_data[ean] = price_float
                except ValueError:
                    print(f"⚠️ Neplatný formát ceny u EAN {ean}: {price_str}")
    except Exception as e:
        print(f"❌ Error parsing XML: {e}")
    return eans_data

def get_product_by_ean(ean: str, storefront: str):
    response = make_request("GET", f"/products/ean/{ean}", {"storefront": storefront})
    if not response or response.status_code >= 400:
        return None
    data = response.json()
    return data.get("data", {})

def get_product_units(id_product: int, storefront: str):
    response = make_request("GET", "/units", {
        "storefront": storefront,
        "id_product": id_product,
        "fulfillment_type": "fulfilled_by_merchant",
        "limit": 30,
        "offset": 0,
    })
    if not response or response.status_code >= 400:
        return None
    data = response.json()
    return data.get("data", [])

def update_unit_price(id_unit: int, listing_price: int, storefront: str):
    payload = {"listing_price": listing_price}
    body = json.dumps(payload)
    response = make_request("PATCH", f"/units/{id_unit}", {"storefront": storefront}, body)
    if not response:
        return False
    if response.status_code >= 400:
        try:
            print(f"  ❌ Chyba při aktualizaci unit_id {id_unit}: HTTP {response.status_code} - {response.json()}")
        except:
            print(f"  ❌ Chyba při aktualizaci unit_id {id_unit}: HTTP {response.status_code} - {response.text}")
        return False
    return True

def main():
    print(f"\n📊 Aktualizace cen z {XML_FILE.name}")
    print(f"Koeficient: {PRICE_MULTIPLIER}\n")
    
    if not XML_FILE.exists():
        print(f"❌ Soubor nenalezen: {XML_FILE}")
        sys.exit(1)
        
    eans_data = extract_prices_from_xml(XML_FILE)
    print(f"📋 Nalezeno {len(eans_data)} produktů s EAN a PRICE_RCMD v XML\n")
    
    csv_fieldnames = ['EAN', 'PRICE_RCMD']
    for sf in TARGET_STOREFRONTS:
        csv_fieldnames.append(f'PRICE_{sf.upper()}')

    try:
        f = open(OUTPUT_CSV_FILE, 'w', newline='', encoding='utf-8')
        writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
        writer.writeheader()
    except Exception as e:
        print(f"❌ Chyba při otevírání výstupního souboru: {e}")
        sys.exit(1)

    updated_count = 0
    not_found_product_count = 0
    not_found_unit_count = 0
    errors = 0
    
    try:
        for ean, price_rcmd in eans_data.items():
            base_czk_price = price_rcmd * PRICE_MULTIPLIER
            
            row_data = {
                'EAN': ean,
                'PRICE_RCMD': price_rcmd
            }
            
            # Předpočítat ceny pro všechny storefronty pro CSV
            storefront_prices = {}
            for sf in TARGET_STOREFRONTS:
                rate = EXCHANGE_RATES.get(sf, 1.0)
                localized_price_float = base_czk_price / rate
                # Kaufland API očekává cenu v centech/haléřích
                listing_price_int = int(round(localized_price_float * 100))
                storefront_prices[sf] = listing_price_int
                
                # Uložíme do CSV jako reálné číslo (např. v EUR/PLN)
                row_data[f'PRICE_{sf.upper()}'] = listing_price_int / 100.0

            # Zápis do CSV
            writer.writerow(row_data)
            
            success_for_product = False
            
            for sf in TARGET_STOREFRONTS:
                listing_price_int = storefront_prices[sf]
                new_price_display = listing_price_int / 100.0
                
                # Ziskani id_product
                product = get_product_by_ean(ean, sf)
                if not product:
                    # Nenalezen produkt na daném storefrontu
                    not_found_product_count += 1
                    continue
                    
                id_product = product.get('id_product')
                
                # Ziskani unit_id
                units = get_product_units(id_product, sf)
                if not units:
                    not_found_unit_count += 1
                    continue
                    
                for unit in units:
                    id_unit = unit.get('id_unit')
                    old_listing_price = unit.get('listing_price')
                    
                    print(f"🔄 Aktualizuji EAN {ean} na storefrontu {sf.upper()} (unit_id: {id_unit}): cena {old_listing_price/100 if old_listing_price else 'N/A'} -> {new_price_display}")
                    success = update_unit_price(id_unit, listing_price_int, sf)
                    if success:
                        success_for_product = True
                    else:
                        errors += 1
                        
                # Sleep to avoid rate limiting
                time.sleep(0.5)
                
            if success_for_product:
                updated_count += 1

    except KeyboardInterrupt:
        print("\n⚠️ Přerušeno uživatelem.")
    finally:
        f.close()
        
    print(f"\n{'='*70}")
    print(f"SOUHRN")
    print(f"{'='*70}")
    print(f"Zpracováno položek z XML: {len(eans_data)}")
    print(f"✅ Úspěšně aktualizovaných produktů: {updated_count}")
    print(f"❌ Produkt nenalezen na Kauflandu: {not_found_product_count}")
    print(f"❌ Prodejní jednotka nenalezena: {not_found_unit_count}")
    print(f"⚠️ Chyb při updatu (API error): {errors}")

if __name__ == "__main__":
    main()
