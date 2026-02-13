"""Main entry point for Jiri Models Feed ingester."""

import argparse
import logging
import os
import ssl
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Tuple
import certifi
import requests

from shared_db.session import session_scope
from shared_db.models import JiriModelsFeedItem
from shared_db.dao import set_last_run

logger = logging.getLogger(__name__)

JOB_NAME = "jiri-models-feed"

ENV_FEED_URL = "JIRI_MODELS_FEED_URL"


def get_feed_url() -> str:
    """Return feed URL from environment. Raises ValueError if not set."""
    url = os.environ.get(ENV_FEED_URL)
    if not url or not url.strip():
        raise ValueError(f"Environment variable {ENV_FEED_URL} must be set")
    return url.strip()


def setup_ssl_certificates():
    """
    Nastaví SSL certifikát Zscaler Root CA.
    Vytvoří bundle se systémovými certifikáty + Zscaler certifikátem.
    """
    cert_file = 'Zscaler Root CA.crt'
    cert_path = Path(cert_file)
    
    if not cert_path.exists():
        print(f"Varování: Certifikát {cert_file} nebyl nalezen. Používám systémové certifikáty.")
        return
    
    try:
        # Načteme systémové certifikáty z certifi
        system_certs_path = certifi.where()
        
        # Vytvoříme bundle s oběma certifikáty
        bundle_path = Path("cert_bundle.pem")
        
        with open(system_certs_path, 'r', encoding='utf-8') as f:
            system_certs = f.read()
        
        with open(cert_path, 'r', encoding='utf-8') as f:
            zscaler_cert = f.read()
        
        # Zkombinujeme certifikáty
        combined_certs = system_certs + "\n" + zscaler_cert
        
        with open(bundle_path, 'w', encoding='utf-8') as f:
            f.write(combined_certs)
        
        bundle_path_str = str(bundle_path.absolute())
        
        # Vytvoříme SSL context s bundle certifikátů
        context = ssl.create_default_context(cafile=bundle_path_str)
        
        # Nastavíme globální SSL context
        ssl._create_default_https_context = lambda: context
        
        # Nastavíme environment proměnné
        os.environ['SSL_CERT_FILE'] = bundle_path_str
        os.environ['REQUESTS_CA_BUNDLE'] = bundle_path_str
        
        print(f"Používám SSL certifikát: {cert_file} (v bundle: {bundle_path})")
        
    except Exception as e:
        print(f"Varování: Nepodařilo se nastavit SSL certifikát: {e}")
        # Fallback - zkusíme použít jen Zscaler certifikát
        try:
            cert_path_str = str(cert_path.absolute())
            context = ssl.create_default_context(cafile=cert_path_str)
            ssl._create_default_https_context = lambda: context
            os.environ['SSL_CERT_FILE'] = cert_path_str
            os.environ['REQUESTS_CA_BUNDLE'] = cert_path_str
            print(f"Používám SSL certifikát: {cert_file} (bez bundle)")
        except Exception as e2:
            print(f"Varování: Nepodařilo se načíst certifikát: {e2}")
            

def _text(elem: ET.Element | None) -> str:
    """Return element text or empty string."""
    if elem is not None and elem.text is not None:
        return elem.text.strip()
    return ""


def _find_child(parent: ET.Element, tag: str) -> ET.Element | None:
    """Find direct child by tag, with or without namespace. Avoids Element truth-value deprecation."""
    elem = parent.find(tag)
    if elem is not None:
        return elem
    return parent.find(f"{{*}}{tag}")


def fetch_feed_xml(url: str) -> str:
    """
    Download XML feed from URL.

    Args:
        url: Feed URL

    Returns:
        Raw XML string

    Raises:
        requests.RequestException: On HTTP error
    """
    logger.info("Fetching feed from %s", url)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def parse_products(xml_content: str) -> List[Tuple[str, str, str]]:
    """
    Parse XML and extract (code, ean, stock) for each <product>.

    Args:
        xml_content: Raw XML string

    Returns:
        List of (code, ean, stock) tuples
    """
    root = ET.fromstring(xml_content)
    # namespace might be absent; local tag without ns
    products = root.findall(".//product")
    if not products:
        products = root.findall(".//{*}product")
    result: List[Tuple[str, str, str]] = []
    for product in products:
        code = _text(_find_child(product, "CODE"))
        ean = _text(_find_child(product, "EAN"))
        stock = _text(_find_child(product, "STOCK"))
        if ean:
            result.append((code or "", ean, stock or ""))
        else:
            logger.warning("Skipping product with empty EAN: %s", ET.tostring(product, encoding="unicode")[:200])
    return result


def ingest_products(items: List[Tuple[str, str, str]]) -> None:
    """
    Insert new EANs and update STOCK (and code) for existing EANs in jiri_models_feed_item.
    """
    if not items:
        logger.warning("No products to ingest")
        return

    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0

    with session_scope() as session:
        for code, ean, stock in items:
            row = session.query(JiriModelsFeedItem).filter(JiriModelsFeedItem.ean == ean).first()
            if row:
                if row.stock != stock:
                    row.stock = stock
                    row.changed_at = now
                    updated += 1
            else:
                session.add(
                    JiriModelsFeedItem(
                        ean=ean,
                        code=code,
                        stock=stock,
                        changed_at=now,
                        ingested_at=now,
                    )
                )
                inserted += 1

    logger.info("Ingested: %d inserted, %d updated", inserted, updated)


def update_job_checkpoint() -> None:
    """Update job checkpoint in job_state."""
    now = datetime.now(timezone.utc)
    logger.info("Updating job checkpoint: %s at %s", JOB_NAME, now)
    with session_scope() as session:
        set_last_run(session, JOB_NAME, now)
    logger.info("Job checkpoint updated successfully")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jiri Models Feed Ingester")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local SSL certificate setup (Zscaler Root CA)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    if args.local:
        logger.info("Running in local mode - setting up SSL certificates")
        setup_ssl_certificates()
    try:
        """Download feed, parse products, ingest into DB, update checkpoint."""
        logger.info("Starting Jiri Models feed ingest at %s", datetime.now(timezone.utc))
        xml_content = fetch_feed_xml(get_feed_url())
        items = parse_products(xml_content)
        logger.info("Parsed %d products from feed", len(items))
        ingest_products(items)
        update_job_checkpoint()
        logger.info("Feed ingest completed successfully")
    except Exception as e:
        logger.error("Error during feed ingest: %s", e, exc_info=True)
        try:
            update_job_checkpoint()
        except Exception as checkpoint_error:
            logger.error("Failed to update job checkpoint: %s", checkpoint_error)
        raise
