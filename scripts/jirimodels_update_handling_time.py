#!/usr/bin/env python3
"""
Update Kaufland offers handling time (processing days) to 6 days.

Script parses data/jirimodels.xml to extract EAN codes, finds offers in Kaufland
API, and updates handling_time to 6 working days.

"""

import xml.etree.ElementTree as ET
import os
import sys
import json
import hashlib
import hmac
import time
import logging
import csv
from typing import List, Dict, Any
from pathlib import Path

import requests
from dotenv import load_dotenv

# ============================================================================
# CONFIGURATION & CONSTANTS
# ============================================================================

SCRIPTS_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPTS_DIR / ".env")

# Logging configuration
logger = logging.getLogger(__name__)

# API Configuration
KAUFLAND_BASE_URL = os.getenv("KAUFLAND_BASE_URL", "https://sellerapi.kaufland.com/v2")
KAUFLAND_STOREFRONT = os.getenv("KAUFLAND_STOREFRONT", "cz")
DEFAULT_TARGET_STOREFRONTS = ["cz","de", "sk", "pl", "es", "fr", "nl", "at", "it"]

# Update parameters
HANDLING_TIME_TARGET = 600  # days
UNITS_LIMIT = 100  # per API request

# User agent for API requests
USER_AGENT = "jirimodels_handling_time_updater"

# XML configuration
XML_DATA_FILE = SCRIPTS_DIR / "data" / "jirimodels.xml"


# ============================================================================
# KAUFLAND API CLIENT
# ============================================================================

class KauflandAPIClient:
    """Kaufland REST API client with HMAC-SHA256 signing."""

    def __init__(self, storefront: str = None):
        self.client_key = os.getenv("KAUFLAND_CLIENT_KEY")
        self.secret_key = os.getenv("KAUFLAND_SECRET_KEY")
        
        if not self.client_key:
            raise ValueError("KAUFLAND_CLIENT_KEY not set in .env")
        if not self.secret_key:
            raise ValueError("KAUFLAND_SECRET_KEY not set in .env")
        
        self.base_url = KAUFLAND_BASE_URL
        self.storefront = storefront or KAUFLAND_STOREFRONT

    def _sign_request(self, method: str, uri: str, body: str, timestamp: int) -> str:
        """Generate HMAC-SHA256 signature for request."""
        string_to_sign = "\n".join([method.upper(), uri, body or "", str(timestamp)])
        return hmac.new(
            self.secret_key.encode(), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()

    def _build_uri(self, endpoint: str, params: Dict[str, Any] = None) -> str:
        """Build full URI with query parameters."""
        uri = f"{self.base_url}{endpoint}"
        if params:
            query_parts = [f"{k}={v}" for k, v in params.items() if v is not None]
            if query_parts:
                uri += "?" + "&".join(query_parts)
        return uri

    def get(self, endpoint: str, params: Dict[str, Any] = None) -> requests.Response:
        """Send GET request with authentication."""
        uri = self._build_uri(endpoint, params)
        timestamp = int(time.time())
        signature = self._sign_request("GET", uri, "", timestamp)
        
        headers = {
            "Accept": "application/json",
            "Shop-Client-Key": self.client_key,
            "Shop-Timestamp": str(timestamp),
            "Shop-Signature": signature,
            "User-Agent": USER_AGENT,
        }
        
        logger.debug(f"GET {uri}")
        return requests.get(uri, headers=headers)

    def patch(self, endpoint: str, data: Dict, params: Dict[str, Any] = None) -> requests.Response:
        """Send PATCH request with authentication."""
        uri = self._build_uri(endpoint, params)
        body = json.dumps(data)
        timestamp = int(time.time())
        signature = self._sign_request("PATCH", uri, body, timestamp)
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Shop-Client-Key": self.client_key,
            "Shop-Timestamp": str(timestamp),
            "Shop-Signature": signature,
            "User-Agent": USER_AGENT,
        }
        
        logger.debug(f"PATCH {uri}")
        logger.debug(f"Body: {body}")
        return requests.patch(uri, headers=headers, data=body)


# ============================================================================
# BUSINESS LOGIC
# ============================================================================

def extract_eans_from_xml(xml_file: Path) -> List[str]:
    """Extract and deduplicate EAN codes from XML file."""
    try:
        tree = ET.parse(xml_file)
        eans = []
        
        for product in tree.getroot().findall("product"):
            ean = product.find("EAN")
            if ean is not None and ean.text:
                eans.append(ean.text.strip())
        
        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for ean in eans:
            if ean not in seen:
                unique.append(ean)
                seen.add(ean)
        return unique
    except Exception as e:
        print(f"Error parsing XML: {e}")
        return []


def find_units_by_ean(client: KauflandAPIClient, ean: str) -> List[Dict]:
    """Get all units for a given EAN by first finding the product."""
    try:
        # Step 1: Get product by EAN
        logger.debug(f"Finding product for EAN: {ean}")
        response = client.get(f"/products/ean/{ean}", {
            "storefront": client.storefront
        })
        
        if response.status_code != 200:
            print(f"  Error finding product: HTTP {response.status_code}")
            try:
                errors = response.json().get("errors", [])
                if errors:
                    print(f"    {errors[0].get('message', 'Unknown error')}")
            except:
                pass
            return []
        
        product = response.json().get("data", {})
        product_id = product.get("id_product")
        
        if not product_id:
            logger.warning(f"No product found for EAN {ean}")
            return []
        
        logger.debug(f"Found product ID: {product_id}")
        
        # Step 2: Get product with embedded units
        logger.debug(f"Fetching product {product_id} with embedded units")
        response = client.get(f"/products/{product_id}", {
            "storefront": client.storefront,
            "embedded": "units"
        })
        
        if response.status_code != 200:
            print(f"  Error fetching product units: HTTP {response.status_code}")
            return []
        
        product = response.json().get("data", {})
        units = product.get("units", [])
        
        logger.debug(f"Found {len(units)} unit(s)")
        return units
    except Exception as e:
        print(f"  Error: {e}")
        return []


