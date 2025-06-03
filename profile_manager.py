import json
import os
import logging
from typing import List, Dict, Any, Set

logger = logging.getLogger(__name__)
PROFILES_FILENAME = "user_profiles.json"

def load_profiles(filepath: str = PROFILES_FILENAME) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    if not os.path.exists(filepath):
        logger.warning(f"Soubor profilů '{filepath}' nenalezen. Vracím prázdný seznam.")
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump([], f)
            logger.info(f"Vytvořen prázdný soubor profilů: '{filepath}'")
        except IOError as e:
            logger.error(f"Nepodařilo se vytvořit prázdný soubor profilů '{filepath}': {e}")
        return profiles

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
            if not isinstance(loaded_data, list):
                logger.error(f"Obsah souboru '{filepath}' není seznam. Vracím prázdný seznam.")
                return []

        for i, p_data in enumerate(loaded_data):
            if not isinstance(p_data, dict):
                logger.warning(f"Položka #{i} v '{filepath}' není slovník. Přeskakuji.")
                continue
            
            p_data.setdefault("name", f"Profil bez jména #{i+1}")
            p_data.setdefault("vinted_url", "") # Nový klíč pro URL
            p_data.setdefault("filters", {})    # Pro lokální filtry (must_have, exclude)
            p_data.setdefault("enabled", True)  # Přidáno pro frontend
            
            seen_ids_data = p_data.get("seen_ids")
            if isinstance(seen_ids_data, list):
                p_data["seen_ids"] = set(seen_ids_data)
            elif not isinstance(seen_ids_data, set):
                p_data["seen_ids"] = set()
            
            profiles.append(p_data)
        logger.info(f"Úspěšně načteno {len(profiles)} profilů z '{filepath}'.")

    except json.JSONDecodeError:
        logger.error(f"Chyba při parsování JSON souboru profilů '{filepath}'.", exc_info=False)
    except Exception as e:
        logger.error(f"Neočekávaná chyba při načítání profilů z '{filepath}': {e}.", exc_info=True)
    
    return profiles


