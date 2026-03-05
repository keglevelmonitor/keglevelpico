# keglevel-lite app
# main_kivy.py
# test for update function with windows deployment
# versioning system added 2026-03-04
import os
import threading
import uuid
import subprocess
import sys
import glob
from datetime import datetime

# Force UTF-8 on stdout/stderr so print() works with non-ASCII characters
# on systems whose locale defaults to latin-1 (e.g. Raspberry Pi).
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# --- 0. OS ENVIRONMENT & ICON SETUP (Must be first) ---
import os

# 1. DEV MACHINE (X11): Preserves the exact association for your development machine
os.environ['SDL_VIDEO_X11_WMCLASS'] = "KegLevel Lite"

# 2. PROD MACHINE (Wayland): Forces the app_id to strictly match "keglevel.desktop"
# os.environ['SDL_VIDEO_WAYLAND_WMCLASS'] = "keglevel"

from kivy.config import Config

# Calculate path to icon immediately
current_dir = os.path.dirname(os.path.abspath(__file__))
icon_path = os.path.join(current_dir, 'assets', 'beer-keg.png')

# Set the icon globally for the window
Config.set('kivy', 'window_icon', icon_path)

# --- 1. KIVY CONFIGURATION ---
Config.set('graphics', 'width', '800')
Config.set('graphics', 'height', '418')
Config.set('graphics', 'resizable', '1')

# CRITICAL PORT FROM BATCHFLOW: Disables Kivy's multitouch overlay on the touchscreen
# which prevents the window context from detaching from the taskbar icon.
Config.set('input', 'mouse', 'mouse,disable_multitouch')

from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition, NoTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.uix.behaviors import ButtonBehavior
from kivy.properties import StringProperty, NumericProperty, ObjectProperty, ListProperty, BooleanProperty
from kivy.utils import get_color_from_hex
from kivy.core.window import Window
from kivy.metrics import Metrics

# --- 2. IMPORT BACKEND LOGIC ---
from settings_manager import SettingsManager, UNASSIGNED_KEG_ID, UNASSIGNED_BEVERAGE_ID
from sensor_logic import SensorLogic, FLOW_SENSOR_PINS
from notification_manager import NotificationManager
from version import APP_VERSION

# Special flag for the "Keg Kicked" action
KEG_KICKED_ID = "keg_kicked_action"

# Constants for Unit Conversion
KG_TO_LBS = 2.20462
LITERS_TO_GAL = 0.264172

# --- SRM COLOR LOGIC ---
def get_srm_color_rgba(srm):
    """Returns Kivy RGBA tuple for a given SRM (0-40). 0=White/Water."""
    if srm is None or srm < 0: return (1, 0.75, 0, 1) # Default Amber fallback
    srm_hex_map = {
        0: "#FFFFFF", 1: "#FFE699", 2: "#FFD878", 3: "#FFCA5A", 4: "#FFBF42", 5: "#FBB123",
        6: "#F8A600", 7: "#F39C00", 8: "#EA8F00", 9: "#E58500", 10: "#DE7C00", 11: "#D77200",
        12: "#CF6900", 13: "#CB6200", 14: "#C35900", 15: "#BB5100", 16: "#B54C00", 17: "#B04500",
        18: "#A63E00", 19: "#A13700", 20: "#9B3200", 21: "#962D00", 22: "#8F2900", 23: "#882300",
        24: "#821E00", 25: "#7B1A00", 26: "#771900", 27: "#701400", 28: "#6A0E00", 29: "#660D00",
        30: "#5E0B00", 31: "#5A0A02", 32: "#600903", 33: "#520907", 34: "#4C0505", 35: "#470606",
        36: "#440607", 37: "#3F0708", 38: "#3B0607", 39: "#3A070B", 40: "#36080A"
    }
    lookup_val = int(srm)
    if lookup_val > 40: lookup_val = 40
    if lookup_val < 0: lookup_val = 0
    hex_color = srm_hex_map.get(lookup_val, "#E5A128")
    return get_color_from_hex(hex_color)

# --- 3. WIDGET LOGIC CLASSES ---

class LevelGauge(Widget):
    percent = NumericProperty(0)
    liquid_color = ListProperty([1, 0.75, 0, 1])

class TapWidget(ButtonBehavior, BoxLayout):
    tap_index = NumericProperty(0)
    tap_title = StringProperty("Tap ?")
    beverage_name = StringProperty("Empty")
    stats_text = StringProperty("") 
    liquid_color = ListProperty([1, 0.75, 0, 1])
    percent_full = NumericProperty(0)
    remaining_text = StringProperty("-- L")
    status_text = StringProperty("Idle")
    
    def on_release(self):
        app = App.get_running_app()
        app.open_tap_selector(self.tap_index)

class KegListItem(BoxLayout):
    title = StringProperty()
    contents = StringProperty()
    keg_id = StringProperty()
    index = NumericProperty(0)

class BeverageListItem(BoxLayout):
    name = StringProperty()
    bev_id = StringProperty()
    index = NumericProperty(0)

class KegSelectPopup(Popup):
    pass

class SettingsConfigTab(BoxLayout):
    """Logic for the Configuration Tab."""
    def init_ui(self):
        app = App.get_running_app()
        app._suppress_dirty = True
        try:
            units = app.settings_manager.get_display_units()
            if units == 'imperial':
                self.ids.btn_imperial.state = 'down'
                self.ids.btn_metric.state = 'normal'
            else:
                self.ids.btn_metric.state = 'down'
                self.ids.btn_imperial.state = 'normal'
            taps = app.settings_manager.get_displayed_taps()
            self.ids.spin_taps.text = str(taps)
        finally:
            app._suppress_dirty = False

    def save_config(self):
        app = App.get_running_app()
        new_units = 'imperial' if self.ids.btn_imperial.state == 'down' else 'metric'
        app.settings_manager.save_display_units(new_units)
        try:
            new_taps = int(self.ids.spin_taps.text)
            app.settings_manager.save_displayed_taps(new_taps)
        except ValueError: pass
        app.is_settings_dirty = False
        app.apply_config_changes()

    def request_monitor_import(self):
        """Triggers the backend migration tool and reports results."""
        from kivy.uix.popup import Popup
        from kivy.uix.label import Label
        from kivy.uix.button import Button
        from kivy.uix.checkbox import CheckBox
        
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        content.add_widget(Label(text="Import data from 'KegLevel Monitor'?\n\nThis will merge unique Kegs & Beverages\nand update Tap Assignments.", halign='center'))
        
        # Checkbox Row
        chk_box = BoxLayout(orientation='horizontal', size_hint_y=None, height=40, spacing=5)
        # Checkbox active by default
        chk = CheckBox(active=True, size_hint_x=None, width=40)
        lbl = Label(text="Import sensor calibration factors?", halign='left', valign='middle')
        lbl.bind(size=lbl.setter('text_size')) # Ensure text wrapping/alignment works if needed
        
        chk_box.add_widget(chk)
        chk_box.add_widget(lbl)
        content.add_widget(chk_box)
        
        btns = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        # Use lambda to capture the specific popup instance and check state
        btn_cancel = Button(text="Cancel", on_release=lambda x: popup.dismiss())
        btn_go = Button(text="IMPORT", background_color=(0, 0.6, 0, 1), 
                        on_release=lambda x: self._run_import(popup, chk.active))
        
        btns.add_widget(btn_cancel)
        btns.add_widget(btn_go)
        content.add_widget(btns)
        
        popup = Popup(title="Confirm Import", content=content, size_hint=(None, None), size=(450, 300))
        popup.open()

    def _run_import(self, popup, import_calibration):
        popup.dismiss()
        app = App.get_running_app()
        success, msg = app.settings_manager.import_data_from_monitor(import_calibration=import_calibration)
        
        # Result Popup
        from kivy.uix.popup import Popup
        from kivy.uix.label import Label
        from kivy.uix.button import Button
        
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        content.add_widget(Label(text=msg, text_size=(380, None), halign='center', valign='middle'))
        
        res_popup = None
        def close_res(instance):
            if res_popup: res_popup.dismiss()
            
        btn = Button(text="OK", size_hint_y=None, height=50, on_release=close_res)
        content.add_widget(btn)
        
        res_popup = Popup(title="Import Result", content=content, size_hint=(None, None), size=(400, 250))
        res_popup.open()
        
        if success:
            app.refresh_keg_list()
            app.refresh_beverage_list()
            app.refresh_dashboard_metadata()
            app.sensor_logic.force_recalculation()

class ConfirmPopup(Popup):
    text = StringProperty("")
    action_callback = ObjectProperty(None)
    
    def confirm(self):
        if self.action_callback:
            self.action_callback()
        self.dismiss()

