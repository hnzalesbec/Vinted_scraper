import requests
import time
import json
import logging
from datetime import datetime, timezone 
from utils import (
    get_random_user_agent, 
    exponential_backoff_sleep, 
    get_api_headers,
    build_api_params_from_url 
)

logger = logging.getLogger(__name__)
MAX_RETRIES = 5

def get_vinted_session(manual_cookie: str = None, proxies: dict = None):
    session = requests.Session()
    initial_ua = get_random_user_agent()
    session.headers.update({"User-Agent": initial_ua})

    if proxies:
        session.proxies.update(proxies)
        logger.info(f"Session bude používat proxy: {list(proxies.keys())}")

    if manual_cookie:
        session.headers.update({"Cookie": manual_cookie})
        logger.info("Manuální cookie byla nastavena pro session.")
    
    try:
        from utils import DEFAULT_VINTED_BASE_URL as WARMUP_BASE_URL
    except ImportError: 
        WARMUP_BASE_URL = "https://www.vinted.cz" 
        logger.warning(f"Nepodařilo se importovat DEFAULT_VINTED_BASE_URL z utils, používám {WARMUP_BASE_URL}")

    logger.info(f"Inicializace Vinted session s User-Agent: {initial_ua} pro {WARMUP_BASE_URL}")

    try:
        warmup_headers = {
            "User-Agent": initial_ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
            "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1",
        }
        
        logger.debug(f"GET {WARMUP_BASE_URL} (hlavní stránka)...")
        response_main = session.get(WARMUP_BASE_URL, headers=warmup_headers, timeout=30)
        response_main.raise_for_status()
        logger.info(f"Hlavní stránka OK ({response_main.status_code}). Cookies v session: {bool(session.cookies)}")

        catalog_url = f"{WARMUP_BASE_URL}/catalog"
        warmup_headers_catalog = warmup_headers.copy()
        warmup_headers_catalog["Referer"] = WARMUP_BASE_URL
        logger.debug(f"GET {catalog_url} (katalog)...")
        response_catalog = session.get(catalog_url, headers=warmup_headers_catalog, timeout=30)
        response_catalog.raise_for_status()
        logger.info(f"Katalog OK ({response_catalog.status_code}). Cookies v session: {bool(session.cookies)}")
        
        if not manual_cookie and session.cookies: logger.info("Automaticky získané cookies pro session.")
        elif not manual_cookie and not session.cookies: logger.warning("Nepodařilo se automaticky získat cookies a nebyla poskytnuta manuální cookie.")
        elif manual_cookie and not session.cookies and not manual_cookie_in_session_headers(session, manual_cookie):
             logger.warning("Manuální cookie byla poskytnuta, ale nezdá se být aktivní v session po zahřívacích requestech.")
        elif manual_cookie: logger.info("Použita manuální cookie.")

    except requests.exceptions.RequestException as e:
        logger.error(f"Kritická chyba při inicializaci/zahřívání Vinted session: {e}", exc_info=False)
        return None
        
    logger.info("Vinted session připravena.")
    return session

def manual_cookie_in_session_headers(session, manual_cookie_value):
    if not manual_cookie_value: return False
    session_cookie_header = session.headers.get("Cookie", "")
    return manual_cookie_value in session_cookie_header

