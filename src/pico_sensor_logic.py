# pico_sensor_logic.py
# Pico W sensor backend for KegLevel Pico.
#
# Implements the same interface as SensorLogic (sensor_logic.py) but polls
# the Pico W REST API instead of reading GPIO pins directly.
#
# Architecture: the Pico is the single source of truth for the keg/beverage
# library and for dispensed volumes.  The app maintains an in-memory cache
# populated from the Pico on every connect, and writes a local backup
# (keg_library.json / beverages_library.json) so a freshly reflashed Pico
# can be automatically restored.  All keg/beverage edits are pushed to the
# Pico first; the local cache is updated afterwards.

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
REQUEST_TIMEOUT_S    = 6.0   # MicroPython GC pauses can exceed 3 s on Pico 2 W
POLL_INTERVAL_S      = 1.5   # idle poll rate — gives Pico breathing room between GC
POUR_POLL_INTERVAL_S = 0.15  # fast poll rate during pour — matches Pico 250ms updates
OFFLINE_RETRY_S      = 5.0   # sleep between polls once truly offline
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

        # Firmware version from last /api/state poll (for Updates tab)
        self._pico_version = None

        # Calibration state
        self._auto_cal_mode           = False
        self._auto_cal_locked_tap     = -1
        self._auto_cal_session_pulses = 0
        self._cal_started_on_pico     = False
        self._is_calibrating          = False   # compatibility stub checked by screen on_leave
        self._sim_cal_active          = False   # True when sim is driving the cal session

        # Simulation mode — per-tap flag keeps sensor loop from overwriting "Pouring"
        self._sim_is_pouring       = [False] * self.num_sensors

        # Lifetime pulse tracking — used to pass real pulse deltas to the keg
        # record so that Keg-Kicked calibration has accurate pulse data.
        self._last_lifetime_pulses = [None] * self.num_sensors

        # Per-tap pulse accumulator for keg-kick calibration.
        # Counts pulses since the current keg was assigned; flushed to the
        # Pico keg record when the keg is removed or the app closes.
        self._session_pulses_by_tap: dict = {}

        # Threading
        self._running      = False
        self.is_paused     = False
        self.sensor_thread = None
        self._pico_online  = False
        self._use_fast_poll = False  # True when pouring or saw flow delta (optimistic)

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

    def _put(self, path, data=None):
        """HTTP PUT with JSON body → parsed JSON dict, or None on any error."""
        try:
            body = json.dumps(data or {}).encode()
            req  = _urllib_request.Request(
                self.base_url + path,
                data=body,
                method="PUT",
                headers={"Content-Type": "application/json",
                         "Accept":       "application/json"}
            )
            with _urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    def _delete(self, path):
        """HTTP DELETE → parsed JSON dict, or None on any error."""
        try:
            req = _urllib_request.Request(
                self.base_url + path,
                method="DELETE",
                headers={"Accept": "application/json"}
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
            # UDP broadcast is blocked by Windows Firewall on many machines and
            # mDNS .local resolution is unreliable on Windows without Bonjour.
            # Fall back to an active subnet scan after 10 s if still not found.
            scan_thread = threading.Thread(
                target=self._auto_scan_fallback, daemon=True
            )
            scan_thread.start()
            print(f"[PicoSensor] Discovery mode — listening on UDP port {DISCOVERY_PORT}...")
        else:
            print(f"[PicoSensor] Using configured host: {self.host}")
        if self.sensor_thread is None or not self.sensor_thread.is_alive():
            self.sensor_thread = threading.Thread(
                target=self._sensor_loop, daemon=True
            )
            self.sensor_thread.start()

    def _auto_scan_fallback(self):
        """
        If UDP broadcast and mDNS haven't resolved the Pico within 10 seconds,
        run a one-shot subnet scan — the same reliable approach used by FIND PICO.
        Uses a generous probe timeout (2 s) to handle Picos with high latency.
        Exits silently if discovery already succeeded before the scan finishes.
        """
        time.sleep(10.0)
        if not self._running or self.base_url is not None:
            return  # Already found via UDP or mDNS
        prefix = get_local_subnet_prefix()
        print(f"[PicoSensor] Auto-scan fallback: scanning subnet {prefix}.0/24 ...")
        ip = scan_for_pico(prefix, timeout=2.0) if prefix else None
        if ip and self.base_url is None:
            print(f"[PicoSensor] Auto-scan: Pico found at {ip}")
            self.host     = ip
            self.base_url = f"http://{ip}"
        elif not ip:
            print("[PicoSensor] Auto-scan: Pico not found on subnet.")

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

    def set_host(self, ip):
        """Update the Pico host at runtime (e.g. after FIND PICO succeeds)."""
        ip = (ip or "").strip()
        if not ip:
            return
        self._manual_host = ip
        self._discovery_mode = False
        self.host = ip
        self.base_url = f"http://{ip}"
        print(f"[PicoSensor] Host updated to {ip}")

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

            # Wait for discovery to find the Pico before making any requests.
            # While UDP broadcast discovery hasn't resolved a host yet, try the
            # well-known mDNS name as a fallback.  This handles the common case
            # where the Pico was already running before the app started (its boot
            # broadcast was missed) — mDNS resolves regardless of boot timing.
            if self.base_url is None:
                try:
                    req = _urllib_request.Request(
                        f"http://{DEFAULT_PICO_HOST}/api/state",
                        headers={"Accept": "application/json"}
                    )
                    with _urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                        json.loads(resp.read().decode())
                    self.host     = DEFAULT_PICO_HOST
                    self.base_url = f"http://{DEFAULT_PICO_HOST}"
                    print(f"[PicoSensor] mDNS fallback: connected via {DEFAULT_PICO_HOST}")
                except Exception:
                    time.sleep(OFFLINE_RETRY_S)
                continue

            endpoint = "/api/state/fast" if self._use_fast_poll else "/api/state"
            state = self._get(endpoint)
            if state is None:
                time.sleep(0.3)
                state = self._get(endpoint)  # one retry for transient failures

            if state is None:
                _consecutive_failures += 1
                print(f"[PicoSensor] Poll failed (#{_consecutive_failures})")

                if _consecutive_failures >= self._OFFLINE_THRESHOLD:
                    # Sustained outage — tell the UI and slow down polling
                    if self._pico_online:
                        self._pico_online = False
                        print("[PicoSensor] Pico offline — slowing retry rate")
                        cb = self.ui_callbacks.get("pico_online_cb")
                        if cb:
                            cb(False)
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

                # 1. Push calibrated k_factors — a reflashed Pico resets to
                #    firmware defaults; pushing immediately avoids bad readings.
                try:
                    k_factors = self.settings_manager.get_flow_calibration_factors()
                    self.push_k_factors_to_pico(k_factors)
                except Exception as _kf_err:
                    print(f"[PicoSensor] Could not push k_factors on connect: {_kf_err}")

                # 2. If Pico just had its flash wiped, restore the local backup.
                try:
                    self._restore_backup_if_pico_empty()
                except Exception as _rb_err:
                    print(f"[PicoSensor] Backup restore error: {_rb_err}")

                # 3. Pull the authoritative library from the Pico and update the
                #    in-memory cache so any screen that opens sees fresh data.
                try:
                    self._refresh_library_from_pico()
                    self._load_initial_volumes()
                except Exception as _rl_err:
                    print(f"[PicoSensor] Library refresh error: {_rl_err}")

                cb = self.ui_callbacks.get("pico_online_cb")
                if cb:
                    cb(True)

            # Cache temperature and version for main_kivy to read
            self._pico_temperature = state.get("temperature")
            self._pico_version = state.get("version")

            taps          = state.get("taps", [])
            displayed     = self.settings_manager.get_displayed_taps()
            displayed     = min(displayed, self.num_sensors, len(taps))

            if self._auto_cal_mode:
                self._process_calibration(taps, displayed)
                self._use_fast_poll = False
                time.sleep(POLL_INTERVAL_S)
                continue

            any_delta_this_round = False
            for i in range(displayed):
                tap            = taps[i]
                pico_dispensed = float(tap.get("dispensed_liters", 0.0))
                pico_pulses    = int(tap.get("lifetime_pulses", 0))
                # Pico computes remaining_liters = starting_volume - dispensed.
                # This is the single source of truth — no local re-computation.
                pico_remaining = float(tap.get("remaining_liters", 0.0))
                pouring        = bool(tap.get("pouring", False))
                flow_rate      = float(tap.get("flow_rate_lpm", 0.0))

                # Simulation mode — advance baselines silently so the delta
                # stays correct when sim ends; UI driven by simulate_pulse_increment.
                if self._sim_is_pouring[i]:
                    self._last_dispensed[i]       = pico_dispensed
                    self._last_lifetime_pulses[i] = pico_pulses
                    continue

                # First poll for this tap — use Pico's remaining directly.
                # Any volume dispensed while the app was offline is already
                # reflected in pico_remaining, so no offline-delta needed.
                if self._last_dispensed[i] is None:
                    self._last_dispensed[i]       = pico_dispensed
                    self._last_lifetime_pulses[i] = pico_pulses
                    self.last_known_remaining_liters[i] = pico_remaining
                    self._update_ui(i, 0.0, pico_remaining, "Idle",
                                    self.last_pour_volumes[i])
                    continue

                # If Pico dispensed went backwards (keg change / tap reset), re-baseline.
                if pico_dispensed < self._last_dispensed[i]:
                    print(f"[PicoSensor] Tap {i+1}: Pico dispensed reset — re-baselining.")
                    self._last_dispensed[i]       = pico_dispensed
                    self._last_lifetime_pulses[i] = pico_pulses

                delta = max(0.0, pico_dispensed - self._last_dispensed[i])
                self._last_dispensed[i] = pico_dispensed

                # Accumulate pulse delta for keg-kick calibration.
                if self._last_lifetime_pulses[i] is None:
                    self._last_lifetime_pulses[i] = pico_pulses
                pulse_delta = max(0, pico_pulses - self._last_lifetime_pulses[i])
                self._last_lifetime_pulses[i] = pico_pulses

                if delta > 0:
                    any_delta_this_round = True
                    self.current_pour_volume[i] += delta
                    self.keg_dispensed_liters[i] += delta
                    # Accumulate pulses; flushed to Pico on pour end.
                    self._session_pulses_by_tap[i] = (
                        self._session_pulses_by_tap.get(i, 0) + pulse_delta
                    )

                # Pico is the source of truth for remaining volume.
                self.last_known_remaining_liters[i] = pico_remaining

                if pouring:
                    self.tap_is_active[i] = True
                    self._update_ui(i, flow_rate, pico_remaining,
                                    "Pouring", self.current_pour_volume[i])
                elif self.tap_is_active[i]:
                    # Pour just stopped — save UI state and flush keg stats to Pico.
                    self.tap_is_active[i]      = False
                    self.last_pour_volumes[i]  = self.current_pour_volume[i]
                    self.current_pour_volume[i] = 0.0
                    self.settings_manager.save_last_pour_volumes(self.last_pour_volumes)
                    keg_id = self.keg_ids_assigned[i]
                    if keg_id and keg_id != "unassigned_keg_id":
                        self._flush_keg_stats_to_pico(i, keg_id)
                    self._update_ui(i, 0.0, pico_remaining, "Idle",
                                    self.last_pour_volumes[i])
                else:
                    self._update_ui(i, 0.0, pico_remaining, "Idle",
                                    self.last_pour_volumes[i])

            # Use fast poll + lightweight endpoint when pouring or saw flow delta
            # (optimistic: switch to fast mode on dispensed increase, before Pico flags pouring).
            self._use_fast_poll = any(self.tap_is_active) or any_delta_this_round
            if self._use_fast_poll:
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
            pouring   = bool(tap.get("pouring", False))

            if self._auto_cal_locked_tap == -1:
                # Accept either a meaningful flow rate OR the Pico's pour flag
                # so we catch mid-pour states where flow_rate can momentarily dip.
                if flow_rate > 0.02 or pouring:
                    self._auto_cal_locked_tap = i
                    self._cal_started_on_pico = False
                    print(f"[PicoSensor] Cal: locking to tap {i+1}")

            if i == self._auto_cal_locked_tap:
                # Sim is driving this session — don't POST or GET, just watch.
                if self._sim_cal_active:
                    continue
                if not self._cal_started_on_pico:
                    result = self._post(f"/api/taps/{i}/calibrate/start")
                    if result:
                        self._cal_started_on_pico     = True
                        self._auto_cal_session_pulses = result.get("pulses", 0)
                else:
                    result = self._get(f"/api/taps/{i}/calibrate")
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
        was_active = self._auto_cal_mode
        if self._auto_cal_locked_tap >= 0 and self._cal_started_on_pico:
            self._post(f"/api/taps/{self._auto_cal_locked_tap}/calibrate/stop")
        self._auto_cal_mode           = False
        self._auto_cal_locked_tap     = -1
        self._auto_cal_session_pulses = 0
        self._cal_started_on_pico     = False
        self._sim_cal_active          = False
        if was_active:
            print("[PicoSensor] Auto-Calibration Mode STOPPED")

    def reset_auto_calibration_state(self):
        if self._auto_cal_locked_tap >= 0 and self._cal_started_on_pico:
            self._post(f"/api/taps/{self._auto_cal_locked_tap}/calibrate/stop")
        self._auto_cal_locked_tap     = -1
        self._auto_cal_session_pulses = 0
        self._cal_started_on_pico     = False
        self._sim_cal_active          = False
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

    def get_pico_version(self):
        """Return the firmware version from the last /api/state poll, or empty string."""
        return self._pico_version or ""

    def is_pico_online(self):
        return self._pico_online

    def notify_keg_change(self, tap_index):
        """
        Called when the user assigns a new keg to a tap via the UI.
        Flushes any accumulated pour stats for the outgoing keg, then
        resets local tracking so the new keg starts from zero.
        The actual Pico tap-assignment is handled by assign_keg_to_tap_on_pico()
        which is called from main_kivy before this method.
        """
        old_keg_id = self.keg_ids_assigned[tap_index] if tap_index < len(self.keg_ids_assigned) else None
        if old_keg_id and old_keg_id != "unassigned_keg_id" and self._pico_online:
            try:
                self._flush_keg_stats_to_pico(tap_index, old_keg_id)
            except Exception:
                pass
        self._last_dispensed[tap_index]        = 0.0
        self._last_lifetime_pulses[tap_index]  = 0
        self._session_pulses_by_tap[tap_index] = 0
        print(f"[PicoSensor] Tap {tap_index+1} local tracking reset.")

    def push_k_factors_to_pico(self, k_factors):
        """Push updated K-factors to the Pico after calibration."""
        result = self._put("/api/config", {"k_factors": k_factors})
        if result:
            print(f"[PicoSensor] K-factors pushed to Pico: {k_factors}")
        else:
            print("[PicoSensor] Warning: could not push K-factors to Pico.")

    # ------------------------------------------------------------------
    # Pico library management  (Pico is the single source of truth)
    # ------------------------------------------------------------------

    def create_keg_on_pico(self, data: dict):
        """POST /api/kegs — create a keg on the Pico and return the record."""
        result = self._post("/api/kegs", data)
        if result:
            print(f"[PicoSensor] Keg created on Pico: {result.get('id')} '{result.get('name')}'")
        else:
            print("[PicoSensor] Warning: could not create keg on Pico.")
        return result

    def update_keg_on_pico(self, keg_id: str, data: dict):
        """PUT /api/kegs/<keg_id> — update keg fields on the Pico."""
        result = self._put(f"/api/kegs/{keg_id}", data)
        if not result:
            print(f"[PicoSensor] Warning: could not update keg {keg_id} on Pico.")
        return result

    def delete_keg_on_pico(self, keg_id: str):
        """DELETE /api/kegs/<keg_id> — remove a keg from the Pico."""
        result = self._delete(f"/api/kegs/{keg_id}")
        if result:
            print(f"[PicoSensor] Keg {keg_id} deleted from Pico.")
        else:
            print(f"[PicoSensor] Warning: could not delete keg {keg_id} from Pico.")
        return result

    def create_bev_on_pico(self, data: dict):
        """POST /api/beverages — create a beverage on the Pico and return the record."""
        result = self._post("/api/beverages", data)
        if result:
            print(f"[PicoSensor] Beverage created on Pico: {result.get('id')} '{result.get('name')}'")
        else:
            print("[PicoSensor] Warning: could not create beverage on Pico.")
        return result

    def update_bev_on_pico(self, bev_id: str, data: dict):
        """PUT /api/beverages/<bev_id> — update beverage fields on the Pico."""
        result = self._put(f"/api/beverages/{bev_id}", data)
        if not result:
            print(f"[PicoSensor] Warning: could not update beverage {bev_id} on Pico.")
        return result

    def delete_bev_on_pico(self, bev_id: str):
        """DELETE /api/beverages/<bev_id> — remove a beverage from the Pico."""
        result = self._delete(f"/api/beverages/{bev_id}")
        if result:
            print(f"[PicoSensor] Beverage {bev_id} deleted from Pico.")
        else:
            print(f"[PicoSensor] Warning: could not delete beverage {bev_id} from Pico.")
        return result

    def assign_keg_to_tap_on_pico(self, tap_index: int, keg_id: str):
        """
        PUT /api/taps/<tap_n> — tell the Pico which keg is on a tap.
        Passing keg_id="" unassigns the tap.  The Pico resets the tap's
        dispensed counter automatically when a new keg is assigned.
        """
        result = self._put(f"/api/taps/{tap_index}", {"keg_id": keg_id})
        if result:
            print(f"[PicoSensor] Tap {tap_index+1} assignment updated on Pico → '{keg_id}'")
            # Re-baseline local tracking so the sensor loop doesn't compute a
            # false pour-delta against the previous keg's counters.
            self._last_dispensed[tap_index]       = 0.0
            self._last_lifetime_pulses[tap_index] = 0
            self._session_pulses_by_tap[tap_index] = 0
        else:
            print(f"[PicoSensor] Warning: could not update tap {tap_index+1} assignment on Pico.")
        return result

    def _flush_keg_stats_to_pico(self, tap_index: int, keg_id: str):
        """
        Persist pour-session stats (dispensed volume + pulse count) back to the
        keg record on the Pico.  Called when a pour ends or when a keg is
        unassigned.  Uses the Pico's own remaining_liters as the source of truth
        for computing current_dispensed_liters.
        """
        keg = self.settings_manager.get_keg_by_id(keg_id)
        if not keg:
            return
        starting = (keg.get("calculated_starting_volume_liters")
                    or keg.get("starting_volume_liters", 0.0))
        remaining   = self.last_known_remaining_liters[tap_index]
        dispensed   = max(0.0, starting - remaining)

        prev_pulses    = int(keg.get("total_dispensed_pulses", 0))
        session_pulses = self._session_pulses_by_tap.get(tap_index, 0)
        new_pulses     = prev_pulses + session_pulses
        self._session_pulses_by_tap[tap_index] = 0

        payload = {
            "current_dispensed_liters": round(dispensed, 4),
            "total_dispensed_pulses":   new_pulses,
        }
        self.update_keg_on_pico(keg_id, payload)
        self.settings_manager.update_keg_in_cache(keg_id, payload)

    def _refresh_library_from_pico(self):
        """
        Pull the full keg and beverage library from the Pico and replace the
        app's in-memory cache.  Also derives tap and beverage assignments from
        each keg's tap_index field.  Called every time the Pico comes online.
        """
        kegs = self._get("/api/kegs")
        bevs = self._get("/api/beverages")
        if kegs is None or bevs is None:
            print("[PicoSensor] Warning: could not refresh library from Pico.")
            return

        self.settings_manager.populate_keg_cache(kegs)
        self.settings_manager.populate_beverage_cache(bevs)

        # Derive tap→keg assignments from each keg's tap_index field
        assignments = ["unassigned_keg_id"] * self.num_sensors
        bev_assigns = ["unassigned_beverage_id"] * self.num_sensors
        for keg in kegs:
            ti = int(keg.get("tap_index", -1))
            if 0 <= ti < self.num_sensors:
                assignments[ti] = keg["id"]
                bev_assigns[ti] = keg.get("beverage_id", "unassigned_beverage_id") or "unassigned_beverage_id"

        self.settings_manager.populate_keg_assignments(assignments)
        self.settings_manager.populate_bev_assignments(bev_assigns)

        # Write backup so a freshly reflashed Pico can be restored
        self._write_pico_backup(kegs, bevs)
        print(f"[PicoSensor] Library refreshed: {len(kegs)} kegs, {len(bevs)} beverages.")

    def _write_pico_backup(self, kegs: list, beverages: list):
        """Persist a snapshot of the Pico library to the local data directory."""
        try:
            self.settings_manager.save_pico_library_backup(kegs, beverages)
        except Exception as e:
            print(f"[PicoSensor] Warning: could not write Pico backup: {e}")

    def _restore_backup_if_pico_empty(self):
        """
        If the Pico has an empty library (freshly reflashed) but the local
        backup has data, push the backup to the Pico to restore the user's
        keg and beverage library automatically.
        """
        pico_kegs = self._get("/api/kegs") or []
        if pico_kegs:
            return  # Pico already has data — nothing to restore

        backup_kegs, backup_bevs = self.settings_manager.load_pico_library_backup()
        if not backup_kegs and not backup_bevs:
            return  # No backup either — fresh install

        print("[PicoSensor] Pico library empty — restoring from local backup...")

        # Push beverages first; kegs reference beverage IDs
        bev_id_map: dict = {}
        for bev in backup_bevs:
            result = self.create_bev_on_pico({
                "id":          bev.get("id", ""),
                "name":        bev.get("name", ""),
                "style":       bev.get("style", ""),
                "bjcp":        bev.get("bjcp", ""),
                "abv":         bev.get("abv", ""),
                "ibu":         bev.get("ibu", ""),
                "srm":         bev.get("srm"),
                "description": bev.get("description", ""),
            })
            if result:
                bev_id_map[bev.get("id", "")] = result["id"]

        # Retrieve tap assignments from the local settings (may be stale but
        # better than nothing; will be corrected by _refresh_library_from_pico)
        local_assignments = self.settings_manager.get_sensor_keg_assignments()

        for keg in backup_kegs:
            old_bev_id = keg.get("beverage_id", "")
            new_bev_id = bev_id_map.get(old_bev_id, old_bev_id)

            tap_idx = -1
            for j, k_id in enumerate(local_assignments):
                if k_id == keg.get("id"):
                    tap_idx = j
                    break

            self.create_keg_on_pico({
                "id":                         keg.get("id", ""),
                "name":                       keg.get("title", keg.get("name", "Keg")),
                "title":                      keg.get("title", keg.get("name", "Keg")),
                "beverage_id":                new_bev_id,
                "starting_volume_liters":     keg.get("calculated_starting_volume_liters",
                                              keg.get("starting_volume_liters", 0.0)),
                "tap_index":                  tap_idx,
                "tare_weight_kg":             keg.get("tare_weight_kg", 0.0),
                "starting_total_weight_kg":   keg.get("starting_total_weight_kg", 0.0),
                "maximum_full_volume_liters": keg.get("maximum_full_volume_liters", 0.0),
                "current_dispensed_liters":   keg.get("current_dispensed_liters", 0.0),
                "total_dispensed_pulses":     keg.get("total_dispensed_pulses", 0),
                "fill_date":                  keg.get("fill_date", ""),
            })

        print("[PicoSensor] Backup restore complete.")

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
        """Called by on_stop — flush keg stats to Pico then halt the polling thread."""
        if self._pico_online and self.base_url:
            for i in range(self.num_sensors):
                keg_id = self.keg_ids_assigned[i]
                if keg_id and keg_id != "unassigned_keg_id":
                    try:
                        self._flush_keg_stats_to_pico(i, keg_id)
                    except Exception:
                        pass
        self._running = False
        print("[PicoSensor] Monitoring stopped.")

    def simulate_pulse_increment(self, tap_index, pulse_amount):
        """
        Inject a simulated pour into local volume tracking.

        When auto-calibration mode is active (calibration tab open), pulses
        are routed to the calibration callback instead of pour volume tracking
        — mirroring how the Pico handles real pulses during a calibration
        session.  In normal mode, converts pulses → liters via the app-side
        K-factor and updates all volume state the same way the sensor loop
        does for a real pour.
        """
        if tap_index >= self.num_sensors or pulse_amount <= 0:
            return

        if self._auto_cal_mode:
            # Route sim pulses to the calibration tab, not pour volume tracking.
            # Set _sim_cal_active so _process_calibration doesn't POST to the
            # Pico and overwrite _auto_cal_session_pulses with zero.
            if self._auto_cal_locked_tap == -1:
                self._auto_cal_locked_tap     = tap_index
                self._auto_cal_session_pulses = 0
                self._sim_cal_active          = True
                print(f"[PicoSensor] Cal (sim): locking to tap {tap_index + 1}")
            if self._auto_cal_locked_tap == tap_index:
                self._auto_cal_session_pulses += pulse_amount
                cb = self.ui_callbacks.get("auto_cal_pulse_cb")
                if cb:
                    cb(tap_index, self._auto_cal_session_pulses)
            return

        k_factors = self.settings_manager.get_flow_calibration_factors()
        k = k_factors[tap_index] if tap_index < len(k_factors) else 5100.0
        if k <= 0:
            return
        liters = pulse_amount / k
        keg_id = self.keg_ids_assigned[tap_index]
        if keg_id:
            self.keg_dispensed_liters[tap_index] += liters
            self.settings_manager.update_keg_dispensed_volume(
                keg_id, self.keg_dispensed_liters[tap_index], pulses=0
            )
        self.last_known_remaining_liters[tap_index] -= liters
        self.current_pour_volume[tap_index]         += liters
        self.tap_is_active[tap_index]                = True
        self._sim_is_pouring[tap_index]              = True
        self._update_ui(tap_index, 3.0,
                        self.last_known_remaining_liters[tap_index],
                        "Pouring",
                        self.current_pour_volume[tap_index])

    def end_sim_pour(self, tap_index):
        """
        Finalise a simulated pour: save volumes, update UI to Idle, clear flag.
        Called by the app when continuous sim is toggled off or after the
        one-shot (PINT) delay fires.
        No-op when a calibration session is active — calibration manages its
        own state and any deferred PINT call must not disturb it.
        """
        if tap_index >= self.num_sensors:
            return
        if self._sim_cal_active:
            return
        self._sim_is_pouring[tap_index]    = False
        self.tap_is_active[tap_index]      = False
        self.last_pour_volumes[tap_index]  = self.current_pour_volume[tap_index]
        self.current_pour_volume[tap_index] = 0.0
        self.settings_manager.save_last_pour_volumes(self.last_pour_volumes)
        self.settings_manager.save_all_keg_dispensed_volumes()
        self._update_ui(tap_index, 0.0,
                        self.last_known_remaining_liters[tap_index],
                        "Idle",
                        self.last_pour_volumes[tap_index])

    # ------------------------------------------------------------------
    # Diagnostic mode — wiring tests
    # ------------------------------------------------------------------

    def enter_diagnostic_mode(self) -> bool:
        """Tell the Pico to enter diagnostic mode (suspends flow IRQs)."""
        if not self.base_url:
            return False
        result = self._post("/api/diagnostic/enter")
        return result is not None

    def exit_diagnostic_mode(self) -> bool:
        """Tell the Pico to exit diagnostic mode (restores flow IRQs)."""
        if not self.base_url:
            return False
        result = self._post("/api/diagnostic/exit")
        return result is not None

    def run_tap_diagnostic_test(self, tap_index: int):
        """
        Run GND / 3V3 / idle / continuity tests for a flow-sensor tap.
        Returns result dict {passed, message, details} or None on comms error.
        Diagnostic mode must already be active on the Pico.
        """
        if not self.base_url:
            return None
        return self._post(f"/api/diagnostic/test/tap/{tap_index}")

    def run_temp_diagnostic_test(self):
        """
        Run GND / 3V3 / continuity tests for the DS18B20 sensor.
        Returns result dict {passed, message, details} or None on comms error.
        Diagnostic mode must already be active on the Pico.
        """
        if not self.base_url:
            return None
        return self._post("/api/diagnostic/test/temp")

    def run_board_diagnostic_test(self):
        """
        Run all-pairs short-circuit test for the 17 GPIOs on physical pins 1-20.
        Returns result dict {passed, message, details} or None on comms error.
        Diagnostic mode must already be active on the Pico.
        Run with the sensor test harness DISCONNECTED to avoid false positives.
        """
        if not self.base_url:
            return None
        return self._post("/api/diagnostic/test/board")

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

    # Deadline scales with timeout: ceil(254/25) batches × timeout, plus headroom.
    deadline = time.time() + max(12.0, (254 / MAX_CONCURRENT + 1) * timeout * 1.3)
    for t in threads:
        remaining = deadline - time.time()
        if remaining <= 0 or found[0]:
            break
        t.join(timeout=min(remaining, 0.3))

    return found[0]