class SettingsUpdatesTab(BoxLayout):
    """Logic for System Updates."""
    log_text = StringProperty(f"Version: {APP_VERSION}\nReady to check for updates.\n")
    is_working = BooleanProperty(False)
    install_enabled = BooleanProperty(False)

    def check_updates(self):
        self.log_text = "Checking for updates...\n"
        self.is_working = True
        self.install_enabled = False
        threading.Thread(target=self._run_update_process, args=(["--check"], True)).start()

    def install_updates(self):
        self.log_text += "\nStarting Install Process...\n"
        self.is_working = True
        self.install_enabled = False
        threading.Thread(target=self._run_update_process, args=([], False)).start()

    def restart_app(self):
        """Safely restarts the application."""
        app = App.get_running_app()
        print("[System] Restarting application...")
        
        # 1. Stop background threads
        if hasattr(app, 'notification_manager') and app.notification_manager:
            app.notification_manager.stop_scheduler()
        if hasattr(app, 'sensor_logic') and app.sensor_logic:
            app.sensor_logic.stop_monitoring()
            app.sensor_logic.cleanup_gpio()

        # 2. Exec new process
        python = sys.executable
        script = os.path.abspath(sys.argv[0])
        args = sys.argv[1:]
        os.execv(python, [python, script] + args)

    def _run_update_process(self, flags, is_check_mode):
        """Runs the platform-appropriate update script."""
        src_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(src_dir)

        if sys.platform == "win32":
            script_path = os.path.join(project_root, "update.bat")
            cmd = ["cmd", "/c", script_path] + flags
        elif sys.platform == "darwin":
            script_path = os.path.join(project_root, "update_mac.sh")
            cmd = ["bash", script_path] + flags
        else:
            script_path = os.path.join(project_root, "update.sh")
            cmd = ["bash", script_path] + flags

        if not os.path.exists(script_path):
            self._append_log(f"Error: Script not found at {script_path}\n")
            self._finish_work(False)
            return

        try:
            # Run process and capture output line-by-line
            # FIX: Explicitly set cwd to project_root so git commands work from autostart
            # On Windows, use CREATE_NO_WINDOW to avoid flashing a console window
            popen_kw = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=project_root
            )
            if sys.platform == "win32":
                popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            process = subprocess.Popen(cmd, **popen_kw)
            
            update_available = False
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None: break
                if line:
                    self._append_log(line)
                    if "Update Available!" in line: update_available = True

            return_code = process.poll()

            if is_check_mode:
                if update_available:
                    self._append_log("\n[Result] Update Available! Click Install.")
                    self._finish_work(True)
                else:
                    self._append_log("\n[Result] Up to date.")
                    self._finish_work(False)
            else:
                if return_code == 0:
                    self._append_log("\n[Complete] Update Installed. Please Restart.")
                else:
                    self._append_log(f"\n[Error] Update failed with code {return_code}.")
                self._finish_work(False)

        except Exception as e:
            self._append_log(f"\n[Error] {e}\n")
            self._finish_work(False)

    def _append_log(self, text):
        Clock.schedule_once(lambda dt: setattr(self, 'log_text', self.log_text + text))

    def _finish_work(self, enable_install):
        def _reset(dt):
            self.is_working = False
            self.install_enabled = enable_install
        Clock.schedule_once(_reset)

class InventoryScreen(Screen):
    def show_kegs(self):
        self.ids.tab_manager.current = 'tab_kegs'
    def show_bevs(self):
        self.ids.tab_manager.current = 'tab_bevs'
    def add_new_item(self):
        app = App.get_running_app()
        current = self.ids.tab_manager.current
        if current == 'tab_kegs': app.open_keg_edit(None)
        else: app.open_beverage_edit(None)

class SettingsScreen(Screen):
    """
    Manages the Settings Tabs and the corresponding dynamic Footer.
    """
    # KV-observable flag that drives tab-button disabled/opacity bindings
    _cal_mode_active = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_click_time = 0
        self.click_count = 0
        self.mimic_mode_active = False

    def on_pre_enter(self, *args):
        """Always reset to the System tab when opening Settings."""
        app = App.get_running_app()
        app.is_settings_dirty = False   # fresh entry — discard any stale flag
        self._cal_mode_active = False
        self.mimic_mode_active = False
        self.set_active_tab('conf')

    def on_leave(self, *args):
        """Ensure we exit calibration/dirty mode when leaving the screen."""
        app = App.get_running_app()
        app.is_settings_dirty = False   # safety net
        self._cal_mode_active = False
        self.mimic_mode_active = False
        
        # Force backend to stop calibration mode immediately
        if hasattr(app, 'sensor_logic') and app.sensor_logic:
            app.sensor_logic.stop_auto_calibration_mode()
            if app.sensor_logic._is_calibrating:
                app.sensor_logic._is_calibrating = False
                app.sensor_logic._cal_target_tap = -1

    def handle_secret_click(self):
        """Handles clicks on the invisible top-right header button."""
        # 1. Only activate if we are on the Calibration Tab
        if self.ids.settings_manager.current != 'tab_cal':
            return

        # 2. Check Timing (5 Rapid Clicks)
        import time
        current_time = time.time()
        
        # 500ms threshold between clicks
        if current_time - self.last_click_time < 0.5: 
            self.click_count += 1
        else:
            self.click_count = 1
        
        self.last_click_time = current_time

        if self.click_count >= 5:
            self.click_count = 0
            self.toggle_mimic_mode()

    def set_active_tab(self, tab_code):
        """
        Manually handles tab switching.
        tab_code: 'conf', 'upd', 'about', 'cal', 'alerts'
        """
        app = App.get_running_app()

        # Block tab switching when settings are dirty — user must Save or Exit first
        if app.is_settings_dirty:
            return

        # Block tab switching when in calibration mode (except re-clicking 'cal')
        if self._cal_mode_active and tab_code != 'cal':
            return

        # Map codes to UI IDs and Screen Names
        tab_map = {
            'conf':   ('btn_sys',    'tab_conf',   'footer_conf'),
            'upd':    ('btn_upd',    'tab_upd',    'footer_upd'),
            'about':  ('btn_about',  'tab_about',  'footer_about'),
            'cal':    ('btn_cal',    'tab_cal',    'footer_cal'),
            'alerts': ('btn_alerts', 'tab_alerts', 'footer_alerts'),
        }

        if tab_code not in tab_map: return

        target_btn, target_content, target_footer = tab_map[tab_code]

        # Switch Screens
        self.ids.settings_manager.current = target_content
        
        # Only switch footer if we are NOT in mimic mode (or if we are leaving cal)
        if not self.mimic_mode_active or tab_code != 'cal':
            self.ids.footer_manager.current = target_footer

        # Update Buttons state
        for code, (btn_id, _, _) in tab_map.items():
            if btn_id in self.ids:
                self.ids[btn_id].state = 'down' if code == tab_code else 'normal'

        # Special Handling: If entering Calibration, LOCK the others
        if tab_code == 'cal':
            self.set_calibration_mode(active=True)
            
        # Initialize UI when entering tabs that load from backend
        # Suppress dirty-flag changes while programmatically loading values
        if tab_code == 'conf':
            app._suppress_dirty = True
            try:
                self.ids.tab_conf_content.init_ui()
            finally:
                app._suppress_dirty = False
        elif tab_code == 'alerts':
            app._suppress_dirty = True
            try:
                self.ids.tab_alerts_content.init_ui()
            finally:
                app._suppress_dirty = False

    def set_calibration_mode(self, active):
        """
        active=True:  Locks all non-calibration tabs (modal calibration mode).
        active=False: Restores normal tab navigation.
        KV bindings on each tab button observe _cal_mode_active to apply
        disabled/opacity automatically.
        """
        self._cal_mode_active = active
        if not active:
            self.mimic_mode_active = False

    def toggle_mimic_mode(self):
        """Switches the footer between Standard Calibration and Mimic Pour."""
        self.mimic_mode_active = not self.mimic_mode_active
        
        if self.mimic_mode_active:
            self.populate_mimic_footer()
            self.ids.footer_manager.current = 'footer_cal_mimic'
        else:
            self.ids.footer_manager.current = 'footer_cal'

    def populate_mimic_footer(self):
        """Dynamically fills the mimic footer with SimControlWidgets (Pint/Continuous)."""
        container = self.ids.mimic_container
        container.clear_widgets()
        
        app = App.get_running_app()
        num_taps = app.settings_manager.get_displayed_taps()
        
        # We reuse the existing SimControlWidget defined in KV
        from kivy.factory import Factory
        
        for i in range(num_taps):
            # Create the widget that already has "Pint" and "Continuous" stacked
            widget = Factory.SimControlWidget()
            widget.tap_index = i
            container.add_widget(widget)
                    
class SimulationPopup(Popup):
    def on_open(self):
        app = App.get_running_app()
        grid = self.ids.tap_grid
        grid.clear_widgets()
        for i in range(app.num_sensors):
            from kivy.factory import Factory
            row = Factory.SimTapRow()
            row.tap_index = i
            row.tap_name = f"Tap {i+1}"
            grid.add_widget(row)
            
        if app.simulated_temp is not None:
             self.ids.temp_slider.value = app.simulated_temp

    # --- ADD THIS METHOD ---
    def on_dismiss(self):
        """Safety: Stop all flows when the popup is closed."""
        app = App.get_running_app()
        app.stop_all_simulations()
    # -----------------------

    def set_sim_temp(self, val):
        app = App.get_running_app()
        app.simulated_temp = val
        app.update_kegerator_temp(0)

    def reset_temp(self):
        app = App.get_running_app()
        app.simulated_temp = None
        app.update_kegerator_temp(0)
        
        
