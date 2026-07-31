#!/usr/bin/env python3
"""
Process EANs from shoptet.csv, get product details and units,
enrich with seller info and output to shoptet_verify_seller_units.csv
"""

import os
import sys
import hashlib
import hmac
import time
import csv
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import requests

# Load environment variables from .env
SCRIPTS_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPTS_DIR / ".env")

KAUFLAND_CLIENT_KEY = os.getenv("KAUFLAND_CLIENT_KEY")
KAUFLAND_SECRET_KEY = os.getenv("KAUFLAND_SECRET_KEY")
KAUFLAND_BASE_URL = os.getenv("KAUFLAND_BASE_URL", "https://sellerapi.kaufland.com/v2")
KAUFLAND_STOREFRONT = os.getenv("KAUFLAND_STOREFRONT", "cz")

CSV_FILE = SCRIPTS_DIR / "data" / "shoptet.csv"
OUTPUT_FILE = SCRIPTS_DIR / "data" / "shoptet_verify_seller_units.csv"

DEFAULT_TARGET_STOREFRONTS = "cz"


def extract_eans_from_csv(csv_file) -> Dict[str, Dict[str, Any]]:
    """Extract EANs and their details from shoptet.csv"""
    eans_data = {}
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                visibility = row.get('productVisibility', '').strip().lower()
                if visibility == 'hidden':
                    continue
                
                ean = row.get('ean', '').strip()
                if ean:
                    eans_data[ean] = {
                        'code': row.get('code', '').strip(),
                        'name': row.get('name', '').strip(),
                        'price': row.get('price', '').strip(),
                        'stock': row.get('stock', '').strip(),
                    }
    except Exception as e:
        print(f"Error parsing CSV: {e}")
        return {}
    return eans_data


def build_uri(endpoint, params=None):
    """Build full URI with query parameters"""
    uri = f"{KAUFLAND_BASE_URL}{endpoint}"
    if params:
        query_parts = [f"{k}={v}" for k, v in params.items() if v is not None]
        if query_parts:
            uri += "?" + "&".join(query_parts)
    return uri


def sign_request(method, uri, body, timestamp):
    """Generate HMAC-SHA256 signature"""
    string_to_sign = "\n".join([method.upper(), uri, body or "", str(timestamp)])
    signature = hmac.new(
        KAUFLAND_SECRET_KEY.encode(),
        string_to_sign.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature


def make_request(method, endpoint, params=None, body=""):
    """Make authenticated request to Kaufland API"""
    url = build_uri(endpoint, params)
    
    timestamp = int(time.time())
    signature = sign_request(method, url, body, timestamp)
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Shop-Client-Key": KAUFLAND_CLIENT_KEY,
        "Shop-Timestamp": str(timestamp),
        "Shop-Signature": signature,
        "User-Agent": "verify_seller_units",
    }
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=10)
        else:
            response = requests.request(method, url, headers=headers, data=body, timeout=10)
        
        if response.status_code >= 400:
            return None
        return response.json()
    except Exception as e:
        return None


def get_product_by_ean(ean: str, storefront: str) -> Optional[Dict[str, Any]]:
    """Get product details by EAN and storefront"""
    response = make_request("GET", f"/products/ean/{ean}", {"storefront": storefront})
    
    if not response or not response.get("data"):
        return None
    
    return response.get("data", {})


def get_product_units(id_product: int, storefront: str) -> Optional[List[Dict[str, Any]]]:
    """Get units for a product via /units endpoint with id_product filter"""
    response = make_request("GET", "/units", {
        "storefront": storefront,
        "id_product": id_product,
        "fulfillment_type": "fulfilled_by_merchant",
        "limit": 30,
        "offset": 0,
    })

    if not response:
        return None

    data = response.get("data")
    if not isinstance(data, list):
        return None

    return data


def get_unit_detail(id_unit: int, storefront: str) -> Optional[Dict[str, Any]]:
    """Get single unit detail via /units/{id_unit}"""
    response = make_request("GET", f"/units/{id_unit}", {"storefront": storefront})
    if not response or not response.get("data"):
        return None
    return response.get("data", {})


