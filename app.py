import streamlit as st

# st.set_page_config MUSÍ BÝT PRVNÍ STREAMLIT PŘÍKAZ
st.set_page_config(layout="wide", page_title="Vinted Scraper Panel")

import json
import os
import subprocess
import signal
import psutil
from datetime import datetime, timezone, timedelta
import time
import sys

# --- Názvy souborů ---
PROFILES_FILENAME = "user_profiles.json"
SCRAPER_SETTINGS_FILENAME = "scraper_settings.json"
NEW_FINDS_FILENAME = "new_finds.jsonl"
PID_FILENAME = "vinted_scraper.pid"
SCRAPER_LOG_FILENAME = "scraper.log" 
STATUS_FILENAME = "scraper_current_status.txt"

# --- Výchozí hodnoty ---
DEFAULT_SCRAPER_SETTINGS = {
    "manual_cookie": "", "proxies_config": None, "main_loop_sleep_seconds": 300,
    "profile_sleep_min": 25, "profile_sleep_max": 55, "cycles_before_session_refresh": 10,
    "cycles_before_profiles_save": 1, "log_level": "INFO"
}

# --- Pomocné funkce ---
# (V těchto funkcích se teď vyhneme přímému volání st.error/st.toast, pokud je to možné,
# nebo zajistíme, že se volají až poté, co je stránka nakonfigurována.
# Pro jednoduchost teď ponecháme st.error/st.toast, ale set_page_config je už nahoře.)

def load_json_file(filepath, default_data=None, is_jsonl=False):
    if default_data is None: default_data = [] if is_jsonl else {}
    if not os.path.exists(filepath):
        if is_jsonl: return [] 
        # Tyto save_json_file by se neměly volat před set_page_config,
        # ale protože set_page_config je teď první, je to v pořádku.
        if filepath == PROFILES_FILENAME: save_json_file(filepath, [], is_jsonl=False); return []
        if filepath == SCRAPER_SETTINGS_FILENAME: save_json_file(filepath, DEFAULT_SCRAPER_SETTINGS, is_jsonl=False); return DEFAULT_SCRAPER_SETTINGS.copy()
        return default_data
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            if is_jsonl: return [json.loads(line) for line in f if line.strip()]
            else:
                content = f.read()
                if not content.strip(): return default_data
                return json.loads(content)
    except (json.JSONDecodeError, IOError) as e: 
        st.error(f"Chyba při načítání {filepath}: {e}") # Toto je v pořádku, pokud je set_page_config už zavoláno
        return default_data

def save_json_file(filepath, data, is_jsonl=False):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            if is_jsonl: 
                for item in data: f.write(json.dumps(item, ensure_ascii=False) + "\n")
            else: json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except IOError as e: st.error(f"Chyba při ukládání {filepath}: {e}"); return False


def get_scraper_pid():
    if os.path.exists(PID_FILENAME):
        try:
            with open(PID_FILENAME, 'r') as f:
                pid_str = f.read().strip()
                if pid_str: return int(pid_str)
        except (ValueError, IOError): return None
    return None

def is_scraper_running():
    pid = get_scraper_pid()
    if pid:
        try:
            process = psutil.Process(pid)
            if process.is_running() and "python" in process.name().lower():
                cmdline = process.cmdline()
                if any("main.py" in part for part in cmdline): return True
                else: try_remove_stale_pid(); return False
        except psutil.NoSuchProcess: try_remove_stale_pid(); return False
        except Exception: return False 
    return False

def try_remove_stale_pid():
    if os.path.exists(PID_FILENAME):
        try: 
            os.remove(PID_FILENAME)
            # st.toast() je Streamlit příkaz, měl by být volán až po set_page_config
            # Pro jednoduchost ho zde můžeme nechat, protože set_page_config je nyní první
            st.toast("Zastaralý/neplatný PID soubor byl odstraněn.")
        except OSError: pass 

@st.cache_data(ttl=3) # Mírně delší cache pro status text
def get_scraper_live_status_text_cached(): 
    if os.path.exists(STATUS_FILENAME):
        try:
            with open(STATUS_FILENAME, 'r', encoding='utf-8') as f:
                status_line = f.read().strip()
                if " - " in status_line:
                    return status_line.split(" - ", 1)[1]
                return status_line
        except IOError:
            return "Chyba čtení statusu."
    elif is_scraper_running_cached():
        return "Scraper běží (čeká na status)..."
    return "Scraper neběží / status neznámý."