def extract_item_details(item_data_raw, base_url_for_item_url) -> dict: 
    title = item_data_raw.get('title', 'N/A')
    item_id_for_log = item_data_raw.get('id', 'N/A')
    price_amount_str, currency_str = "N/A", item_data_raw.get('currency', 'CZK')
    price_obj = item_data_raw.get('price')
    if isinstance(price_obj, str): price_amount_str = price_obj
    elif isinstance(price_obj, dict):
        price_amount_str = price_obj.get('amount', 'N/A')
        currency_str = price_obj.get('currency', price_obj.get('currency_code', currency_str))
    
    try: price_numeric = float(price_amount_str)
    except (ValueError, TypeError): price_numeric = None

    status = item_data_raw.get('status', 'N/A')
    size = item_data_raw.get('size_title', 'N/A')
    brand = item_data_raw.get('brand_title', 'N/A')
    
    photo_url = None
    vinted_item_timestamp = None 
    timestamp_source = "Nenalezen"
    
    photo_data = item_data_raw.get('photo')
    if isinstance(photo_data, dict):
        photo_url = photo_data.get('url') # Získáme hlavní URL fotky
        
        # VŽDY se pokusíme získat timestamp z high_resolution, pokud existuje
        high_res_photo = photo_data.get('high_resolution')
        if isinstance(high_res_photo, dict):
            if not photo_url: # Pokud hlavní URL nebylo, vezmeme ho z high-res
                photo_url = high_res_photo.get('url')
            
            if high_res_photo.get("timestamp") is not None:
                try:
                    vinted_item_timestamp = int(high_res_photo.get("timestamp"))
                    timestamp_source = "photo.high_resolution.timestamp"
                except ValueError:
                    logger.warning(f"Item ID {item_id_for_log}: Neplatný formát photo.high_resolution.timestamp: {high_res_photo.get('timestamp')}")

    if vinted_item_timestamp is None and item_data_raw.get("created_at_ts") is not None:
        try:
            vinted_item_timestamp = int(item_data_raw.get("created_at_ts"))
            timestamp_source = "created_at_ts"
        except ValueError:
            logger.warning(f"Item ID {item_id_for_log}: Neplatný formát created_at_ts: {item_data_raw.get('created_at_ts')}")

    if vinted_item_timestamp is None:
        created_at_iso = item_data_raw.get("created_at") 
        if created_at_iso and isinstance(created_at_iso, str):
            try:
                dt_obj = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
                vinted_item_timestamp = int(dt_obj.timestamp())
                timestamp_source = f"created_at (ISO: {created_at_iso})"
            except ValueError:
                logger.debug(f"Item ID {item_id_for_log}: Nepodařilo se parsovat ISO timestamp z 'created_at': {created_at_iso}")
        elif created_at_iso:
             logger.debug(f"Item ID {item_id_for_log}: Pole 'created_at' není string: {created_at_iso} (typ: {type(created_at_iso)})")

    if vinted_item_timestamp is None:
        logger.warning(f"Item ID {item_id_for_log}: PLATNÝ TIMESTAMP NENALEZEN! Titulek: {title[:30]}. Bude řazena s TS 0.")
        vinted_item_timestamp = 0 
        timestamp_source = "Fallback na 0"
        if logger.getEffectiveLevel() <= logging.DEBUG:
            logger.debug(f"Item ID {item_id_for_log}: RAW DATA pro položku s TS=0 (po všech pokusech):\n{json.dumps(item_data_raw, indent=2, ensure_ascii=False)}")
    
    logger.debug(f"Item ID {item_id_for_log}: Finální TS = {vinted_item_timestamp}, Zdroj = '{timestamp_source}', Titulek = {title[:30]}")

    url_path = item_data_raw.get('url', '')
    full_url = base_url_for_item_url + url_path if url_path and not url_path.startswith("http") else url_path or "N/A"

    return {
        "id": item_id_for_log, "title": title, "price_numeric": price_numeric,
        "price_str": price_amount_str, "currency": currency_str, "status": status,
        "size": size, "brand": brand, "url": full_url, "photo_url": photo_url,
        "vinted_item_timestamp": vinted_item_timestamp , 
        "_timestamp_source": timestamp_source, 
    }

def format_item_for_display(item_details_dict: dict) -> str:
    title = item_details_dict.get('title', 'N/A')
    price_numeric = item_details_dict.get('price_numeric')
    currency = item_details_dict.get('currency', 'CZK')
    
    if price_numeric is not None:
        formatted_price = f"{price_numeric:,.0f}".replace(",", " ") + f" {currency}"
    else:
        formatted_price = f"{item_details_dict.get('price_str', 'N/A')} {currency}"

    status = item_details_dict.get('status', 'N/A')
    size = item_details_dict.get('size', 'N/A')
    brand = item_details_dict.get('brand', 'N/A')
    full_url = item_details_dict.get('url', 'N/A')
    
    details_parts = []
    if status and status != 'N/A': details_parts.append(f"Stav: {status}")
    if size and size != 'N/A': details_parts.append(f"Velikost: {size}")
    if brand and brand != 'N/A' and brand.lower() not in title.lower():
        details_parts.append(f"Značka: {brand}")
    
    details_output_str = " – ".join(filter(None, details_parts))
    return f"[🆕] {title} – {formatted_price} – {details_output_str}\n     {full_url}"