def update_unit_handling_time(client: KauflandAPIClient, unit_id: int) -> bool:
    """Update handling_time for a unit."""
    try:
        response = client.patch(
            f"/units/{unit_id}", 
            {"handling_time": HANDLING_TIME_TARGET},
            params={"storefront": client.storefront}
        )
        if response.status_code == 200:
            return True
        else:
            print(f"  Failed (HTTP {response.status_code})")
            
            try:
                resp_json = response.json()
                if "errors" in resp_json:
                    for error in resp_json["errors"]:
                        print(f"    Error: {error.get('field')} - {error.get('message')}")
                elif "message" in resp_json:
                    print(f"    {resp_json['message']}")
            except:
                print(f"    {response.text[:200]}")
            return False
    except Exception as e:
        print(f"  Error: {e}")
        return False


def process_ean(client: KauflandAPIClient, ean: str) -> tuple[bool, List[Dict[str, Any]]]:
    """Process single EAN: find units and update handling_time. Returns (success, results)."""
    results = []
    units = find_units_by_ean(client, ean)
    
    if not units:
        print(f"  No units found")
        return False, results
    
    print(f"  Found {len(units)} unit(s)")
    success = True
    
    for unit in units:
        unit_id = unit.get("id_unit")
        current_time = unit.get("handling_time")
        
        display_time = current_time if current_time is not None else "?"
        print(f"    Unit {unit_id} (current: {display_time}d)", end=" → ")
        
        if current_time == HANDLING_TIME_TARGET:
            print(f"✓ Already set to {HANDLING_TIME_TARGET}d (skipped)")
            results.append({
                "ean": ean,
                "storefront": client.storefront,
                "unit_id": unit_id,
                "status": "skipped"
            })
            continue
        
        if update_unit_handling_time(client, unit_id):
            print(f"✓ Updated to {HANDLING_TIME_TARGET}d")
            results.append({
                "ean": ean,
                "storefront": client.storefront,
                "unit_id": unit_id,
                "status": "success"
            })
        else:
            success = False
            results.append({
                "ean": ean,
                "storefront": client.storefront,
                "unit_id": unit_id,
                "status": "failed"
            })
    
    return success, results


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main entry point."""
    # Configure logging
    log_level = logging.DEBUG if os.getenv("DEBUG") else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="[%(levelname)s] %(message)s",
        stream=sys.stdout
    )
    
    # Validate configuration
    if not os.getenv("KAUFLAND_CLIENT_KEY"):
        print("❌ Error: KAUFLAND_CLIENT_KEY not set")
        print("\nSetup:")
        print("  1. cp .env.example .env")
        print("  2. Edit .env with your credentials")
        print("  3. python update_handling_time.py")
        print("\nFor debugging:")
        print("  DEBUG=true python update_handling_time.py")
        sys.exit(1)
    
    # Initialize client and extract EANs
    try:
        client = KauflandAPIClient()  # Validate credentials with default storefront
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)
    
    print(f"✓ Connected to: {client.base_url}\n")
    
    eans = extract_eans_from_xml(XML_DATA_FILE)
    if not eans:
        print(f"❌ No EANs found in {XML_DATA_FILE}")
        sys.exit(1)
    
    print(f"✓ Found {len(eans)} EANs")
    print(f"✓ Target storefronts: {', '.join(DEFAULT_TARGET_STOREFRONTS)}")
    print(f"✓ Updating handling_time to {HANDLING_TIME_TARGET} days\n")
    
    # Process all storefronts
    total_success = 0
    total_failed = 0
    all_results = []
    
    for storefront in DEFAULT_TARGET_STOREFRONTS:
        print(f"\n{'='*50}")
        print(f"Processing storefront: {storefront.upper()}")
        print(f"{'='*50}")
        
        try:
            client = KauflandAPIClient(storefront=storefront)
        except ValueError as e:
            print(f"❌ Error creating client: {e}")
            continue
        
        success_count = 0
        for i, ean in enumerate(eans, 1):
            print(f"[{i:3d}/{len(eans)}] EAN {ean}: ", end="")
            success, results = process_ean(client, ean)
            if success:
                success_count += 1
            all_results.extend(results)
        
        failed_count = len(eans) - success_count
        print(f"\n{'-'*50}")
        print(f"Storefront {storefront.upper()}: {success_count}/{len(eans)} ✓")
        
        total_success += success_count
        total_failed += failed_count
    
    # Final Summary
    print(f"\n\n{'='*50}")
    print(f"FINAL SUMMARY (ALL STOREFRONTS)")
    print(f"{'='*50}")
    print(f"Total EANs:        {len(eans)}")
    print(f"Total Storefronts: {len(DEFAULT_TARGET_STOREFRONTS)}")
    print(f"Total Updates:     {total_success} ✓")
    print(f"Total Failed:      {total_failed} ✗")
    print(f"{'='*50}")
    
    # Save results to CSV
    csv_file = SCRIPTS_DIR / "data" / "handling_time_updates.csv"
    if all_results:
        try:
            with open(csv_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["ean", "storefront", "unit_id", "status"])
                writer.writeheader()
                writer.writerows(all_results)
            print(f"\n✓ Results saved to: {csv_file}")
        except Exception as e:
            print(f"\n❌ Error saving CSV: {e}")
    else:
        print(f"\n⚠ No updates recorded, CSV not created")


if __name__ == "__main__":
    main()
