# keglevel app
#
# temperature_logic.py
import time
import threading
import json
import os
import glob
from datetime import datetime, timedelta

class TemperatureLogic:
    
    def __init__(self, ui_callbacks, settings_manager):
        self.ui_callbacks = ui_callbacks
        self.settings_manager = settings_manager

        self.ambient_sensor = None
        self._temp_thread = None
        self._running = False
        self._stop_event = threading.Event()
        self.last_known_temp_f = None
        self.last_update_time = None
        
        # Use SettingsManager's resolved data_dir
        base_dir = self.settings_manager.get_data_dir()
        self.log_file = os.path.join(base_dir, "temperature_log.json")
        
        # Initialize full log structure including RPi keys
        self.log_data = {
            "daily_log": [],      
            "weekly_log": [],     
            "monthly_log": [],    
            "high_low_avg": {
                "day": {"high": None, "low": None, "avg": None, "last_updated": None},
                "week": {"high": None, "low": None, "avg": None, "last_updated": None},
                "month": {"high": None, "low": None, "avg": None, "last_updated": None},
            },
            # --- NEW: RPi Internal Temp Logs (Source: Celsius) ---
            "rpi_daily_log": [],
            "rpi_weekly_log": [],
            "rpi_monthly_log": [],
            "rpi_high_low_avg": {
                "day": {"high": None, "low": None, "avg": None, "last_updated": None},
                "week": {"high": None, "low": None, "avg": None, "last_updated": None},
                "month": {"high": None, "low": None, "avg": None, "last_updated": None},
            }
        }
        self._load_log_data()

    def reset_log(self):
        """Clears all in-memory log data and saves the reset log to file."""
        self.log_data = {
            "daily_log": [],
            "weekly_log": [],
            "monthly_log": [],
            "high_low_avg": {
                "day": {"high": None, "low": None, "avg": None, "last_updated": None},
                "week": {"high": None, "low": None, "avg": None, "last_updated": None},
                "month": {"high": None, "low": None, "avg": None, "last_updated": None},
            },
            "rpi_daily_log": [],
            "rpi_weekly_log": [],
            "rpi_monthly_log": [],
            "rpi_high_low_avg": {
                "day": {"high": None, "low": None, "avg": None, "last_updated": None},
                "week": {"high": None, "low": None, "avg": None, "last_updated": None},
                "month": {"high": None, "low": None, "avg": None, "last_updated": None},
            }
        }
        self._save_log_data()
        print("TemperatureLogic: Temperature log has been reset.")

    def get_assigned_sensor(self):
        """Gets the assigned ambient sensor ID based on settings."""
        self.ambient_sensor = self.settings_manager.get_system_settings().get('ds18b20_ambient_sensor', None)
        
        if not self.ambient_sensor or self.ambient_sensor == 'unassigned':
            print("TemperatureLogic: No ambient sensor assigned or found.")
            
    def detect_ds18b20_sensors(self):
        """Finds all available DS18B20 sensors and returns their IDs by reading the filesystem."""
        base_dir = '/sys/bus/w1/devices/'
        device_folders = glob.glob(base_dir + '28-*')
        return [os.path.basename(f) for f in device_folders]

    def _read_temp_from_id(self, sensor_id):
        """Reads the temperature from a sensor given its ID (Returns F)."""
        if not sensor_id or sensor_id == 'unassigned':
            return None

        device_folder = f'/sys/bus/w1/devices/{sensor_id}/'
        device_file = device_folder + 'w1_slave'

        if not os.path.exists(device_file):
            print(f"TemperatureLogic: Sensor file not found for ID {sensor_id}.")
            return None

        try:
            with open(device_file, 'r') as f:
                lines = f.readlines()
            
            # Simple retry mechanism for busy sensor
            attempts = 0
            while lines[0].strip()[-3:] != 'YES' and attempts < 3:
                time.sleep(0.2)
                with open(device_file, 'r') as f:
                    lines = f.readlines()
                attempts += 1
            
            equals_pos = lines[1].find('t=')
            if equals_pos != -1:
                temp_string = lines[1][equals_pos+2:]
                temp_c = float(temp_string) / 1000.0
                temp_f = temp_c * 9.0 / 5.0 + 32.0
                return temp_f
            
        except Exception as e:
            print(f"TemperatureLogic: Error reading temperature from sensor {sensor_id}: {e}")
            return None
        
        return None

    def _read_rpi_internal_temp(self):
        """Reads the Raspberry Pi internal temperature (Returns C)."""
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp_str = f.read()
            # Value is in millidegrees Celsius
            return float(temp_str) / 1000.0
        except Exception:
            # Silently fail if not on Pi or error reading
            return None

    def start_monitoring(self):
        if not self._running:
            self._running = True
            self.get_assigned_sensor()
            # Start thread regardless of sensor assignment so RPi temp is still logged
            self._temp_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._temp_thread.start()
            print("TemperatureLogic: Monitoring thread started.")

    def stop_monitoring(self):
        if self._running:
            self._running = False
            self._stop_event.set()
            if self._temp_thread and self._temp_thread.is_alive():
                print("TemperatureLogic: Waiting for thread to stop...")
                self._temp_thread.join(timeout=2)
                if self._temp_thread.is_alive():
                    print("TemperatureLogic: Thread did not stop gracefully.")
                else:
                    print("TemperatureLogic: Thread stopped.")

    def _monitor_loop(self):
        while self._running:
            try:
                # 1. Read Kegerator Sensor
                amb_temp_f = self.read_ambient_temperature()
                
                # Update Live Display
                if amb_temp_f is not None:
                    self.last_known_temp_f = amb_temp_f
                    display_units = self.settings_manager.get_display_units()
                    if display_units == "imperial":
                        self.ui_callbacks.get("update_temp_display_cb")(amb_temp_f, "F")
                    else:
                        temp_c = (amb_temp_f - 32) * (5/9)
                        self.ui_callbacks.get("update_temp_display_cb")(temp_c, "C")
                else:
                    self.last_known_temp_f = None
                    if self.ambient_sensor and self.ambient_sensor != 'unassigned':
                        self.ui_callbacks.get("update_temp_display_cb")(None, "Error")
                    else:
                        self.ui_callbacks.get("update_temp_display_cb")(None, "No Sensor")

                # 2. Read RPi Internal Sensor
                rpi_temp_c = self._read_rpi_internal_temp()

                # 3. Log Data
                self._log_temperature_reading(amb_temp_f, rpi_temp_c)
                
                # Sleep for 5 minutes
                self._stop_event.wait(300)

            except Exception as e:
                print(f"TemperatureLogic: Error in monitor loop: {e}")
                self._stop_event.wait(60) # Wait a bit before retry on error

        print("TemperatureLogic: Monitor loop ended.")

    def _load_log_data(self):
        """Loads log data from the JSON file."""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    data = json.load(f)
                    
                    # --- MIGRATION: Ensure new RPi keys exist ---
                    if "rpi_daily_log" not in data:
                        data["rpi_daily_log"] = []
                        data["rpi_weekly_log"] = []
                        data["rpi_monthly_log"] = []
                        data["rpi_high_low_avg"] = {
                            "day": {"high": None, "low": None, "avg": None, "last_updated": None},
                            "week": {"high": None, "low": None, "avg": None, "last_updated": None},
                            "month": {"high": None, "low": None, "avg": None, "last_updated": None},
                        }
                    
                    self.log_data = data
                    
                    # Rehydrate datetime objects from strings if present
                    for section in ["high_low_avg", "rpi_high_low_avg"]:
                        for key in ["day", "week", "month"]:
                            ts = self.log_data[section][key]["last_updated"]
                            if ts:
                                self.log_data[section][key]["last_updated"] = datetime.fromisoformat(ts)
                                
                print(f"TemperatureLogic: Log data loaded from {self.log_file}.")
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"TemperatureLogic: Error loading log data from file: {e}. Starting with new log.")

    def _save_log_data(self):
        """Saves log data to the JSON file."""
        try:
            data_to_save = self.log_data.copy()
            
            # Serialize datetimes to strings
            for section in ["high_low_avg", "rpi_high_low_avg"]:
                for key in ["day", "week", "month"]:
                    ts = data_to_save[section][key]["last_updated"]
                    if isinstance(ts, datetime):
                        data_to_save[section][key]["last_updated"] = ts.isoformat()
            
            with open(self.log_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, indent=4)
            # print(f"TemperatureLogic: Log data saved.") # Commented out to reduce spam
        except Exception as e:
            print(f"TemperatureLogic: Error saving log data: {e}")

    def _log_temperature_reading(self, temp_f, rpi_temp_c=None):
        """Adds new temperature readings to the in-memory log and triggers a save."""
        now = datetime.now()
        timestamp = now.isoformat()
        
        # Log Kegerator Temp (if available)
        if temp_f is not None:
            entry = {"timestamp": timestamp, "temp_f": temp_f}
            self.log_data["daily_log"].append(entry)
            self.log_data["weekly_log"].append(entry)
            self.log_data["monthly_log"].append(entry)

        # Log RPi Temp (if available)
        if rpi_temp_c is not None:
            entry = {"timestamp": timestamp, "temp_c": rpi_temp_c}
            self.log_data["rpi_daily_log"].append(entry)
            self.log_data["rpi_weekly_log"].append(entry)
            self.log_data["rpi_monthly_log"].append(entry)

        self._prune_logs(now)
        self._calculate_stats_and_update_log()
        self._save_log_data()

    def _prune_logs(self, now):
        """Removes old entries from the in-memory log data."""
        # Kegerator Logs
        self.log_data["daily_log"] = [e for e in self.log_data["daily_log"] if datetime.fromisoformat(e["timestamp"]) >= now - timedelta(days=1)]
        self.log_data["weekly_log"] = [e for e in self.log_data["weekly_log"] if datetime.fromisoformat(e["timestamp"]) >= now - timedelta(weeks=1)]
        self.log_data["monthly_log"] = [e for e in self.log_data["monthly_log"] if datetime.fromisoformat(e["timestamp"]) >= now - timedelta(days=30)]

        # RPi Logs
        self.log_data["rpi_daily_log"] = [e for e in self.log_data["rpi_daily_log"] if datetime.fromisoformat(e["timestamp"]) >= now - timedelta(days=1)]
        self.log_data["rpi_weekly_log"] = [e for e in self.log_data["rpi_weekly_log"] if datetime.fromisoformat(e["timestamp"]) >= now - timedelta(weeks=1)]
        self.log_data["rpi_monthly_log"] = [e for e in self.log_data["rpi_monthly_log"] if datetime.fromisoformat(e["timestamp"]) >= now - timedelta(days=30)]

    def _calculate_stats(self, log_list, key_name="temp_f"):
        """Calculates high, low, and average from a list of readings."""
        if not log_list:
            return None, None, None
        
        temps = [e[key_name] for e in log_list]
        return max(temps), min(temps), sum(temps) / len(temps)

    def _calculate_stats_and_update_log(self):
        """Calculates and updates stats for day, week, and month for both sensors."""
        now = datetime.now()
        
        # --- Kegerator Stats (Source: F) ---
        for period, log_list in [("day", self.log_data["daily_log"]), 
                                 ("week", self.log_data["weekly_log"]), 
                                 ("month", self.log_data["monthly_log"])]:
            high, low, avg = self._calculate_stats(log_list, key_name="temp_f")
            self.log_data["high_low_avg"][period] = {"high": high, "low": low, "avg": avg, "last_updated": now}

        # --- RPi Stats (Source: C) ---
        for period, log_list in [("day", self.log_data["rpi_daily_log"]), 
                                 ("week", self.log_data["rpi_weekly_log"]), 
                                 ("month", self.log_data["rpi_monthly_log"])]:
            high, low, avg = self._calculate_stats(log_list, key_name="temp_c")
            self.log_data["rpi_high_low_avg"][period] = {"high": high, "low": low, "avg": avg, "last_updated": now}

    def get_temperature_log(self):
        """Returns the current log data structured for UI display with unit conversion."""
        display_units = self.settings_manager.get_display_units()
        
        # Prepare Data Structure
        ui_data = {
            "keg": {
                "day": self.log_data["high_low_avg"]["day"].copy(),
                "week": self.log_data["high_low_avg"]["week"].copy(),
                "month": self.log_data["high_low_avg"]["month"].copy(),
            },
            "rpi": {
                "day": self.log_data["rpi_high_low_avg"]["day"].copy(),
                "week": self.log_data["rpi_high_low_avg"]["week"].copy(),
                "month": self.log_data["rpi_high_low_avg"]["month"].copy(),
            }
        }

        # --- UNIT CONVERSION LOGIC ---
        # Keg Source is F. RPi Source is C.
        
        if display_units == "metric":
            # Convert Keg (F -> C)
            for period in ["day", "week", "month"]:
                for stat in ["high", "low", "avg"]:
                    val = ui_data["keg"][period][stat]
                    if val is not None:
                        ui_data["keg"][period][stat] = (val - 32) * (5/9)
            # RPi is already C, do nothing.

        else: # imperial
            # Keg is already F, do nothing.
            # Convert RPi (C -> F)
            for period in ["day", "week", "month"]:
                for stat in ["high", "low", "avg"]:
                    val = ui_data["rpi"][period][stat]
                    if val is not None:
                        ui_data["rpi"][period][stat] = (val * 9/5) + 32
        
        return ui_data

    def read_ambient_temperature(self):
        """Reads the temperature from the assigned ambient sensor."""
        if self.ambient_sensor:
            temp_f = self._read_temp_from_id(self.ambient_sensor)
            return temp_f
        return None