def check_keywords(title_to_check: str, profile_filters: dict) -> bool:
    must_have_config = profile_filters.get("must_have_keywords", [])
    exclude_keywords_list = profile_filters.get("exclude_keywords", [])
    case_sensitive = profile_filters.get("keywords_case_sensitive", False)

    if not case_sensitive:
        title_to_check = title_to_check.lower()

    if exclude_keywords_list:
        for ex_keyword_orig in exclude_keywords_list:
            ex_keyword = str(ex_keyword_orig) 
            processed_ex_keyword = ex_keyword if case_sensitive else ex_keyword.lower()
            if processed_ex_keyword.strip() and processed_ex_keyword.strip() in title_to_check:
                logger.debug(f"Položka vyloučena kvůli slovu '{ex_keyword_orig}': {title_to_check[:50]}...")
                return False

    if must_have_config:
        if not isinstance(must_have_config, list):
            logger.warning(f"Neplatný formát must_have_keywords: {must_have_config}.")
            return True 
        if not must_have_config: return True

        if all(isinstance(item, list) for item in must_have_config): 
            for or_group in must_have_config:
                if not isinstance(or_group, list) or not or_group: continue 
                found_in_or_group = False
                for keyword_orig in or_group:
                    keyword = str(keyword_orig)
                    processed_keyword = keyword if case_sensitive else keyword.lower()
                    if processed_keyword.strip() and processed_keyword.strip() in title_to_check:
                        found_in_or_group = True; break 
                if not found_in_or_group:
                    logger.debug(f"Položka nesplnila OR skupinu {or_group} v must_have_keywords: {title_to_check[:50]}...")
                    return False
            return True 
        elif all(isinstance(item, str) for item in must_have_config): 
            for keyword_orig in must_have_config:
                keyword = str(keyword_orig)
                processed_keyword = keyword if case_sensitive else keyword.lower()
                if processed_keyword.strip() and processed_keyword.strip() not in title_to_check:
                    logger.debug(f"Položka nesplnila AND klíčové slovo '{keyword_orig}' v must_have_keywords: {title_to_check[:50]}...")
                    return False
            return True 
        else: 
            logger.warning(f"Neplatný smíšený formát must_have_keywords: {must_have_config}.")
            return True 
    return True


