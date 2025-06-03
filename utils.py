import random
import time
import re 
import logging
from urllib.parse import urlparse, parse_qs, urlunparse

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

DEFAULT_VINTED_BASE_URL = "https://www.vinted.cz"
# API URL se nyní určuje dynamicky z profilového URL

BRAND_MAP = {} 
SIZE_MAP = {}
STATUS_MAP = {}
CATALOG_MAP = {}
COLOR_MAP = {}
MATERIAL_MAP = {}
COUNTRY_MAP = {}

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def get_api_headers(session_ua=None, origin_url=DEFAULT_VINTED_BASE_URL, referer_url=None):
    ua = session_ua if session_ua else get_random_user_agent()
    headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8,sk;q=0.7,pl;q=0.6,de;q=0.5",
        "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
        "Origin": origin_url, 
        "Referer": referer_url if referer_url else f"{origin_url}/catalog",
    }
    try:
        if "Chrome/" in ua or "Edg/" in ua:
            browser_name = "Google Chrome" if "Chrome/" in ua and "Edg/" not in ua else "Microsoft Edge"
            version_match = re.search(r"(Chrome|Edg)/(\d+)", ua)
            if version_match:
                major_version = version_match.group(2)
                brands_list = [
                    {"brand": "Not_A Brand", "version": "8"},
                    {"brand": "Chromium", "version": major_version},
                    {"brand": browser_name, "version": major_version}
                ]
                if "Edg/" in ua:
                     brands_list = [
                        {"brand": "Not_A Brand", "version": "8"},
                        {"brand": "Chromium", "version": major_version},
                        {"brand": "Microsoft Edge", "version": major_version.split('.')[0]}
                    ]
                headers["Sec-CH-UA"] = ", ".join([f'"{b["brand"]}";v="{b["version"]}"' for b in brands_list])
        
        headers["Sec-CH-UA-Mobile"] = "?0"
        if "Windows" in ua: platform = '"Windows"'
        elif "Macintosh" in ua or "Mac OS X" in ua : platform = '"macOS"'
        elif "Linux" in ua: platform = '"Linux"'
        elif "Android" in ua: platform = '"Android"'; headers["Sec-CH-UA-Mobile"] = "?1"
        elif "iPhone" in ua or "iPad" in ua: platform = '"iOS"'; headers["Sec-CH-UA-Mobile"] = "?1"
        else: platform = '"Unknown"'
        headers["Sec-CH-UA-Platform"] = platform
    except Exception as e:
        logger.debug(f"Chyba při generování Sec-CH-UA hlaviček: {e}", exc_info=False)
        if "Sec-CH-UA" in headers: del headers["Sec-CH-UA"]
        if "Sec-CH-UA-Mobile" in headers: del headers["Sec-CH-UA-Mobile"]
        if "Sec-CH-UA-Platform" in headers: del headers["Sec-CH-UA-Platform"]
    return headers

def build_api_params_from_url(vinted_url: str, profile_name: str) -> tuple[str, dict, str, str]:
    if not vinted_url or not vinted_url.startswith("http"):
        default_api_url = f"{DEFAULT_VINTED_BASE_URL}/api/v2/catalog/items"
        logger.error(f"(Profil: '{profile_name}') Neplatné Vinted URL: '{vinted_url}'. Používám: {default_api_url}")
        return default_api_url, {"order": "newest_first", "per_page": "96"}, DEFAULT_VINTED_BASE_URL, "/catalog"

    try:
        parsed_url = urlparse(vinted_url)
        query_params_raw = parse_qs(parsed_url.query, keep_blank_values=True)
        
        base_url_for_origin_referer = f"{parsed_url.scheme}://{parsed_url.netloc}"
        api_endpoint = f"{base_url_for_origin_referer}/api/v2/catalog/items"
        original_parsed_url_path_query = urlunparse(('', '', parsed_url.path, '', parsed_url.query, ''))

        api_params = {}
        for key, value_list in query_params_raw.items():
            processed_key = key[:-2] if key.endswith("[]") else key
            if value_list:
                 api_params[processed_key] = ",".join(value_list)

        api_params.setdefault("order", "newest_first")
        api_params.setdefault("per_page", "96")
        
        params_to_remove = ["search_id", "time", "page"] 
        for p_rem in params_to_remove:
            if p_rem in api_params:
                del api_params[p_rem]
        
        logger.debug(f"(Profil: '{profile_name}') Parametry z URL '{vinted_url}': {api_params}")
        return api_endpoint, api_params, base_url_for_origin_referer, original_parsed_url_path_query

    except Exception as e:
        default_api_url = f"{DEFAULT_VINTED_BASE_URL}/api/v2/catalog/items"
        logger.error(f"(Profil: '{profile_name}') Chyba parsování URL '{vinted_url}': {e}. Používám: {default_api_url}", exc_info=True)
        return default_api_url, {"order": "newest_first", "per_page": "96"}, DEFAULT_VINTED_BASE_URL, "/catalog"

def exponential_backoff_sleep(attempt, base_delay=4, max_delay=240, context="API"):
    delay = min(max_delay, base_delay * (1.8 ** attempt)) + random.uniform(0.5, 2.0)
    logger.info(f"    ⏳ {context} chyba/omezení. Opakuji pokus za {delay:.2f} sekund (pokus č. {attempt + 1})...")
    time.sleep(delay)