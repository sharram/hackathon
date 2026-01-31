import requests  # intentionally missing dependency

def fetch():
    return requests.get("https://example.com").status_code