class KegEditScreen(Screen):
    screen_title = StringProperty("Edit Keg")
    keg_id = StringProperty("")
    beverage_name = StringProperty("Select Beverage")
    beverage_list = ListProperty([])
    
    # These properties hold the CURRENT SLIDER VALUE (whether it is Liters or Gallons)
    max_volume_liters = NumericProperty(19.0)
    tare_weight_kg = NumericProperty(4.5)
    total_weight_kg = NumericProperty(23.5)
    
    # Dynamic Constraints (populated based on Unit setting)
    vol_min = NumericProperty(0.0)
    vol_max = NumericProperty(60.0)
    vol_step = NumericProperty(0.5)
    
    tare_min = NumericProperty(0.0)
    tare_max = NumericProperty(20.0)
    tare_step = NumericProperty(0.1)
    
    total_min = NumericProperty(0.0)
    total_max = NumericProperty(80.0)
    total_step = NumericProperty(0.1)

    # UI Labels
    ui_max_vol_text = StringProperty("")
    ui_tare_text = StringProperty("")
    ui_total_text = StringProperty("")
    ui_calculated_text = StringProperty("")
    ui_remaining_text = StringProperty("")
    keg_label_text = StringProperty("Contents:")

    is_metric = True
    _current_dispensed_liters = 0.0

    def on_pre_enter(self):
        app = App.get_running_app()
        if app:
            # 1. Detect Units
            units = app.settings_manager.get_display_units()
            self.is_metric = (units == "metric")
            
            # 2. Configure Slider Ranges & Steps
            if self.is_metric:
                # Metric defaults
                self.vol_min, self.vol_max, self.vol_step = 1.0, 60.0, 0.5
                self.tare_min, self.tare_max, self.tare_step = 0.0, 20.0, 0.1
                self.total_min, self.total_max, self.total_step = 0.0, 80.0, 0.1
            else:
                # Imperial defaults (User Requested)
                self.vol_min, self.vol_max, self.vol_step = 0.0, 16.0, 0.1  # Gal
                self.tare_min, self.tare_max, self.tare_step = 0.0, 40.0, 0.1 # Lbs
                self.total_min, self.total_max, self.total_step = 0.0, 170.0, 0.1 # Lbs

            # 3. Load Current Database Values (Always Metric in DB)
            self.keg_label_text = "Contents:"
            self._current_dispensed_liters = 0.0

            keg = app.settings_manager.get_keg_by_id(self.keg_id)
            if keg:
                raw_vol = keg.get('maximum_full_volume_liters', 19.0)
                raw_tare = keg.get('tare_weight_kg', 4.0)
                raw_total = keg.get('starting_total_weight_kg', raw_tare)

                # 4. Convert Values to Target Unit for Sliders
                if self.is_metric:
                    self.max_volume_liters = raw_vol
                    self.tare_weight_kg = raw_tare
                    self.total_weight_kg = raw_total
                else:
                    self.max_volume_liters = raw_vol * LITERS_TO_GAL
                    self.tare_weight_kg = raw_tare * KG_TO_LBS
                    self.total_weight_kg = raw_total * KG_TO_LBS

                # Keg title label
                keg_title = keg.get('title', '')
                if keg_title:
                    self.keg_label_text = f"{keg_title}  Contents:"

                # Current dispensed amount (for remaining volume display)
                self._current_dispensed_liters = keg.get('current_dispensed_liters', 0.0)

                # Fetch Beverage Name
                b_id = keg.get('beverage_id')
                lib = app.settings_manager.get_beverage_library().get('beverages', [])
                found = next((b for b in lib if b['id'] == b_id), None)
                self.beverage_name = found['name'] if found else "Select Beverage"

            self.update_display_labels()

    def update_display_labels(self, *args):
        # Calculate Liquid Volume based on current slider values and unit mode
        density = 1.014

        if self.is_metric:
            # Inputs are already kg/L
            liquid_kg = self.total_weight_kg - self.tare_weight_kg
            vol_liters = liquid_kg / density
            remaining_liters = max(0.0, vol_liters - self._current_dispensed_liters)

            # Update UI Strings
            self.ui_max_vol_text = f"{self.max_volume_liters:.1f} L"
            self.ui_tare_text = f"{self.tare_weight_kg:.2f} kg"
            self.ui_total_text = f"{self.total_weight_kg:.2f} kg"
            self.ui_calculated_text = f"{vol_liters:.2f} L"
            self.ui_remaining_text = f"{remaining_liters:.2f} L"

        else:
            # Inputs are Lbs/Gal
            # 1. Convert Lbs -> Kg for density math
            total_kg = self.total_weight_kg / KG_TO_LBS
            tare_kg = self.tare_weight_kg / KG_TO_LBS
            liquid_kg = total_kg - tare_kg

            # 2. Get Liters
            vol_liters = liquid_kg / density
            remaining_liters = max(0.0, vol_liters - self._current_dispensed_liters)

            # 3. Convert Liters -> Gal for display
            vol_gal = vol_liters * LITERS_TO_GAL
            remaining_gal = remaining_liters * LITERS_TO_GAL

            # Update UI Strings
            self.ui_max_vol_text = f"{self.max_volume_liters:.1f} Gal"
            self.ui_tare_text = f"{self.tare_weight_kg:.1f} lb"
            self.ui_total_text = f"{self.total_weight_kg:.1f} lb"
            self.ui_calculated_text = f"{vol_gal:.2f} Gal"
            self.ui_remaining_text = f"{remaining_gal:.2f} Gal"

    def set_max_volume_from_slider(self, value):
        self.max_volume_liters = value
        self.update_display_labels()

    def set_tare_from_slider(self, value):
        self.tare_weight_kg = value
        self.update_display_labels()

    def set_total_from_slider(self, value):
        self.total_weight_kg = value
        self.update_display_labels()

    def _generate_next_keg_title(self, all_kegs):
        """
        Finds the first available 'Keg {nn}' title by looking for gaps
        in the existing numbering sequence.
        """
        existing_numbers = set()
        
        for k in all_kegs:
            title = k.get('title', '')
            # Check if title follows standard "Keg " format
            if title.startswith("Keg "):
                try:
                    # Extract the number part
                    num_str = title[4:].strip()
                    if num_str.isdigit():
                        existing_numbers.add(int(num_str))
                except ValueError:
                    continue
        
        # Start looking from 1 upwards for the first empty slot
        next_num = 1
        while next_num in existing_numbers:
            next_num += 1
            
        return f"Keg {next_num:02}"

    def save_keg_edit(self):
        app = App.get_running_app()
        
        # 1. Determine Metric Values from Sliders
        units = app.settings_manager.get_display_units()
        using_metric = (units == "metric")
        
        if using_metric:
            final_vol_liters = self.max_volume_liters
            final_tare_kg = self.tare_weight_kg
            final_total_kg = self.total_weight_kg
        else:
            final_vol_liters = self.max_volume_liters / LITERS_TO_GAL
            final_tare_kg = self.tare_weight_kg / KG_TO_LBS
            final_total_kg = self.total_weight_kg / KG_TO_LBS

        # 2. Resolve Beverage ID
        bev_name = self.beverage_name
        bev_id = UNASSIGNED_BEVERAGE_ID
        if bev_name != "Empty":
            lib = app.settings_manager.get_beverage_library().get('beverages', [])
            found = next((b for b in lib if b['name'] == bev_name), None)
            if found: bev_id = found['id']

        # 3. Calculate Volume
        density = 1.014
        liquid_kg = final_total_kg - final_tare_kg
        calc_start_vol = liquid_kg / density

        # 4. Handle ID and Title
        is_new = (self.keg_id == "")
        new_keg_id = self.keg_id if not is_new else str(uuid.uuid4())
        
        # --- DATA PRESERVATION START ---
        # Fetch existing record to preserve extra fields (e.g. from Monitor)
        existing_record = app.settings_manager.get_keg_by_id(new_keg_id)
        if existing_record and not is_new:
            keg_data = existing_record.copy()
        else:
            keg_data = {
                "id": new_keg_id,
                "current_dispensed_liters": 0.0,
                "total_dispensed_pulses": 0,
                "fill_date": datetime.now().strftime("%Y-%m-%d")
            }
            # Use backend helper for naming
            keg_data['title'] = app.settings_manager.generate_next_keg_title()
        # --- DATA PRESERVATION END ---

        # 5. Update with Form Data
        keg_data.update({
            "tare_weight_kg": float(final_tare_kg),
            "starting_total_weight_kg": float(final_total_kg),
            "maximum_full_volume_liters": float(final_vol_liters),
            "calculated_starting_volume_liters": float(calc_start_vol),
            "beverage_id": bev_id
        })

        # --- FIX: SMART RESET LOGIC ---
        # If the new Calculated Volume is LESS than what we supposedly dispensed,
        # it means the math has drifted or the keg was refilled/reset.
        # Auto-correct history to prevent negative volume display.
        current_dispensed = keg_data.get('current_dispensed_liters', 0.0)
        if calc_start_vol < current_dispensed:
            print(f"KegEdit: correcting dispensed volume ({current_dispensed:.2f}L) > start volume ({calc_start_vol:.2f}L). Resetting history.")
            keg_data['current_dispensed_liters'] = 0.0
            keg_data['total_dispensed_pulses'] = 0
            keg_data['fill_date'] = datetime.now().strftime("%Y-%m-%d")
        # ------------------------------

        # 6. Save
        all_kegs = app.settings_manager.get_keg_definitions()
        if is_new:
            all_kegs.append(keg_data)
        else:
            for i, k in enumerate(all_kegs):
                if k['id'] == new_keg_id:
                    all_kegs[i] = keg_data
                    break
        
        app.settings_manager.save_keg_definitions(all_kegs)
        
        # 7. Refresh
        app.refresh_keg_list()
        app.refresh_dashboard_metadata()
        app.sensor_logic.force_recalculation()
        app.navigate_to('inventory')

class BeverageEditScreen(Screen):
    screen_title = StringProperty("Edit Beverage")
    bev_id = StringProperty("")
    bev_name = StringProperty("")
    # --- NEW PROPERTIES ---
    bev_bjcp = StringProperty("")
    bjcp_list = ListProperty([])
    # ----------------------
    bev_style = StringProperty("")
    # These properties hold the CURRENT SLIDER VALUE
    bev_abv = NumericProperty(0.0)
    bev_ibu = NumericProperty(0)
    bev_srm = NumericProperty(5)
    preview_color = ListProperty([1, 0.75, 0, 1])
    
    def on_bev_srm(self, instance, value):
        self.preview_color = get_srm_color_rgba(int(value))

