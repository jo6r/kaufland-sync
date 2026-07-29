#!/usr/bin/env python3
"""
Process EANs from jirimodels.xml.
If the unit has a listing_price lower than 200 CZK, delete it.
"""

import os
import sys
import json
import hashlib
import hmac
import time
import xml.etree.ElementTree as ET
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

XML_FILE = SCRIPTS_DIR / "data" / "jirimodels.xml"
DEFAULT_TARGET_STOREFRONTS = "cz"
PRICE_THRESHOLD = 19800  # 198 CZK in halere


def extract_eans_from_xml(xml_file) -> List[str]:
    """Extract EANs from jirimodels.xml"""
    eans = []
    try:
        tree = ET.parse(xml_file)
        root = tree.getroot()
        for product in root.findall('.//product'):
            ean_elem = product.find('EAN')
            if ean_elem is not None and ean_elem.text:
                eans.append(ean_elem.text.strip())
    except Exception as e:
        print(f"Error parsing XML: {e}")
    return sorted(list(set(eans)))


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
        "User-Agent": "delete_cheap_units",
    }
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=10)
        else:
            response = requests.request(method, url, headers=headers, data=body, timeout=10)
        
        if response.status_code >= 400:
            return None
        # DELETE might return 204 No Content and empty body
        if method == "DELETE" and response.status_code == 204:
            return {"status": "success"}
        if response.text:
            return response.json()
        return {"status": "success"}
    except Exception as e:
        print(f"Request failed: {e}")
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


def delete_unit(id_unit: int, storefront: str) -> bool:
    """Delete a unit by id"""
    response = make_request("DELETE", f"/units/{id_unit}", {"storefront": storefront})
    return response is not None


def main():
    if not KAUFLAND_CLIENT_KEY or not KAUFLAND_SECRET_KEY:
        print("❌ KAUFLAND_CLIENT_KEY and KAUFLAND_SECRET_KEY must be set in .env")
        sys.exit(1)

    print(f"\n🗑️  Processing EANs from {XML_FILE.name}")
    print(f" Target storefront: {DEFAULT_TARGET_STOREFRONTS}")
    print(f" Price threshold: {PRICE_THRESHOLD / 100} CZK\n")
    
    if not XML_FILE.exists():
        print(f"❌ File not found: {XML_FILE}")
        sys.exit(1)
    
    eans = extract_eans_from_xml(XML_FILE)
    print(f"📋 Found {len(eans)} unique EANs\n")
    
    deleted_count = 0
    errors = 0
    
    try:
        for ean_idx, ean in enumerate(eans, 1):
            if ean_idx % 50 == 0:
                print(f"[{ean_idx}/{len(eans)}] Processing EAN {ean}...")
            
            product = get_product_by_ean(ean, DEFAULT_TARGET_STOREFRONTS)
            if not product:
                continue
            
            id_product = product.get('id_product')
            units = get_product_units(id_product, DEFAULT_TARGET_STOREFRONTS)
            
            if not units:
                continue
            
            for unit in units:
                listing_price = unit.get('listing_price')
                id_unit = unit.get('id_unit')
                
                if listing_price is not None and listing_price < PRICE_THRESHOLD:
                    print(f"🗑️ Deleting unit {id_unit} for EAN {ean} (price: {listing_price / 100} CZK)")
                    success = delete_unit(id_unit, DEFAULT_TARGET_STOREFRONTS)
                    if success:
                        deleted_count += 1
                        print(f"  ✅ Deleted successfully")
                    else:
                        errors += 1
                        print(f"  ❌ Failed to delete unit {id_unit}")
                        
    except KeyboardInterrupt:
        print(f"\n⚠️  Interrupted by user")
    except Exception as e:
        print(f"❌ Error during processing: {e}")
        
    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"✅ Total units deleted: {deleted_count}")
    print(f"⚠️  API Errors during deletion: {errors}")


if __name__ == "__main__":
    main()