@st.cache_data(ttl=2)
def is_scraper_running_cached():
    pid = get_scraper_pid_cached()
    if pid:
        try:
            process = psutil.Process(pid)
            if process.is_running() and "python" in process.name().lower():
                cmdline = process.cmdline() 
                if any("main.py" in part for part in cmdline): return True
                else: try_remove_stale_pid(); return False
        except psutil.NoSuchProcess: try_remove_stale_pid(); return False
        except Exception: return False 
    return False

@st.cache_data(ttl=2)
def get_scraper_pid_cached():
    if os.path.exists(PID_FILENAME):
        try:
            with open(PID_FILENAME, 'r') as f:
                pid_str = f.read().strip()
                if pid_str: return int(pid_str)
        except (ValueError, IOError): return None
    return None

def sort_finds_key(item):
    vinted_ts = item.get("vinted_item_timestamp", 0)
    our_ts = item.get("timestamp_found_unix", 0)
    if vinted_ts and vinted_ts > 0:
        return (1, vinted_ts, our_ts) 
    else:
        return (0, our_ts, 0) 

# --- Inicializace session state ---
if "profiles" not in st.session_state:
    st.session_state.profiles = load_json_file(PROFILES_FILENAME, default_data=[])
if "scraper_settings" not in st.session_state:
    st.session_state.scraper_settings = load_json_file(SCRAPER_SETTINGS_FILENAME, default_data=DEFAULT_SCRAPER_SETTINGS.copy())
if "selected_profile_index" not in st.session_state: 
    st.session_state.selected_profile_index = None
if "all_finds_cache" not in st.session_state: 
    st.session_state.all_finds_cache = load_json_file(NEW_FINDS_FILENAME, default_data=[], is_jsonl=True)
    st.session_state.all_finds_cache.sort(key=sort_finds_key, reverse=True) 
if "live_scraper_status" not in st.session_state:
    st.session_state.live_scraper_status = get_scraper_live_status_text_cached()


# --- UI Aplikace ---
# st.set_page_config() bylo přesunuto na začátek souboru

st.title("🤖 Vinted Scraper Panel")

# --- Definice fragmentu pro status ---
@st.fragment 
def update_live_status_fragment_runner(run_every_x_seconds=10): 
    current_status = get_scraper_live_status_text_cached() 
    if st.session_state.get("live_scraper_status") != current_status:
        st.session_state.live_scraper_status = current_status
    time.sleep(run_every_x_seconds)

update_live_status_fragment_runner(run_every_x_seconds=10)


# --- SIDEBAR ---
# ... (zbytek kódu pro sidebar a hlavní obsah zůstává stejný jako v mé předchozí odpovědi,
#      kde jsem posílal kompletní app.py pro automatický refresh statusu) ...
#      Ujisti se, že všechny klíče (key=...) pro widgety jsou unikátní.
st.sidebar.header("🚀 Ovládání Scraperu")
scraper_is_active_on_load = is_scraper_running_cached() 

if scraper_is_active_on_load:
    st.sidebar.success("✅ Scraper je aktivní (dle PID souboru).")
    if st.sidebar.button("🔴 Zastavit Scraper", type="primary", use_container_width=True, key="stop_scraper_btn_frag_v4_fix"):
        pid = get_scraper_pid_cached() 
        if pid:
            try:
                process = psutil.Process(pid); process.send_signal(signal.SIGTERM) 
                st.toast(f"Signál SIGTERM odeslán procesu {pid}. Čekejte..."); time.sleep(3) 
                if is_scraper_running_cached(): 
                    st.sidebar.warning("Scraper se nepodařilo korektně ukončit, zkouším SIGKILL."); process.kill(); time.sleep(1)
                try_remove_stale_pid(); st.rerun()
            except psutil.NoSuchProcess: st.sidebar.info("Proces scraperu již neběžel."); try_remove_stale_pid(); st.rerun()
            except Exception as e: st.sidebar.error(f"Chyba při zastavování: {e}")
        else: st.sidebar.warning("PID scraperu nenalezen.")
