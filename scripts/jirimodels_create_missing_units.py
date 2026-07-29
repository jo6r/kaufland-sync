#!/usr/bin/env python3
import os
import csv
import json
import time
import hashlib
import hmac
import requests
import logging
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPTS_DIR / ".env")

# Fallback to the hardcoded keys from curl if .env doesn't have them, 
# although we prefer .env for security.
KAUFLAND_CLIENT_KEY = os.getenv("KAUFLAND_CLIENT_KEY")
KAUFLAND_SECRET_KEY = os.getenv("KAUFLAND_SECRET_KEY")
KAUFLAND_BASE_URL = os.getenv("KAUFLAND_BASE_URL", "https://sellerapi.kaufland.com/v2")

PRICE_MULTIPLIER = 2.7

def build_uri(endpoint, params=None):
    uri = f"{KAUFLAND_BASE_URL}{endpoint}"
    if params:
        query_parts = [f"{k}={v}" for k, v in params.items() if v is not None]
        if query_parts:
            uri += "?" + "&".join(query_parts)
    return uri

def sign_request(method, uri, body, timestamp, secret_key):
    string_to_sign = "\n".join([method.upper(), uri, body or "", str(timestamp)])
    signature = hmac.new(
        secret_key.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

def make_request(method, endpoint, params=None, body=""):
    if not KAUFLAND_SECRET_KEY:
        logger.error("KAUFLAND_SECRET_KEY is missing. Please set it in .env")
        return None

    url = build_uri(endpoint, params)
    timestamp = int(time.time())
    signature = sign_request(method, url, body, timestamp, KAUFLAND_SECRET_KEY)
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Shop-Client-Key": KAUFLAND_CLIENT_KEY,
        "Shop-Timestamp": str(timestamp),
        "Shop-Signature": signature,
    }
    
    try:
        if method.upper() == 'POST':
            response = requests.post(url, headers=headers, data=body)
        else:
            raise ValueError(f"Unsupported method: {method}")
        return response
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None

def main():
    csv_file = SCRIPTS_DIR / "data" / "jirimodels_verify_seller_units.csv"
    
    if not csv_file.exists():
        logger.error(f"CSV file not found: {csv_file}")
        return

    with open(csv_file, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("kaufland_exists") == "YES" and row.get("unit_exists") == "NO":
                id_product = row.get("id_product")
                ean = row.get("ean")
                
                if not id_product:
                    logger.warning(f"Skipping ean={ean}: missing id_product")
                    continue

                source_price_str = row.get("source_price", "0")
                try:
                    float_price = float(source_price_str.replace(',', '.'))
                    listing_price = int(round(float_price * 100 * PRICE_MULTIPLIER))
                except ValueError:
                    listing_price = 0

                if listing_price <= 19800:
                    logger.info(f"Skipping ean={ean}: listing_price {listing_price} is not greater than 198 CZK")
                    continue

                payload_dict = {
                    "amount": 0,
                    "handling_time": 6,
                    "id_shipping_group": 176532,
                    "id_warehouse": 71151,
                    "ean": ean,
                    "id_product": int(id_product) if id_product.isdigit() else id_product,
                    "id_offer": row.get("source_code"),
                    "condition": "NEW",
                    "vat_indicator": "standard_rate",
                    "listing_price": listing_price
                }
                
                body = json.dumps(payload_dict)
                endpoint = "/units"
                params = {"storefront": "cz", "embedded": "eco_participation"}
                
                logger.info(f"Processing ean={ean}, id_product={id_product}, listing_price={listing_price}")
                response = make_request('POST', endpoint, params, body)
                
                if response is not None:
                    try:
                        logger.info(f"Response status: {response.status_code}")
                    except json.JSONDecodeError:
                        logger.info(f"Response status: {response.status_code}, body: {response.text}")
                    
                    if response.status_code == 429:
                        logger.error("Rate limited. Stopping.")
                        break

                # Sleep to avoid rate limiting
                time.sleep(1)

if __name__ == "__main__":
    main()