def save_profiles_state(
    current_in_memory_profiles: List[Dict[str, Any]], 
    filepath: str = PROFILES_FILENAME
) -> bool:
    # ... (kód save_profiles_state zůstává stejný jako v poslední kompletní odpovědi) ...
    logger.debug(f"Pokus o uložení stavu {len(current_in_memory_profiles)} profilů do '{filepath}'.")
    
    disk_profiles_list: List[Dict[str, Any]] = []
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                if content.strip(): 
                    disk_profiles_list = json.loads(content)
                if not isinstance(disk_profiles_list, list): 
                    logger.warning(f"Obsah souboru '{filepath}' při načítání pro uložení nebyl seznam. Bude přepsán.")
                    disk_profiles_list = []
        except json.JSONDecodeError:
            logger.error(f"Soubor '{filepath}' je poškozený (JSONDecodeError) při načítání pro uložení. Bude přepsán aktuálním stavem z paměti.", exc_info=False)
            disk_profiles_list = [] 
        except Exception as e:
            logger.error(f"Chyba při načítání '{filepath}' pro uložení: {e}. Pokračuji s uložením pouze dat z paměti.", exc_info=True)
            disk_profiles_list = []

    disk_profiles_map: Dict[str, Dict[str, Any]] = {}
    for dp in disk_profiles_list:
        if isinstance(dp, dict) and "name" in dp:
            disk_profiles_map[dp["name"]] = dp
        else:
            logger.warning(f"Nalezena nevalidní položka na disku při ukládání: {dp}")

    final_profiles_to_save: List[Dict[str, Any]] = []
    processed_in_memory_names: Set[str] = set()

    for mem_profile in current_in_memory_profiles:
        mem_profile_name = mem_profile.get("name")
        if not mem_profile_name:
            profile_copy = mem_profile.copy()
            if "seen_ids" in profile_copy and isinstance(profile_copy["seen_ids"], set):
                profile_copy["seen_ids"] = sorted(list(profile_copy["seen_ids"])) 
            final_profiles_to_save.append(profile_copy)
            continue

        processed_in_memory_names.add(mem_profile_name)
        disk_version = disk_profiles_map.get(mem_profile_name)

        if disk_version: # Profil existuje na disku, aktualizujeme jen seen_ids a enabled, filtry bereme z disku
            profile_to_save = disk_version.copy() 
            if "seen_ids" in mem_profile and isinstance(mem_profile["seen_ids"], set):
                profile_to_save["seen_ids"] = sorted(list(mem_profile["seen_ids"]))
            else: 
                profile_to_save.setdefault("seen_ids", [])
                if isinstance(profile_to_save["seen_ids"], set): 
                     profile_to_save["seen_ids"] = sorted(list(profile_to_save["seen_ids"]))
            # Převezmeme 'enabled' stav z paměti, pokud existuje (mohl být změněn frontendem a pak backendem)
            if "enabled" in mem_profile:
                profile_to_save["enabled"] = mem_profile["enabled"]

            final_profiles_to_save.append(profile_to_save)
        else: # Profil je nový v paměti nebo byl smazán z disku a znovu vytvořen v paměti
            logger.info(f"Profil '{mem_profile_name}' je v paměti, ale nebyl nalezen na disku (nebo je to nový). Bude uložen.")
            profile_copy = mem_profile.copy()
            if "seen_ids" in profile_copy and isinstance(profile_copy["seen_ids"], set):
                profile_copy["seen_ids"] = sorted(list(profile_copy["seen_ids"]))
            else:
                profile_copy.setdefault("seen_ids", []) 
            final_profiles_to_save.append(profile_copy)

    for disk_name, disk_profile_data in disk_profiles_map.items():
        if disk_name not in processed_in_memory_names: # Profil je na disku, ale ne v paměti backendu
            profile_copy = disk_profile_data.copy()
            if "seen_ids" in profile_copy and isinstance(profile_copy["seen_ids"], set):
                 profile_copy["seen_ids"] = sorted(list(profile_copy["seen_ids"]))
            elif "seen_ids" not in profile_copy: 
                 profile_copy["seen_ids"] = []
            final_profiles_to_save.append(profile_copy)
            logger.debug(f"Přidávám profil '{disk_name}' z disku, který nebyl v aktuálním paměťovém zpracování.")

    if not final_profiles_to_save and os.path.exists(filepath) and os.path.getsize(filepath) > 2: 
        logger.warning("Výsledný seznam profilů k uložení je prázdný, ale soubor na disku obsahuje data. Ukládání se neprovede.")
        return False

    backup_filepath = "" 
    try:
        if os.path.exists(filepath):
            backup_filepath = filepath + ".bak"
            try:
                import shutil
                shutil.copy2(filepath, backup_filepath)
                logger.debug(f"Vytvořena záloha profilů: {backup_filepath}")
            except Exception as e_backup:
                logger.warning(f"Nepodařilo se vytvořit zálohu souboru profilů: {e_backup}")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(final_profiles_to_save, f, indent=4, ensure_ascii=False)
        logger.info(f"Stav profilů (celkem {len(final_profiles_to_save)}) úspěšně uložen do '{filepath}'.")
        
        if backup_filepath and os.path.exists(backup_filepath):
            try:
                os.remove(backup_filepath)
                logger.debug(f"Záloha {backup_filepath} smazána.")
            except Exception as e_remove_bak:
                logger.warning(f"Nepodařilo se smazat záložní soubor {backup_filepath}: {e_remove_bak}")
        return True
    except IOError as e:
        logger.error(f"Chyba při zápisu profilů do souboru '{filepath}': {e}", exc_info=True)
        if backup_filepath and os.path.exists(backup_filepath):
            try:
                import shutil
                shutil.copy2(backup_filepath, filepath) 
                logger.info(f"Obnoveno ze zálohy: {filepath}")
            except Exception as e_restore:
                logger.error(f"Nepodařilo se obnovit ze zálohy {backup_filepath}: {e_restore}")
        return False
    except Exception as e: 
        logger.error(f"Neočekávaná chyba při ukládání profilů: {e}", exc_info=True)
        return False