else:
    st.sidebar.info("❌ Scraper není aktivní (nebo PID soubor chybí).")
    if st.sidebar.button("🟢 Spustit Scraper", use_container_width=True, key="start_scraper_btn_frag_v4_fix"):
        try:
            flags = {}; 
            if os.name == 'nt': flags['creationflags'] = subprocess.CREATE_NO_WINDOW
            else: flags['start_new_session'] = True
            process = subprocess.Popen([sys.executable, "main.py"], **flags)
            with open(PID_FILENAME, 'w') as f: f.write(str(process.pid))
            st.sidebar.success(f"Scraper spuštěn (PID: {process.pid})."); time.sleep(2); 
            st.session_state.live_scraper_status = "Scraper právě startuje..." 
            st.rerun()
        except Exception as e: st.sidebar.error(f"Chyba při spouštění: {e}")

st.sidebar.markdown("---")
st.sidebar.subheader("ℹ️ Aktuální Stav Scraperu")
st.sidebar.text_area("Status:", st.session_state.get("live_scraper_status", "Načítám status..."), height=100, disabled=True, key="status_display_area_from_state_v4_fix")

if st.sidebar.button("🔄 Manuálně obnovit status (celá str.)", key="refresh_status_button_sidebar_manual_v4_fix"):
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("⚙️ Správa Profilů")
profile_options = ["--- Vytvořit nový profil ---"] + [p.get("name", f"Profil {i+1}") for i, p in enumerate(st.session_state.profiles)]
if st.session_state.selected_profile_index is not None and st.session_state.selected_profile_index >= len(st.session_state.profiles):
    st.session_state.selected_profile_index = None 
default_profile_radio_index = 0
if st.session_state.selected_profile_index is not None:
    default_profile_radio_index = st.session_state.selected_profile_index + 1
elif st.session_state.profiles: 
    st.session_state.selected_profile_index = 0; default_profile_radio_index = 1

selected_profile_display_name = st.sidebar.selectbox(
    "Vyberte profil:", options=profile_options, index=default_profile_radio_index, key="profile_selector_sidebar_final_v6_frag_fix"
)

if selected_profile_display_name == "--- Vytvořit nový profil ---":
    st.session_state.selected_profile_index = None
else:
    try:
        st.session_state.selected_profile_index = [p.get("name") for p in st.session_state.profiles].index(selected_profile_display_name)
    except ValueError: st.session_state.selected_profile_index = None


# --- HLAVNÍ OBSAH - ZÁLOŽKY ---
tab_form, tab_finds, tab_settings, tab_logs_display = st.tabs([
    "📝 Profil Editor", "✨ Nalezené Položky", "🔧 Nastavení Scraperu", "📜 Logy Scraperu"
])