class SettingsCalibrationTab(BoxLayout):
    """
    Logic for the Auto-Detect Calibration Workflow.
    """
    is_metric = BooleanProperty(True)
    locked_tap_index = NumericProperty(-1)
    current_pulses = NumericProperty(0)
    measured_volume = NumericProperty(0.0)
    calculated_k = StringProperty("----")
    # --- NEW PROPERTY ---
    current_k_display = StringProperty("----")
    
    instruction_text = StringProperty("Open any tap to begin calibration.")
    deduct_inventory = BooleanProperty(True)

    def on_kv_post(self, base_widget):
        self.init_ui()

    def on_parent(self, widget, parent):
        if parent:
            parent.bind(on_enter=self.on_tab_enter)
            parent.bind(on_leave=self.on_tab_leave)

    def on_tab_enter(self, *args):
        self.init_ui()
        app = App.get_running_app()
        if hasattr(app, 'sensor_logic') and app.sensor_logic:
            app.sensor_logic.start_auto_calibration_mode()

    def on_tab_leave(self, *args):
        app = App.get_running_app()
        if hasattr(app, 'sensor_logic') and app.sensor_logic:
            app.sensor_logic.stop_auto_calibration_mode()

    def init_ui(self):
        """Reset UI state and load preferences."""
        app = App.get_running_app()
        from kivy.metrics import dp
        
        # 1. Units
        units = app.settings_manager.get_display_units()
        self.is_metric = (units == "metric")
        
        # 2. Load Checkbox Preference
        self.deduct_inventory = app.settings_manager.get_calibration_deduct_inventory()

        # 3. Generate Tap Buttons Dynamically
        if 'tap_buttons_container' not in self.ids:
            return 

        container = self.ids.tap_buttons_container
        container.clear_widgets()
        num_taps = app.settings_manager.get_displayed_taps()
        
        self.tap_buttons = []
        for i in range(num_taps):
            from kivy.uix.button import Button
            btn = Button(
                text=f"TAP {i+1}", 
                font_size='18sp',
                bold=True,
                background_normal='',            
                background_down='',              
                background_color=(0.2, 0.2, 0.2, 1), # Dark Grey
                color=(0.5, 0.5, 0.5, 1),        # Dimmed Text
                size_hint_x=None,
                width=dp(150)
            )
            container.add_widget(btn)
            self.tap_buttons.append(btn)
            
        # 4. Set Slider defaults
        slider = self.ids.vol_slider
        if self.is_metric:
            slider.max = 1000
            slider.step = 10
            self.measured_volume = 500
        else:
            slider.max = 32
            slider.step = 0.1
            self.measured_volume = 16.0 

        # 5. Defaults
        self.reset_form()

    def update_pulse_data(self, tap_index, pulses):
        """Callback from SensorLogic."""
        # 1. Lock In Logic
        if self.locked_tap_index == -1:
            self.locked_tap_index = tap_index
            self.instruction_text = f"Tap {tap_index+1} Detected! Close tap when done, then adjust volume."
            self._update_tap_buttons(tap_index)
            
            # --- NEW: Fetch Current K-Factor for Comparison ---
            app = App.get_running_app()
            factors = app.settings_manager.get_flow_calibration_factors()
            if 0 <= tap_index < len(factors):
                self.current_k_display = f"{factors[tap_index]:.2f}"
            # --------------------------------------------------
            
        # 2. Update Data
        if self.locked_tap_index == tap_index:
            self.current_pulses = pulses
            self.recalculate_k()

    def _update_tap_buttons(self, active_index):
        for i, btn in enumerate(self.tap_buttons):
            if i == active_index:
                btn.background_color = (0.2, 0.6, 1, 1) # Blue
                btn.color = (1, 1, 1, 1)
            else:
                btn.background_color = (0.2, 0.2, 0.2, 1) # Dark Grey
                btn.color = (0.5, 0.5, 0.5, 1)

    def adjust_volume(self, delta):
        slider = self.ids.vol_slider
        new_val = slider.value + delta
        new_val = max(slider.min, min(slider.max, new_val))
        slider.value = new_val

    def on_measured_volume(self, instance, value):
        self.recalculate_k()

    def recalculate_k(self):
        if self.measured_volume <= 0 or self.current_pulses <= 0:
            self.calculated_k = "----"
            return
            
        vol_liters = 0.0
        if self.is_metric:
            vol_liters = self.measured_volume / 1000.0
        else:
            vol_liters = self.measured_volume * 0.0295735
            
        if vol_liters > 0:
            k = self.current_pulses / vol_liters
            self.calculated_k = f"{k:.2f}"

    def save_calibration(self):
        if self.locked_tap_index == -1: return
        try:
            new_k = float(self.calculated_k)
        except ValueError:
            return

        app = App.get_running_app()
        
        # 1. Save K-Factor
        factors = app.settings_manager.get_flow_calibration_factors()
        factors[self.locked_tap_index] = new_k
        app.settings_manager.save_flow_calibration_factors(factors)
        
        # 2. Save Checkbox Pref
        app.settings_manager.save_calibration_deduct_inventory(self.deduct_inventory)
        
        # 3. Deduct Inventory (Optional)
        if self.deduct_inventory:
            vol_liters = 0.0
            if self.is_metric: vol_liters = self.measured_volume / 1000.0
            else: vol_liters = self.measured_volume * 0.0295735
            if app.sensor_logic:
                app.sensor_logic.deduct_volume_from_keg(self.locked_tap_index, vol_liters)
                
        # 4. Feedback & Reset
        self.instruction_text = f"Saved! Tap {self.locked_tap_index+1} calibrated. Ready for next tap."
        self.reset_form_soft()

    def set_to_default_k_factor(self):
        """Restores default K-factor (5100) for the active tap."""
        if self.locked_tap_index == -1: return
        from sensor_logic import DEFAULT_K_FACTOR
        
        app = App.get_running_app()
        factors = app.settings_manager.get_flow_calibration_factors()
        factors[self.locked_tap_index] = DEFAULT_K_FACTOR
        app.settings_manager.save_flow_calibration_factors(factors)
        
        self.instruction_text = f"Tap {self.locked_tap_index+1} reset to default K-Factor ({DEFAULT_K_FACTOR})."
        self.reset_form_soft()

    def reset_calibration(self):
        """User clicked Reset/Cancel."""
        self.instruction_text = "Calibration cancelled. Ready for next tap."
        self.reset_form_soft()

    def reset_form_soft(self):
        """Resets the transient calibration data, keeps the mode open."""
        app = App.get_running_app()
        if hasattr(app, 'sensor_logic') and app.sensor_logic:
            app.sensor_logic.reset_auto_calibration_state()
            
        self.locked_tap_index = -1
        self.current_pulses = 0
        self.calculated_k = "----"
        # --- NEW: Reset Display ---
        self.current_k_display = "----"
        
        # Reset Buttons
        if hasattr(self, 'tap_buttons'):
            for btn in self.tap_buttons:
                btn.background_color = (0.2, 0.2, 0.2, 1)
                btn.color = (0.5, 0.5, 0.5, 1)

    def reset_form(self):
        """Full reset including volume defaults."""
        self.reset_form_soft()
        self.instruction_text = "Open any tap to begin calibration."

class SettingsAlertsTab(BoxLayout):
    """Logic for the Alerts / Notifications settings tab."""

    # Sentinel values must match the constants in notification_manager.py
    VOLUME_OFF    = 0.0
    LOW_TEMP_OFF  = 27.0
    HIGH_TEMP_OFF = 61.0

    def init_ui(self):
        """Load current settings into all widgets."""
        app  = App.get_running_app()
        app._suppress_dirty = True
        try:
            push = app.settings_manager.get_push_notification_settings()
            cond = app.settings_manager.get_conditional_notification_settings()

            # Push notification fields
            self.ids.spin_frequency.text      = push.get("frequency", "None")
            self.ids.txt_smtp_server.text     = str(push.get("smtp_server", ""))
            port_val = push.get("smtp_port", "")
            self.ids.txt_smtp_port.text       = str(port_val) if port_val else ""
            self.ids.txt_server_email.text    = str(push.get("server_email", ""))
            self.ids.txt_server_password.text = str(push.get("server_password", ""))
            self.ids.txt_email_recipient.text = str(push.get("email_recipient", ""))

            # Conditional sliders — clamp to valid slider ranges on load
            vol = float(cond.get("threshold_liters", self.VOLUME_OFF))
            self.ids.slider_volume.value = max(0.0, min(5.0, vol))
            self.on_volume_slider(self.ids.slider_volume.value)

            low_t = float(cond.get("low_temp_f", self.LOW_TEMP_OFF))
            self.ids.slider_low_temp.value = max(27.0, min(45.0, low_t))
            self.on_low_temp_slider(self.ids.slider_low_temp.value)

            high_t = float(cond.get("high_temp_f", self.HIGH_TEMP_OFF))
            self.ids.slider_high_temp.value = max(35.0, min(61.0, high_t))
            self.on_high_temp_slider(self.ids.slider_high_temp.value)
        finally:
            app._suppress_dirty = False

    # --- Slider label callbacks (called from KV on_value) ---

    def on_volume_slider(self, value):
        lbl = self.ids.get("lbl_volume")
        if lbl:
            lbl.text = "OFF" if value <= self.VOLUME_OFF else f"{value:.2f} L"
        App.get_running_app().mark_settings_dirty()

    def on_low_temp_slider(self, value):
        lbl = self.ids.get("lbl_low_temp")
        if lbl:
            lbl.text = "OFF" if value <= self.LOW_TEMP_OFF else f"{int(value)}°F"
        App.get_running_app().mark_settings_dirty()

    def on_high_temp_slider(self, value):
        lbl = self.ids.get("lbl_high_temp")
        if lbl:
            lbl.text = "OFF" if value >= self.HIGH_TEMP_OFF else f"{int(value)}°F"
        App.get_running_app().mark_settings_dirty()

    # --- Save / Test ---

    def _save_to_backend(self):
        """Persists all alert settings without navigating away."""
        app = App.get_running_app()

        # Load existing dicts first to preserve fields we don't expose
        push = app.settings_manager.get_push_notification_settings()
        push["frequency"]         = self.ids.spin_frequency.text
        push["smtp_server"]       = self.ids.txt_smtp_server.text.strip()
        push["smtp_port"]         = self.ids.txt_smtp_port.text.strip()
        push["server_email"]      = self.ids.txt_server_email.text.strip()
        push["server_password"]   = self.ids.txt_server_password.text.strip()
        push["email_recipient"]   = self.ids.txt_email_recipient.text.strip()
        push["notification_type"] = "Email"
        app.settings_manager.save_push_notification_settings(push)

        cond = app.settings_manager.get_conditional_notification_settings()
        cond["threshold_liters"] = float(self.ids.slider_volume.value)
        cond["low_temp_f"]       = float(self.ids.slider_low_temp.value)
        cond["high_temp_f"]      = float(self.ids.slider_high_temp.value)
        app.settings_manager.save_conditional_notification_settings(cond)

        print("SettingsAlertsTab: Notification settings saved.")

        if hasattr(app, "notification_manager") and app.notification_manager:
            Clock.schedule_once(lambda dt: app.notification_manager.force_reschedule(), 0.1)

    def save_all_settings(self):
        """Save settings and remain on the tab."""
        self._save_to_backend()
        App.get_running_app().is_settings_dirty = False

    def test_send(self):
        """Save current UI values then fire an immediate test email."""
        self._save_to_backend()
        app = App.get_running_app()
        app.is_settings_dirty = False
        if hasattr(app, "notification_manager") and app.notification_manager:
            app.notification_manager.send_manual_status()