def main():
    """Main function"""
    print(f"\n📊 Processing EANs from {CSV_FILE.name}")
    print(f" Target storefront: {DEFAULT_TARGET_STOREFRONTS}\n")
    
    if not CSV_FILE.exists():
        print(f"❌ File not found: {CSV_FILE}")
        sys.exit(1)
    
    # Extract EANs from CSV
    eans_data = extract_eans_from_csv(CSV_FILE)
    eans = sorted(list(eans_data.keys()))
    print(f"📋 Found {len(eans)} unique EANs\n")
    
    # Open output CSV and write header
    output_fieldnames = [
        'ean', 'source_code', 'source_name', 'source_price', 'source_stock',
        'storefront', 'id_product', 'unit_id',
        'condition', 'handling_time', 'id_warehouse',
        'price', 'listing_price', 'amount', 'status',
        'kaufland_exists', 'unit_exists',
    ]
    
    try:
        f = open(OUTPUT_FILE, 'w', newline='', encoding='utf-8')
        writer = csv.DictWriter(f, fieldnames=output_fieldnames)
        writer.writeheader()
        f.flush()
    except Exception as e:
        print(f"❌ Error opening output file: {e}")
        sys.exit(1)
    
    # Process each EAN
    unit_found_count = 0
    no_unit_count = 0
    errors = 0
    total_rows = 0
    found_eans = set()
    
    try:
        for ean_idx, ean in enumerate(eans, 1):
            # Progress indicator
            if ean_idx % 50 == 0:
                print(f"[{ean_idx}/{len(eans)}] Processing EAN {ean} ({DEFAULT_TARGET_STOREFRONTS})...", flush=True)
            
            # Step 1: Get product by EAN
            product = get_product_by_ean(ean, DEFAULT_TARGET_STOREFRONTS)
            if not product:
                errors += 1
                continue
            
            # Mark this EAN as found
            found_eans.add(ean)
            
            id_product = product.get('id_product')
            
            # Step 2: Get our units via /units endpoint (returns only authenticated seller's units)
            units = get_product_units(id_product, DEFAULT_TARGET_STOREFRONTS)

            if not units:
                # Product exists in Kaufland but we have no unit for it
                no_unit_row = {
                    'ean': ean,
                    'source_code': eans_data[ean]['code'],
                    'source_name': eans_data[ean]['name'],
                    'source_price': eans_data[ean]['price'],
                    'source_stock': eans_data[ean]['stock'],
                    'storefront': DEFAULT_TARGET_STOREFRONTS,
                    'id_product': id_product,
                    'unit_id': '',
                    'condition': '',
                    'handling_time': '',
                    'id_warehouse': '',
                    'price': '',
                    'listing_price': '',
                    'amount': '',
                    'status': '',
                    'kaufland_exists': 'YES',
                    'unit_exists': 'NO',
                }
                writer.writerow(no_unit_row)
                no_unit_count += 1
                total_rows += 1
                if total_rows % 50 == 0:
                    f.flush()
                continue

            # Step 3: Write row for each of our units
            for unit in units:
                enriched_row = {
                    'ean': ean,
                    'source_code': eans_data[ean]['code'],
                    'source_name': eans_data[ean]['name'],
                    'source_price': eans_data[ean]['price'],
                    'source_stock': eans_data[ean]['stock'],
                    'storefront': DEFAULT_TARGET_STOREFRONTS,
                    'id_product': id_product,
                    'unit_id': unit.get('id_unit', ''),
                    'condition': unit.get('condition', ''),
                    'handling_time': unit.get('handling_time', ''),
                    'id_warehouse': unit.get('id_warehouse', ''),
                    'price': unit.get('price', ''),
                    'listing_price': unit.get('listing_price', ''),
                    'amount': unit.get('amount', ''),
                    'status': unit.get('status', ''),
                    'kaufland_exists': 'YES',
                    'unit_exists': 'YES',
                }
                writer.writerow(enriched_row)
                unit_found_count += 1
                total_rows += 1
                if total_rows % 50 == 0:
                    f.flush()
        
        # Step 4: Write rows for EANs not found in Kaufland at all
        not_found_count = 0
        for ean in eans:
            if ean not in found_eans:
                not_found_row = {
                    'ean': ean,
                    'source_code': eans_data[ean]['code'],
                    'source_name': eans_data[ean]['name'],
                    'source_price': eans_data[ean]['price'],
                    'source_stock': eans_data[ean]['stock'],
                    'storefront': DEFAULT_TARGET_STOREFRONTS,
                    'id_product': '',
                    'unit_id': '',
                    'condition': '',
                    'handling_time': '',
                    'id_warehouse': '',
                    'price': '',
                    'listing_price': '',
                    'amount': '',
                    'status': '',
                    'kaufland_exists': 'NO',
                    'unit_exists': 'NO',
                }
                writer.writerow(not_found_row)
                not_found_count += 1
                total_rows += 1
                if total_rows % 50 == 0:
                    f.flush()
    
    except KeyboardInterrupt:
        print(f"\n⚠️  Interrupted by user")
    except Exception as e:
        print(f"❌ Error during processing: {e}")
    finally:
        f.close()
    
    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"EANs processed: {len(eans)}")
    print(f"EANs found in Kaufland: {len(found_eans)}")
    print(f"EANs NOT found in Kaufland: {len(eans) - len(found_eans)} (no product on Kaufland)")
    print(f"✅ EANs with our unit: {unit_found_count}")
    print(f"❌ EANs without our unit (product exists): {no_unit_count}")
    print(f"⚠️  API Errors: {errors}")
    print(f"Total rows written: {total_rows}")
    print(f"\n✅ Output saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
