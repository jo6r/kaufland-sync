"""Main entry point for Kaufland Unit Mapper."""

import os
import sys
import ssl
import logging
import argparse
import certifi
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from kaufland_rest_api import KauflandAPIClient
from shared_db.session import session_scope
from shared_db.dao import get_all_eans, bulk_upsert_mapping, set_last_run

logger = logging.getLogger(__name__)

JOB_NAME = "shoptet-unit-mapper"


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


def fetch_unit_info_for_ean(
    client: KauflandAPIClient,
    ean: str,
    storefront: str = "cz"
) -> Optional[Dict[str, Any]]:
    """
    Fetch id_unit and status for a given EAN from Kaufland API.

    Args:
        client: Kaufland API client
        ean: EAN code
        storefront: Storefront code (default: 'cz')

    Returns:
        Dict with keys 'id_unit' and 'status' if found, None otherwise

    Raises:
        requests.RequestException: If API request fails
    """
    response = client.get(
        endpoint="/v2/units",
        params={"storefront": storefront, "ean": ean}
    )
    response.raise_for_status()
    response = response.json()
    
    # Response structure:
    # {
    #   "data": [
    #     {
    #       "id_unit": 391018568513,
    #       "status": "AVAILABLE",
    #       ...
    #     }
    #   ],
    #   "pagination": {...}
    # }
    
    if not isinstance(response, dict):
        logger.warning(f"Unexpected response type for EAN {ean}. Expected dict, got {type(response)}")
        return None
    
    if 'data' not in response:
        logger.warning(f"No 'data' field in response for EAN {ean}")
        return None
    
    data = response['data']
    if not isinstance(data, list):
        logger.warning(f"'data' is not a list for EAN {ean}")
        return None
    
    if len(data) == 0:
        logger.info(f"No units found for EAN {ean} (empty data list)")
        return None
    
    # Get first unit from data
    unit = data[0]
    if not isinstance(unit, dict):
        logger.warning(f"Unit is not a dict for EAN {ean}")
        return None
    
    # Extract id_unit and status from API response
    if 'id_unit' not in unit:
        logger.warning(f"No 'id_unit' field in unit for EAN {ean}")
        return None

    id_unit = str(unit['id_unit'])
    api_status = unit.get('status', 'UNKNOWN')

    return {
        'id_unit': id_unit,
        'status': api_status
    }


def map_eans_to_unit_mappings(
    client: KauflandAPIClient,
    eans: List[str],
    storefront: str = "cz"
) -> List[Dict[str, Any]]:
    """
    Map EANs to unit mappings (id_unit) by calling Kaufland API.

    Args:
        client: Kaufland API client
        eans: List of EAN codes
        storefront: Storefront code (default: 'cz')

    Returns:
        List of dicts with keys: ean, id_unit, status
        Only includes mappings where id_unit was found (id_unit is required in DB)
    """
    mappings = []
    fetched_at = datetime.now(timezone.utc)
    
    for i, ean in enumerate(eans, 1):
        logger.info(f"Processing EAN {i}/{len(eans)}: {ean}")
        
        try:
            unit_info = fetch_unit_info_for_ean(client, ean, storefront=storefront)
            
            if unit_info:
                mappings.append({
                    'ean': ean,
                    'id_unit': unit_info['id_unit'],
                    'status': unit_info['status']
                })
                logger.info(f"Mapped EAN {ean} -> id_unit {unit_info['id_unit']}, status {unit_info['status']}")
            else:
                # Skip EAN if not found
                logger.warning(f"Could not find id_unit for EAN {ean}, skipping")
                continue
        except requests.HTTPError as e:
            # Check for 401 Unauthorized - terminate script
            if e.response is not None and e.response.status_code == 401:
                logger.error(f"Received 401 Unauthorized from API. Authentication failed. Terminating script.")
                logger.error(f"Error details: {e}")
                sys.exit(1)
            # Handle other HTTP errors
            logger.error(f"HTTP error processing EAN {ean}: {e}")
        except Exception as e:
            # Handle other API errors
            logger.error(f"Error processing EAN {ean}: {e}")
    
    return mappings


def save_mappings(mappings: List[Dict[str, Any]]) -> None:
    """
    Save mappings to database.

    Args:
        mappings: List of dicts with keys: ean, id_unit, status
    """
    if not mappings:
        logger.warning("No mappings to save")
        return
    
    logger.info(f"Saving {len(mappings)} mappings to database")
    
    with session_scope() as session:
        bulk_upsert_mapping(session, mappings)
    
    logger.info(f"Successfully saved {len(mappings)} mappings")


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
    parser = argparse.ArgumentParser(description='Kaufland Unit Mapper')
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
    
    # Initialize Kaufland API client
    try:
        client = KauflandAPIClient()
    except ValueError as e:
        logger.error(f"Failed to initialize Kaufland API client: {e}")
        raise
    
    # Run mapping cycle
    try:
        logger.info(f"Starting mapping cycle at {datetime.now(timezone.utc)}")
        
        # Get all EANs from shoptet_stock
        logger.info("Fetching EANs from shoptet_stock")
        with session_scope() as session:
            eans = get_all_eans(session)
        
        if not eans:
            logger.warning("No EANs found in shoptet_stock")
            # Update job checkpoint even if no EANs found
            update_job_checkpoint()
        else:
            logger.info(f"Found {len(eans)} EANs to process")
            
            # Map EANs to unit mappings (id_unit)
            mappings = map_eans_to_unit_mappings(client, eans)
            
            # Save mappings to database
            save_mappings(mappings)
            
            # Update job checkpoint
            update_job_checkpoint()
            
            logger.info(f"Mapping cycle completed successfully: {len(mappings)} mappings processed")
            
    except Exception as e:
        logger.error(f"Error during mapping cycle: {e}", exc_info=True)
        # Update job checkpoint even on error (to track last attempt)
        try:
            update_job_checkpoint()
        except Exception as checkpoint_error:
            logger.error(f"Failed to update job checkpoint: {checkpoint_error}")
        raise
