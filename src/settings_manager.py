# keglevel app
# 
# settings_manager.py
import json
import os
import time
import uuid
import sys 
import hmac
import hashlib
from datetime import datetime, timedelta
# Import pathlib for safe path expansion
from pathlib import Path

SETTINGS_FILE = "settings.json"
BEVERAGES_FILE = "beverages_library.json"
PROCESS_FLOW_FILE = "process_flow.json" 
BJCP_2021_FILE = "bjcp_2021_library.json" 
KEG_LIBRARY_FILE = "keg_library.json" 
# OBSOLETE LOCAL TRIAL FILE
TRIAL_RECORD_FILE = "trial_record.dat" 


# DEFINE the constants directly. No try/except needed.
UNASSIGNED_KEG_ID = "unassigned_keg_id"
UNASSIGNED_BEVERAGE_ID = "unassigned_beverage_id"

# --- Import Flow Constants for initial defaults ---
from sensor_logic import FLOW_SENSOR_PINS, DEFAULT_K_FACTOR

class SettingsManager:
    
    def _get_default_sensor_labels(self):
        return [f"Tap {i+1}" for i in range(self.num_sensors)]

    def _get_default_keg_definitions(self):
        defs = []
        # FIX: Generate enough default kegs for ALL sensors (10) instead of just 5
        # FIX: Initialize them as EMPTY (0.0 Volume)
        for i in range(self.num_sensors):
            keg_def = {
                "id": str(uuid.uuid4()),
                "title": f"Keg {i+1:02}",
                "tare_weight_kg": 4.50,         
                "starting_total_weight_kg": 4.50, # Full weight = Tare weight -> 0 Volume
                "maximum_full_volume_liters": 18.93,
                "calculated_starting_volume_liters": 0.0, # Explicitly 0.0
                "current_dispensed_liters": 0.0,
                "total_dispensed_pulses": 0,
                # --- NEW RICH DATA FIELDS ---
                "beverage_id": UNASSIGNED_BEVERAGE_ID,
                "fill_date": "" 
            }
            # Recalculate to ensure consistency
            keg_def["calculated_starting_volume_liters"] = self._calculate_volume_from_weight(
                keg_def["starting_total_weight_kg"], keg_def["tare_weight_kg"]
            )
            defs.append(keg_def)
        return defs

           
    def _get_default_sensor_keg_assignments(self):
        return [UNASSIGNED_KEG_ID] * self.num_sensors
        
    def _get_default_beverage_assignments(self):
        default_beverages = self._get_default_beverage_library().get('beverages', []) 
        default_id = default_beverages[0]['id'] if default_beverages else None 
        return [default_id] * self.num_sensors

    def _get_default_push_notification_settings(self):
        return {
            "notification_type": "None", "frequency": "Daily", "server_email": "", "server_password": "", 
            "email_recipient": "", "smtp_server": "", "smtp_port": "", "sms_number": "", 
            "sms_carrier_gateway": "",
            "notify_on_update": True
        }
    
    def _get_default_status_request_settings(self):
        return {
            "enable_status_request": False,
            "authorized_sender": "",
            "rpi_email_address": "",
            "rpi_email_password": "",
            "imap_server": "",
            "imap_port": "",
            "smtp_server": "",
            "smtp_port": ""
        }
        
    def _get_default_conditional_notification_settings(self):
        return {
            "notification_type": "None", "threshold_liters": 4.0, "sent_notifications": [False] * self.num_sensors, 
            "low_temp_f": 35.0, "high_temp_f": 45.0, "temp_sent_timestamps": [], 
            "error_reported_times": {"push": 0, "volume": 0, "temperature": 0}
        }
    
    # settings_manager.py

    def _get_default_system_settings(self):
        return {
            "display_units": "metric", "displayed_taps": 5, "ds18b20_ambient_sensor": "unassigned", 
            "ui_mode": "basic", "autostart_enabled": False, 
            "launch_workflow_on_start": False,
            "flow_calibration_factors": [DEFAULT_K_FACTOR] * self.num_sensors,
            "metric_pour_ml": 355, "imperial_pour_oz": 12,
            "flow_calibration_notes": "", "flow_calibration_to_be_poured": 500.0,
            "last_pour_averages": [0.0] * self.num_sensors,
            "last_pour_volumes": [0.0] * self.num_sensors,
            "force_numlock": False,
            "eula_agreed": False,
            "show_eula_on_launch": True,
            "window_geometry": None,
            "check_updates_on_launch": True,
            "notify_on_update": True,
            "setup_complete": False,
            "workflow_view_mode": "paged",
            "workflow_window_geometry": None,
            "enable_pour_log": True,
            # --- NEW: Calibration Preference ---
            "calibration_deduct_inventory": True,
            # --- NEW: App Window Persistence ---
            "window_x": -1,
            "window_y": -1,
            "window_width": 800,
            "window_height": 417
        }
    
    # --- NEW METHODS for App Window Persistence ---
    # --- NEW METHODS for App Window Persistence ---
    def get_app_window_settings(self):
        """Returns a dict with x, y, width, height."""
        defaults = self._get_default_system_settings()
        sys_set = self.settings.get('system_settings', {})
        
        # Enforce minimum boundaries at the data level
        raw_width = sys_set.get("window_width", defaults["window_width"])
        raw_height = sys_set.get("window_height", defaults["window_height"])
        
        return {
            "x": sys_set.get("window_x", defaults["window_x"]),
            "y": sys_set.get("window_y", defaults["window_y"]),
            "width": max(raw_width, 800),
            "height": max(raw_height, 418)
        }
        
    def save_app_window_settings(self, x, y, width, height):
        sys_set = self.settings.get('system_settings', self._get_default_system_settings())
        sys_set['window_x'] = int(x)
        sys_set['window_y'] = int(y)
        sys_set['window_width'] = int(width)
        sys_set['window_height'] = int(height)
        self.settings['system_settings'] = sys_set
        self._save_all_settings()
    
    # --- NEW METHODS for Calibration Preferences ---
    def get_calibration_deduct_inventory(self):
        return self.get_system_settings().get('calibration_deduct_inventory', True)

    def save_calibration_deduct_inventory(self, enabled):
        sys_set = self.settings.get('system_settings', self._get_default_system_settings())
        sys_set['calibration_deduct_inventory'] = bool(enabled)
        self.settings['system_settings'] = sys_set
        self._save_all_settings()
        print(f"SettingsManager: Calibration deduct inventory saved: {enabled}")

    # --- NEW METHODS for Pour Log ---
    def get_enable_pour_log(self):
        return self.settings.get('system_settings', {}).get('enable_pour_log', True)

    def save_enable_pour_log(self, is_enabled):
        sys_set = self.settings.get('system_settings', self._get_default_system_settings())
        sys_set['enable_pour_log'] = bool(is_enabled)
        self.settings['system_settings'] = sys_set
        self._save_all_settings()
        print(f"SettingsManager: Enable Pour Log saved: {is_enabled}")

    # --- NEW METHODS for Workflow Window Geometry ---
    def get_workflow_window_geometry(self):
        return self.get_system_settings().get('workflow_window_geometry')

    def save_workflow_window_geometry(self, geometry_string):
        sys_set = self.settings.get('system_settings', self._get_default_system_settings())
        sys_set['workflow_window_geometry'] = geometry_string
        self.settings['system_settings'] = sys_set
        self._save_all_settings()

    # --- NEW METHODS for Workflow View Mode ---
    def get_workflow_view_mode(self):
        """Returns 'paged' or 'dashboard'."""
        return self.get_system_settings().get('workflow_view_mode', 'paged')

    def save_workflow_view_mode(self, mode):
        if mode in ['paged', 'dashboard']:
            sys_set = self.settings.get('system_settings', self._get_default_system_settings())
            sys_set['workflow_view_mode'] = mode
            self.settings['system_settings'] = sys_set
            self._save_all_settings()
            print(f"SettingsManager: Workflow view mode saved: {mode}")
        
    def get_setup_complete(self):
        return self.get_system_settings().get('setup_complete', False)

    def set_setup_complete(self, is_complete):
        sys_set = self.settings.get('system_settings', self._get_default_system_settings())
        sys_set['setup_complete'] = bool(is_complete)
        self.settings['system_settings'] = sys_set
        self._save_all_settings()
        print(f"SettingsManager: Setup Complete flag set to {is_complete}")

    # --- NEW METHODS for Last Pour Volume ---
    def get_last_pour_volumes(self):
        defaults = self._get_default_system_settings().get('last_pour_volumes')
        vols = self.settings.get('system_settings', {}).get('last_pour_volumes', defaults)
        if not isinstance(vols, list) or len(vols) != self.num_sensors:
            return [0.0] * self.num_sensors
        return [float(x) for x in vols]

    def save_last_pour_volumes(self, volumes_list):
        if len(volumes_list) == self.num_sensors:
            self.settings.setdefault('system_settings', self._get_default_system_settings())['last_pour_volumes'] = volumes_list
            self._save_all_settings()

    # --- NEW METHODS for Num Lock ---
    def get_force_numlock(self):
        return self.get_system_settings().get('force_numlock', False)

    def save_force_numlock(self, enabled):
        sys_set = self.settings.get('system_settings', self._get_default_system_settings())
        sys_set['force_numlock'] = bool(enabled)
        self.settings['system_settings'] = sys_set
        self._save_all_settings()
        print(f"SettingsManager: Force Num Lock saved: {enabled}")

        # --- NEW METHODS for Smart Flow Rate ---
    def get_last_pour_averages(self):
        defaults = self._get_default_system_settings().get('last_pour_averages')
        avgs = self.settings.get('system_settings', {}).get('last_pour_averages', defaults)
        if not isinstance(avgs, list) or len(avgs) != self.num_sensors:
            return [0.0] * self.num_sensors
        return [float(x) for x in avgs]

    def save_last_pour_averages(self, averages_list):
        if len(averages_list) == self.num_sensors:
            self.settings.setdefault('system_settings', self._get_default_system_settings())['last_pour_averages'] = averages_list
            self._save_all_settings()

    def get_system_settings(self):
        defaults = self._get_default_system_settings() 
        sys_set = self.settings.get('system_settings', defaults).copy() 
        
        # Ensure all default keys exist
        for key, val in defaults.items():
            if key not in sys_set:
                sys_set[key] = val
            
        return sys_set

    def get_check_updates_on_launch(self):
        return self.get_system_settings().get('check_updates_on_launch', True)

    def save_check_updates_on_launch(self, enabled):
        sys_set = self.settings.get('system_settings', self._get_default_system_settings())
        sys_set['check_updates_on_launch'] = bool(enabled)
        self.settings['system_settings'] = sys_set
        self._save_all_settings()
        print(f"SettingsManager: Check updates on launch saved: {enabled}")

    def get_window_geometry(self):
        return self.get_system_settings().get('window_geometry')

    def save_window_geometry(self, geometry_string):
        sys_set = self.settings.get('system_settings', self._get_default_system_settings())
        sys_set['window_geometry'] = geometry_string
        self.settings['system_settings'] = sys_set
        self._save_all_settings()

    def _get_default_beverage_library(self):
        return {
            "beverages": [
                {
                    "id": str(uuid.uuid4()), "name": "House Pale Ale", "bjcp": "18(b)", "abv": "5.0", 
                    "ibu": 35, "srm": 5, "description": "A refreshing, hop-forward American Pale Ale with a balanced malt background and a clean, dry finish. Our go-to beer."
                }
            ]
        }

    def _calculate_volume_from_weight(self, total_weight_kg, empty_weight_kg, density=1.014):
        liquid_weight_kg = total_weight_kg - empty_weight_kg
        return max(0.0, liquid_weight_kg / density)

    def _calculate_weight_from_volume(self, volume_liters, empty_weight_kg, density=1.014):
        liquid_weight_kg = volume_liters * density
        return empty_weight_kg + liquid_weight_kg
    
    def __init__(self, num_sensors_expected):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        print(f"SettingsManager: Using script path: {base_dir}")
        self.base_dir = base_dir 
        
        # --- PATH CHANGE FOR LITE VERSION ---
        self.data_dir = os.path.abspath(os.path.join(self.base_dir, "..", "..", "keglevel_lite-data"))
        print(f"SettingsManager: Using data path: {self.data_dir}")
        
        if not os.path.exists(self.data_dir):
            try:
                os.makedirs(self.data_dir)
                print(f"SettingsManager: Created data directory at {self.data_dir}")
            except Exception as e:
                print(f"SettingsManager: CRITICAL ERROR: Could not create data directory: {e}")
            
        self.settings_file_path = os.path.join(self.data_dir, SETTINGS_FILE)
        self.beverages_file_path = os.path.join(self.data_dir, BEVERAGES_FILE)
        self.process_flow_file_path = os.path.join(self.data_dir, PROCESS_FLOW_FILE)
        self.keg_library_file_path = os.path.join(self.data_dir, KEG_LIBRARY_FILE)
        self.trial_record_file_path = os.path.join(self.data_dir, TRIAL_RECORD_FILE)
        self.bjcp_2021_file_path = os.path.join(self.data_dir, BJCP_2021_FILE)

        self.num_sensors = num_sensors_expected
        
        self.beverage_library = self._load_beverage_library()
        self.keg_library, self.keg_map = self._load_keg_library()
        self.settings = self._load_settings()

    def get_base_dir(self):
        return self.base_dir
    
    def get_data_dir(self):
        return self.data_dir

    def _load_keg_library(self):
        defaults = self._get_default_keg_definitions()
        if os.path.exists(self.keg_library_file_path):
            try:
                with open(self.keg_library_file_path, 'r') as f: 
                    library = json.load(f) 
                    if not isinstance(library.get('kegs'), list) or not library.get('kegs'): 
                         print(f"Keg Library: Contents corrupted or empty. Using default.") 
                         library = {"kegs": defaults}
                    
                    keg_list = library.get('kegs', [])
                    
                    migrated_list = []
                    default_keg_profile = self._get_default_keg_definitions()[0]
                    library_was_modified = False 
                    
                    # Load RAW settings to avoid circular dependency for migration check
                    raw_settings = {}
                    if os.path.exists(self.settings_file_path):
                        try:
                            with open(self.settings_file_path, 'r') as sf:
                                raw_settings = json.load(sf)
                        except Exception: pass
                    
                    current_keg_assignments = raw_settings.get('sensor_keg_assignments', [])
                    current_bev_assignments = raw_settings.get('sensor_beverage_assignments', [])
                    
                    while len(current_keg_assignments) < self.num_sensors: current_keg_assignments.append(UNASSIGNED_KEG_ID)
                    while len(current_bev_assignments) < self.num_sensors: current_bev_assignments.append(UNASSIGNED_BEVERAGE_ID)

                    active_map = {}
                    for i, k_id in enumerate(current_keg_assignments):
                        if k_id != UNASSIGNED_KEG_ID and i < len(current_bev_assignments):
                            active_map[k_id] = current_bev_assignments[i]

                    for k in keg_list:
                        if 'empty_weight_kg' in k:
                            k['tare_weight_kg'] = k.pop('empty_weight_kg')
                            library_was_modified = True
                        if 'starting_volume_liters' in k:
                            k['calculated_starting_volume_liters'] = k.pop('starting_volume_liters')
                            library_was_modified = True
                        if 'maximum_full_volume_liters' not in k:
                             k['maximum_full_volume_liters'] = default_keg_profile['maximum_full_volume_liters']
                             library_was_modified = True
                        if 'tare_weight_kg' not in k: k['tare_weight_kg'] = default_keg_profile['tare_weight_kg']; library_was_modified = True
                        if 'starting_total_weight_kg' not in k: k['starting_total_weight_kg'] = default_keg_profile['starting_total_weight_kg']; library_was_modified = True
                        if 'calculated_starting_volume_liters' not in k: k['calculated_starting_volume_liters'] = default_keg_profile['calculated_starting_volume_liters']; library_was_modified = True
                        if 'current_dispensed_liters' not in k: k['current_dispensed_liters'] = default_keg_profile['current_dispensed_liters']; library_was_modified = True
                        
                        existing_liters = k.get('current_dispensed_liters', 0.0)
                        current_pulses = k.get('total_dispensed_pulses', 0)
                        if 'total_dispensed_pulses' not in k:
                            k['total_dispensed_pulses'] = int(existing_liters * DEFAULT_K_FACTOR)
                            library_was_modified = True
                        elif current_pulses == 0 and existing_liters > 0.01:
                            k['total_dispensed_pulses'] = int(existing_liters * DEFAULT_K_FACTOR)
                            library_was_modified = True
                            
                        if 'beverage_id' not in k:
                            k_id = k.get('id')
                            if k_id in active_map:
                                k['beverage_id'] = active_map[k_id]
                                k['fill_date'] = datetime.now().strftime("%Y-%m-%d")
                            else:
                                k['beverage_id'] = UNASSIGNED_BEVERAGE_ID
                                k['fill_date'] = ""
                            library_was_modified = True
                            
                        if 'fill_date' not in k:
                            k['fill_date'] = ""
                            library_was_modified = True

                        migrated_list.append(k)

                    library['kegs'] = migrated_list
                    
                    if library_was_modified:
                        print("SettingsManager: Keg library migration detected. Updating file on disk.")
                        self._save_keg_library(library)

                    keg_map = {k['id']: k for k in migrated_list if 'id' in k}
                    return library, keg_map
            except Exception as e:
                print(f"Keg Library: Error loading or decoding JSON: {e}. Using default.") 
                return {"kegs": defaults}, {k['id']: k for k in defaults}
        else:
            print(f"{KEG_LIBRARY_FILE} not found. Creating with defaults.") 
            library = {"kegs": defaults}
            self._save_keg_library(library) 
            return library, {k['id']: k for k in defaults}

    def _save_keg_library(self, library):
        try:
            with open(self.keg_library_file_path, 'w', encoding='utf-8') as f: 
                json.dump(library, f, indent=4) 
            print(f"Keg Library saved to {self.keg_library_file_path}.") 
        except Exception as e:
            print(f"Error saving keg library: {e}")

    def get_keg_definitions(self):
        self.keg_library, self.keg_map = self._load_keg_library()
        return self.keg_library.get('kegs', [])
    
    def save_keg_definitions(self, definitions_list):
        if not definitions_list:
            definitions_list = self._get_default_keg_definitions()
        
        self.keg_library['kegs'] = definitions_list
        self.keg_map = {k['id']: k for k in definitions_list}
        self._save_keg_library(self.keg_library)
        print("Keg definitions saved.") 
        
    def delete_keg_definition(self, keg_id_to_delete):
        keg_list = self.get_keg_definitions()
        new_keg_list = [k for k in keg_list if k.get('id') != keg_id_to_delete]
        
        if len(new_keg_list) == len(keg_list):
            return False, "Keg ID not found."

        self.save_keg_definitions(new_keg_list)
        
        assignments = self.get_sensor_keg_assignments()
        first_kept_id = new_keg_list[0]['id'] if new_keg_list else UNASSIGNED_KEG_ID

        needs_assignment_update = False
        for i in range(len(assignments)):
            if assignments[i] == keg_id_to_delete:
                assignments[i] = first_kept_id
                needs_assignment_update = True
        
        if needs_assignment_update:
            for i in range(len(assignments)): 
                self.save_sensor_keg_assignment(i, assignments[i])
            print(f"SettingsManager: Re-assigned taps after deleting Keg ID {keg_id_to_delete}.")
        
        return True, "Keg deleted and assignments updated."
        
    def update_keg_dispensed_volume(self, keg_id, dispensed_liters, pulses=0):
        if keg_id in self.keg_map:
            self.keg_map[keg_id]['current_dispensed_liters'] = dispensed_liters
            current_pulses = self.keg_map[keg_id].get('total_dispensed_pulses', 0)
            self.keg_map[keg_id]['total_dispensed_pulses'] = current_pulses + pulses

            for keg in self.keg_library['kegs']:
                if keg.get('id') == keg_id:
                    keg['current_dispensed_liters'] = dispensed_liters
                    keg['total_dispensed_pulses'] = self.keg_map[keg_id]['total_dispensed_pulses']
                    break
            return True
        return False

    def save_all_keg_dispensed_volumes(self):
        self._save_keg_library(self.keg_library)
        
    def get_keg_by_id(self, keg_id):
        if keg_id == UNASSIGNED_KEG_ID:
            return {"id": UNASSIGNED_KEG_ID, "title": "Offline", "starting_volume_liters": 0.0, "current_dispensed_liters": 0.0}
        return self.keg_map.get(keg_id)
        
    def _load_beverage_library(self):
        if os.path.exists(self.beverages_file_path):
            try:
                with open(self.beverages_file_path, 'r') as f: 
                    library = json.load(f) 
                    if not isinstance(library.get('beverages'), list):
                         print(f"Beverage Library: Error loading library. Contents corrupted. Using default.") 
                         library = {"beverages": self._get_default_beverage_library().get('beverages', [])}
                    
                    beverages = library.get('beverages', [])
                    modified = False
                    for b in beverages:
                        if 'srm' not in b:
                            b['srm'] = None
                            modified = True
                        elif isinstance(b['srm'], float):
                            b['srm'] = int(b['srm'])
                            modified = True
                    
                    if modified:
                        print("SettingsManager: Migrated beverage library to ensure SRM is present and integer.")
                        self._save_beverage_library(library)

                    return library
            except Exception as e:
                print(f"Beverage Library: Error loading or decoding JSON: {e}. Using default.") 
                return {"beverages": self._get_default_beverage_library().get('beverages', [])}
        else:
            print(f"{BEVERAGES_FILE} not found. Creating with defaults.") 
            default_library = self._get_default_beverage_library() 
            self._save_beverage_library(default_library) 
            return default_library
            
    def _save_beverage_library(self, library):
        try:
            with open(self.beverages_file_path, 'w', encoding='utf-8') as f: 
                json.dump(library, f, indent=4) 
            print(f"Beverage Library saved to {self.beverages_file_path}.") 
        except Exception as e:
            print(f"Error saving beverage library: {e}")

    def get_beverage_library(self):
        return self.beverage_library 

    def save_beverage_library(self, new_library_list):
        self.beverage_library['beverages'] = new_library_list
        self._save_beverage_library(self.beverage_library)

    def load_bjcp_styles(self):
        """Loads the strict BJCP styles from the central JSON file."""
        bjcp_file = os.path.join(self.get_base_dir(), "assets", "bjcp_styles.json")
        try:
            with open(bjcp_file, 'r', encoding='utf-8') as f:
                styles = json.load(f)
                return styles
        except Exception as e:
            print(f"SettingsManager Error: Could not load BJCP styles: {e}")
            return []

    # --- Load/Reset Settings ---

    def _load_settings(self, force_defaults=False):
        settings = {}

        default_sensor_labels = self._get_default_sensor_labels()
        default_sensor_keg_assignments = self._get_default_sensor_keg_assignments() 
        default_sensor_beverage_assignments = self._get_default_beverage_assignments() 
        default_system_settings_val = self._get_default_system_settings() 
        default_push_notification_settings_val = self._get_default_push_notification_settings() 
        default_status_request_settings_val = self._get_default_status_request_settings()
        default_conditional_notification_settings_val = self._get_default_conditional_notification_settings() 

        if not force_defaults and os.path.exists(self.settings_file_path):
            try:
                with open(self.settings_file_path, 'r') as f: 
                    settings = json.load(f) 
                print(f"Settings loaded from {self.settings_file_path}") 
            except Exception as e:
                print(f"Error loading or decoding JSON from {self.settings_file_path}: {e}. Using all defaults.") 
                settings = {}
        else:
            if force_defaults: print("Forcing reset to default settings.")
            else: print(f"{self.settings_file_path} not found. Creating with defaults.")
            settings = {}

        is_new_file_or_major_corruption = not os.path.exists(self.settings_file_path) or not settings
        
        if 'sensor_labels' not in settings or not isinstance(settings.get('sensor_labels',[]), list) or len(settings.get('sensor_labels',[])) != self.num_sensors: 
            settings['sensor_labels'] = default_sensor_labels 
        
        if 'sensor_beverage_assignments' not in settings or not isinstance(settings.get('sensor_beverage_assignments', []), list) or len(settings.get('sensor_beverage_assignments', [])) != self.num_sensors: 
            settings['sensor_beverage_assignments'] = default_sensor_beverage_assignments 
            if not is_new_file_or_major_corruption: print("Settings: sensor_beverage_assignments initialized/adjusted.") 
        else:
            assignments = settings['sensor_beverage_assignments'] 
            valid_ids = [b['id'] for b in self.beverage_library.get('beverages', []) if 'id' in b] 
            valid_ids.append(UNASSIGNED_BEVERAGE_ID)
            for i in range(len(assignments)):
                if assignments[i] not in valid_ids: 
                    assignments[i] = default_sensor_beverage_assignments[i] 
            settings['sensor_beverage_assignments'] = assignments 
            
        if 'keg_definitions' in settings:
             del settings['keg_definitions']
        
        default_keg_assignment_id = UNASSIGNED_KEG_ID
        
        if 'sensor_keg_assignments' not in settings or not isinstance(settings.get('sensor_keg_assignments', []), list) or len(settings.get('sensor_keg_assignments', [])) != self.num_sensors: 
            settings['sensor_keg_assignments'] = default_sensor_keg_assignments 
            if not is_new_file_or_major_corruption: print("Settings: sensor_keg_assignments initialized/adjusted.") 
        else:
            assignments = settings['sensor_keg_assignments'] 
            valid_keg_ids = self.keg_map.keys()
            for i_assign in range(len(assignments)): 
                if assignments[i_assign] != UNASSIGNED_KEG_ID and assignments[i_assign] not in valid_keg_ids:
                    assignments[i_assign] = default_keg_assignment_id
            settings['sensor_keg_assignments'] = assignments 

        if 'system_settings' not in settings or not isinstance(settings.get('system_settings'), dict): 
            settings['system_settings'] = default_system_settings_val 
            if not is_new_file_or_major_corruption: print("Settings: system_settings initialized/adjusted.") 
        else:
            # FIX: Check for legacy migration BEFORE merging with defaults
            # If 'setup_complete' is MISSING in the file, we need to detect if it's an existing user
            raw_sys_settings = settings.get('system_settings', {})
            needs_migration_check = 'setup_complete' not in raw_sys_settings
            
            sys_set = default_system_settings_val.copy() 
            sys_set.update(settings['system_settings']) 
            
            if 'velocity_mode' in sys_set: del sys_set['velocity_mode']
            if 'user_temp_input_c' in sys_set: del sys_set['user_temp_input_c']
            
            settings['system_settings'] = sys_set 
            
            # --- MIGRATION: UI MODE (Full/Lite -> Detailed/Basic) ---
            current_mode = settings['system_settings'].get('ui_mode')
            if current_mode == 'full':
                settings['system_settings']['ui_mode'] = 'detailed'
                print("Settings: Migrated UI Mode 'full' -> 'detailed'")
            elif current_mode == 'lite':
                settings['system_settings']['ui_mode'] = 'basic'
                print("Settings: Migrated UI Mode 'lite' -> 'basic'")
            elif current_mode not in ["detailed", "basic"]:
                 settings['system_settings']['ui_mode'] = default_system_settings_val['ui_mode']
            
            # --- MIGRATION: DETECT LEGACY INSTALL ---
            if needs_migration_check:
                # Heuristic: If we have valid keg assignments (not default unassigned)
                assignments = settings.get('sensor_keg_assignments', [])
                has_active_kegs = any(k != UNASSIGNED_KEG_ID for k in assignments)
                
                # Also check for non-default labels if they customized them
                labels = settings.get('sensor_labels', [])
                has_custom_labels = any(l != f"Tap {i+1}" for i, l in enumerate(labels))
                
                # If it looks like a configured system, mark setup as complete
                if has_active_kegs or has_custom_labels:
                    print("Settings: Legacy installation detected. Auto-completing setup.")
                    settings['system_settings']['setup_complete'] = True
                else:
                    # Otherwise it's effectively a new install (or reset state)
                    settings['system_settings']['setup_complete'] = False
            # -----------------------------------------------------------------

            if settings['system_settings'].get('display_units') not in ["imperial", "metric"]: 
                settings['system_settings']['display_units'] = default_system_settings_val['display_units'] 
            current_displayed_taps = settings['system_settings'].get('displayed_taps', self.num_sensors) 
            try: current_displayed_taps = int(current_displayed_taps) 
            except ValueError: current_displayed_taps = self.num_sensors 
            if not (1 <= current_displayed_taps <= self.num_sensors): 
                settings['system_settings']['displayed_taps'] = default_system_settings_val['displayed_taps'] 
            else: 
                settings['system_settings']['displayed_taps'] = current_displayed_taps 
            if 'ds18b20_ambient_sensor' not in settings['system_settings']: 
                settings['system_settings']['ds18b20_ambient_sensor'] = default_system_settings_val['ds18b20_ambient_sensor'] 
            if 'ui_mode' not in settings['system_settings'] or settings['system_settings']['ui_mode'] not in ["detailed", "basic"]:
                 settings['system_settings']['ui_mode'] = default_system_settings_val['ui_mode']
            
            if 'flow_calibration_factors' not in settings['system_settings'] or not isinstance(settings.get('flow_calibration_factors', []), list) or len(settings['system_settings'].get('flow_calibration_factors', [])) != self.num_sensors:
                 settings['system_settings']['flow_calibration_factors'] = default_system_settings_val['flow_calibration_factors']
            else:
                 try:
                      settings['system_settings']['flow_calibration_factors'] = [float(f) for f in settings['system_settings']['flow_calibration_factors']]
                 except (ValueError, TypeError):
                      settings['system_settings']['flow_calibration_factors'] = default_system_settings_val['flow_calibration_factors']

            if 'metric_pour_ml' not in settings['system_settings']:
                settings['system_settings']['metric_pour_ml'] = default_system_settings_val['metric_pour_ml']
            else:
                try: settings['system_settings']['metric_pour_ml'] = int(settings['system_settings']['metric_pour_ml'])
                except (ValueError, TypeError): settings['system_settings']['metric_pour_ml'] = default_system_settings_val['metric_pour_ml']
            
            if 'imperial_pour_oz' not in settings['system_settings']:
                settings['system_settings']['imperial_pour_oz'] = default_system_settings_val['imperial_pour_oz']
            else:
                try: settings['system_settings']['imperial_pour_oz'] = int(settings['system_settings']['imperial_pour_oz'])
                except (ValueError, TypeError): settings['system_settings']['imperial_pour_oz'] = default_system_settings_val['imperial_pour_oz']
            
            if 'flow_calibration_notes' not in settings['system_settings'] or not isinstance(settings['system_settings']['flow_calibration_notes'], str):
                 settings['system_settings']['flow_calibration_notes'] = default_system_settings_val['flow_calibration_notes']
            
            if 'flow_calibration_to_be_poured' not in settings['system_settings']:
                 settings['system_settings']['flow_calibration_to_be_poured'] = default_system_settings_val['flow_calibration_to_be_poured']
            else:
                try: 
                    settings['system_settings']['flow_calibration_to_be_poured'] = float(settings['system_settings']['flow_calibration_to_be_poured'])
                except (ValueError, TypeError): 
                    settings['system_settings']['flow_calibration_to_be_poured'] = default_system_settings_val['flow_calibration_to_be_poured']

            if 'eula_agreed' not in settings['system_settings']:
                settings['system_settings']['eula_agreed'] = default_system_settings_val['eula_agreed']
            if 'show_eula_on_launch' not in settings['system_settings']:
                settings['system_settings']['show_eula_on_launch'] = default_system_settings_val['show_eula_on_launch']
            
            # Validation for Window Geometry
            if 'window_geometry' not in settings['system_settings']:
                settings['system_settings']['window_geometry'] = default_system_settings_val['window_geometry']

            # Validation for Update Check
            if 'check_updates_on_launch' not in settings['system_settings']:
                settings['system_settings']['check_updates_on_launch'] = default_system_settings_val['check_updates_on_launch']
                
            # Validation for Setup Complete
            if 'setup_complete' not in settings['system_settings']:
                settings['system_settings']['setup_complete'] = default_system_settings_val['setup_complete']

        loaded_notif_settings = {}
        if 'notification_settings' in settings: 
            print("Settings: Migrating old 'notification_settings' to 'push_notification_settings'.") 
            loaded_notif_settings = settings.pop('notification_settings') 
        elif 'push_notification_settings' in settings:
            loaded_notif_settings = settings.pop('push_notification_settings') 
        
        notif_set = default_push_notification_settings_val.copy() 
        notif_set.update(loaded_notif_settings) 
        settings['push_notification_settings'] = notif_set 
        if notif_set.get('notification_type') not in ["None", "Email", "Text", "Both"]: notif_set['notification_type'] = default_push_notification_settings_val['notification_type'] 
        if notif_set.get('frequency') not in ["Hourly", "Daily", "Weekly", "Monthly"]: notif_set['frequency'] = default_push_notification_settings_val['frequency'] 
        
        port_val = notif_set.get('smtp_port', default_push_notification_settings_val['smtp_port'])
        notif_set.setdefault('smtp_server', default_push_notification_settings_val['smtp_server'])
        try:
            port_str = str(port_val).strip()
            if port_str.isdigit():
                notif_set['smtp_port'] = int(port_str)
            else:
                notif_set['smtp_port'] = "" 
        except Exception: 
            notif_set['smtp_port'] = ""
        
        loaded_status_request_settings = settings.pop('status_request_settings', {})
        status_req_set = default_status_request_settings_val.copy()
        status_req_set.update(loaded_status_request_settings)
        settings['status_request_settings'] = status_req_set
        
        for key in ['imap_port', 'smtp_port']:
            port_val = status_req_set.get(key)
            try:
                port_str = str(port_val).strip()
                if port_str.isdigit():
                    status_req_set[key] = int(port_str)
                else:
                    status_req_set[key] = ""
            except Exception:
                status_req_set[key] = ""
        
        if 'conditional_notification_settings' not in settings or not isinstance(settings.get('conditional_notification_settings'), dict):
            settings['conditional_notification_settings'] = default_conditional_notification_settings_val 
            if not is_new_file_or_major_corruption: print("Settings: conditional_notification_settings initialized/adjusted.") 
        cond_set = default_conditional_notification_settings_val.copy()
        if 'conditional_notification_settings' in settings:
            cond_set.update(settings['conditional_notification_settings'])
        
        settings['conditional_notification_settings'] = cond_set
        
        if len(settings['conditional_notification_settings'].get('sent_notifications', [])) != self.num_sensors:
            settings['conditional_notification_settings']['sent_notifications'] = [False] * self.num_sensors 
        if 'temp_sent_timestamps' not in settings['conditional_notification_settings'] or not isinstance(settings['conditional_notification_settings']['temp_sent_timestamps'], list): 
            settings['conditional_notification_settings']['temp_sent_timestamps'] = [] 
        
        if 'error_reported_times' not in settings['conditional_notification_settings'] or not isinstance(settings.get('conditional_notification_settings', {}).get('error_reported_times'), dict):
             settings['conditional_notification_settings']['error_reported_times'] = default_conditional_notification_settings_val['error_reported_times']
        else:
             merged_errors = default_conditional_notification_settings_val['error_reported_times'].copy()
             merged_errors.update(cond_set.get('error_reported_times', {}))
             settings['conditional_notification_settings']['error_reported_times'] = merged_errors
        
        try:
            settings['conditional_notification_settings']['threshold_liters'] = float(settings['conditional_notification_settings']['threshold_liters']) 
            settings['conditional_notification_settings']['low_temp_f'] = float(settings['conditional_notification_settings']['low_temp_f']) 
            settings['conditional_notification_settings']['high_temp_f'] = float(settings['conditional_notification_settings']['high_temp_f']) 
        except (ValueError, TypeError):
            print("Settings: Conditional notification thresholds corrupted. Resetting to defaults.") 
            settings['conditional_notification_settings']['threshold_liters'] = default_conditional_notification_settings_val['threshold_liters'] 
            settings['conditional_notification_settings']['low_temp_f'] = default_conditional_notification_settings_val['low_temp_f'] 
            settings['conditional_notification_settings']['high_temp_f'] = default_conditional_notification_settings_val['high_temp_f'] 

        if force_defaults or is_new_file_or_major_corruption:
             self._save_all_settings(current_settings=settings)
        return settings
        
    def reset_all_settings_to_defaults(self):
        print("SettingsManager: Resetting all settings to their default values.") 
        
        self.beverage_library = self._get_default_beverage_library() 
        self._save_beverage_library(self.beverage_library) 
        
        self.keg_library = {"kegs": self._get_default_keg_definitions()}
        self.keg_map = {k['id']: k for k in self.keg_library['kegs']}
        self._save_keg_library(self.keg_library)
        
        self.settings = {
            'sensor_labels': self._get_default_sensor_labels(), 
            'sensor_keg_assignments': self._get_default_sensor_keg_assignments(), 
            'sensor_beverage_assignments': self._get_default_beverage_assignments(), 
            'system_settings': self._get_default_system_settings(), 
            'push_notification_settings': self._get_default_push_notification_settings(), 
            'status_request_settings': self._get_default_status_request_settings(),
            'conditional_notification_settings': self._get_default_conditional_notification_settings(), 
        }
        self._save_all_settings() 
        print("SettingsManager: All settings have been reset to defaults and saved.")
        
    # --- NEW HELPER: Load workflow data from disk directly ---
    def _get_workflow_data_from_disk(self):
        base_dir = self.get_data_dir()
        workflow_file = os.path.join(base_dir, "process_flow.json")
        beverage_library = self.get_beverage_library()
        beverage_map = {b['id']: b['name'] for b in beverage_library.get('beverages', []) if 'id' in b and 'name' in b}
        
        if os.path.exists(workflow_file):
            try:
                with open(workflow_file, 'r') as f:
                    data = json.load(f)
                    return data.get('columns', {}), beverage_map
            except Exception:
                return {}, beverage_map
        return {}, beverage_map

    def get_ui_mode(self): return self.settings.get('system_settings', {}).get('ui_mode', 'basic')
    def save_ui_mode(self, mode_string):
        if mode_string in ["detailed", "basic"]:
            self.settings.setdefault('system_settings', self._get_default_system_settings())['ui_mode'] = mode_string
            self._save_all_settings()
            print(f"SettingsManager: UI Mode saved to {mode_string}.")

    def get_autostart_enabled(self): return self.settings.get('system_settings', {}).get('autostart_enabled', self._get_default_system_settings()['autostart_enabled'])
    def save_autostart_enabled(self, is_enabled):
        self.settings.setdefault('system_settings', self._get_default_system_settings())['autostart_enabled'] = bool(is_enabled)
        self._save_all_settings()
        print(f"SettingsManager: Autostart setting saved as {is_enabled}.")

    def get_launch_workflow_on_start(self): return self.settings.get('system_settings', {}).get('launch_workflow_on_start', self._get_default_system_settings()['launch_workflow_on_start'])
    def save_launch_workflow_on_start(self, is_enabled):
        self.settings.setdefault('system_settings', self._get_default_system_settings())['launch_workflow_on_start'] = bool(is_enabled)
        self._save_all_settings()
        print(f"SettingsManager: Launch workflow on start setting saved as {is_enabled}.")

    def get_flow_calibration_factors(self):
        defaults = self._get_default_system_settings().get('flow_calibration_factors')
        factors = self.settings.get('system_settings', {}).get('flow_calibration_factors', defaults)
        if len(factors) != self.num_sensors: return defaults 
        try:
             return [float(f) for f in factors]
        except (ValueError, TypeError):
             return defaults 
    
    def save_flow_calibration_factors(self, factors_list):
        if len(factors_list) == self.num_sensors:
            self.settings.setdefault('system_settings', self._get_default_system_settings())['flow_calibration_factors'] = factors_list
            self._save_all_settings()
            print(f"SettingsManager: Flow calibration factors saved.")

    def get_flow_calibration_settings(self):
        defaults = self._get_default_system_settings()
        sys_set = self.settings.get('system_settings', defaults)
        return {
            'notes': sys_set.get('flow_calibration_notes', defaults['flow_calibration_notes']),
            'to_be_poured': sys_set.get('flow_calibration_to_be_poured', defaults['flow_calibration_to_be_poured'])
        }

    def save_flow_calibration_settings(self, to_be_poured_value=None, notes=None):
        sys_set = self.settings.setdefault('system_settings', self._get_default_system_settings())
        
        if to_be_poured_value is not None:
            try:
                sys_set['flow_calibration_to_be_poured'] = float(to_be_poured_value)
            except (ValueError, TypeError):
                print("SettingsManager Error: Invalid flow_calibration_to_be_poured format for saving.")
                return
        
        if notes is not None:
            sys_set['flow_calibration_notes'] = str(notes)
            
        self._save_all_settings()
        print("SettingsManager: Flow calibration notes/to_be_poured saved.")

    def get_pour_volume_settings(self):
        defaults = self._get_default_system_settings()
        sys_set = self.settings.get('system_settings', defaults)
        return {
            'metric_pour_ml': sys_set.get('metric_pour_ml', defaults['metric_pour_ml']),
            'imperial_pour_oz': sys_set.get('imperial_pour_oz', defaults['imperial_pour_oz'])
        }

    def save_pour_volume_settings(self, metric_ml, imperial_oz):
        try:
            metric_ml = int(metric_ml)
            imperial_oz = int(imperial_oz)
        except (ValueError, TypeError):
            print("SettingsManager Error: Invalid pour volume format for saving.")
            return

        sys_set = self.settings.setdefault('system_settings', self._get_default_system_settings())
        sys_set['metric_pour_ml'] = metric_ml
        sys_set['imperial_pour_oz'] = imperial_oz
        self._save_all_settings()
        print(f"SettingsManager: Pour volumes saved (Metric: {metric_ml} ml, Imperial: {imperial_oz} oz).")

    def get_sensor_beverage_assignments(self):
        default_assignments = self._get_default_beverage_assignments() 
        assignments = self.settings.get('sensor_beverage_assignments', default_assignments) 
        if len(assignments) != self.num_sensors: 
            assignments = default_assignments 
        return assignments 

    def save_sensor_beverage_assignment(self, sensor_index, beverage_id):
        if not (0 <= sensor_index < self.num_sensors): return 
        
        if 'sensor_beverage_assignments' not in self.settings or len(self.settings.get('sensor_beverage_assignments', [])) != self.num_sensors: 
            self.settings['sensor_beverage_assignments'] = self._get_default_beverage_assignments() 
        
        self.settings['sensor_beverage_assignments'][sensor_index] = beverage_id 
        self._save_all_settings() 
        print(f"Beverage assignment for Tap {sensor_index+1} saved: {beverage_id}.") 
    
    def get_sensor_labels(self):
        assignments = self.get_sensor_beverage_assignments() 
        library = self.get_beverage_library().get('beverages', []) 
        
        id_to_name = {b['id']: b['name'] for b in library if 'id' in b and 'name' in b} 
        
        labels = []
        for i, beverage_id in enumerate(assignments): 
            name = id_to_name.get(beverage_id) 
            if name: 
                labels.append(name) 
            else:
                labels.append(f"Tap {i+1}") 
            
        return labels 

    def save_sensor_labels(self, sensor_labels_list):
        if len(sensor_labels_list) == self.num_sensors: 
            self.settings['sensor_labels'] = sensor_labels_list 
            self._save_all_settings() 

    def get_conditional_notification_settings(self):
        defaults = self._get_default_conditional_notification_settings() 
        
        if 'conditional_notification_settings' not in self.settings:
             self.settings['conditional_notification_settings'] = defaults
             
        cond_set = self.settings.get('conditional_notification_settings', {}).copy()
        for key, default_value in defaults.items():
             if key not in cond_set:
                 cond_set[key] = default_value
        
        settings = cond_set 
        
        if 'sent_notifications' not in settings or len(settings['sent_notifications']) != self.num_sensors: 
            settings['sent_notifications'] = defaults['sent_notifications'] 
            
        if 'temp_sent_timestamps' not in settings or not isinstance(settings['temp_sent_timestamps'], list): 
            settings['temp_sent_timestamps'] = [] 
        
        if 'error_reported_times' not in settings:
             settings['error_reported_times'] = defaults['error_reported_times']
        else:
             merged_errors = defaults['error_reported_times'].copy()
             merged_errors.update(settings['error_reported_times']) 
             settings['error_reported_times'] = merged_errors

        for key, val in defaults.items(): 
            if key not in settings: 
                settings[key] = val 
        return settings
        
    def save_conditional_notification_settings(self, new_settings):
        self.settings['conditional_notification_settings'] = new_settings 
        self._save_all_settings() 
        print("SettingsManager: Conditional notification settings saved.") 
    
    def update_conditional_sent_status(self, tap_index, status):
        cond_notif_settings = self.settings.get('conditional_notification_settings', {}).copy() 
        sent_status_list = cond_notif_settings.get('sent_notifications', []) 
        
        if len(sent_status_list) != self.num_sensors: 
            sent_status_list = [False] * self.num_sensors 

        if 0 <= tap_index < len(sent_status_list): 
            sent_status_list[tap_index] = status 
            cond_notif_settings['sent_notifications'] = sent_status_list 
            self.settings['conditional_notification_settings'] = cond_notif_settings 
            self._save_all_settings() 
            print(f"SettingsManager: Updated conditional notification sent status for tap {tap_index+1} to {status}.") 
        else:
            print(f"SettingsManager Error: Invalid tap index {tap_index} for updating conditional sent status.") 

    def update_temp_sent_timestamp(self, timestamp=None):
        cond_notif_settings = self.settings.get('conditional_notification_settings', {}).copy() 
        timestamps = [timestamp if timestamp is not None else time.time()] 
        cond_notif_settings['temp_sent_timestamps'] = timestamps 
        self.settings['conditional_notification_settings'] = cond_notif_settings 
        self._save_all_settings() 
        print("SettingsManager: Updated conditional temperature sent timestamp.") 
        
    def update_error_reported_time(self, error_type, timestamp):
        cond_notif_settings = self.settings.get('conditional_notification_settings', {}).copy()
        error_reported_times = cond_notif_settings.get('error_reported_times', {})
        if error_type in error_reported_times:
            error_reported_times[error_type] = timestamp
            cond_notif_settings['error_reported_times'] = error_reported_times
            self.settings['conditional_notification_settings'] = cond_notif_settings
            self._save_all_settings()

    def get_error_reported_time(self, error_type):
        cond_notif_settings = self.settings.get('conditional_notification_settings', {})
        return cond_notif_settings.get('error_reported_times', {}).get(error_type, 0.0)

    def get_sensor_keg_assignments(self):
        default_assignments = self._get_default_sensor_keg_assignments() 
        assignments = self.settings.get('sensor_keg_assignments', default_assignments) 
        if len(assignments) != self.num_sensors: assignments = default_assignments 
        
        default_id = UNASSIGNED_KEG_ID
        valid_keg_ids = self.keg_map.keys()
        
        for i in range(len(assignments)): 
            if assignments[i] != UNASSIGNED_KEG_ID and assignments[i] not in valid_keg_ids: 
                assignments[i] = default_id 
                
        return assignments 
    
    def save_sensor_keg_assignment(self, sensor_index, keg_id):
        if not (0 <= sensor_index < self.num_sensors): return 
        if keg_id != UNASSIGNED_KEG_ID and keg_id not in self.keg_map: return 
        
        if 'sensor_keg_assignments' not in self.settings or len(self.settings.get('sensor_keg_assignments', [])) != self.num_sensors: 
            self.settings['sensor_keg_assignments'] = self._get_default_sensor_keg_assignments() 
            
        self.settings['sensor_keg_assignments'][sensor_index] = keg_id
        self._save_all_settings(); 
        print(f"Keg assignment for Tap {sensor_index+1} saved to Keg ID: {keg_id}.") 

    def get_display_units(self): return self.settings.get('system_settings', {}).get('display_units', self._get_default_system_settings()['display_units']) 
    def save_display_units(self, unit_system):
        if unit_system in ["imperial", "metric"]: self.settings.setdefault('system_settings', self._get_default_system_settings())['display_units'] = unit_system; 
        self._save_all_settings() 
    def get_displayed_taps(self):
        system_settings = self.settings.get('system_settings', {}) 
        default_taps = self._get_default_system_settings()['displayed_taps'] 
        displayed_taps = system_settings.get('displayed_taps', default_taps) 
        try: displayed_taps = int(displayed_taps) 
        except ValueError: displayed_taps = default_taps 
        return max(1, min(displayed_taps, self.num_sensors)) 
    def save_displayed_taps(self, number_of_taps):
        if isinstance(number_of_taps, int) and 1 <= number_of_taps <= self.num_sensors: 
            self.settings.setdefault('system_settings', self._get_default_system_settings())['displayed_taps'] = number_of_taps; self._save_all_settings() 
            
    def get_push_notification_settings(self):
        current_notif_settings = self.settings.get('push_notification_settings', {}).copy(); 
        defaults = self._get_default_push_notification_settings() 
        for key, default_value in defaults.items(): 
            if key not in current_notif_settings: 
                current_notif_settings[key] = default_value 
        
        port_val = current_notif_settings.get('smtp_port')
        if isinstance(port_val, str):
            if port_val.strip().isdigit():
                current_notif_settings['smtp_port'] = int(port_val)
            else:
                current_notif_settings['smtp_port'] = ""
        
        return current_notif_settings 

    def save_push_notification_settings(self, new_notif_settings):
        defaults = self._get_default_push_notification_settings() 
        for key in defaults.keys(): 
            if key not in new_notif_settings: new_notif_settings[key] = defaults[key] 
        if new_notif_settings.get('notification_type') not in ["None", "Email", "Text", "Both"]: new_notif_settings['notification_type'] = defaults['notification_type'] 
        if new_notif_settings.get('frequency') not in ["Hourly", "Daily", "Weekly", "Monthly"]: new_notif_settings['frequency'] = defaults['frequency'] 
        
        port_val = new_notif_settings.get('smtp_port', defaults['smtp_port'])
        try:
            port_str = str(port_val).strip()
            if port_str.isdigit():
                 new_notif_settings['smtp_port'] = int(port_str)
            else:
                 new_notif_settings['smtp_port'] = ""
        except Exception:
            new_notif_settings['smtp_port'] = ""
        
        self.settings['push_notification_settings'] = new_notif_settings 
        self._save_all_settings(); 
        print("Push Notification settings saved.") 

    def get_status_request_settings(self):
        current_status_req_settings = self.settings.get('status_request_settings', {}).copy()
        defaults = self._get_default_status_request_settings()
        for key, default_value in defaults.items():
             if key not in current_status_req_settings:
                 current_status_req_settings[key] = default_value
        
        for key in ['imap_port', 'smtp_port']:
            port_val = current_status_req_settings.get(key)
            if isinstance(port_val, str) and port_val.strip().isdigit():
                current_status_req_settings[key] = int(port_val.strip())
            elif not isinstance(port_val, int):
                current_status_req_settings[key] = ""
                
        return current_status_req_settings

    def save_status_request_settings(self, new_status_req_settings):
        defaults = self._get_default_status_request_settings()
        for key in defaults.keys(): 
            if key not in new_status_req_settings: new_status_req_settings[key] = defaults[key]
        
        for key in ['imap_port', 'smtp_port']:
            port_val = new_status_req_settings.get(key)
            try:
                port_str = str(port_val).strip()
                if port_str.isdigit():
                    new_status_req_settings[key] = int(port_str)
                else:
                    new_status_req_settings[key] = ""
            except Exception:
                new_status_req_settings[key] = ""
        
        self.settings['status_request_settings'] = new_status_req_settings
        self._save_all_settings()
        print("Status Request settings saved.") 
    
    def _get_desktop_shortcut_path(self):
        return os.path.expanduser("~/.local/share/applications/keglevel.desktop")

    def get_terminal_setting_state(self):
        path = self._get_desktop_shortcut_path()
        if not os.path.exists(path):
            return False
        
        try:
            with open(path, 'r') as f:
                for line in f:
                    parts = line.split('=', 1)
                    if len(parts) == 2 and parts[0].strip() == "Terminal":
                        return parts[1].strip().lower() == "true"
        except Exception as e:
            print(f"SettingsManager Error reading shortcut: {e}")
            return False
        return False

    def save_terminal_setting_state(self, enable_terminal):
        path = self._get_desktop_shortcut_path()
        if not os.path.exists(path):
            return False, f"Shortcut not found at {path}"

        if not os.access(path, os.W_OK):
            return False, "File is read-only. Check permissions (likely owned by root)."

        try:
            lines = []
            with open(path, 'r') as f:
                lines = f.readlines()

            new_lines = []
            key_found = False
            val = "true" if enable_terminal else "false"

            for line in lines:
                parts = line.split('=', 1)
                if len(parts) == 2 and parts[0].strip() == "Terminal":
                    new_lines.append(f"Terminal={val}\n")
                    key_found = True
                else:
                    new_lines.append(line)
            
            if not key_found:
                new_lines.append(f"Terminal={val}\n")

            with open(path, 'w') as f:
                f.writelines(new_lines)
                
            print(f"SettingsManager: Updated shortcut Terminal={enable_terminal}")
            return True, "Success"
            
        except Exception as e:
            print(f"SettingsManager Error updating shortcut: {e}")
            return False, str(e)

    def set_ds18b20_ambient_sensor(self, ambient_id):
        system_settings = self.settings.get('system_settings', self._get_default_system_settings()) 
        system_settings['ds18b20_ambient_sensor'] = ambient_id 
        self.settings['system_settings'] = system_settings 
        self._save_all_settings() 
        print(f"SettingsManager: DS18B20 ambient sensor assignment saved: Ambient ID={ambient_id}") 

    def get_ds18b20_ambient_sensor(self):
        system_settings = self.settings.get('system_settings', self._get_default_system_settings()) 
        return {
            'ambient': system_settings.get('ds18b20_ambient_sensor', 'unassigned') 
        }

    def _save_all_settings(self, current_settings=None):
        settings_to_save = current_settings if current_settings is not None else self.settings
        try:
            with open(self.settings_file_path, 'w', encoding='utf-8') as f: json.dump(settings_to_save, f, indent=4) 
            print(f"Settings saved to {self.settings_file_path}.") 
        except Exception as e: print(f"Error saving all settings to {self.settings_file_path}: {e}")

    # --- NEW: Helper for logical naming (Moved from UI) ---
    def generate_next_keg_title(self):
        """
        Finds the first available 'Keg {nn}' title by looking for gaps
        in the existing numbering sequence of the current library.
        """
        all_kegs = self.keg_library.get('kegs', [])
        existing_numbers = set()
        
        for k in all_kegs:
            title = k.get('title', '')
            if title.startswith("Keg "):
                try:
                    num_str = title[4:].strip()
                    if num_str.isdigit():
                        existing_numbers.add(int(num_str))
                except ValueError:
                    continue
        
        next_num = 1
        while next_num in existing_numbers:
            next_num += 1
            
        return f"Keg {next_num:02}"

    # --- NEW: Migration Tool ---
    def import_data_from_monitor(self):
        """
        Imports data from the sibling 'keglevel-data' folder.
        Preserves existing Lite data.
        Renames incoming Keg Titles to 'Keg {nn}'.
        """
        monitor_dir = os.path.abspath(os.path.join(self.data_dir, "..", "keglevel-data"))
        if not os.path.exists(monitor_dir):
            return False, "Monitor data folder not found."

        try:
            # 1. Load Monitor Data
            with open(os.path.join(monitor_dir, "beverages_library.json"), 'r') as f:
                mon_bevs = json.load(f).get('beverages', [])
            with open(os.path.join(monitor_dir, "keg_library.json"), 'r') as f:
                mon_kegs = json.load(f).get('kegs', [])
            with open(os.path.join(monitor_dir, "settings.json"), 'r') as f:
                mon_settings = json.load(f)
                
            stats = {"bevs": 0, "kegs": 0}

            # 2. Merge Beverages
            existing_bev_ids = {b['id'] for b in self.beverage_library['beverages']}
            existing_bev_names = {b['name'] for b in self.beverage_library['beverages']}
            
            for b in mon_bevs:
                # Prevent overwriting existing IDs
                if b['id'] in existing_bev_ids: 
                    continue
                
                # Handle Name Collision
                if b['name'] in existing_bev_names:
                    b['name'] = f"{b['name']} (Monitor)"
                
                self.beverage_library['beverages'].append(b)
                stats["bevs"] += 1
                
            self._save_beverage_library(self.beverage_library)

            # 3. Merge Kegs
            existing_keg_ids = {k['id'] for k in self.keg_library['kegs']}
            
            for k in mon_kegs:
                # Prevent overwriting existing IDs
                if k['id'] in existing_keg_ids:
                    continue
                
                # RE-TITLE: Use Lite's standard naming (Keg 01, Keg 02...)
                # We call generate for EACH keg to fill gaps sequentially
                k['title'] = self.generate_next_keg_title()
                
                self.keg_library['kegs'].append(k)
                stats["kegs"] += 1
                
            self._save_keg_library(self.keg_library)
            
            # 4. Import Tap Settings (Assignments & Calibration)
            # Only import for the number of sensors Lite has (e.g., 5)
            mon_sys = mon_settings.get('system_settings', {})
            mon_k_factors = mon_sys.get('flow_calibration_factors', [])
            mon_keg_assigns = mon_settings.get('sensor_keg_assignments', [])
            mon_bev_assigns = mon_settings.get('sensor_beverage_assignments', [])
            
            current_sys = self.get_system_settings()
            current_factors = current_sys.get('flow_calibration_factors', [])
            
            # Map Calibration Factors
            for i in range(min(self.num_sensors, len(mon_k_factors))):
                current_factors[i] = mon_k_factors[i]
            self.save_flow_calibration_factors(current_factors)
            
            # Map Assignments
            # Since we imported the Keg/Bev UUIDs above, these IDs are valid references.
            for i in range(min(self.num_sensors, len(mon_keg_assigns))):
                self.save_sensor_keg_assignment(i, mon_keg_assigns[i])
                
            for i in range(min(self.num_sensors, len(mon_bev_assigns))):
                self.save_sensor_beverage_assignment(i, mon_bev_assigns[i])

            return True, f"Imported {stats['bevs']} Beverages and {stats['kegs']} Kegs. Settings updated."

        except Exception as e:
            print(f"Import Error: {e}")
            return False, f"Import Failed: {e}"
            
    def import_data_from_monitor(self, import_calibration=True):
        """
        Imports data from the sibling 'keglevel-data' folder.
        Preserves existing Lite data.
        Renames incoming Keg Titles to 'Keg {nn}'.
        Optionally imports flow calibration factors.
        """
        monitor_dir = os.path.abspath(os.path.join(self.data_dir, "..", "keglevel-data"))
        if not os.path.exists(monitor_dir):
            return False, "Monitor data folder not found."

        try:
            # 1. Load Monitor Data
            with open(os.path.join(monitor_dir, "beverages_library.json"), 'r') as f:
                mon_bevs = json.load(f).get('beverages', [])
            with open(os.path.join(monitor_dir, "keg_library.json"), 'r') as f:
                mon_kegs = json.load(f).get('kegs', [])
            with open(os.path.join(monitor_dir, "settings.json"), 'r') as f:
                mon_settings = json.load(f)
                
            stats = {"bevs": 0, "kegs": 0}

            # 2. Merge Beverages
            existing_bev_ids = {b['id'] for b in self.beverage_library['beverages']}
            existing_bev_names = {b['name'] for b in self.beverage_library['beverages']}
            
            for b in mon_bevs:
                # Prevent overwriting existing IDs
                if b['id'] in existing_bev_ids: 
                    continue
                
                # Handle Name Collision
                if b['name'] in existing_bev_names:
                    b['name'] = f"{b['name']} (Monitor)"
                
                self.beverage_library['beverages'].append(b)
                stats["bevs"] += 1
                
            self._save_beverage_library(self.beverage_library)

            # 3. Merge Kegs
            existing_keg_ids = {k['id'] for k in self.keg_library['kegs']}
            
            for k in mon_kegs:
                # Prevent overwriting existing IDs
                if k['id'] in existing_keg_ids:
                    continue
                
                # RE-TITLE: Use Lite's standard naming (Keg 01, Keg 02...)
                k['title'] = self.generate_next_keg_title()
                
                self.keg_library['kegs'].append(k)
                stats["kegs"] += 1
            
            # CRITICAL FIX: Update the memory map so assignments below recognize the new IDs
            self.keg_map = {k['id']: k for k in self.keg_library['kegs']}
            self._save_keg_library(self.keg_library)
            
            # 4. Import Tap Settings (Assignments & Calibration)
            mon_sys = mon_settings.get('system_settings', {})
            mon_k_factors = mon_sys.get('flow_calibration_factors', [])
            mon_keg_assigns = mon_settings.get('sensor_keg_assignments', [])
            mon_bev_assigns = mon_settings.get('sensor_beverage_assignments', [])
            
            # -- Conditional Calibration Import --
            if import_calibration:
                current_sys = self.get_system_settings()
                current_factors = current_sys.get('flow_calibration_factors', [])
                
                for i in range(min(self.num_sensors, len(mon_k_factors))):
                    current_factors[i] = mon_k_factors[i]
                self.save_flow_calibration_factors(current_factors)
            
            # Map Assignments
            for i in range(min(self.num_sensors, len(mon_keg_assigns))):
                self.save_sensor_keg_assignment(i, mon_keg_assigns[i])
                
            for i in range(min(self.num_sensors, len(mon_bev_assigns))):
                self.save_sensor_beverage_assignment(i, mon_bev_assigns[i])

            msg = f"Imported {stats['bevs']} Beverages and {stats['kegs']} Kegs."
            if import_calibration:
                msg += " Calibration factors updated."
            return True, msg

        except Exception as e:
            print(f"Import Error: {e}")
            return False, f"Import Failed: {e}"
