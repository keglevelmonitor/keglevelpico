# keglevel app
# sensor_logic.py
import time
import threading

# --- HARDWARE IMPORT SAFETY ---
try:
    import RPi.GPIO as GPIO
    if GPIO.getmode() != GPIO.BCM: GPIO.setmode(GPIO.BCM)
    IS_RASPBERRY_PI_MODE = True
except (ImportError, RuntimeError):
    print("WARNING: RPi.GPIO not found. Running in simulation mode.")
    IS_RASPBERRY_PI_MODE = False
    class MockGPIO:
        BCM = "BCM"; IN = "IN"; PUD_DOWN = "PUD_DOWN"; RISING = "RISING"
        @staticmethod
        def setmode(mode): pass
        @staticmethod
        def setup(pin, mode, pull_up_down=None): pass
        @staticmethod
        def add_event_detect(pin, edge, callback, bouncetime=None): pass
        @staticmethod
        def remove_event_detect(pin): pass
        @staticmethod
        def cleanup(): pass
    GPIO = MockGPIO

def is_raspberry_pi(): return IS_RASPBERRY_PI_MODE

# --- PINS & CONSTANTS ---
FLOW_SENSOR_PINS = [5, 6, 12, 13, 16] 
READING_INTERVAL_SECONDS = 0.5 
FLOW_DEBOUNCE_MS = 5
FLOW_PULSES_FOR_ACTIVITY = 10
FLOW_PULSES_FOR_STOPPED = 3
DEFAULT_K_FACTOR = 5100.0
GPIO_LIB = GPIO 

# Global counter (must be global for interrupt)
global_pulse_counts = [0] * len(FLOW_SENSOR_PINS)
last_check_time = [0.0] * len(FLOW_SENSOR_PINS) 

def count_pulse(channel):
    try:
        sensor_index = FLOW_SENSOR_PINS.index(channel)
        global_pulse_counts[sensor_index] += 1
    except ValueError: pass

