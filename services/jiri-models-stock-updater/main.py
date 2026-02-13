"""Main entry point for Jiri Models Feed Stock Updater.

Prochází všechny EANy z tabulky jiri_models_feed_item, ověří na Kaufland REST API
existenci id_unit a aktualizuje amount: stock=ANO -> amount=5, stock=NE -> amount=0.
"""

import os
import sys
import ssl
import json
import logging
import argparse
import certifi
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from kaufland_rest_api import KauflandAPIClient
from shared_db.session import session_scope
from shared_db.dao import get_all_jiri_feed_items, get_last_run, set_last_run

logger = logging.getLogger(__name__)

JOB_NAME = "jiri-models-stock-updater"

# stock ANO -> amount 5, stock NE -> amount 0
AMOUNT_IN_STOCK = 5
AMOUNT_OUT_OF_STOCK = 0


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
        system_certs_path = certifi.where()
        bundle_path = Path("cert_bundle.pem")

        with open(system_certs_path, 'r', encoding='utf-8') as f:
            system_certs = f.read()

        with open(cert_path, 'r', encoding='utf-8') as f:
            zscaler_cert = f.read()

        combined_certs = system_certs + "\n" + zscaler_cert

        with open(bundle_path, 'w', encoding='utf-8') as f:
            f.write(combined_certs)

        bundle_path_str = str(bundle_path.absolute())
        context = ssl.create_default_context(cafile=bundle_path_str)
        ssl._create_default_https_context = lambda: context
        os.environ['SSL_CERT_FILE'] = bundle_path_str
        os.environ['REQUESTS_CA_BUNDLE'] = bundle_path_str

        print(f"Používám SSL certifikát: {cert_file} (v bundle: {bundle_path})")

    except Exception as e:
        print(f"Varování: Nepodařilo se nastavit SSL certifikát: {e}")
        try:
            cert_path_str = str(cert_path.absolute())
            context = ssl.create_default_context(cafile=cert_path_str)
            ssl._create_default_https_context = lambda: context
            os.environ['SSL_CERT_FILE'] = cert_path_str
            os.environ['REQUESTS_CA_BUNDLE'] = cert_path_str
            print(f"Používám SSL certifikát: {cert_file} (bez bundle)")
        except Exception as e2:
            print(f"Varování: Nepodařilo se načíst certifikát: {e2}")


def fetch_id_unit_for_ean(
    client: KauflandAPIClient,
    ean: str,
    storefront: str = "cz"
) -> Optional[str]:
    """
    Získá id_unit pro EAN z Kaufland API.

    Returns:
        id_unit jako řetězec, nebo None pokud jednotka neexistuje.
    """
    response = client.get_units_by_ean(ean, storefront=storefront)

    if not isinstance(response, dict) or 'data' not in response:
        return None

    data = response['data']
    if not isinstance(data, list) or len(data) == 0:
        return None

    unit = data[0]
    if not isinstance(unit, dict) or 'id_unit' not in unit:
        return None

    return str(unit['id_unit'])


def resolve_id_units(
    client: KauflandAPIClient,
    feed_items: List[Dict[str, Any]],
    storefront: str = "cz"
) -> List[Dict[str, Any]]:
    """
    Pro každý feed item ověří existenci id_unit na Kaufland API.
    Vrací pouze položky, u kterých id_unit existuje (ean, stock, id_unit, amount).
    """
    resolved = []
    for i, item in enumerate(feed_items, 1):
        ean = item['ean']
        stock = item['stock']
        logger.info(f"Resolving EAN {i}/{len(feed_items)}: {ean}")

        try:
            id_unit = fetch_id_unit_for_ean(client, ean, storefront=storefront)
            if not id_unit:
                logger.warning(f"id_unit not found for EAN {ean}, skipping")
                continue

            amount = AMOUNT_IN_STOCK if stock.upper() == "ANO" else AMOUNT_OUT_OF_STOCK
            resolved.append({
                'ean': ean,
                'stock': stock,
                'id_unit': id_unit,
                'amount': amount,
            })
            logger.debug(f"EAN {ean} -> id_unit {id_unit}, amount={amount}")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                logger.error("Received 401 Unauthorized. Terminating.")
                sys.exit(1)
            logger.error(f"HTTP error for EAN {ean}: {e}")
        except Exception as e:
            logger.error(f"Error for EAN {ean}: {e}")

    return resolved