with tab_form:
    if st.session_state.selected_profile_index is not None:
        st.header(f"Úprava profilu: {st.session_state.profiles[st.session_state.selected_profile_index].get('name')}")
        current_profile_data = st.session_state.profiles[st.session_state.selected_profile_index]
        profile_index_for_form = st.session_state.selected_profile_index
    else:
        st.header("Vytvoření Nového Profilu")
        current_profile_data = {"name": "", "vinted_url": "", "filters": {}, "seen_ids": [], "enabled": True} 
        profile_index_for_form = -1

    with st.form(key=f"profile_form_url_{profile_index_for_form if profile_index_for_form != -1 else 'new_url_profile_final_v6_frag_fix'}"):
        new_name = st.text_input("Název profilu*", value=current_profile_data.get("name", ""), placeholder="Např. Carhartt Bundy z URL")
        profile_enabled = st.checkbox("Profil aktivní", value=current_profile_data.get("enabled", True), help="Zda má scraper tento profil prohledávat.")
        st.markdown("---")
        vinted_url_input = st.text_area("Vinted URL pro vyhledávání*", value=current_profile_data.get("vinted_url", ""), height=100, placeholder="Vložte sem URL z Vinted s nastavenými filtry...")
        st.markdown("**Lokální filtry (aplikují se na výsledky z Vinted URL):**")
        local_filters = current_profile_data.get("filters", {})
        must_have_simple_str = ""
        must_have_raw = local_filters.get("must_have_keywords", [])
        if isinstance(must_have_raw, list) and all(isinstance(item, str) for item in must_have_raw): must_have_simple_str = ", ".join(must_have_raw)
        elif isinstance(must_have_raw, list) and must_have_raw:
            try: must_have_simple_str = json.dumps(must_have_raw)
            except: must_have_simple_str = str(must_have_raw)
            st.caption("Detekována pokročilá struktura 'Musí obsahovat'.")
        must_have_keywords_ui = st.text_input("Musí obsahovat v názvu (slova odd. čárkou = AND)", value=must_have_simple_str, help="Pro (A nebo B) a C zadejte: [[A, B], C] přímo v JSONu, nebo použijte ; pro oddělení OR skupin, např. 'air max,jordan;boty'")
        exclude_keywords_ui = st.text_input("Nesmí obsahovat v názvu (slova odd. čárkou)", value=", ".join(local_filters.get("exclude_keywords", [])))
        keywords_case_sensitive = st.checkbox("Rozlišovat velikost písmen u klíč. slov", value=local_filters.get("keywords_case_sensitive", False))
        submitted = st.form_submit_button("💾 Uložit profil")
        if submitted: 
            if not new_name.strip(): st.error("Název profilu je povinný!")
            elif not vinted_url_input.strip() or not (vinted_url_input.startswith("http://") or vinted_url_input.startswith("https://")):
                st.error("Vinted URL pro vyhledávání je povinné a musí být platné URL!")
            else:
                parsed_must_have_final = []
                if must_have_keywords_ui.strip():
                    if (must_have_keywords_ui.strip().startswith("[[") and must_have_keywords_ui.strip().endswith("]]")) or \
                       (must_have_keywords_ui.strip().startswith("[") and must_have_keywords_ui.strip().endswith("]") and "," in must_have_keywords_ui and any(sub.strip().startswith("[") for sub in must_have_keywords_ui.split("],"))):
                        try:
                            parsed_must_have_final = json.loads(must_have_keywords_ui)
                            if not (isinstance(parsed_must_have_final, list) and \
                                    all(isinstance(group, list) and all(isinstance(kw, str) for kw in group) for group in parsed_must_have_final if isinstance(group, list)) and \
                                    all(isinstance(item, str) for item in parsed_must_have_final if not isinstance(item, list)) ):
                                raise json.JSONDecodeError("Není validní struktura pro must_have_keywords", must_have_keywords_ui,0)
                        except json.JSONDecodeError:
                            st.warning("Formát 'Musí obsahovat' vypadá jako pokročilý, ale není validní JSON. Zpracovávám jako jednoduchý AND seznam slov oddělených čárkou."); parsed_must_have_final = [kw.strip() for kw in must_have_keywords_ui.split(',') if kw.strip()]
                    elif ";" in must_have_keywords_ui:
                        or_groups_str = must_have_keywords_ui.split(';')
                        for group_str in or_groups_str:
                            and_keywords = [kw.strip() for kw in group_str.split(',') if kw.strip()]
                            if and_keywords: parsed_must_have_final.append(and_keywords)
                    else: parsed_must_have_final = [kw.strip() for kw in must_have_keywords_ui.split(',') if kw.strip()]
                updated_local_filters_data = {
                    "must_have_keywords": parsed_must_have_final or [], 
                    "exclude_keywords": [kw.strip() for kw in exclude_keywords_ui.split(',') if kw.strip()] or [],
                    "keywords_case_sensitive": keywords_case_sensitive,
                }
                updated_local_filters_data = {k:v for k,v in updated_local_filters_data.items() if v or isinstance(v, bool)}
                new_profile_data = {
                    "name": new_name.strip(), "vinted_url": vinted_url_input.strip(),
                    "filters": updated_local_filters_data, "enabled": profile_enabled,
                    "seen_ids": current_profile_data.get("seen_ids", []) 
                }
                if profile_index_for_form != -1: 
                    other_profile_names = [p["name"] for i, p in enumerate(st.session_state.profiles) if i != profile_index_for_form]
                    if new_name.strip() in other_profile_names: st.error(f"Profil s názvem '{new_name.strip()}' již existuje!")
                    else:
                        st.session_state.profiles[profile_index_for_form] = new_profile_data
                        if save_json_file(PROFILES_FILENAME, st.session_state.profiles):
                            st.success(f"Profil '{new_name.strip()}' úspěšně uložen."); st.session_state.selected_profile_index = profile_index_for_form; st.rerun()
                        else: st.error("Nepodařilo se uložit profil.")
                else: 
                    if new_name.strip() in [p.get("name") for p in st.session_state.profiles]: st.error(f"Profil s názvem '{new_name.strip()}' již existuje!")
                    else:
                        st.session_state.profiles.append(new_profile_data)
                        if save_json_file(PROFILES_FILENAME, st.session_state.profiles):
                            st.success(f"Profil '{new_name.strip()}' úspěšně vytvořen."); st.session_state.selected_profile_index = len(st.session_state.profiles) - 1; st.rerun()
                        else: st.error("Nepodařilo se vytvořit profil.")
    if st.session_state.selected_profile_index is not None:
        delete_button_key_final_v6_frag_fix = f"delete_btn_url_final_v6_frag_fix_{st.session_state.profiles[st.session_state.selected_profile_index].get('name', st.session_state.selected_profile_index)}"
        if st.button(f"🗑️ Smazat profil '{st.session_state.profiles[st.session_state.selected_profile_index].get('name')}'", type="primary", use_container_width=True, key=delete_button_key_final_v6_frag_fix):
            profile_to_delete_name = st.session_state.profiles[st.session_state.selected_profile_index].get('name')
            st.session_state.profiles.pop(st.session_state.selected_profile_index)
            if save_json_file(PROFILES_FILENAME, st.session_state.profiles):
                st.success(f"Profil '{profile_to_delete_name}' smazán."); st.session_state.selected_profile_index = None; st.rerun()
            else: st.error("Nepodařilo se smazat profil.")

