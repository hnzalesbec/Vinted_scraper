import time
import random
import sys
import logging
import signal
import json 
import os   
import datetime

from profile_manager import load_profiles, save_profiles_state, PROFILES_FILENAME
from scraper import fetch_new_items, get_vinted_session 

DEFAULT_SETTINGS = {
    "manual_cookie": "", "proxies_config": None, "main_loop_sleep_seconds": 300,
    "profile_sleep_min": 25, "profile_sleep_max": 55, "cycles_before_session_refresh": 10,
    "cycles_before_profiles_save": 1, "log_level": "INFO" 
}
SCRAPER_SETTINGS_FILENAME = "scraper_settings.json"
NEW_FINDS_FILENAME = "new_finds.jsonl" 
SCRAPER_LOG_FILENAME = "scraper.log"
STATUS_FILENAME = "scraper_current_status.txt" # Nový soubor pro status

_temp_settings_for_log_level = DEFAULT_SETTINGS.copy()
if os.path.exists(SCRAPER_SETTINGS_FILENAME):
    try:
        with open(SCRAPER_SETTINGS_FILENAME, 'r', encoding='utf-8') as _f:
            _loaded_s = json.load(_f)
            _temp_settings_for_log_level.update(_loaded_s)
    except Exception: pass 

_log_level_str = _temp_settings_for_log_level.get("log_level", "INFO").upper()
_numeric_log_level = getattr(logging, _log_level_str, logging.INFO)

logging.basicConfig(
    level=_numeric_log_level, 
    format='%(asctime)s - %(levelname)s - [%(module)s.%(funcName)s:%(lineno)d] - %(message)s',
    handlers=[ 
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(SCRAPER_LOG_FILENAME, encoding='utf-8', mode='a')
    ]
)
logger = logging.getLogger(__name__) 

def update_status_file(message: str):
    """Zapíše aktuální status scraperu do souboru."""
    try:
        with open(STATUS_FILENAME, 'w', encoding='utf-8') as f:
            f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}")
        logger.debug(f"Status soubor aktualizován: {message}")
    except IOError as e:
        logger.error(f"Chyba při zápisu do status souboru '{STATUS_FILENAME}': {e}")

def load_scraper_settings(filepath: str = SCRAPER_SETTINGS_FILENAME) -> dict:
    settings = DEFAULT_SETTINGS.copy() 
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                loaded_settings = json.load(f)
                settings.update(loaded_settings) 
                logger.info(f"Konfigurace scraperu úspěšně načtena z '{filepath}'.")
        except json.JSONDecodeError:
            logger.error(f"Chyba při parsování JSON v '{filepath}'. Používají se výchozí nastavení.", exc_info=True)
        except Exception as e:
            logger.error(f"Neočekávaná chyba při načítání '{filepath}': {e}. Používají se výchozí nastavení.", exc_info=True)
    else:
        logger.warning(f"Soubor '{filepath}' nenalezen. Používají se výchozí nastavení a bude vytvořen nový.")
        try: 
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=4)
            logger.info(f"Vytvořen nový konfigurační soubor '{filepath}' s výchozími hodnotami.")
        except IOError as e_create:
            logger.error(f"Nepodařilo se vytvořit konfigurační soubor '{filepath}': {e_create}")
    
    new_log_level_str = settings.get("log_level", "INFO").upper()
    new_numeric_log_level = getattr(logging, new_log_level_str, logging.INFO)
    if logger.getEffectiveLevel() != new_numeric_log_level:
        logger.setLevel(new_numeric_log_level)
        logger.info(f"Úroveň logování aktualizována na: {new_log_level_str} podle nastavení.")
        for handler in logging.getLogger().handlers: 
            handler.setLevel(new_numeric_log_level)
    return settings

SCRAPER_SETTINGS = load_scraper_settings() 
PROFILES_IN_MEMORY: list = []

def signal_handler_fn(signum, frame):
    status_msg = f"Přijat signál {signal.Signals(signum).name}. Ukončuji..."
    logger.info(status_msg)
    update_status_file(status_msg)
    if PROFILES_IN_MEMORY: 
        save_profiles_state(PROFILES_IN_MEMORY)
    logger.info("Stav profilů uložen. Ukončuji."); 
    sys.exit(0)