class DashboardScreen(Screen):
    kegerator_temp = StringProperty("--.- °F")
    
    # --- Existing 5-Click Logic (PRESERVED) ---
    _click_count = 0
    _reset_event = None

    def on_temp_area_click(self):
        """Hidden trigger: 5 rapid clicks toggles simulation mode."""
        self._click_count += 1
        if self._reset_event: self._reset_event.cancel()
        self._reset_event = Clock.schedule_once(self._reset_clicks, 1.0)
        
        if self._click_count >= 5:
            self._reset_clicks(0)
            
            # Toggle based on current state
            if self.ids.footer_manager.current == 'sim_mode':
                self.toggle_sim_footer(False)
            else:
                self.toggle_sim_footer(True)

    def _reset_clicks(self, dt):
        self._click_count = 0

    # --- Updated Footer Logic (FIXED: No Height Change) ---
    def toggle_sim_footer(self, show_sim):
        """Switches footer mode without changing height."""
        sm = self.ids.footer_manager
        
        if show_sim:
            # 1. Slide Up to Sim Mode
            sm.transition.direction = 'up'
            sm.current = 'sim_mode'
            
            # Populate controls
            self._populate_sim_controls()
        else:
            # 1. Slide Down to Nav Mode
            sm.transition.direction = 'down'
            sm.current = 'nav_mode'

    def _populate_sim_controls(self):
        """Dynamically adds SimControlWidgets to match the active taps."""
        app = App.get_running_app()
        container = self.ids.sim_container
        container.clear_widgets()
        
        from kivy.factory import Factory
        # Use get_displayed_taps() to ensure we match the visible configuration
        num_taps = app.settings_manager.get_displayed_taps()
        
        for i in range(num_taps):
            widget = Factory.SimControlWidget()
            widget.tap_index = i
            container.add_widget(widget)
            
# --- 4. MAIN APP CLASS ---