with tab_finds:
    # ... (stejný kód jako předtím, ale s novými klíči pro widgety)
    st.header("✨ Nalezené Položky")
    MAX_DISPLAY_FINDS = 100 
    HIGHLIGHT_NEW_VINTED_FOR_HOURS = 24 
    if st.button("🔄 Obnovit nálezy", key="refresh_finds_tab_final_v6_frag_fix"):
        st.session_state.all_finds_cache = load_json_file(NEW_FINDS_FILENAME, default_data=[], is_jsonl=True)
        st.session_state.all_finds_cache.sort(key=sort_finds_key, reverse=True) 
        st.rerun()
    if not st.session_state.all_finds_cache:
        st.info("Zatím žádné nálezy. Spusťte scraper nebo počkejte na další cyklus.")
    else:
        search_finds_query = st.text_input("🔍 Hledat v nálezech (v názvu):", key="finds_search_input_tab_final_v6_frag_fix").lower()
        unique_profile_names_in_finds = sorted(list(set(find.get("profile_name_found", "Neznámý") for find in st.session_state.all_finds_cache)))
        available_profiles_for_finds_filter = ["Všechny profily"] + unique_profile_names_in_finds
        selected_profile_filter = st.selectbox("Filtrovat podle profilu:", available_profiles_for_finds_filter, key="finds_profile_filter_tab_final_v6_frag_fix")
        items_to_display = []
        now_ts_utc_for_highlight = datetime.now(timezone.utc).timestamp() 
        highlight_vinted_threshold_ts = now_ts_utc_for_highlight - (HIGHLIGHT_NEW_VINTED_FOR_HOURS * 3600)
        for find_item in st.session_state.all_finds_cache: 
            profile_match = (selected_profile_filter == "Všechny profily" or find_item.get("profile_name_found") == selected_profile_filter)
            title_match = (search_finds_query in find_item.get("title", "").lower()) if search_finds_query else True
            if profile_match and title_match:
                item_vinted_ts = find_item.get("vinted_item_timestamp", 0)
                is_highlighted_as_new_on_vinted = (item_vinted_ts is not None and item_vinted_ts > 0 and item_vinted_ts > highlight_vinted_threshold_ts)
                items_to_display.append({"data": find_item, "highlight": is_highlighted_as_new_on_vinted})
        if not items_to_display: st.info(f"Pro zadaná kritéria nebyly nalezeny žádné položky.")
        else:
            st.write(f"Zobrazeno položek: {len(items_to_display[:MAX_DISPLAY_FINDS])} (z celkem {len(items_to_display)} odpovídajících filtru, řazeno dle času Vinted / času nálezu)")
            for item_wrapper in items_to_display[:MAX_DISPLAY_FINDS]: 
                find_item_data = item_wrapper["data"]
                container_style = "border-left: 5px solid #28a745; background-color: #223322; padding: 10px; margin-bottom: 10px; border-radius: 5px;" if item_wrapper["highlight"] else "margin-bottom: 10px; padding: 10px; border: 1px solid #333;"
                with st.container():
                    st.markdown(f"<div style='{container_style}'>", unsafe_allow_html=True)
                    col_img, col_details = st.columns([1,4])
                    with col_img:
                        if find_item_data.get("photo_url"): st.image(find_item_data.get("photo_url"), width=120)
                        else: st.markdown("🖼️", unsafe_allow_html=True) 
                    with col_details:
                        title_display = find_item_data.get('title', 'N/A')
                        if item_wrapper["highlight"]: title_display = f"🔥 NOVÉ (Vinted < {HIGHLIGHT_NEW_VINTED_FOR_HOURS}h): {title_display}"
                        st.markdown(f"**{title_display}**")
                        price_num = find_item_data.get('price_numeric')
                        price_str_display = f"{price_num:,.0f}".replace(",", " ") + f" {find_item_data.get('currency', 'CZK')}" if price_num is not None else find_item_data.get('price_str', 'N/A')
                        st.markdown(f"Cena: **{price_str_display}** | Profil: _{find_item_data.get('profile_name_found', 'N/A')}_")
                        st.markdown(f"Stav: {find_item_data.get('status', 'N/A')} | Velikost: {find_item_data.get('size', 'N/A')} | Značka: {find_item_data.get('brand', 'N/A')}")
                        display_time_str = "Čas nenalezen"
                        vinted_ts_val = find_item_data.get("vinted_item_timestamp")
                        if vinted_ts_val and vinted_ts_val > 0: 
                            try:
                                dt_object_vinted = datetime.fromtimestamp(vinted_ts_val, tz=timezone.utc); dt_object_local = dt_object_vinted.astimezone(None) 
                                now_local_compare = datetime.now(timezone.utc).astimezone(None); time_ago = now_local_compare - dt_object_local
                                if time_ago.total_seconds() < 5: time_ago_str = "právě teď"
                                elif time_ago.total_seconds() < 60: time_ago_str = f"před {int(time_ago.total_seconds())} s"
                                elif time_ago.total_seconds() < 3600: time_ago_str = f"před {int(time_ago.total_seconds() // 60)} min"
                                elif time_ago.total_seconds() < 86400*2 : time_ago_str = f"před {int(time_ago.total_seconds() // 3600)} hod"
                                else: time_ago_str = f"dne {dt_object_local.strftime('%d.%m.%Y %H:%M')}"
                                display_time_str = f"Vystaveno (Vinted): {time_ago_str}"
                            except Exception: display_time_str = f"Vinted čas (TS): {vinted_ts_val}" 
                        else: 
                            our_ts_iso = find_item_data.get("timestamp_found_iso")
                            if our_ts_iso:
                                try: dt_object = datetime.fromisoformat(our_ts_iso.replace("Z", "+00:00")); dt_object_local = dt_object.astimezone(None); display_time_str = f"Nalezeno scraperem: {dt_object_local.strftime('%d.%m.%Y %H:%M')}"
                                except: pass
                        st.caption(f"{display_time_str} | [Odkaz na Vinted]({find_item_data.get('url', '#')})")
                    st.markdown("</div>", unsafe_allow_html=True)