def main():
    global PROFILES_IN_MEMORY
    update_status_file("Scraper se spouští, inicializace...")
    
    try:
        signal.signal(signal.SIGINT, signal_handler_fn)
        signal.signal(signal.SIGTERM, signal_handler_fn)
    except Exception as e: 
        logger.warning(f"Chyba při registraci signal handlerů: {e}")

    manual_cookie = SCRAPER_SETTINGS.get("manual_cookie", DEFAULT_SETTINGS["manual_cookie"])
    proxies_config = SCRAPER_SETTINGS.get("proxies_config", DEFAULT_SETTINGS["proxies_config"])
    main_loop_sleep = SCRAPER_SETTINGS.get("main_loop_sleep_seconds", DEFAULT_SETTINGS["main_loop_sleep_seconds"])
    profile_sleep_min = SCRAPER_SETTINGS.get("profile_sleep_min", DEFAULT_SETTINGS["profile_sleep_min"])
    profile_sleep_max = SCRAPER_SETTINGS.get("profile_sleep_max", DEFAULT_SETTINGS["profile_sleep_max"])
    cycles_session_refresh = SCRAPER_SETTINGS.get("cycles_before_session_refresh", DEFAULT_SETTINGS["cycles_before_session_refresh"])
    cycles_profiles_save = SCRAPER_SETTINGS.get("cycles_before_profiles_save", DEFAULT_SETTINGS["cycles_before_profiles_save"])

    logger.info("🚀 Vinted Scraper Backend (status file) spuštěn.")
    # ... (ostatní INFO logy zůstávají) ...
    update_status_file("Načítání profilů a session...")

    PROFILES_IN_MEMORY = load_profiles()
    if not PROFILES_IN_MEMORY:
        msg = f"Nebyly načteny žádné profily z '{PROFILES_FILENAME}'. Ukončuji."
        logger.critical(msg); update_status_file(msg); return
        
    logger.info(f"Načteno {len(PROFILES_IN_MEMORY)} profilů ke zpracování:")
    # ... (logování detailů profilů) ...
    for i, p in enumerate(PROFILES_IN_MEMORY):
        profile_name = p.get('name', 'N/A')
        vinted_url = p.get('vinted_url', 'N/A')
        local_filters = p.get('filters', {})
        must_haves = local_filters.get('must_have_keywords', [])
        excludes = local_filters.get('exclude_keywords', [])
        case_sensitive = local_filters.get('keywords_case_sensitive', False)
        logger.info(f"  Profil {i+1}: {profile_name} (URL: '{vinted_url}', Lokální filtry - Musí: {must_haves}, Nesmí: {excludes}, CaseSensitive: {case_sensitive})")
    logger.info("-" * 40)


    vinted_session = get_vinted_session(manual_cookie=manual_cookie, proxies=proxies_config)
    if not vinted_session:
        msg = "Kritická chyba: Nepodařilo se vytvořit Vinted session. Ukončuji."
        logger.critical(msg); update_status_file(msg); return

    run_count = 0
    try:
        while True:
            run_count += 1
            status_msg_cycle = f"Začíná HLAVNÍ CYKLUS č. {run_count}"
            logger.info(f"\n🏁 ========== {status_msg_cycle} ({time.strftime('%Y-%m-%d %H:%M:%S')}) ==========")
            update_status_file(status_msg_cycle)
            
            if run_count > 1 and (run_count % cycles_session_refresh == 0):
                update_status_file(f"Obnova session (po {run_count-1} cyklech)...")
                # ... (kód obnovy session) ...
                logger.info(f"Preventivní obnova Vinted session po {run_count-1} cyklech...")
                if hasattr(vinted_session, 'close'): vinted_session.close()
                vinted_session = get_vinted_session(manual_cookie=manual_cookie, proxies=proxies_config)
                if not vinted_session:
                    msg = "Kritická chyba: Nepodařilo se OBNOVIT Vinted session. Ukončuji."
                    logger.critical(msg); update_status_file(msg)
                    save_profiles_state(PROFILES_IN_MEMORY); return
                logger.info("Nová session pro další cykly je připravena.")


            any_new_item_in_this_cycle = False
            active_profiles_for_run = [p for p in PROFILES_IN_MEMORY if p.get("vinted_url") and p.get("enabled", True)]
            if not active_profiles_for_run:
                status_msg_no_profiles = "Žádné aktivní profily k dispozici. Čekám..."
                logger.warning(status_msg_no_profiles); update_status_file(status_msg_no_profiles)
                time.sleep(main_loop_sleep); continue

            current_run_profiles = random.sample(active_profiles_for_run, len(active_profiles_for_run))
            logger.debug(f"Pořadí profilů v tomto cyklu: {[p.get('name', 'N/A') for p in current_run_profiles]}")

            for profile_index, profile_config in enumerate(current_run_profiles):
                profile_name = profile_config.get("name", f"Profil bez jména #{profile_index+1}")
                status_msg_profile = f"Zpracovávám profil ({profile_index + 1}/{len(current_run_profiles)}): '{profile_name}'"
                logger.info(f"\n  🔎 {status_msg_profile}"); update_status_file(status_msg_profile)

                if not isinstance(profile_config.get("seen_ids"), set):
                    profile_config["seen_ids"] = set()

                new_items_strings, new_items_data_list, found_ids_for_profile = fetch_new_items(vinted_session, profile_config)

                if new_items_data_list: 
                    any_new_item_in_this_cycle = True
                    profile_config["seen_ids"].update(found_ids_for_profile)
                    logger.info(f"Profil '{profile_name}': Aktualizováno {len(found_ids_for_profile)} ID. Celkem v paměti: {len(profile_config['seen_ids'])}")
                    
                    try:
                        with open(NEW_FINDS_FILENAME, "a", encoding="utf-8") as f_finds:
                            for item_detail_dict in new_items_data_list:
                                item_to_save = item_detail_dict.copy()
                                item_to_save["profile_name_found"] = profile_name
                                item_to_save["timestamp_found_iso"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                                item_to_save["timestamp_found_unix"] = time.time()
                                f_finds.write(json.dumps(item_to_save, ensure_ascii=False) + "\n")
                        logger.info(f"Profil '{profile_name}': {len(new_items_data_list)} nových nálezů uloženo do {NEW_FINDS_FILENAME}")
                    except IOError as e_io:
                        logger.error(f"Chyba při zápisu do {NEW_FINDS_FILENAME} pro profil '{profile_name}': {e_io}")
                
                if profile_config != current_run_profiles[-1]: 
                    sleep_duration = random.uniform(profile_sleep_min, profile_sleep_max)
                    status_msg_sleep = f"Pauza {sleep_duration:.1f}s před dalším profilem..."
                    logger.info(f"    💤 {status_msg_sleep}"); update_status_file(status_msg_sleep)
                    time.sleep(sleep_duration)
            
            if not any_new_item_in_this_cycle:
                logger.info(f"✓ Cyklus č. {run_count} dokončen. Žádné nové položky.")
            else:
                logger.info(f"✓ Cyklus č. {run_count} dokončen s novými nálezy.")

            if run_count % cycles_profiles_save == 0 or any_new_item_in_this_cycle:
                update_status_file(f"Ukládání stavu profilů po cyklu č. {run_count}...")
                logger.info(f"Ukládání stavu profilů (seen_ids) po cyklu č. {run_count}...")
                save_profiles_state(PROFILES_IN_MEMORY)
            
            status_msg_wait = f"Čekám {main_loop_sleep}s do dalšího cyklu (č. {run_count + 1})..."
            logger.info(f"⏱️ {status_msg_wait}"); update_status_file(status_msg_wait)
            time.sleep(main_loop_sleep)

    except KeyboardInterrupt: 
        logger.info("🛑 Přerušeno uživatelem (KeyboardInterrupt v main loop).")
        update_status_file("Scraper ukončen uživatelem.")
    except SystemExit: 
        logger.info("Systémový požadavek na ukončení zpracován.")
        update_status_file("Scraper ukončen systémovým požadavkem.")
    except Exception as e: 
        status_msg_error = f"💥 Neočekávaná KRITICKÁ chyba: {e}"
        logger.critical(status_msg_error, exc_info=True)
        update_status_file(status_msg_error)
    finally:
        final_status = "Scraper se ukončuje (finally blok)..."
        logger.info(final_status); update_status_file(final_status)
        if PROFILES_IN_MEMORY:
            logger.info("Ukládám finální stav profilů (seen_ids)...")
            save_profiles_state(PROFILES_IN_MEMORY)
            logger.info("Finální stav profilů uložen.")
        
        if 'vinted_session' in locals() and vinted_session and hasattr(vinted_session, 'close'):
            vinted_session.close()
            logger.info("Vinted session byla uzavřena.")
        
        update_status_file("Scraper ZASTAVEN.")
        logger.info("👋 Scraper ukončen.")

if __name__ == "__main__":
    if sys.stdout.encoding != 'utf-8' and hasattr(sys.stdout, 'reconfigure'):
        try: 
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception: pass
    main()