#!/usr/bin/env python3
"""
Test script to verify unit_id from API vs GUI
"""
import os
import sys
import urllib.request
import json
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import urlencode
import requests
from dotenv import load_dotenv

EAN = "8032780604378"
STOREFRONT = "cz"

load_dotenv(Path(__file__).resolve().parent / ".env")

KAUFLAND_CLIENT_KEY = os.getenv("KAUFLAND_CLIENT_KEY")
KAUFLAND_SECRET_KEY = os.getenv("KAUFLAND_SECRET_KEY")
KAUFLAND_BASE_URL = os.getenv("KAUFLAND_BASE_URL", "https://sellerapi.kaufland.com/v2")

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
        "User-Agent": "test_unit_id",
    }
    
    print(f"\n{'='*60}")
    print(f"Request: {method} {url}")
    print(f"Headers: {headers}")
    print(f"{'='*60}")
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers)
        elif method == "PATCH":
            response = requests.patch(url, headers=headers, data=body)
        else:
            response = requests.request(method, url, headers=headers, data=body)
        
        print(f"Status: {response.status_code}")
        if response.status_code >= 400:
            print(f"Error: {response.text}")
            return None
        return response.json()
    except Exception as e:
        print(f"Error: {e}")
        return None



print(f"\n🔍 Testing EAN {EAN} on storefront {STOREFRONT}")

# Step 1: Get product by EAN
print("\n[STEP 1] GET /products/ean/{ean}")
product_response = make_request(
    "GET",
    f"/products/ean/{EAN}",
    {"storefront": STOREFRONT}
)

if not product_response:
    print("❌ Failed to get product")
    sys.exit(1)

print(f"\nFull response: {json.dumps(product_response, indent=2)}")

product_data = product_response.get("data", [])
if isinstance(product_data, dict):
    # Single product response
    product = product_data
elif isinstance(product_data, list) and len(product_data) > 0:
    # List response
    product = product_data[0]
else:
    print("❌ No product data found")
    sys.exit(1)
product_id = product.get("id_product") or product.get("id")
print(f"\n✅ Product found: ID={product_id}")

# Step 2: Get units for this product
print("\n[STEP 2] GET /units?storefront={storefront}&id_product={product_id}")
units_response = make_request(
    "GET",
    "/units",
    {
        "storefront": STOREFRONT,
        "id_product": product_id,
        "limit": 30,
        "offset": 0,
        "fulfillment_type": "fulfilled_by_merchant"
    }
)

if not units_response:
    print("❌ Failed to get units")
    sys.exit(1)

print(f"\nFull units response: {json.dumps(units_response, indent=2)}")

units = units_response.get("data", [])
print(f"\n✅ Found {len(units)} unit(s):")

for idx, unit in enumerate(units, 1):
    unit_id = unit.get("id")
    handling_time = unit.get("handling_time_days", "?")
    print(f"\n  Unit #{idx}:")
    print(f"    ID: {unit_id}")