with tab_settings:
    # ... (stejný kód jako předtím, ale s novými klíči pro formulář)
    st.header("🔧 Globální Nastavení Scraperu")
    st.caption(f"Nastavení se ukládají do souboru: `{SCRAPER_SETTINGS_FILENAME}`")
    current_settings = st.session_state.scraper_settings.copy()
    with st.form("scraper_settings_form_final_v6_frag_fix"): 
        st.markdown("#### Nastavení Připojení")
        current_settings["manual_cookie"] = st.text_area("Manuální Vinted Cookie", value=current_settings.get("manual_cookie", ""), height=75, help="Pokud automatické získání session selhává.")
        proxies_current_val = current_settings.get("proxies_config")
        proxies_ui_input = st.text_area("Proxy (JSON formát)", value=json.dumps(proxies_current_val, indent=2) if isinstance(proxies_current_val, dict) else (proxies_current_val or ""), height=100, placeholder='Např. {"http": "http://user:pass@1.2.3.4:8080"}', help="Zadejte platný JSON objekt nebo nechte prázdné.")
        st.markdown("---"); st.markdown("#### Časové Intervaly (v sekundách)")
        current_settings["main_loop_sleep_seconds"] = st.number_input("Hlavní interval mezi cykly", min_value=30, value=int(current_settings.get("main_loop_sleep_seconds", 300)), step=10)
        s_col1, s_col2 = st.columns(2)
        with s_col1: current_settings["profile_sleep_min"] = st.number_input("Min. pauza mezi profily", min_value=5, value=int(current_settings.get("profile_sleep_min", 25)), step=1)
        with s_col2: current_settings["profile_sleep_max"] = st.number_input("Max. pauza mezi profily", min_value=10, value=int(current_settings.get("profile_sleep_max", 55)), step=1)
        st.markdown("---"); st.markdown("#### Údržba a Logování")
        current_settings["cycles_before_session_refresh"] = st.number_input("Počet cyklů pro obnovu session", min_value=1, value=int(current_settings.get("cycles_before_session_refresh", 10)), step=1)
        current_settings["cycles_before_profiles_save"] = st.number_input("Počet cyklů pro uložení stavu profilů", min_value=1, value=int(current_settings.get("cycles_before_profiles_save", 1)), step=1, help="Ukládá seen_ids. Pokud jsou nové nálezy, ukládá se vždy.")
        log_level_options = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]; current_log_level = current_settings.get("log_level", "INFO").upper()
        log_level_index = log_level_options.index(current_log_level) if current_log_level in log_level_options else 1
        current_settings["log_level"] = st.selectbox("Úroveň logování backendu", options=log_level_options, index=log_level_index)
        submitted_settings = st.form_submit_button("💾 Uložit Nastavení Scraperu")
        if submitted_settings:
            if proxies_ui_input.strip():
                try:
                    parsed_proxies = json.loads(proxies_ui_input)
                    if not (isinstance(parsed_proxies, dict) or parsed_proxies is None): st.error("Proxy konfigurace musí být platný JSON objekt (slovník) nebo prázdná/null."); st.stop()
                    current_settings["proxies_config"] = parsed_proxies
                except json.JSONDecodeError: st.error("Chybný JSON formát pro Proxy konfiguraci."); st.stop()
            else: current_settings["proxies_config"] = None
            if save_json_file(SCRAPER_SETTINGS_FILENAME, current_settings):
                st.session_state.scraper_settings = current_settings 
                st.success("Nastavení scraperu uložena. Změny se projeví při příštím startu/cyklu scraperu."); st.rerun()
            else: st.error("Nepodařilo se uložit nastavení scraperu.")

