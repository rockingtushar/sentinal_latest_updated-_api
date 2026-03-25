import os
import requests

def get_access_token():
    base_url = os.getenv("SENTINELHUB_BASE_URL", "https://services.sentinel-hub.com")
    client_id = os.getenv("SENTINELHUB_CLIENT_ID")
    client_secret = os.getenv("SENTINELHUB_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise RuntimeError("Missing Sentinel Hub credentials in .env")

    url = f"{base_url}/oauth/token"
    resp = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        },
        timeout=60
    )
    resp.raise_for_status()
    return resp.json()["access_token"]