def create_bulk_payload(resolved: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Vytvoří payload pro POST /v2/units/bulk.
    Max 150 jednotek na request.
    """
    units = []
    seen = set()

    for item in resolved:
        id_unit = item['id_unit']
        id_unit_int = int(id_unit)
        if id_unit_int in seen:
            logger.warning(f"Skipping duplicate id_unit {id_unit_int} for EAN {item['ean']}")
            continue
        seen.add(id_unit_int)
        units.append({
            "id_unit": id_unit_int,
            "unit_data": {"amount": item['amount']},
        })

    if len(units) > 150:
        logger.warning(f"Payload has {len(units)} units; API allows max 150 per request. Will chunk.")
    return units


def update_units_bulk(
    client: KauflandAPIClient,
    payload: List[Dict[str, Any]],
    storefront: str = "cz"
) -> Dict[str, Any]:
    """Odešle bulk update na Kaufland API."""
    logger.info(f"Sending bulk update for {len(payload)} units (storefront={storefront})")
    response = client.post(
        endpoint="/v2/units/bulk",
        data=payload,
        params={"storefront": storefront}
    )
    response.raise_for_status()
    return response.json()


def update_job_checkpoint() -> None:
    """Aktualizuje job_state."""
    now = datetime.now(timezone.utc)
    logger.info(f"Updating job checkpoint: {JOB_NAME} at {now}")
    with session_scope() as session:
        set_last_run(session, JOB_NAME, now)
    logger.info("Job checkpoint updated successfully")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Jiri Models Feed Stock Updater')
    parser.add_argument('--local', action='store_true', help='Use local SSL (Zscaler Root CA)')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    if args.local:
        logger.info("Running in local mode - setting up SSL certificates")
        setup_ssl_certificates()

    try:
        client = KauflandAPIClient()
    except ValueError as e:
        logger.error(f"Failed to initialize Kaufland API client: {e}")
        raise

    try:
        logger.info(f"Starting cycle at {datetime.now(timezone.utc)}")

        with session_scope() as session:
            feed_items = get_all_jiri_feed_items(session)

        if not feed_items:
            logger.warning("No items in jiri_models_feed_item")
            update_job_checkpoint()
        else:
            logger.info(f"Found {len(feed_items)} items in jiri_models_feed_item")

            resolved = resolve_id_units(client, feed_items)
            if not resolved:
                logger.warning("No id_units resolved - nothing to update")
                update_job_checkpoint()
            else:
                payload = create_bulk_payload(resolved)
                max_per_request = 150
                total_updated = 0

                for i in range(0, len(payload), max_per_request):
                    chunk = payload[i:i + max_per_request]
                    logger.info(f"Processing chunk {i // max_per_request + 1} ({len(chunk)} units)")

                    try:
                        response = update_units_bulk(client, chunk)
                        if 'data' in response and isinstance(response['data'], list):
                            for item in response['data']:
                                if item.get('status_code') == 200:
                                    total_updated += 1
                                else:
                                    logger.warning(
                                        f"Unit {item.get('id_unit')} failed: {item.get('message', '')}"
                                    )
                        else:
                            total_updated += len(chunk)
                    except requests.HTTPError as e:
                        if e.response is not None and e.response.status_code == 401:
                            logger.error("401 Unauthorized. Terminating.")
                            raise
                        logger.error(f"HTTP error: {e}")
                        if e.response is not None:
                            try:
                                logger.error(f"Response: {json.dumps(e.response.json(), indent=2)}")
                            except Exception:
                                logger.error(f"Response text: {e.response.text}")
                        raise
                    except Exception as e:
                        logger.error(f"Error updating units: {e}", exc_info=True)
                        raise

                update_job_checkpoint()
                logger.info(f"Cycle completed: {total_updated} units updated")

    except Exception as e:
        logger.error(f"Error during cycle: {e}", exc_info=True)
        try:
            update_job_checkpoint()
        except Exception as ce:
            logger.error(f"Failed to update checkpoint: {ce}")
        raise