with tab_logs_display:
    st.header("📜 Logy Scraperu")
    st.caption(f"Zobrazuje posledních cca 100 řádků z `{SCRAPER_LOG_FILENAME}` (pokud backend loguje do souboru).")
    if st.button("🔄 Obnovit Logy", key="refresh_log_tab_button_final_v6_frag_fix"): 
        st.rerun()
    if os.path.exists(SCRAPER_LOG_FILENAME):
        try:
            with open(SCRAPER_LOG_FILENAME, 'r', encoding='utf-8') as f:
                log_lines_content = f.readlines()
            st.text_area("Logy:", "".join(log_lines_content[-100:]), height=400, disabled=True, key="log_display_area_tab_final_v6_frag_fix")
        except Exception as e:
            st.warning(f"Nepodařilo se načíst logovací soubor '{SCRAPER_LOG_FILENAME}': {e}")
    else:
        st.info(f"Logovací soubor '{SCRAPER_LOG_FILENAME}' zatím neexistuje. Ujistěte se, že backend loguje do souboru (v main.py).")

st.sidebar.markdown("---")
st.sidebar.caption(f"Profily: .../{os.path.basename(PROFILES_FILENAME)}") 
st.sidebar.caption(f"Nastavení: .../{os.path.basename(SCRAPER_SETTINGS_FILENAME)}")
st.sidebar.caption(f"Nálezy: .../{os.path.basename(NEW_FINDS_FILENAME)}")
st.sidebar.caption(f"Log soubor: .../{os.path.basename(SCRAPER_LOG_FILENAME)}")