class KegLevelApp(App):
    simulated_temp    = None   # None = use sensor, float (°C) = override
    current_temp_f    = None   # Always °F; read by NotificationManager
    _sim_flow_event   = None
    _active_sim_taps  = set()
    is_settings_dirty = BooleanProperty(False)
    _suppress_dirty   = False
    version           = StringProperty(APP_VERSION)

    # ------------------------------------------------------------------
    # Dirty-settings helpers
    # ------------------------------------------------------------------

    def mark_settings_dirty(self):
        """Flag that the active settings tab has unsaved changes."""
        if not self._suppress_dirty:
            self.is_settings_dirty = True

    def attempt_exit_settings(self):
        """Exit settings; show a dirty popup if there are unsaved changes."""
        if self.is_settings_dirty:
            from kivy.factory import Factory
            Clock.schedule_once(lambda dt: Factory.DirtySettingsPopup().open(), 0)
        else:
            self.navigate_to('dashboard')

    def discard_settings(self):
        """Discard unsaved changes and return to the dashboard."""
        self.is_settings_dirty = False
        self.navigate_to('dashboard')

    def save_and_exit_settings(self):
        """Save the currently active settings tab, then return to the dashboard."""
        try:
            settings_screen = self.root.get_screen('settings')
            current_tab = settings_screen.ids.settings_manager.current
            if current_tab == 'tab_conf':
                settings_screen.ids.tab_conf_content.save_config()
            elif current_tab == 'tab_alerts':
                settings_screen.ids.tab_alerts_content._save_to_backend()
        except Exception as e:
            print(f"[Settings] save_and_exit error: {e}")
        self.is_settings_dirty = False
        self.navigate_to('dashboard')

    def build(self):
        self.title = "KegLevel Lite"
        Builder.load_file('keglevel_ui.kv')
        
        self.sm = ScreenManager(transition=SlideTransition())
        
        # Create a temporary blank screen to satisfy Kivy while we load.
        # The Splash Screen (Tkinter) is "always on top", so it will hide this.
        self.temp_screen = Screen(name='temp_loading')
        self.sm.add_widget(self.temp_screen)
        
        return self.sm

    def navigate_to(self, target_screen):
        """
        Navigates top-level screens with consistent horizontal direction.
        """
        if not self.root:
            return

        screen_depth = {
            'dashboard': 0,
            'inventory': 1,
            'settings': 1,
            'keg_edit': 2,
            'bev_edit': 2
        }

        current_screen = self.root.current
        current_depth = screen_depth.get(current_screen)
        target_depth = screen_depth.get(target_screen)

        if current_depth is not None and target_depth is not None:
            if target_depth > current_depth:
                self.root.transition.direction = 'left'
            elif target_depth < current_depth:
                self.root.transition.direction = 'right'

        self.root.current = target_screen

    def on_start(self):
        # Schedule initialization.
        # Note: We DO NOT kill the splash screen here anymore.
        Clock.schedule_once(self.finalize_startup, 0.1)

    def finalize_startup(self, dt):
        """
        Performs heavy initialization in the correct order.
        """
        # 1. Initialize Data Manager
        self.settings_manager = SettingsManager(len(FLOW_SENSOR_PINS))
        self.num_sensors = self.settings_manager.get_displayed_taps()
        
        # --- NEW: Restore Window Position AND Size ---
        win_cfg = self.settings_manager.get_app_window_settings()
        
        # Restore Position
        if win_cfg['x'] != -1 and win_cfg['y'] != -1:
            Window.left = win_cfg['x']
            Window.top = win_cfg['y']
            
        # Restore Size (Enforce strict minimums)
        safe_width = max(win_cfg['width'], 800)
        safe_height = max(win_cfg['height'], 418)
        Window.size = (safe_width, safe_height)
        # ---------------------------------------------
        
        # 2. Instantiate Screens
        self.dashboard_screen = DashboardScreen(name='dashboard')
        self.inventory_screen = InventoryScreen(name='inventory')
        self.keg_edit_screen = KegEditScreen(name='keg_edit')
        self.bev_edit_screen = BeverageEditScreen(name='bev_edit')
        self.settings_screen = SettingsScreen(name='settings')
        
        # 3. Add Screens to Manager
        self.sm.add_widget(self.dashboard_screen)
        self.sm.add_widget(self.inventory_screen)
        self.sm.add_widget(self.keg_edit_screen)
        self.sm.add_widget(self.bev_edit_screen)
        self.sm.add_widget(self.settings_screen)
        
        # 4. Populate Dashboard Widgets
        self.tap_widgets = []
        tap_container = self.dashboard_screen.ids.tap_container
        tap_container.clear_widgets()
        for i in range(self.num_sensors):
            widget = TapWidget()
            widget.tap_index = i
            tap_container.add_widget(widget)
            self.tap_widgets.append(widget)
            
        # 5. Define Logic Callbacks
        def bridge_callback(idx, rate, rem, status, pour_vol):
            Clock.schedule_once(lambda dt: self.update_tap_ui(idx, rate, rem, status, pour_vol))
        
        def cal_bridge_callback(idx, pulses):
             cal_tab = self.settings_screen.ids.get('tab_cal_content') 
             if cal_tab:
                 Clock.schedule_once(lambda dt: cal_tab.update_pulse_data(idx, pulses))

        callbacks = {
            "update_sensor_data_cb": bridge_callback,
            "update_cal_data_cb": lambda x, y: None, 
            "auto_cal_pulse_cb": cal_bridge_callback 
        }

        # 6. Initialize Sensor Logic
        self.sensor_logic = SensorLogic(self.num_sensors, callbacks, self.settings_manager)
        
        # 7. Refresh UI & Start Hardware
        self.refresh_dashboard_metadata()
        self.refresh_keg_list()
        self.refresh_beverage_list()
        self.sensor_logic.start_monitoring()
        self.init_temp_sensor()

        # 8. Initialize Notification Manager
        self.notification_manager = NotificationManager(
            self.settings_manager,
            get_temp_f_cb=lambda: self.current_temp_f,
        )
        self.notification_manager.start_scheduler()

        # 9. Switch to Dashboard
        # The Dashboard is now "Active" logically, but not yet rendered.
        self.sm.current = 'dashboard'

        # 10. Schedule Splash Dismissal (DELAYED)
        # We wait 0.5 seconds to allow the Main Thread to finish this function,
        # return to the Kivy Loop, and render the Dashboard frame *under* the splash window.
        Clock.schedule_once(self.dismiss_splash, 0.5)

    def dismiss_splash(self, dt):
        """
        Kills the splash screen after the UI is fully rendered.
        """
        if hasattr(self, 'splash_queue'):
            self.splash_queue.put("STOP")
            
        # Remove the temp screen to free up memory
        if hasattr(self, 'temp_screen') and self.temp_screen in self.sm.screens:
            self.sm.remove_widget(self.temp_screen)

    def init_temp_sensor(self):
        """Finds 1-wire temp sensor and starts update loop. Non-Pi platforms use a default temp."""
        if sys.platform in ("win32", "darwin"):
            # No DS18B20 on Windows or macOS - use default temp
            self.temp_device_file = None
            Clock.schedule_interval(self.update_kegerator_temp, 5.0)
            self.update_kegerator_temp(0)
            return

        base_dir = '/sys/bus/w1/devices/'
        device_folder = glob.glob(base_dir + '28*')

        if device_folder:
            self.temp_device_file = device_folder[0] + '/w1_slave'
            # Update every 5 seconds
            Clock.schedule_interval(self.update_kegerator_temp, 5.0)
            # Run once immediately
            self.update_kegerator_temp(0)
        else:
            print("No 1-wire sensor found.")
            # Explicitly set to blank if no sensor found at startup
            if self.dashboard_screen:
                self.dashboard_screen.kegerator_temp = ""

    def update_kegerator_temp(self, dt):
        """Updates the temp display, preferring Simulation value if set, else reading Hardware."""

        # 1. Check Simulation Override
        if self.simulated_temp is not None:
            t_f = self.simulated_temp * 9.0 / 5.0 + 32.0
            self.current_temp_f = t_f
            units = self.settings_manager.get_display_units()
            if units == 'imperial':
                self.dashboard_screen.kegerator_temp = f"{t_f:.1f} °F (Sim)"
            else:
                self.dashboard_screen.kegerator_temp = f"{self.simulated_temp:.1f} °C (Sim)"
            return

        # 2. Non-Pi platforms (Windows and macOS) — no DS18B20
        if sys.platform in ("win32", "darwin"):
            self.current_temp_f = 68.0
            units = self.settings_manager.get_display_units()
            if units == 'imperial':
                self.dashboard_screen.kegerator_temp = "68.0 °F"
            else:
                self.dashboard_screen.kegerator_temp = "20.0 °C"
            return

        # 3. Hardware Sensor Logic
        try:
            # If no device file detected during startup or it vanished, blank it
            if not hasattr(self, 'temp_device_file') or self.temp_device_file is None or not os.path.exists(self.temp_device_file):
                self.current_temp_f = None
                self.dashboard_screen.kegerator_temp = ""
                return

            with open(self.temp_device_file, 'r') as f:
                lines = f.readlines()

            # Check CRC and parse
            if len(lines) > 0 and lines[0].strip()[-3:] == 'YES':
                equals_pos = lines[1].find('t=')
                if equals_pos != -1:
                    temp_string = lines[1][equals_pos+2:]
                    temp_c = float(temp_string) / 1000.0
                    temp_f = temp_c * 9.0 / 5.0 + 32.0
                    self.current_temp_f = temp_f

                    units = self.settings_manager.get_display_units()
                    if units == 'imperial':
                        self.dashboard_screen.kegerator_temp = f"{temp_f:.1f} °F"
                    else:
                        self.dashboard_screen.kegerator_temp = f"{temp_c:.1f} °C"
        except Exception:
            # On read error, default to blank
            self.current_temp_f = None
            self.dashboard_screen.kegerator_temp = ""


    # --- NEW: Simulation Methods ---
    def open_simulation_dashboard(self):
        """Switches the Dashboard footer to Simulation Mode."""
        # Ensure we are on the dashboard screen
        self.navigate_to('dashboard')
        self.dashboard_screen.toggle_sim_footer(True)
        self.simulated_temp = 4.0 # Default start temp
        self.update_kegerator_temp(0)

    def close_simulation_dashboard(self):
        """Stops flows and returns footer to normal."""
        self.stop_all_simulations()
        self.simulated_temp = None
        self.update_kegerator_temp(0)
        self.dashboard_screen.toggle_sim_footer(False)
        
        
    def sim_pour_volume(self, tap_index, volume_liters):
        """Instantly inject pulses to simulate a poured volume."""
        k = self.settings_manager.get_flow_calibration_factors()[tap_index]
        pulses = int(volume_liters * k)
        if self.sensor_logic:
            self.sensor_logic.simulate_pulse_increment(tap_index, pulses)

    def sim_toggle_flow(self, tap_index, is_flowing):
        """Add/Remove tap from continuous flow loop."""
        if is_flowing:
            self._active_sim_taps.add(tap_index)
            if not self._sim_flow_event:
                # Start the loop (20Hz)
                self._sim_flow_event = Clock.schedule_interval(self._sim_flow_loop, 0.05)
        else:
            self._active_sim_taps.discard(tap_index)
            if not self._active_sim_taps and self._sim_flow_event:
                self._sim_flow_event.cancel()
                self._sim_flow_event = None

    def _sim_flow_loop(self, dt):
        """Inject small pulses periodically to simulate flow rate."""
        # Simulate ~3 L/min (50ml per second -> 2.5ml per 0.05s tick)
        # pulses = 2.5ml * k-factor (approx 5 pulses per tick)
        for tap_idx in self._active_sim_taps:
            k = self.settings_manager.get_flow_calibration_factors()[tap_idx]
            # Calculate pulses for 0.05s at 3L/min
            # 3 L/min = 0.05 L/sec = 0.0025 L/tick
            pulses = int(0.0025 * k)
            if self.sensor_logic:
                self.sensor_logic.simulate_pulse_increment(tap_idx, max(1, pulses))
            
        # --- NEW METHOD: Stop all flows ---
    def stop_all_simulations(self):
        """Stops the continuous flow loop and clears active taps."""
        if self._sim_flow_event:
            self._sim_flow_event.cancel()
            self._sim_flow_event = None
        self._active_sim_taps.clear()
        print("Simulation: All flows stopped.")

    
    def update_tap_ui(self, idx, rate, rem, status, pour_vol):
        if idx >= len(self.tap_widgets): return
        widget = self.tap_widgets[idx]
        keg_id = self.sensor_logic.keg_ids_assigned[idx]
        is_offline = (not keg_id) or (keg_id == UNASSIGNED_KEG_ID)
        
        if is_offline:
            widget.status_text = "OFFLINE"
            widget.remaining_text = "--"
            widget.percent_full = 0
            return
            
        units = self.settings_manager.get_display_units()
        if units == "metric": widget.remaining_text = f"{rem:.2f} L"
        else: widget.remaining_text = f"{(rem * LITERS_TO_GAL):.2f} Gal"
        
        if rate > 0: widget.status_text = "Pouring"
        else: widget.status_text = "Idle"

        keg = self.settings_manager.get_keg_by_id(keg_id)
        max_vol = keg.get('maximum_full_volume_liters', 19.0) if keg else 19.0
        if max_vol <= 0: max_vol = 19.0
        percent = (rem / max_vol) * 100.0
        widget.percent_full = max(0, min(100, percent))

    def refresh_dashboard_metadata(self):
        assignments = self.settings_manager.get_sensor_keg_assignments()
        bev_assigns = self.settings_manager.get_sensor_beverage_assignments()
        bev_lib = self.settings_manager.get_beverage_library().get('beverages', [])
        
        for i, widget in enumerate(self.tap_widgets):
            widget.tap_title = f"Tap {i+1}"
            k_id = assignments[i] if i < len(assignments) else None
            
            if not k_id or k_id == UNASSIGNED_KEG_ID:
                widget.beverage_name = "No Keg"
                widget.stats_text = ""
                widget.liquid_color = (0.2, 0.2, 0.2, 1) # Dark grey for empty
            else:
                found_keg = self.settings_manager.get_keg_by_id(k_id)
                b_id = bev_assigns[i] if i < len(bev_assigns) else None
                found_bev = next((b for b in bev_lib if b['id'] == b_id), None)
                
                if found_bev:
                    widget.beverage_name = found_bev['name']
                    abv = found_bev.get('abv', '?')
                    ibu = found_bev.get('ibu', '?')
                    widget.stats_text = f"{abv}% ABV  •  {ibu} IBU"
                    
                    srm = found_bev.get('srm')
                    try: srm = int(srm)
                    except: srm = 5
                    widget.liquid_color = get_srm_color_rgba(srm)
                else:
                    widget.beverage_name = "Empty"
                    widget.stats_text = ""
                    widget.liquid_color = (1, 0.75, 0, 1)

    def refresh_keg_list(self):
        kegs = self.settings_manager.get_keg_definitions()
        
        # Sort kegs by title to ensure logical display (Keg 01, Keg 02...)
        kegs.sort(key=lambda k: k.get('title', ''))

        bev_lib = self.settings_manager.get_beverage_library().get('beverages', [])
        bev_map = {b['id']: b['name'] for b in bev_lib}
        data_list = []
        for i, keg in enumerate(kegs):
            b_id = keg.get('beverage_id')
            b_name = bev_map.get(b_id, "Empty")
            data_list.append({
                'title': keg.get('title', 'Unknown'),
                'contents': b_name,
                'keg_id': keg.get('id'),
                'index': i
            })
        self.inventory_screen.ids.kegs_tab.ids.rv_kegs.data = data_list

    def refresh_beverage_list(self):
        bevs = self.settings_manager.get_beverage_library().get('beverages', [])
        bevs = sorted(bevs, key=lambda x: x.get('name', '').lower())
        data_list = []
        for i, b in enumerate(bevs):
            data_list.append({
                'name': b.get('name', 'Unknown'),
                'bev_id': b.get('id'),
                'index': i
            })
        self.inventory_screen.ids.bevs_tab.ids.rv_bevs.data = data_list

    def open_tap_selector(self, tap_index):
        popup = KegSelectPopup(title=f"Select Keg for Tap {tap_index+1}")
        all_kegs = self.settings_manager.get_keg_definitions()
        
        # Sort kegs by title to ensure logical display (Keg 01, Keg 02...)
        all_kegs.sort(key=lambda k: k.get('title', ''))
        
        assignments = self.settings_manager.get_sensor_keg_assignments()
        assigned_set = set(assignments)
        
        data_list = []
        current_keg = assignments[tap_index]
        if current_keg and current_keg != UNASSIGNED_KEG_ID:
            data_list.append({
                'text': "[ ! ]  KEG KICKED (CALIBRATE)  [ ! ]",
                'background_color': (0.35, 0.35, 0.35, 1),
                'on_release': lambda: self.select_keg_for_tap(tap_index, KEG_KICKED_ID, popup)
            })
        data_list.append({
            'text': "[ Disconnect Tap ]",
            'background_color': (0.2, 0.2, 0.2, 1),
            'on_release': lambda: self.select_keg_for_tap(tap_index, UNASSIGNED_KEG_ID, popup)
        })
        for keg in all_kegs:
            k_id = keg['id']
            # Show keg if it's unassigned OR if it's currently assigned to THIS tap
            if (k_id not in assigned_set) or (assignments[tap_index] == k_id):
                b_id = keg.get('beverage_id')
                bev_lib = self.settings_manager.get_beverage_library().get('beverages', [])
                found_bev = next((b for b in bev_lib if b['id'] == b_id), None)
                b_name = found_bev['name'] if found_bev else "Empty"
                
                # --- FIX: Use calculated start volume, not max capacity ---
                start = keg.get('calculated_starting_volume_liters', 0.0)
                disp = keg.get('current_dispensed_liters', 0)
                
                # Calculate remaining (allowing negative for calibration visibility)
                rem = start - disp
                # ----------------------------------------------------------
                
                units = self.settings_manager.get_display_units()
                vol_str = f"{rem:.2f}L" if units == "metric" else f"{(rem * LITERS_TO_GAL):.2f}Gal"
                
                data_list.append({
                    'text': f"{keg['title']} ({b_name}) - {vol_str}",
                    'background_color': (0.2, 0.2, 0.2, 1),
                    'on_release': lambda x=k_id: self.select_keg_for_tap(tap_index, x, popup)
                })
        popup.ids.rv_select.data = data_list
        popup.open()

    def select_keg_for_tap(self, tap_index, keg_id, popup_instance):
        # --- INTERCEPT KEG KICKED ACTION ---
        if keg_id == KEG_KICKED_ID:
            # Prepare data for the Calibration Screen
            self.prepare_keg_kick_screen(tap_index, popup_instance)
            return
        # -----------------------------------

        popup_instance.dismiss()
        
        self.settings_manager.save_sensor_keg_assignment(tap_index, keg_id)
        if keg_id == UNASSIGNED_KEG_ID:
            self.settings_manager.save_sensor_beverage_assignment(tap_index, UNASSIGNED_BEVERAGE_ID)
        else:
            keg = self.settings_manager.get_keg_by_id(keg_id)
            b_id = keg.get('beverage_id', UNASSIGNED_BEVERAGE_ID)
            self.settings_manager.save_sensor_beverage_assignment(tap_index, b_id)
            
        self.sensor_logic.force_recalculation()
        self.refresh_dashboard_metadata()
        self.update_tap_ui(tap_index, 0, 0, "Idle", 0)
        
    def prepare_keg_kick_screen(self, tap_index, popup):
        """Calculates stats for the kicked keg and switches popup to calibrate view."""
        # 1. Identify the Keg currently on this tap
        assignments = self.settings_manager.get_sensor_keg_assignments()
        if tap_index >= len(assignments): return
        
        keg_id = assignments[tap_index]
        keg = self.settings_manager.get_keg_by_id(keg_id)
        
        if not keg or keg_id == UNASSIGNED_KEG_ID:
            print("Error: No keg assigned to this tap to calibrate.")
            popup.dismiss()
            return

        # 2. Gather Data
        start_vol = keg.get('calculated_starting_volume_liters', 0.0)
        total_pulses = keg.get('total_dispensed_pulses', 0)
        current_k = self.settings_manager.get_flow_calibration_factors()[tap_index]

        # 3. Calculate New K
        new_k = 0.0
        is_valid = False
        if start_vol > 0 and total_pulses > 0:
            new_k = total_pulses / start_vol
            is_valid = True

        # 4. Populate Popup Properties
        popup.tap_index = tap_index
        popup.cal_keg_id = keg_id
        popup.cal_keg_title = keg.get('title', 'Unknown')
        popup.cal_start_vol = f"{start_vol:.2f} L"
        popup.cal_total_pulses = str(int(total_pulses))
        popup.cal_old_k = f"{current_k:.2f}"
        popup.cal_new_k = f"{new_k:.2f}"
        popup.cal_is_valid = is_valid
        
        # Reset checkbox state
        popup.ids.chk_confirm.active = False
        popup.cal_confirmed = False

        # 5. Switch Screen
        popup.ids.sm.current = 'calibrate'

    def commit_keg_kick_calibration(self, popup):
        """Atomic Save: Updates K-Factor, Unassigns Tap, Resets Keg, Closes Popup."""
        try:
            new_k = float(popup.cal_new_k)
        except ValueError:
            return # Should be prevented by UI disabled state

        tap_index = popup.tap_index
        keg_id = popup.cal_keg_id
        
        print(f"Committing Calibration for Tap {tap_index+1}. New K: {new_k}")

        # 1. Update K-Factor for the Tap
        factors = self.settings_manager.get_flow_calibration_factors()
        factors[tap_index] = new_k
        self.settings_manager.save_flow_calibration_factors(factors)

        # 2. Unassign Keg from Tap
        self.settings_manager.save_sensor_keg_assignment(tap_index, UNASSIGNED_KEG_ID)
        self.settings_manager.save_sensor_beverage_assignment(tap_index, UNASSIGNED_BEVERAGE_ID)

        # 3. Reset Keg Data
        all_kegs = self.settings_manager.get_keg_definitions()
        for keg in all_kegs:
            if keg.get('id') == keg_id:
                # Clear contents and counters
                keg['beverage_id'] = UNASSIGNED_BEVERAGE_ID
                keg['fill_date'] = ""
                keg['current_dispensed_liters'] = 0.0
                keg['total_dispensed_pulses'] = 0
                
                # CRITICAL: Reset the physical weight to empty (Tare)
                # This ensures the "Starting Volume" becomes 0.0L
                tare = keg.get('tare_weight_kg', 0.0)
                keg['starting_total_weight_kg'] = tare
                keg['calculated_starting_volume_liters'] = 0.0
                break
                
        self.settings_manager.save_keg_definitions(all_kegs)

        # 4. Refresh System
        self.sensor_logic.force_recalculation()
        self.refresh_dashboard_metadata()
        self.update_tap_ui(tap_index, 0, 0, "Idle", 0)

        # 5. Close Popup
        popup.dismiss()

    # --- Actions: KEGS ---
    def open_keg_edit(self, keg_id):
        self.inventory_screen.show_kegs()
        bev_lib = self.settings_manager.get_beverage_library().get('beverages', [])
        bev_names = sorted([b['name'] for b in bev_lib])
        self.keg_edit_screen.beverage_list = ["Empty"] + bev_names
        
        # Check units to determine display values
        units = self.settings_manager.get_display_units()
        is_metric = (units == "metric")

        if keg_id:
            self.keg_edit_screen.screen_title = "Edit Keg"
            self.keg_edit_screen.keg_id = keg_id
            keg = self.settings_manager.get_keg_by_id(keg_id)
            b_id = keg.get('beverage_id')
            found_bev = next((b for b in bev_lib if b['id'] == b_id), None)
            self.keg_edit_screen.beverage_name = found_bev['name'] if found_bev else "Empty"
            
            # Load from DB (Always Metric)
            raw_vol = float(keg.get('maximum_full_volume_liters', 19.0))
            raw_tare = float(keg.get('tare_weight_kg', 4.0))
            raw_total = float(keg.get('starting_total_weight_kg', 4.0))

            # Convert to Display Units if needed
            if is_metric:
                self.keg_edit_screen.max_volume_liters = raw_vol
                self.keg_edit_screen.tare_weight_kg = raw_tare
                self.keg_edit_screen.total_weight_kg = raw_total
            else:
                self.keg_edit_screen.max_volume_liters = raw_vol * LITERS_TO_GAL
                self.keg_edit_screen.tare_weight_kg = raw_tare * KG_TO_LBS
                self.keg_edit_screen.total_weight_kg = raw_total * KG_TO_LBS
        else:
            self.keg_edit_screen.screen_title = "Add New Keg"
            self.keg_edit_screen.keg_id = "" 
            self.keg_edit_screen.beverage_name = "Empty"
            
            # UPDATED: Set Defaults based on Units
            if is_metric:
                # Metric Defaults: 19.0 L, 4.0 kg Tare, 4.0 kg Total
                self.keg_edit_screen.max_volume_liters = 19.0
                self.keg_edit_screen.tare_weight_kg = 4.0
                self.keg_edit_screen.total_weight_kg = 4.0
            else:
                # Imperial Defaults: 5.0 Gal, 8.8 lb Tare, 8.8 lb Total
                self.keg_edit_screen.max_volume_liters = 5.0
                self.keg_edit_screen.tare_weight_kg = 8.8
                self.keg_edit_screen.total_weight_kg = 8.8

        self.keg_edit_screen.update_display_labels()
        self.navigate_to('keg_edit')

    def request_delete_keg(self, keg_id):
        keg = self.settings_manager.get_keg_by_id(keg_id)
        title = keg.get('title', 'Unknown Keg') if keg else "Unknown Keg"
        
        popup = ConfirmPopup(title="Confirm Deletion")
        popup.text = f"Permanently delete {title}?\nTap assignment will be cleared."
        popup.action_callback = lambda: self.perform_delete_keg(keg_id)
        popup.open()

    def perform_delete_keg(self, keg_id):
        self.settings_manager.delete_keg_definition(keg_id)
        self.refresh_keg_list()
        self.refresh_dashboard_metadata()
        self.sensor_logic.force_recalculation()

    def add_new_keg(self): self.open_keg_edit(None)

    # --- Actions: BEVERAGES ---
    def open_beverage_edit(self, bev_id):
        self.inventory_screen.show_bevs()
        
        # --- NEW: Load BJCP Styles ---
        try:
            styles = self.settings_manager.load_bjcp_styles()
            # Format as "Code Name" (e.g., "18B American Pale Ale")
            style_list = [f"{s.get('code', '?')} {s.get('name', '?')}" for s in styles]
            self.bev_edit_screen.bjcp_list = style_list
        except Exception as e:
            print(f"Error loading BJCP styles: {e}")
            self.bev_edit_screen.bjcp_list = []
        # -----------------------------

        if bev_id:
            self.bev_edit_screen.screen_title = "Edit Beverage"
            self.bev_edit_screen.bev_id = bev_id
            lib = self.settings_manager.get_beverage_library().get('beverages', [])
            found = next((b for b in lib if b['id'] == bev_id), None)
            if found:
                self.bev_edit_screen.bev_name = found.get('name', '')
                
                # --- NEW: Load BJCP selection ---
                self.bev_edit_screen.bev_bjcp = found.get('bjcp', '')
                # --------------------------------
                
                # UPDATED: Load ABV (Handle "" as 0.0 for slider)
                raw_abv = found.get('abv')
                try: 
                    self.bev_edit_screen.bev_abv = float(raw_abv)
                except (ValueError, TypeError): 
                    self.bev_edit_screen.bev_abv = 0.0

                # UPDATED: Load IBU (Handle "" as 0 for slider)
                raw_ibu = found.get('ibu')
                try: 
                    self.bev_edit_screen.bev_ibu = int(raw_ibu)
                except (ValueError, TypeError): 
                    self.bev_edit_screen.bev_ibu = 0
                
                srm_val = found.get('srm')
                try: self.bev_edit_screen.bev_srm = int(srm_val)
                except: self.bev_edit_screen.bev_srm = 5
        else:
            self.bev_edit_screen.screen_title = "Add New Beverage"
            self.bev_edit_screen.bev_id = ""
            self.bev_edit_screen.bev_name = ""
            self.bev_edit_screen.bev_bjcp = "" # Reset
            
            # UPDATED: Defaults for new beverage
            self.bev_edit_screen.bev_abv = 0.0
            self.bev_edit_screen.bev_ibu = 0
            self.bev_edit_screen.bev_srm = 5
        self.navigate_to('bev_edit')

    def save_beverage_edit(self):
        scr = self.bev_edit_screen
        
        # UPDATED: Logic to convert 0 to "" as requested
        final_ibu = int(scr.bev_ibu) if scr.bev_ibu > 0 else ""
        
        # FIX: Round to 1 decimal place to prevent slider float artifacts (e.g. 5.19999)
        rounded_abv = round(scr.bev_abv, 1)
        final_abv = rounded_abv if rounded_abv > 0 else ""
        
        is_new = (scr.bev_id == "")
        new_id = scr.bev_id if not is_new else str(uuid.uuid4())
        
        lib_container = self.settings_manager.get_beverage_library()
        lib = lib_container.get('beverages', [])
        
        # --- DATA PRESERVATION START ---
        # Find existing record to preserve extra fields (e.g. from Monitor)
        existing_record = next((b for b in lib if b['id'] == new_id), None)
        
        if existing_record and not is_new:
            # Copy existing data to preserve hidden fields
            new_data = existing_record.copy()
        else:
            new_data = {
                'id': new_id,
                'description': ''
            }
        # --- DATA PRESERVATION END ---

        # Update with Form Data
        new_data.update({
            'name': scr.bev_name,
            'bjcp': scr.bev_bjcp, # --- NEW: Save BJCP ---
            'abv': final_abv, # Saves as float or ""
            'ibu': final_ibu, # Saves as int or ""
            'srm': int(scr.bev_srm)
        })

        # Save back to list
        if is_new: 
            lib.append(new_data)
        else:
            for i, b in enumerate(lib):
                if b['id'] == new_id: 
                    lib[i] = new_data; 
                    break
        
        self.settings_manager.save_beverage_library(lib)
        self.refresh_beverage_list()
        self.refresh_dashboard_metadata()
        self.navigate_to('inventory')
    
    def request_delete_beverage(self, bev_id):
        lib = self.settings_manager.get_beverage_library().get('beverages', [])
        found = next((b for b in lib if b['id'] == bev_id), None)
        name = found.get('name', 'Unknown') if found else "Unknown Beverage"
        
        popup = ConfirmPopup(title="Confirm Deletion")
        popup.text = f"Permanently delete{name}?\nKeg and Tap assignments will be cleared."
        popup.action_callback = lambda: self.perform_delete_beverage(bev_id)
        popup.open()

    def perform_delete_beverage(self, bev_id):
        # 1. Remove Beverage from Library
        lib = self.settings_manager.get_beverage_library().get('beverages', [])
        new_lib = [b for b in lib if b['id'] != bev_id]
        self.settings_manager.save_beverage_library(new_lib)
        
        # 2. Determine Defaults based on current Unit Settings
        units = self.settings_manager.get_display_units()
        is_metric = (units == "metric")
        
        # Constants from top of file: LITERS_TO_GAL = 0.264172, KG_TO_LBS = 2.20462
        if is_metric:
            def_vol = 19.0
            def_tare = 4.0
            def_total = 4.0
        else:
            # Calculate Metric equivalents for 5.0 Gal / 8.8 lb
            def_vol = 5.0 / LITERS_TO_GAL      # ~18.93 L
            def_tare = 8.8 / KG_TO_LBS         # ~3.99 kg
            def_total = 8.8 / KG_TO_LBS

        # 3. Reset Kegs containing this beverage
        kegs = self.settings_manager.get_keg_definitions()
        affected_keg_ids = set()
        
        for k in kegs:
            if k.get('beverage_id') == bev_id:
                affected_keg_ids.add(k.get('id'))
                
                # Reset to calculated defaults -> Empty
                k['beverage_id'] = UNASSIGNED_BEVERAGE_ID
                k['maximum_full_volume_liters'] = def_vol
                k['tare_weight_kg'] = def_tare
                k['starting_total_weight_kg'] = def_total
                k['calculated_starting_volume_liters'] = 0.0
                k['current_dispensed_liters'] = 0.0
                k['total_dispensed_pulses'] = 0
                k['fill_date'] = ""
                
        self.settings_manager.save_keg_definitions(kegs)
        
        # 4. Set Taps to Offline if they were assigned an affected Keg
        tap_keg_assigns = self.settings_manager.get_sensor_keg_assignments()
        tap_bev_assigns = self.settings_manager.get_sensor_beverage_assignments()
        
        for i in range(len(tap_keg_assigns)):
            k_id = tap_keg_assigns[i]
            b_id = tap_bev_assigns[i] if i < len(tap_bev_assigns) else None
            
            should_offline_tap = False
            
            # Rule: If tap assigned to a keg that had the deleted beverage -> Offline
            if k_id in affected_keg_ids:
                should_offline_tap = True
                self.settings_manager.save_sensor_keg_assignment(i, UNASSIGNED_KEG_ID)
            
            # Cleanup: If tap thinks it has this beverage (or we offlined it) -> Clear Bev
            if b_id == bev_id or should_offline_tap:
                self.settings_manager.save_sensor_beverage_assignment(i, UNASSIGNED_BEVERAGE_ID)

        # 5. Refresh System
        self.refresh_beverage_list()
        self.refresh_keg_list()
        self.refresh_dashboard_metadata()
        if hasattr(self, 'sensor_logic') and self.sensor_logic:
            self.sensor_logic.force_recalculation()

    def apply_config_changes(self):
        print("Applying Configuration Changes...")
        
        if hasattr(self, 'sensor_logic') and self.sensor_logic:
            self.sensor_logic.stop_monitoring()
            self.sensor_logic.cleanup_gpio()
            
        self.num_sensors = self.settings_manager.get_displayed_taps()
        
        tap_container = self.dashboard_screen.ids.tap_container
        tap_container.clear_widgets()
        self.tap_widgets = []
        
        for i in range(self.num_sensors):
            widget = TapWidget()
            widget.tap_index = i
            tap_container.add_widget(widget)
            self.tap_widgets.append(widget)
            
        def bridge_callback(idx, rate, rem, status, pour_vol):
            Clock.schedule_once(lambda dt: self.update_tap_ui(idx, rate, rem, status, pour_vol))
            
        callbacks = {
            "update_sensor_data_cb": bridge_callback,
            "update_cal_data_cb": lambda x, y: None 
        }

        self.sensor_logic = SensorLogic(
            num_sensors_from_config=self.num_sensors,
            ui_callbacks=callbacks,
            settings_manager=self.settings_manager
        )
        
        self.refresh_dashboard_metadata()
        self.sensor_logic.start_monitoring()

    def on_stop(self):
        if hasattr(self, 'settings_manager'):
            # Window.width/height are physical pixels on DPI-scaled displays (e.g. 125%).
            # Dividing by Metrics.density converts back to logical pixels before saving,
            # which prevents the window from growing larger on every close/reopen cycle.
            # On Linux/Pi, Metrics.density = 1.0 so this is a no-op there.
            dpi = Metrics.density if Metrics.density > 0 else 1.0
            safe_width  = max(int(round(Window.width  / dpi)), 800)
            safe_height = max(int(round(Window.height / dpi)), 418)

            self.settings_manager.save_app_window_settings(
                Window.left,
                Window.top,
                safe_width,
                safe_height
            )

        if hasattr(self, 'notification_manager') and self.notification_manager:
            self.notification_manager.stop_scheduler()

        if hasattr(self, 'sensor_logic') and self.sensor_logic:
            self.sensor_logic.cleanup_gpio()

