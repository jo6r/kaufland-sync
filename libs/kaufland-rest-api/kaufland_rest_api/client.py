"""Kaufland REST API client."""

import os
import time
import hmac
import hashlib
import requests
from typing import Optional, Dict, Any


class KauflandAPIClient:
    """Client for Kaufland REST API."""
    
    def __init__(
        self,
        client_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        storefront: Optional[str] = None
    ):
        """
        Initialize Kaufland API client.
        
        Args:
            client_key: Client key (or from KAUFLAND_CLIENT_KEY env var)
            secret_key: Secret key (or from KAUFLAND_SECRET_KEY env var)
            base_url: Base URL for API (or from KAUFLAND_BASE_URL env var)
            storefront: Storefront code (or from KAUFLAND_STOREFRONT env var, default: 'cz')
        """
        self.client_key = client_key or os.getenv('KAUFLAND_CLIENT_KEY')
        self.secret_key = secret_key or os.getenv('KAUFLAND_SECRET_KEY')
        self.base_url = base_url or os.getenv('KAUFLAND_BASE_URL')
        self.storefront = storefront or os.getenv('KAUFLAND_STOREFRONT', 'cz')
        
        if not self.client_key:
            raise ValueError("KAUFLAND_CLIENT_KEY must be provided or set as environment variable")
        if not self.secret_key:
            raise ValueError("KAUFLAND_SECRET_KEY must be provided or set as environment variable")
    
    def _sign_request(self, method: str, uri: str, body: str, timestamp: int) -> str:
        """
        Generate HMAC SHA-256 signature for the request.
        
        Args:
            method: HTTP method (e.g., 'GET', 'POST')
            uri: Full URI including https://
            body: Request body (empty string for GET requests)
            timestamp: Unix timestamp in seconds
        
        Returns:
            Hex-encoded HMAC SHA-256 signature
        """
        # Concatenate method, uri, body, and timestamp separated by newlines
        string_to_sign = "\n".join([
            method.upper(),
            uri,
            body if body else "",
            str(timestamp)
        ])
        
        # Generate HMAC SHA-256 signature
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return signature
    
    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        """
        Make a signed GET request to Kaufland API.
        
        Args:
            endpoint: API endpoint path (e.g., '/v2/units')
            params: Query parameters
            
        Returns:
            requests.Response object
        """
        # Build full URI
        uri = f"{self.base_url}{endpoint}"
        if params:
            # Build query string
            query_parts = []
            for key, value in params.items():
                if value is not None:
                    query_parts.append(f"{key}={value}")
            if query_parts:
                uri += "?" + "&".join(query_parts)
        
        # Get current Unix timestamp
        timestamp = int(time.time())
        
        # Generate signature
        signature = self._sign_request(
            method="GET",
            uri=uri,
            body="",
            timestamp=timestamp
        )
        
        # Prepare headers
        headers = {
            "Accept": "application/json",
            "Shop-Client-Key": self.client_key,
            "Shop-Timestamp": str(timestamp),
            "Shop-Signature": signature,
            "User-Agent": "mamitocz_development"
        }
        
        # Make the request
        response = requests.get(
            url=uri,
            headers=headers
        )
        
        return response
    
    def post(self, endpoint: str, data: Any, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        """
        Make a signed POST request to Kaufland API.
        
        Args:
            endpoint: API endpoint path (e.g., '/v2/units/bulk')
            data: Request body data (will be JSON encoded)
            params: Query parameters (optional)
            
        Returns:
            requests.Response object
        """
        import json
        
        # Build full URI
        uri = f"{self.base_url}{endpoint}"
        if params:
            # Build query string
            query_parts = []
            for key, value in params.items():
                if value is not None:
                    query_parts.append(f"{key}={value}")
            if query_parts:
                uri += "?" + "&".join(query_parts)
        
        # Encode body to JSON
        body = json.dumps(data) if data else ""
        
        # Get current Unix timestamp
        timestamp = int(time.time())
        
        # Generate signature
        signature = self._sign_request(
            method="POST",
            uri=uri,
            body=body,
            timestamp=timestamp
        )
        
        # Prepare headers
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Shop-Client-Key": self.client_key,
            "Shop-Timestamp": str(timestamp),
            "Shop-Signature": signature,
            "User-Agent": "mamitocz_development"
        }
        
        # Make the request
        response = requests.post(
            url=uri,
            headers=headers,
            data=body
        )
        
        return response