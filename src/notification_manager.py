# keglevel lite app
#
# notification_manager.py

import threading
import time
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

# --- Constants ---

FREQUENCY_SECONDS = {
    "Hourly":  3_600,
    "Daily":   86_400,
    "Weekly":  604_800,
    "Monthly": 2_592_000,   # 30 days
}

CONDITIONAL_CHECK_INTERVAL_S = 60   # How often to evaluate conditional alerts
TEMP_ALERT_COOLDOWN_S = 7200        # 2 hours between repeated temperature alerts
ERROR_DEBOUNCE_S = 3600             # 1 hour between repeated error log entries

# Sentinel values matching the OFF positions on the settings sliders.
# If a stored value equals the sentinel, that alert type is disabled.
VOLUME_OFF    = 0.0     # slider leftmost  (0.0 L  = OFF)
LOW_TEMP_OFF  = 27.0    # slider leftmost  (27 °F  = OFF)
HIGH_TEMP_OFF = 61.0    # slider rightmost (61 °F  = OFF)

LITERS_TO_GAL = 0.264172


class NotificationManager:
    """
    Background notification engine for KegLevel Lite.

    Responsibilities
    ----------------
    1. Scheduled push emails — frequency-gated tap-level / temperature summary.
    2. Conditional alerts   — low-volume per tap, kegerator temp out of range.

    Design notes
    ------------
    * Email-only (no SMS).  Frequency = "None" disables push emails entirely.
    * A single daemon thread drives both push and conditional scheduling.
    * All settings are read fresh from SettingsManager on every tick so that
      changes saved from the UI take effect without restarting.
    * Conditional alert state is persisted through SettingsManager helpers so
      that sent-flags survive an app restart.
    """

    def __init__(self, settings_manager, get_temp_f_cb=None):
        """
        Parameters
        ----------
        settings_manager : SettingsManager
            The app's shared settings object.
        get_temp_f_cb : callable, optional
            Zero-argument callable that returns the current kegerator
            temperature in °F (float), or None if unavailable.
        """
        self.settings_manager = settings_manager
        self.get_temp_f_cb = get_temp_f_cb

        # Scheduler thread state
        self._scheduler_running = False
        self._scheduler_thread  = None
        self._scheduler_event   = threading.Event()

        # Tracks when the last scheduled push email was sent
        self.last_push_sent_time = 0

        # Tracks when conditional alerts were last evaluated.
        # Initialised to now so that no alerts fire at startup.
        self.last_conditional_check_time = time.time()

    # ------------------------------------------------------------------
    # Public lifecycle API
    # ------------------------------------------------------------------

    def start_scheduler(self):
        """Start the background scheduler thread (idempotent)."""
        if self._scheduler_running:
            return

        self._scheduler_running = True
        self._scheduler_event.clear()

        # Initialise the push timer so the first notification fires ~60 s
        # after startup rather than immediately.
        push     = self.settings_manager.get_push_notification_settings()
        freq     = push.get("frequency", "None")
        interval = FREQUENCY_SECONDS.get(freq, 0)

        if interval > 0:
            self.last_push_sent_time = time.time() + 60 - interval
            print(f"[NotificationManager] Push enabled ({freq}). First send in ~60 s.")
        else:
            self.last_push_sent_time = time.time()
            print("[NotificationManager] Push disabled (frequency = None).")

        # Reset so no conditional alerts fire at startup
        self.last_conditional_check_time = time.time()

        if self._scheduler_thread is None or not self._scheduler_thread.is_alive():
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                daemon=True,
                name="KegLevelNotifScheduler",
            )
            self._scheduler_thread.start()

        print("[NotificationManager] Scheduler started.")

    def stop_scheduler(self):
        """Stop the scheduler thread gracefully."""
        if not self._scheduler_running:
            return
        print("[NotificationManager] Stopping scheduler...")
        self._scheduler_running = False
        self._scheduler_event.set()
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=3)
        print("[NotificationManager] Scheduler stopped.")

    def force_reschedule(self):
        """
        Call this whenever notification settings change.  Resets the push
        timer so the next send is ~60 s from now (avoids an immediate flood).
        """
        if not self._scheduler_running:
            return
        print("[NotificationManager] Settings changed -- rescheduling.")
        push     = self.settings_manager.get_push_notification_settings()
        freq     = push.get("frequency", "None")
        interval = FREQUENCY_SECONDS.get(freq, 0)

        if interval > 0:
            self.last_push_sent_time = time.time() + 60 - interval
            print(f"[NotificationManager] Rescheduled ({freq}). Next push in ~60 s.")
        else:
            self.last_push_sent_time = time.time()

        self._scheduler_event.set()   # Wake the loop immediately

    def send_manual_status(self):
        """
        Send a status email immediately on demand (UI TEST SEND button).
        Runs on a short-lived daemon thread so the UI stays responsive.
        """
        def _task():
            ok = self._send_push_notification(is_scheduled=False)
            if ok:
                print("[NotificationManager] Test send succeeded.")
            else:
                print("[NotificationManager] Test send failed -- check SMTP settings.")

        threading.Thread(target=_task, daemon=True,
                         name="KegLevelNotifManual").start()

    # ------------------------------------------------------------------
    # Scheduler loop
    # ------------------------------------------------------------------

    def _scheduler_loop(self):
        print("[NotificationManager] Scheduler loop running.")
        while self._scheduler_running:
            now = time.time()

            # 1. Scheduled push notification
            push     = self.settings_manager.get_push_notification_settings()
            freq     = push.get("frequency", "None")
            interval = FREQUENCY_SECONDS.get(freq, 0)

            if interval > 0 and now >= self.last_push_sent_time + interval:
                print("[NotificationManager] Scheduled push due. Sending.")
                if self._send_push_notification(is_scheduled=True):
                    self.last_push_sent_time = now

            # 2. Conditional alerts (evaluated every 60 s)
            if now >= self.last_conditional_check_time + CONDITIONAL_CHECK_INTERVAL_S:
                self._check_conditional_alerts()
                self.last_conditional_check_time = now

            # Wait up to 10 s before the next tick
            self._scheduler_event.clear()
            self._scheduler_event.wait(timeout=10.0)

            if not self._scheduler_running:
                break

        print("[NotificationManager] Scheduler loop stopped.")

    # ------------------------------------------------------------------
    # Conditional alert logic
    # ------------------------------------------------------------------

    def _check_conditional_alerts(self):
        """
        Evaluates low-volume and temperature conditions and sends email
        alerts as needed, respecting sent-flags and cooldown timers.
        """
        push = self.settings_manager.get_push_notification_settings()
        cond = self.settings_manager.get_conditional_notification_settings()

        # Bail out immediately if SMTP is not configured
        if not self._smtp_config_valid(push):
            return

        recipient = push.get("email_recipient", "").strip()
        if not recipient:
            return

        now = time.time()

        # --- A. Low-Volume Alerts (per tap) ---
        threshold_liters = float(cond.get("threshold_liters", VOLUME_OFF))

        if threshold_liters > VOLUME_OFF:
            from settings_manager import UNASSIGNED_KEG_ID

            num_taps    = self.settings_manager.get_displayed_taps()
            assignments = self.settings_manager.get_sensor_keg_assignments()
            sent_flags  = cond.get("sent_notifications", [False] * num_taps)
            labels      = self.settings_manager.get_sensor_labels()

            for i in range(min(num_taps, len(assignments))):
                keg_id = assignments[i]

                # Tap is offline: clear any stale sent-flag
                if not keg_id or keg_id == UNASSIGNED_KEG_ID:
                    if i < len(sent_flags) and sent_flags[i]:
                        self.settings_manager.update_conditional_sent_status(i, False)
                    continue

                keg = self.settings_manager.get_keg_by_id(keg_id)
                if not keg:
                    continue

                start_vol  = float(keg.get("calculated_starting_volume_liters", 0.0))
                dispensed  = float(keg.get("current_dispensed_liters", 0.0))
                remaining  = max(0.0, start_vol - dispensed)
                already_sent = sent_flags[i] if i < len(sent_flags) else False

                if remaining <= threshold_liters and not already_sent:
                    tap_label = labels[i] if i < len(labels) else f"Tap {i + 1}"
                    vol_str, thresh_str = self._format_volume_strings(
                        remaining, threshold_liters
                    )
                    subject = "KegLevel Pico: Low Keg Volume Alert"
                    body = (
                        f"LOW KEG VOLUME ALERT\n"
                        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        f"Tap {i + 1} ({tap_label}) is running low.\n"
                        f"Remaining:       {vol_str}\n"
                        f"Alert Threshold: {thresh_str}\n\n"
                        f"--\nKegLevel Pico Monitoring"
                    )
                    if self._send_email(subject, body, recipient, push):
                        self.settings_manager.update_conditional_sent_status(i, True)
                        print(f"[NotificationManager] Low-volume alert sent -- Tap {i + 1}.")

                elif remaining > threshold_liters and already_sent:
                    # Keg was refilled or replaced — reset the flag
                    self.settings_manager.update_conditional_sent_status(i, False)
                    print(f"[NotificationManager] Volume alert reset -- Tap {i + 1} (refilled).")

        # --- B. Temperature Out-of-Range Alert ---
        low_temp_f  = float(cond.get("low_temp_f",  LOW_TEMP_OFF))
        high_temp_f = float(cond.get("high_temp_f", HIGH_TEMP_OFF))

        low_enabled  = low_temp_f  > LOW_TEMP_OFF
        high_enabled = high_temp_f < HIGH_TEMP_OFF

        if (low_enabled or high_enabled) and self.get_temp_f_cb is not None:
            current_temp = self.get_temp_f_cb()
            if current_temp is not None:
                timestamps = cond.get("temp_sent_timestamps", [])
                last_sent  = timestamps[0] if timestamps else 0.0
                cooldown_ok = (now - last_sent) > TEMP_ALERT_COOLDOWN_S

                alert_reason = ""
                if low_enabled and current_temp < low_temp_f:
                    alert_reason = (
                        f"Temperature ({current_temp:.1f}°F) is BELOW the low threshold "
                        f"({low_temp_f:.0f}°F)."
                    )
                elif high_enabled and current_temp > high_temp_f:
                    alert_reason = (
                        f"Temperature ({current_temp:.1f}°F) is ABOVE the high threshold "
                        f"({high_temp_f:.0f}°F)."
                    )

                if alert_reason and cooldown_ok:
                    low_str  = f"{low_temp_f:.0f}°F"  if low_enabled  else "OFF"
                    high_str = f"{high_temp_f:.0f}°F" if high_enabled else "OFF"
                    subject = "KegLevel Pico: Kegerator Temperature Alert"
                    body = (
                        f"TEMPERATURE ALERT\n"
                        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        f"{alert_reason}\n\n"
                        f"Configured Range: Low {low_str} — High {high_str}\n\n"
                        f"--\nKegLevel Pico Monitoring"
                    )
                    if self._send_email(subject, body, recipient, push):
                        self.settings_manager.update_temp_sent_timestamp(now)
                        print(f"[NotificationManager] Temperature alert sent.")

    # ------------------------------------------------------------------
    # Status body and push send
    # ------------------------------------------------------------------

    def _build_status_body(self):
        """Builds a human-readable tap-by-tap status email body."""
        from settings_manager import UNASSIGNED_KEG_ID

        units       = self.settings_manager.get_display_units()
        num_taps    = self.settings_manager.get_displayed_taps()
        assignments = self.settings_manager.get_sensor_keg_assignments()
        labels      = self.settings_manager.get_sensor_labels()

        lines = [
            "KegLevel Pico Status Report",
            f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "--- Tap Status ---",
        ]

        for i in range(min(num_taps, len(assignments))):
            keg_id    = assignments[i]
            tap_label = labels[i] if i < len(labels) else f"Tap {i + 1}"

            if not keg_id or keg_id == UNASSIGNED_KEG_ID:
                lines.append(f"Tap {i + 1} ({tap_label}): OFFLINE")
                continue

            keg = self.settings_manager.get_keg_by_id(keg_id)
            if not keg:
                lines.append(f"Tap {i + 1} ({tap_label}): No keg data")
                continue

            start_vol = float(keg.get("calculated_starting_volume_liters", 0.0))
            dispensed = float(keg.get("current_dispensed_liters", 0.0))
            max_vol   = float(keg.get("maximum_full_volume_liters", 19.0))
            remaining = max(0.0, start_vol - dispensed)
            percent   = max(0.0, min(100.0, (remaining / max_vol * 100) if max_vol > 0 else 0.0))

            if units == "metric":
                vol_str = f"{remaining:.2f} L"
            else:
                vol_str = f"{remaining * LITERS_TO_GAL:.2f} Gal"

            lines.append(f"Tap {i + 1} ({tap_label}): {vol_str} remaining ({percent:.0f}%)")

        lines.append("")
        lines.append("--- Kegerator ---")

        if self.get_temp_f_cb is not None:
            temp_f = self.get_temp_f_cb()
            if temp_f is not None:
                if units == "metric":
                    temp_c = (temp_f - 32.0) * 5.0 / 9.0
                    lines.append(f"Temperature: {temp_c:.1f}°C")
                else:
                    lines.append(f"Temperature: {temp_f:.1f}°F")
            else:
                lines.append("Temperature: --")
        else:
            lines.append("Temperature: Not available")

        lines += ["", "--", "KegLevel Pico Monitoring"]
        return "\n".join(lines)

    def _send_push_notification(self, is_scheduled=True):
        """Builds and dispatches the scheduled or manual status email."""
        push = self.settings_manager.get_push_notification_settings()
        freq = push.get("frequency", "None")

        # Scheduled sends are suppressed when frequency is None
        if freq == "None" and is_scheduled:
            return False

        if not self._smtp_config_valid(push):
            self._report_error("push", "SMTP details incomplete. Check Alerts settings.")
            return False

        recipient = push.get("email_recipient", "").strip()
        if not recipient:
            self._report_error("push", "No recipient email address configured.")
            return False

        if is_scheduled:
            tag = freq if freq != "None" else "Scheduled"
        else:
            tag = "Test"
        subject = (
            f"KegLevel Pico {tag} Report — "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        body = self._build_status_body()
        return self._send_email(subject, body, recipient, push)

    # ------------------------------------------------------------------
    # Email transport
    # ------------------------------------------------------------------

    def _send_email(self, subject, body, recipient, smtp_cfg):
        """Sends a plain-text email via SMTP with STARTTLS (port 587 typical)."""
        smtp_server     = str(smtp_cfg.get("smtp_server",     "")).strip()
        smtp_port       = smtp_cfg.get("smtp_port", "")
        server_email    = str(smtp_cfg.get("server_email",    "")).strip()
        server_password = str(smtp_cfg.get("server_password", "")).strip()
        recipient       = str(recipient).strip()

        if not all([smtp_server, smtp_port, server_email, server_password, recipient]):
            return False

        try:
            port = int(smtp_port)
        except (ValueError, TypeError):
            self._report_error("push", f"Invalid SMTP port value: '{smtp_port}'.")
            return False

        try:
            with smtplib.SMTP(smtp_server, port, timeout=15) as server:
                server.starttls()
                server.login(server_email, server_password)
                msg             = MIMEText(body)
                msg["Subject"]  = subject
                msg["From"]     = server_email
                msg["To"]       = recipient
                server.sendmail(server_email, [recipient], msg.as_string())
            print(f"[NotificationManager] Email sent to {recipient}: '{subject}'")
            return True
        except Exception as e:
            self._report_error("push", f"SMTP send failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _smtp_config_valid(self, push_settings):
        """Returns True only when all four required SMTP fields are non-empty."""
        return all([
            str(push_settings.get("smtp_server",     "")).strip(),
            push_settings.get("smtp_port", ""),
            str(push_settings.get("server_email",    "")).strip(),
            str(push_settings.get("server_password", "")).strip(),
        ])

    def _format_volume_strings(self, remaining_liters, threshold_liters):
        """Returns (remaining_str, threshold_str) formatted in the user's display units."""
        units = self.settings_manager.get_display_units()
        if units == "metric":
            return (f"{remaining_liters:.2f} L", f"{threshold_liters:.2f} L")
        return (
            f"{remaining_liters  * LITERS_TO_GAL:.2f} Gal",
            f"{threshold_liters  * LITERS_TO_GAL:.2f} Gal",
        )

    def _report_error(self, error_type, message):
        """
        Logs an error to stdout at most once per ERROR_DEBOUNCE_S to prevent
        console spam when the scheduler fires every 10 s with bad SMTP config.
        """
        now  = time.time()
        last = self.settings_manager.get_error_reported_time(error_type)
        if now - last > ERROR_DEBOUNCE_S:
            print(f"[NotificationManager] {error_type.upper()} ERROR: {message}")
            self.settings_manager.update_error_reported_time(error_type, now)
