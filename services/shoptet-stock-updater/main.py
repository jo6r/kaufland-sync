"""Main entry point for Kaufland Stock Updater."""

import os
import ssl
import json
import logging
import argparse
import certifi
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from shared_db.session import session_scope
from shared_db.dao import get_stock_changed_after, get_mapping_by_eans, get_last_run, set_last_run
from kaufland_rest_api import KauflandAPIClient
import requests

logger = logging.getLogger(__name__)

JOB_NAME = "shoptet-stock-updater"


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


def get_changed_stock(since_dt: Optional[datetime]) -> List[Dict[str, Any]]:
    """
    Get stock items changed since last run.
    
    Args:
        since_dt: Datetime to filter by (changed_at > since_dt), or None for all items
        
    Returns:
        List of dicts with keys: ean, qty
    """
    with session_scope() as session:
        if since_dt is None:
            # First run - get all items (use very old date)
            since_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
        
        stock_items = get_stock_changed_after(session, since_dt)
        logger.info(f"Found {len(stock_items)} stock items changed since {since_dt}")
        for item in stock_items:
            logger.info(f"  Changed EAN: {item.ean}, qty: {item.qty}")
        
        # Extract data from ORM objects while still in session scope
        # to avoid DetachedInstanceError
        result = []
        for item in stock_items:
            result.append({
                'ean': item.ean,
                'qty': item.qty
            })
        
        return result


def map_eans_to_id_units(eans: List[str]) -> Dict[str, str]:
    """
    Map EANs to id_unit from shoptet_unit_mapping table.

    Args:
        eans: List of EAN codes

    Returns:
        Dict mapping ean -> id_unit (only for EANs that have mappings)
    """
    if not eans:
        return {}

    with session_scope() as session:
        mappings = get_mapping_by_eans(session, eans)

        # Extract data from ORM objects while still in session scope
        # to avoid DetachedInstanceError
        # Filter only AVAILABLE mappings
        ean_to_id_unit = {}
        inactive_count = 0
        for mapping in mappings:
            if mapping.status == 'AVAILABLE':
                ean_to_id_unit[mapping.ean] = mapping.id_unit
            else:
                inactive_count += 1
                logger.debug(f"Skipping EAN {mapping.ean} - status is {mapping.status}, not AVAILABLE")

    if inactive_count > 0:
        logger.info(f"Mapped {len(ean_to_id_unit)}/{len(eans)} EANs to id_units ({inactive_count} mappings skipped due to non-AVAILABLE status)")
    else:
        logger.info(f"Mapped {len(ean_to_id_unit)}/{len(eans)} EANs to id_units")

    # Log missing mappings
    missing_eans = set(eans) - set(ean_to_id_unit.keys())
    if missing_eans:
        logger.warning(f"Missing id_unit mappings for {len(missing_eans)} EANs: {list(missing_eans)[:10]}...")

    return ean_to_id_unit


