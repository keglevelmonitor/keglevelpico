# pico_sensor_logic.py
# Pico W sensor backend for KegLevel Lite.
#
# Implements the same interface as SensorLogic (sensor_logic.py) but polls
# the Pico W REST API instead of reading GPIO pins directly.
#
# The host app (Pi / Windows / Mac) remains the source of truth for the keg
# and beverage library. The Pico is the source of truth for raw dispensed
# volumes (pulse counts converted to liters) and temperature.

import threading
import time
import json

try:
    import urllib.request as _urllib_request
    import urllib.error   as _urllib_error
except ImportError:
    _urllib_request = None
    _urllib_error   = None

DEFAULT_PICO_HOST  = "keglevel-pico.local"
REQUEST_TIMEOUT_S  = 2.0   # Pico can take up to ~1s during flash writes / GC
POLL_INTERVAL_S    = 0.5   # normal idle poll rate
POUR_POLL_INTERVAL_S = 0.1 # fast poll rate while any tap is actively pouring
OFFLINE_RETRY_S    = 5.0   # sleep between polls once truly offline
DISCOVERY_PORT     = 5005
DISCOVERY_DEVICE   = "keglevel-pico"


class PicoSensorLogic:
    """
    Drop-in replacement for SensorLogic that reads from the Pico W REST API.

    Constructor signature matches SensorLogic exactly so finalize_startup
    and apply_config_changes require no structural changes.
    """

    def __init__(self, num_sensors_from_config, ui_callbacks, settings_manager):
        self.num_sensors      = num_sensors_from_config
        self.ui_callbacks     = ui_callbacks
        self.settings_manager = settings_manager

        raw_host           = settings_manager.get_pico_w_host()
        self._manual_host  = raw_host.strip()   # empty = use auto-discovery
        self._discovery_mode = not bool(self._manual_host)
        self.host          = self._manual_host if self._manual_host else None
        self.base_url      = f"http://{self.host}" if self.host else None

        # Per-tap state — mirrors what SensorLogic maintains
        self.keg_ids_assigned            = [None] * self.num_sensors
        self.keg_dispensed_liters        = [0.0]  * self.num_sensors
        self.last_known_remaining_liters = [0.0]  * self.num_sensors
        self.current_pour_volume         = [0.0]  * self.num_sensors
        self.last_pour_volumes           = settings_manager.get_last_pour_volumes()[:self.num_sensors]
        self.tap_is_active               = [False] * self.num_sensors

        # Last Pico-reported dispensed values (used to compute deltas)
        self._last_dispensed = [None] * self.num_sensors

        # Pico dispensed baselines saved from the previous app session.
        # Used to capture volume poured while the app was not running.
        self._saved_pico_dispensed = settings_manager.get_pico_tap_last_dispensed()

        # Temperature dict from last /api/state poll
        self._pico_temperature = None

        # Calibration state
        self._auto_cal_mode           = False
        self._auto_cal_locked_tap     = -1
        self._auto_cal_session_pulses = 0
        self._cal_started_on_pico     = False
        self._is_calibrating          = False   # compatibility stub checked by screen on_leave

        # Threading
        self._running      = False
        self.is_paused     = False
        self.sensor_thread = None
        self._pico_online  = False

        self._load_initial_volumes()

    # ------------------------------------------------------------------
    # Volume initialisation (matches SensorLogic._load_initial_volumes)
    # ------------------------------------------------------------------

    def _load_initial_volumes(self):
        assignments = self.settings_manager.get_sensor_keg_assignments()
        for i in range(self.num_sensors):
            if i >= len(assignments):
                break
            keg_id = assignments[i]
            keg    = self.settings_manager.get_keg_by_id(keg_id)
            self.keg_ids_assigned[i] = keg_id
            if keg:
                dispensed    = keg.get('current_dispensed_liters', 0.0)
                starting_vol = keg.get('calculated_starting_volume_liters', 0.0)
                self.keg_dispensed_liters[i]        = dispensed
                self.last_known_remaining_liters[i] = starting_vol - dispensed
            else:
                self.keg_dispensed_liters[i]        = 0.0
                self.last_known_remaining_liters[i] = 0.0

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path):
        """HTTP GET → parsed JSON dict, or None on any error."""
        try:
            req = _urllib_request.Request(
                self.base_url + path,
                headers={"Accept": "application/json"}
            )
            with _urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    def _post(self, path, data=None):
        """HTTP POST with JSON body → parsed JSON dict, or None on any error."""
        try:
            body = json.dumps(data or {}).encode()
            req  = _urllib_request.Request(
                self.base_url + path,
                data=body,
                headers={"Content-Type": "application/json",
                         "Accept":       "application/json"}
            )
            with _urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Monitoring lifecycle (matches SensorLogic.start/stop_monitoring)
    # ------------------------------------------------------------------

    def start_monitoring(self):
        self._running = True
        if self._discovery_mode:
            disc_thread = threading.Thread(
                target=self._discovery_listener, daemon=True
            )
            disc_thread.start()
            print(f"[PicoSensor] Discovery mode — listening on UDP port {DISCOVERY_PORT}...")
        else:
            print(f"[PicoSensor] Using configured host: {self.host}")
        if self.sensor_thread is None or not self.sensor_thread.is_alive():
            self.sensor_thread = threading.Thread(
                target=self._sensor_loop, daemon=True
            )
            self.sensor_thread.start()

    def _discovery_listener(self):
        """
        Listen for UDP broadcast packets from the Pico.
        When found, update self.host and self.base_url so the sensor loop
        connects on its next retry cycle.
        Retries the socket bind every 5 seconds so a Windows Firewall
        prompt that initially blocks the port is handled gracefully.
        """
        import socket as _socket

        while self._running and not self._pico_online:
            sock = None
            try:
                sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
                sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                sock.bind(("0.0.0.0", DISCOVERY_PORT))
                sock.settimeout(2.0)
                print(f"[PicoSensor] Discovery: listening on UDP port {DISCOVERY_PORT}...")

                while self._running and not self._pico_online:
                    try:
                        data, addr = sock.recvfrom(256)
                        payload = json.loads(data.decode())
                        if payload.get("device") == DISCOVERY_DEVICE:
                            ip = payload.get("ip") or addr[0]
                            if ip and ip != self.host:
                                print(f"[PicoSensor] Discovery: Pico found at {ip}")
                                self.host     = ip
                                self.base_url = f"http://{ip}"
                    except _socket.timeout:
                        pass
                    except Exception:
                        pass

            except Exception as e:
                print(f"[PicoSensor] Discovery bind failed ({e}) — retrying in 5 s...")
                time.sleep(5.0)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

        print("[PicoSensor] Discovery listener stopped.")

    def stop_monitoring(self):
        self._running = False
        if self.sensor_thread:
            self.sensor_thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Main polling loop
    # ------------------------------------------------------------------

    # Number of consecutive failures before the UI is told the Pico is offline.
    # Each failure already costs REQUEST_TIMEOUT_S (2s) + POLL_INTERVAL_S (0.5s),
    # so 3 failures = ~7.5 s of confirmed silence before we show "Offline".
    _OFFLINE_THRESHOLD = 3

    def _sensor_loop(self):
        _consecutive_failures = 0

        while self._running:
            if self.is_paused:
                time.sleep(POLL_INTERVAL_S)
                continue

            # Wait for discovery to find the Pico before making any requests
            if self.base_url is None:
                time.sleep(OFFLINE_RETRY_S)
                continue

            state = self._get("/api/state")

            if state is None:
                _consecutive_failures += 1
                print(f"[PicoSensor] Poll failed (#{_consecutive_failures})")

                if _consecutive_failures >= self._OFFLINE_THRESHOLD:
                    # Sustained outage — tell the UI and slow down polling
                    if self._pico_online:
                        self._pico_online = False
                        print("[PicoSensor] Pico offline — slowing retry rate")
                    for i in range(self.num_sensors):
                        self._update_ui(i, 0.0,
                                        self.last_known_remaining_liters[i],
                                        "Offline",
                                        self.last_pour_volumes[i])
                    time.sleep(OFFLINE_RETRY_S)
                else:
                    # Transient blip — stay silent, retry at the normal rate
                    time.sleep(POLL_INTERVAL_S)
                continue

            _consecutive_failures = 0
            if not self._pico_online:
                self._pico_online = True
                print(f"[PicoSensor] Pico online at {self.host}")

            # Cache temperature for main_kivy to read
            self._pico_temperature = state.get("temperature")

            taps          = state.get("taps", [])
            displayed     = self.settings_manager.get_displayed_taps()
            displayed     = min(displayed, self.num_sensors, len(taps))

            if self._auto_cal_mode:
                self._process_calibration(taps, displayed)
                time.sleep(POLL_INTERVAL_S)
                continue

            for i in range(displayed):
                tap           = taps[i]
                pico_dispensed = float(tap.get("dispensed_liters", 0.0))
                pouring        = bool(tap.get("pouring", False))
                flow_rate      = float(tap.get("flow_rate_lpm", 0.0))

                # First poll: check for volume dispensed while app was offline
                if self._last_dispensed[i] is None:
                    saved = self._saved_pico_dispensed[i] if i < len(self._saved_pico_dispensed) else 0.0
                    offline_delta = max(0.0, pico_dispensed - saved)
                    if offline_delta > 0.001:
                        print(f"[PicoSensor] Tap {i+1}: {offline_delta:.3f} L poured while app was offline — applying.")
                        keg_id = self.keg_ids_assigned[i]
                        if keg_id:
                            new_total = self.keg_dispensed_liters[i] + offline_delta
                            self.keg_dispensed_liters[i] = new_total
                            self.settings_manager.update_keg_dispensed_volume(
                                keg_id, new_total, pulses=0
                            )
                        self.last_known_remaining_liters[i] -= offline_delta
                    self._last_dispensed[i] = pico_dispensed
                    self._update_ui(i, 0.0,
                                    self.last_known_remaining_liters[i],
                                    "Idle",
                                    self.last_pour_volumes[i])
                    continue

                # If Pico dispensed went backwards (reset / keg change), re-baseline
                if pico_dispensed < self._last_dispensed[i]:
                    print(f"[PicoSensor] Tap {i+1}: Pico dispensed reset detected — re-baselining.")
                    self._last_dispensed[i] = pico_dispensed

                delta = max(0.0, pico_dispensed - self._last_dispensed[i])
                self._last_dispensed[i] = pico_dispensed

                if delta > 0:
                    keg_id = self.keg_ids_assigned[i]
                    if keg_id:
                        new_total = self.keg_dispensed_liters[i] + delta
                        self.keg_dispensed_liters[i] = new_total
                        self.settings_manager.update_keg_dispensed_volume(
                            keg_id, new_total, pulses=0
                        )
                    self.last_known_remaining_liters[i] -= delta
                    self.current_pour_volume[i]         += delta

                if pouring:
                    self.tap_is_active[i] = True
                    self._update_ui(i, flow_rate,
                                    self.last_known_remaining_liters[i],
                                    "Pouring",
                                    self.current_pour_volume[i])
                elif self.tap_is_active[i]:
                    # Pour just stopped
                    self.tap_is_active[i]    = False
                    self.last_pour_volumes[i] = self.current_pour_volume[i]
                    self.settings_manager.save_last_pour_volumes(self.last_pour_volumes)
                    self.settings_manager.save_all_keg_dispensed_volumes()
                    self.current_pour_volume[i] = 0.0
                    self._update_ui(i, 0.0,
                                    self.last_known_remaining_liters[i],
                                    "Idle",
                                    self.last_pour_volumes[i])
                    # Persist Pico baseline so offline pours are captured next session
                    self._save_pico_baselines()
                else:
                    self._update_ui(i, 0.0,
                                    self.last_known_remaining_liters[i],
                                    "Idle",
                                    self.last_pour_volumes[i])

            # Use fast poll rate while any tap is actively pouring,
            # normal rate otherwise.
            if any(self.tap_is_active):
                time.sleep(POUR_POLL_INTERVAL_S)
            else:
                time.sleep(POLL_INTERVAL_S)

    # ------------------------------------------------------------------
    # Calibration (auto-detect mode matching SensorLogic interface)
    # ------------------------------------------------------------------

    def _process_calibration(self, taps, displayed):
        for i in range(displayed):
            tap       = taps[i]
            flow_rate = float(tap.get("flow_rate_lpm", 0.0))

            if self._auto_cal_locked_tap == -1:
                if flow_rate > 0.05:
                    self._auto_cal_locked_tap = i
                    self._cal_started_on_pico = False
                    print(f"[PicoSensor] Cal: locking to tap {i+1}")

            if i == self._auto_cal_locked_tap:
                if not self._cal_started_on_pico:
                    result = self._get(f"/api/tap/{i}/calibrate")
                    if result:
                        self._cal_started_on_pico     = True
                        self._auto_cal_session_pulses = result.get("pulses", 0)
                else:
                    result = self._get(f"/api/tap/{i}/calibrate")
                    if result:
                        self._auto_cal_session_pulses = result.get("pulses", 0)
                        cb = self.ui_callbacks.get("auto_cal_pulse_cb")
                        if cb:
                            cb(i, self._auto_cal_session_pulses)

    def start_auto_calibration_mode(self):
        self._auto_cal_mode           = True
        self._auto_cal_locked_tap     = -1
        self._auto_cal_session_pulses = 0
        self._cal_started_on_pico     = False
        print("[PicoSensor] Auto-Calibration Mode STARTED")

    def stop_auto_calibration_mode(self):
        if self._auto_cal_locked_tap >= 0 and self._cal_started_on_pico:
            self._post(f"/api/tap/{self._auto_cal_locked_tap}/calibrate")
        self._auto_cal_mode           = False
        self._auto_cal_locked_tap     = -1
        self._auto_cal_session_pulses = 0
        self._cal_started_on_pico     = False
        print("[PicoSensor] Auto-Calibration Mode STOPPED")

    def reset_auto_calibration_state(self):
        if self._auto_cal_locked_tap >= 0 and self._cal_started_on_pico:
            self._post(f"/api/tap/{self._auto_cal_locked_tap}/calibrate")
        self._auto_cal_locked_tap     = -1
        self._auto_cal_session_pulses = 0
        self._cal_started_on_pico     = False
        print("[PicoSensor] Auto-Calibration RESET")

    def start_flow_calibration(self, tap_index, target_vol):
        return True

    def stop_flow_calibration(self, tap_index):
        return 0, 0.0

    # ------------------------------------------------------------------
    # Volume management helpers
    # ------------------------------------------------------------------

    def deduct_volume_from_keg(self, tap_index, liters):
        keg_id = self.keg_ids_assigned[tap_index]
        if keg_id:
            self.keg_dispensed_liters[tap_index] += liters
            self.settings_manager.update_keg_dispensed_volume(
                keg_id, self.keg_dispensed_liters[tap_index]
            )
            self.settings_manager.save_all_keg_dispensed_volumes()
            self._load_initial_volumes()

    def force_recalculation(self):
        self._load_initial_volumes()

    # ------------------------------------------------------------------
    # Pico-specific helpers called from main_kivy.py
    # ------------------------------------------------------------------

    def get_pico_temperature(self):
        """Return the temperature dict from the last /api/state poll, or None."""
        return self._pico_temperature

    def is_pico_online(self):
        return self._pico_online

    def notify_keg_change(self, tap_index):
        """
        Call when the user assigns a new keg to a tap.
        Resets the Pico's dispensed accumulator for that tap so the new keg
        starts at zero, and re-baselines the local tracking accordingly.
        """
        result = self._post(f"/api/tap/{tap_index}/reset")
        if result:
            print(f"[PicoSensor] Tap {tap_index+1} reset on Pico.")
        else:
            print(f"[PicoSensor] Warning: could not reset tap {tap_index+1} on Pico.")
        self._last_dispensed[tap_index] = 0.0

    def push_k_factors_to_pico(self, k_factors):
        """Push updated K-factors to the Pico after calibration."""
        result = self._post("/api/config", {"k_factors": k_factors})
        if result:
            print(f"[PicoSensor] K-factors pushed to Pico: {k_factors}")
        else:
            print("[PicoSensor] Warning: could not push K-factors to Pico.")

    # ------------------------------------------------------------------
    # Compatibility stubs (match SensorLogic interface)
    # ------------------------------------------------------------------

    def _save_pico_baselines(self):
        """Persist the Pico's current dispensed counters to settings so that
        volume poured while the app is closed is captured on next startup."""
        baselines = []
        for i in range(self.num_sensors):
            val = self._last_dispensed[i]
            baselines.append(val if val is not None else 0.0)
        self.settings_manager.save_pico_tap_last_dispensed(baselines)

    def cleanup_gpio(self):
        """Called by on_stop — save Pico baselines then halt the polling thread."""
        self._save_pico_baselines()
        self._running = False
        print("[PicoSensor] Monitoring stopped.")

    def simulate_pulse_increment(self, tap_index, pulse_amount):
        """
        Simulation is not meaningful in Pico W mode (real sensors are active).
        This stub keeps the app from crashing when the simulation panel is used.
        """
        pass

    # ------------------------------------------------------------------
    # Internal UI callback wrapper
    # ------------------------------------------------------------------

    def _update_ui(self, idx, rate, rem, status, pour_vol):
        cb = self.ui_callbacks.get("update_sensor_data_cb")
        if cb:
            cb(idx, rate, rem, status, pour_vol)