def fetch_new_items(session, profile_config):
    profile_name = profile_config["name"]
    vinted_url_from_profile = profile_config.get("vinted_url", "")
    local_filters_def = profile_config.get("filters", {}) 
    seen_ids = profile_config.get("seen_ids", set())
    
    if not vinted_url_from_profile:
        logger.warning(f"Profil '{profile_name}': Chybí 'vinted_url'. Přeskakuji.")
        return [], [], set()

    api_endpoint, api_params, base_url_for_req, original_url_path_query = build_api_params_from_url(vinted_url_from_profile, profile_name)
        
    logger.info(f"Profil '{profile_name}': Stahuji data z API '{api_endpoint}' s parametry: {json.dumps(api_params)}")

    new_items_strings, new_items_data_list, ids_to_mark_as_seen = [], [], set()
    current_session_ua = session.headers.get("User-Agent", get_random_user_agent())

    for attempt in range(MAX_RETRIES):
        api_request_headers = get_api_headers(
            session_ua=current_session_ua,
            origin_url=base_url_for_req,
            referer_url=base_url_for_req + original_url_path_query
        )
        logger.debug(f"Profil '{profile_name}' Pokus {attempt + 1}/{MAX_RETRIES} s UA: {current_session_ua}, Origin: {base_url_for_req}, Referer: {api_request_headers['Referer']}")
        
        response = None
        try:
            response = session.get(api_endpoint, params=api_params, headers=api_request_headers, timeout=35)
            
            if response.status_code in [401, 403, 429, 500, 502, 503, 504]:
                context_msg = f"Profil '{profile_name}' API vrátilo {response.status_code} (Pokus {attempt + 1})"
                logger.warning(context_msg)
                logger.debug(f"Obsah odpovědi při chybě ({response.status_code}): {response.text[:300] if response else 'N/A'}")
                if attempt < MAX_RETRIES - 1:
                    base_delay = 15 if response.status_code in [401, 403] else 7
                    exponential_backoff_sleep(attempt, base_delay=base_delay, context=context_msg)
                    new_ua = get_random_user_agent()
                    if new_ua != current_session_ua:
                        session.headers.update({"User-Agent": new_ua}); current_session_ua = new_ua
                        logger.info(f"Profil '{profile_name}': User-Agent pro session změněn na: {new_ua}")
                    continue
                else:
                    logger.error(f"Profil '{profile_name}': Nepodařilo se načíst data po {MAX_RETRIES} pokusech (status {response.status_code}).")
                    return [], [], set()

            response.raise_for_status()
            data = response.json()
            api_items_raw = data.get("items", [])
            
            if not api_items_raw:
                logger.info(f"Profil '{profile_name}': API nevrátilo žádné položky pro dané filtry.")
                return [], [], set()

            logger.info(f"Profil '{profile_name}': Nalezeno {len(api_items_raw)} položek z API. Zpracovávám a řadím...")

            processed_api_items_with_details = []
            for item_data_raw_loop in api_items_raw:
                item_details_loop = extract_item_details(item_data_raw_loop, base_url_for_req)
                if item_details_loop.get("id"):
                    processed_api_items_with_details.append(item_details_loop)
            
            if logger.getEffectiveLevel() <= logging.DEBUG and processed_api_items_with_details:
                logger.debug(f"Profil '{profile_name}': Prvních 5 položek PŘED lokálním řazením (ID: TS - Titulek):")
                for i, item_debug in enumerate(processed_api_items_with_details[:5]):
                    logger.debug(f"  {i+1}. {item_debug.get('id')}: {item_debug.get('vinted_item_timestamp')} ({item_debug.get('_timestamp_source')}) - {item_debug.get('title', '')[:40]}")

            processed_api_items_with_details.sort(key=lambda x: x.get("vinted_item_timestamp", 0), reverse=True)
            
            if logger.getEffectiveLevel() <= logging.DEBUG and processed_api_items_with_details:
                logger.debug(f"Profil '{profile_name}': Prvních 5 položek PO lokálním řazení (ID: TS - Titulek):")
                for i, item_debug in enumerate(processed_api_items_with_details[:5]):
                    logger.debug(f"  {i+1}. {item_debug.get('id')}: {item_debug.get('vinted_item_timestamp')} ({item_debug.get('_timestamp_source')}) - {item_debug.get('title', '')[:40]}")

            for item_details_sorted in processed_api_items_with_details:
                item_id = item_details_sorted.get("id")
                if item_id not in seen_ids:
                    title_original = item_details_sorted.get('title', '')
                    if not check_keywords(title_original, local_filters_def): 
                        continue 
                    
                    new_items_strings.append(format_item_for_display(item_details_sorted))
                    new_items_data_list.append(item_details_sorted) 
                    ids_to_mark_as_seen.add(item_id)
            
            if new_items_data_list:
                 logger.info(f"Profil '{profile_name}': Nalezeno {len(new_items_data_list)} nových položek po lokálním seřazení a filtrování.")
                 for item_str in new_items_strings: 
                    logger.info(item_str)
            else:
                 logger.info(f"Profil '{profile_name}': Žádné NOVÉ položky (z {len(api_items_raw)} celkem) po lokálním seřazení a filtrování klíčových slov.")
            
            return new_items_strings, new_items_data_list, ids_to_mark_as_seen

        except requests.exceptions.Timeout as e:
            logger.warning(f"Profil '{profile_name}' Timeout (Pokus {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                exponential_backoff_sleep(attempt, base_delay=20, context=f"Timeout pro '{profile_name}'")
                new_ua = get_random_user_agent(); session.headers.update({"User-Agent": new_ua}); current_session_ua = new_ua
                logger.info(f"Profil '{profile_name}': User-Agent změněn na {new_ua} po timeoutu.")
                continue
            logger.error(f"Profil '{profile_name}': Nepodařilo se načíst data po {MAX_RETRIES} pokusech (timeout).")
            return [], [], set()
        except requests.exceptions.SSLError as e:
            logger.error(f"Profil '{profile_name}' SSL Chyba (Pokus {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                exponential_backoff_sleep(attempt, base_delay=30, context=f"SSL Chyba pro '{profile_name}'")
                continue
            logger.error(f"Profil '{profile_name}': Nepodařilo se načíst data po {MAX_RETRIES} pokusech (SSL chyba).")
            return [], [], set()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Profil '{profile_name}' Obecná síťová chyba (Pokus {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                 exponential_backoff_sleep(attempt, base_delay=10, context=f"Síťová chyba pro '{profile_name}'")
                 new_ua = get_random_user_agent(); session.headers.update({"User-Agent": new_ua}); current_session_ua = new_ua
                 logger.info(f"Profil '{profile_name}': User-Agent změněn na {new_ua} po síťové chybě.")
                 continue
            logger.error(f"Profil '{profile_name}': Nepodařilo se načíst data po {MAX_RETRIES} pokusech (síťová chyba).")
            return [], [], set()
        except json.JSONDecodeError as e:
            logger.error(f"Profil '{profile_name}': Chyba při parsování JSON odpovědi: {e}")
            error_response_text = response.text if response else "Žádná odpověď od serveru."
            logger.debug(f"   Text odpovědi (prvních 500 znaků): {error_response_text[:500]}...")
            return [], [], set() 

    logger.error(f"Profil '{profile_name}': Nepodařilo se zpracovat po všech {MAX_RETRIES} pokusech.")
    return [], [], set()