def create_bulk_payload(stock_items: List[Dict[str, Any]], ean_to_id_unit: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Create bulk payload for stock update API.

    According to Kaufland API documentation:
    - Endpoint: POST /units/bulk
    - Format: Array of objects with id_unit and unit_data
    - Maximum 150 units per request
    - No duplicate units allowed

    Args:
        stock_items: List of dicts with keys: ean, qty
        ean_to_id_unit: Dict mapping ean -> id_unit

    Returns:
        List of unit update objects for /units/bulk endpoint
    """
    units = []
    seen_id_units = set()  # Track duplicates

    for stock_item in stock_items:
        ean = stock_item['ean']
        id_unit = ean_to_id_unit.get(ean)

        if not id_unit:
            logger.debug(f"Skipping EAN {ean} - no id_unit mapping found")
            continue

        id_unit_int = int(id_unit)

        # Check for duplicates (API rejects duplicates)
        if id_unit_int in seen_id_units:
            logger.warning(f"Skipping duplicate id_unit {id_unit_int} for EAN {ean}")
            continue

        seen_id_units.add(id_unit_int)

        # Create unit update payload according to API documentation
        # Format: { "id_unit": int, "unit_data": { "amount": int } }
        unit_payload = {
            "id_unit": id_unit_int,
            "unit_data": {
                "amount": stock_item['qty']
            }
        }
        units.append(unit_payload)
    
    # Check limit (API allows max 150 units per request)
    if len(units) > 150:
        logger.warning(f"Payload contains {len(units)} units, but API allows maximum 150. Consider splitting the request.")
    
    logger.info(f"Created bulk payload with {len(units)} units")
    return units


def update_units_bulk(client: KauflandAPIClient, payload: List[Dict[str, Any]], storefront: str = "cz") -> Dict[str, Any]:
    """
    Update units in bulk via Kaufland API.
    
    Args:
        client: Kaufland API client
        payload: List of unit update objects
        storefront: Storefront code (default: 'cz')
        
    Returns:
        Response JSON as dictionary with structure: {"data": [...]}
        
    Raises:
        requests.HTTPError: If API request fails
    """
    logger.info(f"Sending bulk update request for {len(payload)} units (storefront: {storefront})")
    
    response = client.post(
        endpoint="/v2/units/bulk",
        data=payload,
        params={"storefront": storefront}
    )
    
    response.raise_for_status()
    return response.json()


def update_job_checkpoint() -> None:
    """
    Update job checkpoint in job_state.
    """
    now = datetime.now(timezone.utc)
    
    logger.info(f"Updating job checkpoint: {JOB_NAME} at {now}")
    
    with session_scope() as session:
        set_last_run(session, JOB_NAME, now)
    
    logger.info(f"Job checkpoint updated successfully")


if __name__ == '__main__':
    # Setup argument parser
    parser = argparse.ArgumentParser(description='Kaufland Stock Updater')
    parser.add_argument(
        '--local',
        action='store_true',
        help='Use local SSL certificate setup (Zscaler Root CA)'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Setup SSL certificates if running locally
    if args.local:
        logger.info("Running in local mode - setting up SSL certificates")
        setup_ssl_certificates()
        
    # Run update cycle
    try:
        logger.info(f"Starting stock update cycle at {datetime.now(timezone.utc)}")
        
        # Initialize Kaufland API client
        try:
            client = KauflandAPIClient()
        except ValueError as e:
            logger.error(f"Failed to initialize Kaufland API client: {e}")
            raise
        
        # Get last run timestamp
        with session_scope() as session:
            last_run_at = get_last_run(session, JOB_NAME)
        
        if last_run_at:
            logger.info(f"Last run was at {last_run_at}")
        else:
            logger.info("First run - processing all stock items")
        
        # Get changed stock items
        stock_items = get_changed_stock(last_run_at)
        
        if not stock_items:
            logger.info("No stock items changed since last run")
            # Update job checkpoint even if no items changed
            update_job_checkpoint()
        else:
            # Extract EANs from stock items
            eans = [item['ean'] for item in stock_items]
            
            # Map EANs to id_units from shoptet_unit_mapping
            ean_to_id_unit = map_eans_to_id_units(eans)

            if not ean_to_id_unit:
                logger.warning("No id_unit mappings found for any EANs - nothing to update")
                # Update job checkpoint even if no mappings
                update_job_checkpoint()
            else:
                # Create bulk payload
                payload = create_bulk_payload(stock_items, ean_to_id_unit)
                
                if not payload:
                    logger.warning("Bulk payload is empty - nothing to update")
                    # Update job checkpoint even if payload is empty
                    update_job_checkpoint()
                else:
                    # Split payload if it exceeds 150 units (API limit)
                    max_units_per_request = 150
                    total_updated = 0
                    
                    for i in range(0, len(payload), max_units_per_request):
                        chunk = payload[i:i + max_units_per_request]
                        logger.info(f"Processing chunk {i // max_units_per_request + 1} with {len(chunk)} units")
                        
                        try:
                            response = update_units_bulk(client, chunk)
                            
                            # Process response according to API documentation
                            # Response structure: {"data": [{"id_unit": int, "status_code": int, "unit": {...}, "message": str, "errors": [...]}]}
                            if 'data' in response and isinstance(response['data'], list):
                                success_count = 0
                                error_count = 0
                                
                                for item in response['data']:
                                    id_unit = item.get('id_unit')
                                    status_code = item.get('status_code')
                                    
                                    if status_code == 200:
                                        success_count += 1
                                        # Log successful update details
                                        if 'unit' in item:
                                            unit = item['unit']
                                            new_amount = unit.get('amount')
                                            logger.debug(f"Unit {id_unit} updated successfully: amount={new_amount}")
                                    else:
                                        error_count += 1
                                        # Log error details
                                        message = item.get('message', 'Unknown error')
                                        errors = item.get('errors', [])
                                        
                                        error_details = [f"{err.get('field', 'unknown')}: {err.get('message', '')}" for err in errors] if errors else []
                                        
                                        if error_details:
                                            logger.warning(f"Unit {id_unit} update failed (status_code {status_code}): {message}. Errors: {', '.join(error_details)}")
                                        else:
                                            logger.warning(f"Unit {id_unit} update failed (status_code {status_code}): {message}")
                                
                                logger.info(f"Chunk update completed: {success_count} successful, {error_count} errors out of {len(response['data'])} units")
                                total_updated += success_count
                            else:
                                logger.warning(f"Unexpected response format: {response}")
                                total_updated += len(chunk)  # Assume all updated if we can't parse response
                            
                        except requests.HTTPError as e:
                            # Check for 401 Unauthorized - terminate script
                            if e.response is not None and e.response.status_code == 401:
                                logger.error(f"Received 401 Unauthorized from API. Authentication failed. Terminating script.")
                                logger.error(f"Error details: {e}")
                                raise
                            # Handle other HTTP errors
                            logger.error(f"HTTP error updating units: {e}")
                            if e.response is not None:
                                try:
                                    error_body = e.response.json()
                                    logger.error(f"Error response: {json.dumps(error_body, indent=2)}")
                                except:
                                    logger.error(f"Error response text: {e.response.text}")
                            raise
                        except Exception as e:
                            logger.error(f"Error updating units: {e}", exc_info=True)
                            raise
                    
                    # Update job checkpoint on success
                    update_job_checkpoint()
                    
                    logger.info(f"Stock update cycle completed successfully: {total_updated} units updated")
        
    except Exception as e:
        logger.error(f"Error during stock update cycle: {e}", exc_info=True)