def run_splash_screen(queue):
    """
    Runs a standalone Tkinter loading dialog in a separate process.
    This appears immediately, independent of Kivy's loading time.
    """
    import tkinter as tk
    
    try:
        root = tk.Tk()
        # Remove window decorations (frameless)
        root.overrideredirect(True)
        # Keep on top of the launching Kivy window
        root.attributes('-topmost', True)
        
        # Calculate center position
        width = 300
        height = 80
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)
        
        root.geometry(f'{width}x{height}+{x}+{y}')
        root.configure(bg='#222222')
        
        # Add a simple styled frame
        frame = tk.Frame(root, bg='#222222', highlightbackground='#FFC107', highlightthickness=2)
        frame.pack(fill='both', expand=True)
        
        # Add Text (UPDATED)
        lbl = tk.Label(frame, text="KegLevel Lite loading...", font=("Arial", 16, "bold"), fg="#FFC107", bg="#222222")
        lbl.pack(expand=True)
        
        # Force a draw immediately
        root.update()
        
        # Check for kill signal every 100ms
        def check_kill():
            if not queue.empty():
                root.destroy()
            else:
                root.after(100, check_kill)
                
        root.after(100, check_kill)
        root.mainloop()
    except Exception as e:
        print(f"Splash screen error: {e}")

if __name__ == '__main__':
    import multiprocessing

    # Splash screen is only used on Raspberry Pi (Linux).
    # On Windows the main UI loads before the splash anyway (not needed).
    # On macOS spawning a Tkinter subprocess is blocked by the OS (causes a crash).
    USE_SPLASH = sys.platform == "linux"

    if USE_SPLASH:
        splash_queue = multiprocessing.Queue()
        splash_process = multiprocessing.Process(target=run_splash_screen, args=(splash_queue,))
        splash_process.start()

    try:
        app = KegLevelApp()
        if USE_SPLASH:
            app.splash_queue = splash_queue
        app.run()

    except KeyboardInterrupt:
        if hasattr(app, 'sensor_logic') and app.sensor_logic:
            app.sensor_logic.cleanup_gpio()
        print("\nKegLevel Lite App interrupted by user.")

    finally:
        if USE_SPLASH and splash_process.is_alive():
            splash_process.terminate()