# ---------------------------------------------------------------------------
# Module-level discovery helpers (called from SettingsConfigTab.find_pico)
# ---------------------------------------------------------------------------

def get_local_subnet_prefix():
    """
    Return the first three octets of the machine's LAN IP as a string,
    e.g. '192.168.68'.  Returns None if the local IP cannot be determined.
    """
    import socket as _socket
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ".".join(ip.split(".")[:3])
    except Exception:
        return None


def scan_for_pico(subnet_prefix, timeout=0.5):
    """
    Probe every host in <subnet_prefix>.1 – .254 for the Pico's /api/version
    endpoint.  Returns the first IP that responds with a 'version' key, or None.

    Concurrency is capped at MAX_CONCURRENT to avoid flooding the network stack
    and starving the sensor polling thread.
    """
    if _urllib_request is None:
        return None

    MAX_CONCURRENT = 25
    found = [None]
    lock  = threading.Lock()
    sem   = threading.Semaphore(MAX_CONCURRENT)

    def _probe(ip):
        with sem:
            if found[0]:        # stop as soon as another thread succeeds
                return
            try:
                req = _urllib_request.Request(
                    f"http://{ip}/api/version",
                    headers={"Accept": "application/json"}
                )
                with _urllib_request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode())
                    if "version" in data:
                        with lock:
                            if not found[0]:
                                found[0] = ip
            except Exception:
                pass

    threads = [
        threading.Thread(target=_probe, args=(f"{subnet_prefix}.{i}",), daemon=True)
        for i in range(1, 255)
    ]
    for t in threads:
        t.start()

    # Join with a per-thread timeout; break early once the Pico is found
    deadline = time.time() + timeout + 2.0
    for t in threads:
        remaining = deadline - time.time()
        if remaining <= 0 or found[0]:
            break
        t.join(timeout=remaining)

    return found[0]