class SensorLogic:
    def __init__(self, num_sensors_from_config, ui_callbacks, settings_manager):
        # Enforce hardware limit
        self.num_sensors = min(num_sensors_from_config, len(FLOW_SENSOR_PINS))
        self.sensor_pins = FLOW_SENSOR_PINS[:self.num_sensors]
        self.ui_callbacks = ui_callbacks
        self.settings_manager = settings_manager

        # State
        self.keg_ids_assigned = [None] * self.num_sensors 
        self.keg_dispensed_liters = [0.0] * self.num_sensors 
        self.tap_is_active = [False] * self.num_sensors
        self.active_sensor_index = -1 
        
        # --- FIX: Sync with global hardware counts instead of resetting to 0 ---
        # This prevents phantom pours when the settings are saved/reloaded.
        self.last_pulse_count = list(global_pulse_counts[:self.num_sensors])
        
        self.last_known_remaining_liters = [0.0] * self.num_sensors
        
        # Current/Last Pour State
        self.current_pour_volume = [0.0] * self.num_sensors
        self.last_pour_volumes = self.settings_manager.get_last_pour_volumes()[:self.num_sensors]
        
        self._running = False
        self.is_paused = False
        self.sensor_thread = None 
        
        # Calibration State
        self._is_calibrating = False
        self._cal_target_tap = -1
        self._cal_start_pulse_count = 0
        self._cal_current_session_liters = 0.0 

        # --- NEW: Auto-Detect Calibration State ---
        self._auto_cal_mode = False
        self._auto_cal_locked_tap = -1
        self._auto_cal_session_pulses = 0

        self._load_initial_volumes()

    def _load_initial_volumes(self):
        assignments = self.settings_manager.get_sensor_keg_assignments()
        for i in range(self.num_sensors):
             if i >= len(assignments): break
             keg_id = assignments[i]
             keg = self.settings_manager.get_keg_by_id(keg_id)
             self.keg_ids_assigned[i] = keg_id
             if keg:
                 dispensed = keg.get('current_dispensed_liters', 0.0)
                 # Pico uses starting_volume_liters; app uses calculated_starting_volume_liters
                 starting_vol = (keg.get('calculated_starting_volume_liters') or
                                keg.get('starting_volume_liters', 0.0))
                 self.keg_dispensed_liters[i] = dispensed
                 
                 # CHANGED: Allow negative values (Removed max(0.0, ...))
                 self.last_known_remaining_liters[i] = starting_vol - dispensed
             else:
                 self.keg_dispensed_liters[i] = 0.0
                 self.last_known_remaining_liters[i] = 0.0

    def start_monitoring(self):
        self._setup_gpios()
        self._running = True
        if self.sensor_thread is None or not self.sensor_thread.is_alive():
            self.sensor_thread = threading.Thread(target=self._sensor_loop, daemon=True)
            self.sensor_thread.start()

    def stop_monitoring(self):
        """Stops the monitoring loop gracefully."""
        self._running = False
        if self.sensor_thread:
            self.sensor_thread.join(timeout=1.0)

    def _setup_gpios(self):
        try: GPIO_LIB.cleanup()
        except: pass
        GPIO_LIB.setmode(GPIO_LIB.BCM)
        for pin in self.sensor_pins:
            GPIO_LIB.setup(pin, GPIO_LIB.IN, pull_up_down=GPIO_LIB.PUD_DOWN) 
            GPIO_LIB.add_event_detect(pin, GPIO_LIB.RISING, callback=count_pulse, bouncetime=FLOW_DEBOUNCE_MS)

    # --- NEW: Auto-Calibration Control Methods ---
    def start_auto_calibration_mode(self):
        """Enables the auto-detect calibration loop."""
        self._auto_cal_mode = True
        self._auto_cal_locked_tap = -1
        self._auto_cal_session_pulses = 0
        print("SensorLogic: Auto-Calibration Mode STARTED")

    def stop_auto_calibration_mode(self):
        """Disables calibration mode and resumes normal operation."""
        was_active = self._auto_cal_mode
        self._auto_cal_mode = False
        self._auto_cal_locked_tap = -1
        self._auto_cal_session_pulses = 0
        if was_active:
            print("SensorLogic: Auto-Calibration Mode STOPPED")

    def reset_auto_calibration_state(self):
        """Resets the lock without exiting mode (for 'Reset/Cancel' button)."""
        self._auto_cal_locked_tap = -1
        self._auto_cal_session_pulses = 0
        # Reset pulse tracking to avoid immediate re-trigger if flow is still trickling
        for i in range(len(self.last_pulse_count)):
            self.last_pulse_count[i] = global_pulse_counts[i]
        print("SensorLogic: Auto-Calibration RESET")

    def _sensor_loop(self):
        global global_pulse_counts
        # Initialize timing
        if all(t == 0.0 for t in last_check_time):
             now = time.time()
             for i in range(len(FLOW_SENSOR_PINS)): last_check_time[i] = now

        while self._running:
            if self.is_paused:
                time.sleep(0.5); continue
                
            current_time = time.time()
            displayed_taps = self.settings_manager.get_displayed_taps()
            # Safety clamp for loop
            displayed_taps = min(displayed_taps, self.num_sensors)
            
            k_factors = self.settings_manager.get_flow_calibration_factors()
            
            # --- NEW: AUTO-CALIBRATION LOGIC BRANCH ---
            if self._auto_cal_mode:
                for i in range(displayed_taps):
                    # Calculate raw delta pulses since last loop
                    delta_p = global_pulse_counts[i] - self.last_pulse_count[i]
                    self.last_pulse_count[i] = global_pulse_counts[i]
                    last_check_time[i] = current_time

                    # State 1: NO TAP LOCKED - Listen for activity
                    if self._auto_cal_locked_tap == -1:
                        if delta_p > 10: # Threshold to ignore noise
                            self._auto_cal_locked_tap = i
                            self._auto_cal_session_pulses = delta_p # Capture these initial pulses
                            # Notify UI immediately
                            if self.ui_callbacks.get("auto_cal_pulse_cb"):
                                self.ui_callbacks["auto_cal_pulse_cb"](i, self._auto_cal_session_pulses)
                    
                    # State 2: TAP LOCKED - Accumulate pulses only for this tap
                    elif i == self._auto_cal_locked_tap:
                        if delta_p > 0:
                            self._auto_cal_session_pulses += delta_p
                            # Notify UI
                            if self.ui_callbacks.get("auto_cal_pulse_cb"):
                                self.ui_callbacks["auto_cal_pulse_cb"](i, self._auto_cal_session_pulses)
                
                # Sleep and continue (Skip normal pouring logic)
                time.sleep(READING_INTERVAL_SECONDS)
                continue
            # ------------------------------------------

            # 1. Detect Activity
            if not self._is_calibrating and self.active_sensor_index == -1:
                for i in range(displayed_taps):
                    delta_p = global_pulse_counts[i] - self.last_pulse_count[i]
                    if delta_p >= FLOW_PULSES_FOR_ACTIVITY:
                        self.active_sensor_index = i
                        break
            
            # 2. Process Taps
            for i in range(displayed_taps):
                time_interval = current_time - last_check_time[i]
                pulses = global_pulse_counts[i] - self.last_pulse_count[i]
                is_active_target = (i == self.active_sensor_index)
                
                # --- CALIBRATION MODE (OLD MANUAL - Keeping for legacy safety if needed) ---
                if self._is_calibrating and self._cal_target_tap == i:
                    if pulses > 0 and time_interval > 0:
                        lpm = (pulses / k_factors[i]) / (time_interval / 60.0)
                        liters = pulses / k_factors[i]
                        self._cal_current_session_liters += liters
                        if self.ui_callbacks.get("update_cal_data_cb"):
                            self.ui_callbacks.get("update_cal_data_cb")(lpm, self._cal_current_session_liters)

                # --- NORMAL POURING MODE ---
                elif is_active_target:
                    if pulses > 0 and time_interval > 0:
                        self.tap_is_active[i] = True
                        lpm = (pulses / k_factors[i]) / (time_interval / 60.0)
                        liters = pulses / k_factors[i]
                        
                        self.keg_dispensed_liters[i] += liters
                        self.current_pour_volume[i] += liters  # Track current pour
                        
                        # Persist to Memory/Disk
                        keg_id = self.keg_ids_assigned[i]
                        if keg_id:
                            self.settings_manager.update_keg_dispensed_volume(keg_id, self.keg_dispensed_liters[i], pulses=pulses)
                            
                        # Update UI
                        remaining = self.last_known_remaining_liters[i] - liters
                        
                        # CHANGED: Allow negative values (Removed max(0.0, ...))
                        self.last_known_remaining_liters[i] = remaining
                        
                        self._update_ui(i, lpm, self.last_known_remaining_liters[i], "Pouring", self.current_pour_volume[i])
                        
                    elif self.tap_is_active[i] and pulses <= FLOW_PULSES_FOR_STOPPED:
                        # Pour Stopped
                        self.settings_manager.save_all_keg_dispensed_volumes()
                        
                        # Save Last Pour Stats
                        self.last_pour_volumes[i] = self.current_pour_volume[i]
                        self.settings_manager.save_last_pour_volumes(self.last_pour_volumes)
                        self.current_pour_volume[i] = 0.0 # Reset for next
                        
                        self._update_ui(i, 0.0, self.last_known_remaining_liters[i], "Idle", self.last_pour_volumes[i])
                        self.tap_is_active[i] = False
                        self.active_sensor_index = -1

                # --- IDLE UPDATES ---
                else:
                    self._update_ui(i, 0.0, self.last_known_remaining_liters[i], "Idle", self.last_pour_volumes[i])

                self.last_pulse_count[i] = global_pulse_counts[i]
                last_check_time[i] = current_time

            time.sleep(READING_INTERVAL_SECONDS)

    def _update_ui(self, idx, rate, rem, status, pour_vol):
        # (Simplified UI Update wrapper)
        if self.ui_callbacks.get("update_sensor_data_cb"):
            self.ui_callbacks["update_sensor_data_cb"](idx, rate, rem, status, pour_vol)

    # --- Calibration Helpers ---
    def start_flow_calibration(self, tap_index, target_vol):
        self._is_calibrating = True
        self._cal_target_tap = tap_index
        self._cal_start_pulse_count = global_pulse_counts[tap_index]
        self._cal_current_session_liters = 0.0
        return True

    def stop_flow_calibration(self, tap_index):
        total_pulses = global_pulse_counts[tap_index] - self._cal_start_pulse_count
        final_liters = self._cal_current_session_liters
        self._is_calibrating = False
        self._cal_target_tap = -1
        return total_pulses, final_liters
        
    def deduct_volume_from_keg(self, tap_index, liters):
        keg_id = self.keg_ids_assigned[tap_index]
        if keg_id:
            self.keg_dispensed_liters[tap_index] += liters
            self.settings_manager.update_keg_dispensed_volume(keg_id, self.keg_dispensed_liters[tap_index])
            self.settings_manager.save_all_keg_dispensed_volumes()
            self._load_initial_volumes() # Refresh local state

    def cleanup_gpio(self):
        self._running = False
        try: GPIO_LIB.cleanup()
        except: pass
        print("GPIO Cleaned up.")

    def force_recalculation(self):
        self._load_initial_volumes()

    def simulate_pulse_increment(self, tap_index, pulse_amount):
        """Manually increments the global pulse counter for simulation."""
        global global_pulse_counts
        if 0 <= tap_index < len(global_pulse_counts):
            global_pulse_counts[tap_index] += pulse_amount

    def end_sim_pour(self, tap_index):
        """
        Finalise a simulated pour. Called by app after one-shot (PINT) delay
        or when continuous flow is toggled off. SensorLogic's loop handles
        pour-end naturally via pulse deltas; this is a no-op for compatibility.
        """
        pass

    # --- Pico-compat stubs (GPIO backend has no Pico; these are no-ops) ---
    def is_pico_online(self):
        return False

    def notify_keg_change(self, tap_index):
        pass

    def assign_keg_to_tap_on_pico(self, tap_index, keg_id):
        pass

    def push_k_factors_to_pico(self, k_factors):
        pass

    def update_keg_on_pico(self, keg_id, data):
        pass

    def delete_keg_on_pico(self, keg_id):
        pass

    def create_bev_on_pico(self, payload):
        pass

    def update_bev_on_pico(self, bev_id, payload):
        pass

    def delete_bev_on_pico(self, bev_id):
        pass

    def get_pico_version(self):
        """Stub for GPIO/DEMO mode; PicoSensorLogic overrides with real value."""
        return ""
