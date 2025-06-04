import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import serial.tools.list_ports
from serial import Serial, SerialException
import threading
import queue
import platform
import io # For byte streams
from PIL import Image, ImageTk # For image handling
import time 
import re # For parsing memory slot data
import math 

# --- Tooltip Class ---
class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget; self.text = text; self.tooltip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)
        self.widget.bind("<ButtonPress>", self.hide_tip) 

    def show_tip(self, event=None):
        if not self.widget.winfo_exists(): return
        if self.tooltip_window: self.tooltip_window.destroy(); self.tooltip_window = None
        
        x = event.x_root + 15 
        y = event.y_root + 10 
        
        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.wm_geometry(f"+{x}+{y}")

        label = ttk.Label(self.tooltip_window, text=self.text, justify='left', 
                          relief='solid', borderwidth=1, padding=(2,2), background="#FFFFE0", foreground="#000000")
        label.pack(ipadx=1)

    def hide_tip(self, event=None):
        if self.tooltip_window: self.tooltip_window.destroy(); self.tooltip_window = None

# --- Serial Command Constants ---
CMD_VOLUME_UP = 'V'; CMD_VOLUME_DOWN = 'v'; CMD_BAND_NEXT = 'B'; CMD_BAND_PREV = 'b'
CMD_MODE_NEXT = 'M'; CMD_MODE_PREV = 'm'; CMD_STEP_NEXT = 'S'; CMD_STEP_PREV = 's'
CMD_BW_NEXT = 'W'; CMD_BW_PREV = 'w'; CMD_AGC_ATT_UP = 'A'; CMD_AGC_ATT_DOWN = 'a'
CMD_BL_UP = 'L'; CMD_BL_DOWN = 'l'; CMD_CAL_UP = 'I'; CMD_CAL_DOWN = 'i'
CMD_SLEEP_ON = 'O'; CMD_SLEEP_OFF = 'o'; CMD_TOGGLE_LOG = 't'; CMD_SCREENSHOT = 'C'
CMD_SHOW_MEM = '$'; CMD_SET_MEM_PREFIX = '#'
CMD_ENCODER_UP = 'R'; CMD_ENCODER_DOWN = 'r'; CMD_ENCODER_BTN = 'e'
CMD_THEME_EDITOR_TOGGLE = 'T'; CMD_THEME_GET = '@'; CMD_THEME_SET_SUFFIX = '!'


class RadioController:
    SCREENSHOT_DATA_INACTIVITY_TIMEOUT = 10.0 
    MEMORY_DATA_INACTIVITY_TIMEOUT = 1.2 
    THEME_DATA_INACTIVITY_TIMEOUT = 3.0 
    MEMORY_SLOT_PATTERN = re.compile(r"^#?\s*(\d{1,2})\s*,\s*([^,]*?)\s*,\s*(\d+)\s*,\s*([^,]*?)\s*$")
    DATA_LOG_PATTERN = re.compile(r"^\s*\d+\s*(?:,\s*[^,]*\s*){14}$")
    THEME_STRING_LINE_PATTERN = re.compile(r"^Color theme [^:]*:\s*((?:x[0-9a-fA-F]{4})+)$")


    def __init__(self):
        self.ser = None; self.running = False
        self.data_queue = queue.Queue(); self.data_received = False
        self.sleep_mode = False
        self.expecting_screenshot_data = False; self.screenshot_hex_buffer = ""; self.last_screenshot_hex_byte_time = 0 
        self.screenshot_request_time = 0 
        self.expecting_memory_slots = False; self.memory_slots_buffer = []; self.last_memory_slot_time = 0
        self.log_is_on_before_special_op = False 
        self.line_assembly_buffer_bytes = b"" 

        self.expecting_theme_string = False
        self.theme_string_buffer = ""
        self.last_theme_data_time = 0
        self.theme_get_sequence_active = False


    def connect(self, port, baudrate=115200):
        try:
            self.ser = Serial(port, int(baudrate), timeout=0.1) 
            self.running = True; self.data_received = False
            self.expecting_screenshot_data = False; self.screenshot_hex_buffer = ""; self.last_screenshot_hex_byte_time = 0
            self.screenshot_request_time = 0
            self.expecting_memory_slots = False; self.memory_slots_buffer = []; self.last_memory_slot_time = 0
            self.line_assembly_buffer_bytes = b"" 
            
            self.expecting_theme_string = False; self.theme_string_buffer = ""; 
            self.last_theme_data_time = 0; self.theme_get_sequence_active = False

            threading.Thread(target=self.read_serial, daemon=True).start()
            self.send_command(CMD_TOGGLE_LOG, is_user_toggle=True) 
            return True
        except ValueError: messagebox.showerror("Baud Rate Error", f"Invalid baud rate: {baudrate}."); return False
        except SerialException as e: messagebox.showerror("Connection Error", f"Failed to connect to {port} at {baudrate} baud: {str(e)}"); return False
        except Exception as e: messagebox.showerror("Error", f"An unexpected error during connection: {str(e)}"); return False

    def disconnect(self):
        self.running = False; time.sleep(0.05) 
        if self.ser and self.ser.is_open: self.ser.close(); print("Serial port closed by disconnect().")
        self.data_received = False; self.expecting_screenshot_data = False; self.expecting_memory_slots = False
        self.screenshot_hex_buffer = ""; self.memory_slots_buffer = []
        self.last_screenshot_hex_byte_time = 0; self.last_memory_slot_time = 0
        self.line_assembly_buffer_bytes = b""
        
        self.expecting_theme_string = False; self.theme_string_buffer = ""; 
        self.last_theme_data_time = 0; self.theme_get_sequence_active = False


    def _send_raw_command(self, cmd_char):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(cmd_char.encode() + b'\n')
            except Exception as e:
                print(f"Controller: Error sending raw command '{cmd_char}': {e}")

    def send_command(self, cmd, is_user_toggle=False):
        if not (self.ser and self.ser.is_open):
            if cmd in [CMD_SCREENSHOT, CMD_SHOW_MEM]: 
                messagebox.showwarning("Not Connected", "Connect to radio first.")
            return
        try:
            if cmd in [CMD_SCREENSHOT, CMD_SHOW_MEM]: 
                if self.log_is_on_before_special_op: 
                    self._send_raw_command(CMD_TOGGLE_LOG); time.sleep(0.05)
            
            if cmd == CMD_SCREENSHOT:
                self.expecting_screenshot_data = True 
                self.screenshot_hex_buffer = ""; self.last_screenshot_hex_byte_time = time.time() 
                self.screenshot_request_time = time.time() 
                self.ser.write(cmd.encode() + b'\n')
            elif cmd == CMD_SHOW_MEM:
                self.expecting_memory_slots = True
                self.memory_slots_buffer = []; self.last_memory_slot_time = time.time()
                self.ser.write(cmd.encode() + b'\n')
            else: 
                self.ser.write(cmd.encode() + b'\n') 

            if cmd == CMD_TOGGLE_LOG and is_user_toggle: 
                self.log_is_on_before_special_op = not self.log_is_on_before_special_op 
                print(f"Ctrl: Log toggled by user. Assumed radio log state: {'ON' if self.log_is_on_before_special_op else 'OFF'}")

        except Exception as e: 
            print(f"Ctrl: Error sending '{cmd}': {e}"); self.data_queue.put(('serial_error_disconnect', f"Send error: {e}"))

    def request_theme_data(self):
        if not (self.ser and self.ser.is_open):
            self.data_queue.put(('theme_data_error', "Not connected to radio."))
            return
        
        if self.log_is_on_before_special_op: 
            self._send_raw_command(CMD_TOGGLE_LOG) 
            time.sleep(0.05) 

        self._send_raw_command(CMD_THEME_EDITOR_TOGGLE) 
        time.sleep(0.05) 

        self.expecting_theme_string = True
        self.theme_string_buffer = ""
        self.last_theme_data_time = time.time()
        self.theme_get_sequence_active = True 

        self._send_raw_command(CMD_THEME_GET) 


    def _is_hex_string(self, s): return bool(s) and all(c in "0123456789abcdefABCDEF" for c in s)
    def _is_memory_slot_line(self, line): return bool(self.MEMORY_SLOT_PATTERN.match(line.strip()))

    def _finalize_special_op(self, operation_type):
        if operation_type == "Screenshot":
            self.expecting_screenshot_data = False; self.last_screenshot_hex_byte_time = 0
            if self.screenshot_hex_buffer: 
                transfer_duration = time.time() - self.screenshot_request_time
                self.data_queue.put(('screenshot_data', (self.screenshot_hex_buffer, transfer_duration) ))
            else: 
                self.data_queue.put(('screenshot_error', "No screenshot data received."))
            self.screenshot_hex_buffer = ""
        elif operation_type == "Memory":
            self.expecting_memory_slots = False; self.last_memory_slot_time = 0
            if self.memory_slots_buffer:  
                self.data_queue.put(('memory_slots_data', list(self.memory_slots_buffer)))
            else: 
                 self.data_queue.put(('memory_slots_error', "No memory slot data received."))
            self.memory_slots_buffer = []
        elif operation_type == "ThemeGet":
            self.expecting_theme_string = False
            self.last_theme_data_time = 0
            
            self._send_raw_command(CMD_THEME_EDITOR_TOGGLE) 
            time.sleep(0.05) 
            self.theme_get_sequence_active = False

            if self.theme_string_buffer:
                self.data_queue.put(('theme_data', self.theme_string_buffer))
            else:
                self.data_queue.put(('theme_data_error', "No theme string received or timeout." ))
            self.theme_string_buffer = ""
        
        if self.log_is_on_before_special_op and operation_type != "ThemeEditorToggle": 
            time.sleep(0.1); self._send_raw_command(CMD_TOGGLE_LOG);
        

    def read_serial(self):
        while self.running and self.ser and self.ser.is_open:
            try:
                new_bytes = self.ser.readline() 
                if new_bytes:
                    self.line_assembly_buffer_bytes += new_bytes
                elif not self.line_assembly_buffer_bytes: 
                    if self.expecting_screenshot_data and \
                       self.screenshot_hex_buffer and \
                       self.last_screenshot_hex_byte_time > 0 and \
                       (time.time() - self.last_screenshot_hex_byte_time > self.SCREENSHOT_DATA_INACTIVITY_TIMEOUT):
                        self._finalize_special_op("Screenshot")
                    elif self.expecting_memory_slots and self.memory_slots_buffer and \
                         self.last_memory_slot_time > 0 and \
                         (time.time() - self.last_memory_slot_time > self.MEMORY_DATA_INACTIVITY_TIMEOUT):
                        self._finalize_special_op("Memory")
                    elif self.expecting_theme_string and self.last_theme_data_time > 0 and \
                         (time.time() - self.last_theme_data_time > self.THEME_DATA_INACTIVITY_TIMEOUT):
                        self._finalize_special_op("ThemeGet")
                    time.sleep(0.01); continue

                while b'\n' in self.line_assembly_buffer_bytes:
                    complete_line_bytes, self.line_assembly_buffer_bytes = self.line_assembly_buffer_bytes.split(b'\n', 1)
                    line_str = ""
                    try:
                        line_str = complete_line_bytes.decode('ascii').strip()
                    except UnicodeDecodeError:
                        op_type_on_error = None
                        if self.expecting_screenshot_data: op_type_on_error = "Screenshot"
                        elif self.expecting_memory_slots: op_type_on_error = "Memory"
                        elif self.expecting_theme_string: op_type_on_error = "ThemeGet"
                        
                        if op_type_on_error:
                            print(f"Ctrl: UnicodeError during {op_type_on_error}.")
                            err_key = 'screenshot_error' if op_type_on_error == "Screenshot" else \
                                      'memory_slots_error' if op_type_on_error == "Memory" else 'theme_data_error'
                            current_buffer = self.screenshot_hex_buffer if op_type_on_error == "Screenshot" else \
                                             self.memory_slots_buffer if op_type_on_error == "Memory" else \
                                             self.theme_string_buffer
                            msg = f"UnicodeDecodeError at start of {op_type_on_error} data."
                            if current_buffer: msg = f"Unicode corruption after receiving some data for {op_type_on_error}."
                            self.data_queue.put((err_key, msg))

                            if op_type_on_error == "Screenshot": self.screenshot_hex_buffer = ""
                            elif op_type_on_error == "Memory": self.memory_slots_buffer = []
                            elif op_type_on_error == "ThemeGet": self.theme_string_buffer = "" 
                            self._finalize_special_op(op_type_on_error) 
                        else: 
                            try: line_str = complete_line_bytes.decode('utf-8').strip()
                            except UnicodeDecodeError: print(f"Ctrl: Persistent UnicodeDecodeError: {complete_line_bytes[:60]}..."); line_str = None
                        
                        if line_str and not (self.expecting_screenshot_data or self.expecting_memory_slots or self.expecting_theme_string): 
                            self.data_queue.put(line_str)
                        continue 

                    if not line_str: 
                        if self.expecting_screenshot_data and self.screenshot_hex_buffer: self.last_screenshot_hex_byte_time = time.time() 
                        elif self.expecting_memory_slots and self.memory_slots_buffer: self.last_memory_slot_time = time.time()
                        elif self.expecting_theme_string: self.last_theme_data_time = time.time() 
                        continue

                    if self.expecting_screenshot_data:
                        is_hex = self._is_hex_string(line_str)
                        if is_hex: 
                            self.screenshot_hex_buffer += line_str
                        self.last_screenshot_hex_byte_time = time.time() 
                        
                        if not is_hex: 
                            is_simple_ignorable = line_str.strip().upper() == "OK" or \
                                                  "ERROR: EXPECTED NEWLINE" in line_str.upper() or \
                                                  line_str.strip().upper() == CMD_SCREENSHOT.upper()
                            is_data_log = self.DATA_LOG_PATTERN.match(line_str)

                            if is_simple_ignorable:
                                pass 
                            elif is_data_log:
                                self.data_queue.put(line_str)
                        continue 
                    
                    elif self.expecting_memory_slots:
                        is_slot = self._is_memory_slot_line(line_str)
                        is_log = self.DATA_LOG_PATTERN.match(line_str)
                        is_simple_resp = line_str.strip().upper() == "OK" or "Error: Expected newline" in line_str

                        if is_slot: 
                            self.memory_slots_buffer.append(line_str); self.last_memory_slot_time = time.time()
                            if len(self.memory_slots_buffer) >= 32: self._finalize_special_op("Memory"); continue
                        elif self.memory_slots_buffer: 
                            if is_log or (line_str and not is_simple_resp): 
                                self._finalize_special_op("Memory")
                                if is_log: self.data_queue.put(line_str) 
                                continue
                            elif is_simple_resp: self.last_memory_slot_time = time.time() 
                        elif not self.memory_slots_buffer and line_str: 
                            if is_log: self.data_queue.put(line_str)
                    
                    elif self.expecting_theme_string:
                        match = self.THEME_STRING_LINE_PATTERN.match(line_str)
                        if match:
                            self.theme_string_buffer = match.group(1) 
                            print(f"Ctrl: Matched theme string: {self.theme_string_buffer[:60]}...") 
                            self._finalize_special_op("ThemeGet") 
                        elif line_str: 
                            self.last_theme_data_time = time.time() 
                    
                    elif line_str: 
                        if self.DATA_LOG_PATTERN.match(line_str): self.data_queue.put(line_str)

            except SerialException as e: print(f"Ctrl: Serial read error: {e}"); self.data_queue.put(('serial_error_disconnect', f"Serial read error: {e}")); self.running = False; break 
            except Exception as e: 
                print(f"Ctrl: Unexpected error in read loop: {e}")
                op_type = "Screenshot" if self.expecting_screenshot_data else \
                          "Memory" if self.expecting_memory_slots else \
                          "ThemeGet" if self.expecting_theme_string else None
                if op_type: self._finalize_special_op(op_type)
                self.data_queue.put(('serial_error_disconnect', f"Read loop error: {e}")); self.running = False; break

class RadioApp(tk.Tk):
    MIN_BATTERY_VOLTAGE = 3.2; MAX_BATTERY_VOLTAGE = 4.2; MAX_VOLUME = 63; MAX_RSSI_SNR = 127
    PERCENTAGE_MULTIPLIER = 100; LABEL_WIDTH = 14; EMOJI_BUTTON_WIDTH = 2 
    UP_ARROW_EMOJI = "⬆️"; DOWN_ARROW_EMOJI = "⬇️"; REFRESH_EMOJI = "🔃"; SCREENSHOT_EMOJI = "📸"; MEMORY_SLOTS_EMOJI = "💾"
    ENCODER_LEFT_EMOJI = "⬅️"; ENCODER_RIGHT_EMOJI = "➡️"; ENCODER_ARROW_BUTTON_WIDTH = 4 
    BAUD_RATES = [9600, 19200, 38400, 57600, 115200]; DEFAULT_BAUD_RATE = 9600 
    PAD_X_CONN = 2; PAD_Y_CONN = 2; PAD_X_CTRL_GROUP = 5; PAD_Y_CTRL_GROUP = 5 
    PAD_X_MAIN = 5; PAD_Y_MAIN = 5; PAD_LARGE = 10; PAD_MEDIUM = 5; PAD_SMALL = 2
    MODES = ["AM", "FM", "LSB", "USB", "CW"]; BANDS = ["VHF", "ALL", "LW", "MW", "SW", "160M", "80M", "60M", "40M", "30M", "20M", "17M", "15M", "12M", "10M", "6M", "CB"] 
    KNOB_SIZE = 50; KNOB_INDICATOR_LENGTH = 18; ARROWHEAD_LENGTH = 7; ARROWHEAD_WIDTH = 5
    
    MAX_SWATCHES_TO_DISPLAY = 32 
    MIN_COLOR_COUNT_FOR_PALETTE = 16 
    MAX_THEME_SWATCHES = 37 
    
    DEFAULT_SCAN_DWELL_TIME = 0.5 
    DEFAULT_FM_SCAN_SNR_THRESHOLD = 12

    FM_SCAN_MAX_STEPS = 500 
    FM_STEP_CYCLE_STRINGS = ["10k", "50k", "100k", "200k", "1m"] 
    FM_SCAN_TARGET_STEP_STR = "100k"


    def __init__(self):
        super().__init__()
        self.title("ATS-Mini Radio Controller")
        self.resizable(True, True) 
        self.minsize(650, 550) 

        self.port_var = tk.StringVar(master=self)
        self.baud_var = tk.StringVar(master=self, value=str(self.DEFAULT_BAUD_RATE)) 
        self.console_var = tk.BooleanVar(master=self, value=False)

        self.vol_var = tk.StringVar(master=self, value="Vol: --")
        self.band_var = tk.StringVar(master=self, value="Band: --")
        self.mode_var = tk.StringVar(master=self, value="Mode: --")
        self.step_var = tk.StringVar(master=self, value="Step: --")
        self.bw_var = tk.StringVar(master=self, value="BW: --")
        self.agc_var = tk.StringVar(master=self, value="AGC: --")
        self.bl_var = tk.StringVar(master=self, value="Bright: --")
        self.cal_var = tk.StringVar(master=self, value="Cal: --")

        self.freq_var = tk.StringVar(master=self, value="Frequency: --")
        self.agc_status_var = tk.StringVar(master=self, value="Gain Control: --")
        self.rssi_var = tk.StringVar(master=self, value="RSSI: --")
        self.snr_var = tk.StringVar(master=self, value="SNR: --")
        self.batt_var = tk.StringVar(master=self, value="Battery: --")
        self.fw_var = tk.StringVar(master=self, value="Firmware: --")
        
        self.controller = RadioController()
        self.connected = False
        self.console_visible = False 
        
        self.memory_slots_data = [{'slot_num': i, 'band': '', 'freq_hz': '', 'mode': ''} for i in range(1, 33)]
        self.memory_viewer_window = None
        self.memory_slot_display_vars = {}
        self.waiting_for_memory_data_to_build_viewer = False 
        
        self.screenshot_window = None 
        self.ss_image_label = None
        self.ss_palette_outer_frame = None
        self.ss_theme_palette_frame = None 
        self.ss_refresh_button = None
        self.ss_info_label = None 
        self.last_screenshot_rgb565_palette_order = [] 
        self.initial_screenshot_geometry = None 

        self.theme_palette_frame = None 
        self.encoder_click_buttons = [] 
        self.knob_angle_degrees = 0 

        self.fm_scan_active = False
        self.fm_scan_stop_requested = False
        self.fm_scan_results = []
        self.scan_cycle_start_freq_str = "" 
        self.scan_cycle_start_freq_mhz = 0.0 
        self.fm_scan_start_time = 0 
        self.fm_scan_progress_var = tk.StringVar(master=self) 

        self.current_fm_scan_snr_threshold = self.DEFAULT_FM_SCAN_SNR_THRESHOLD
        self.current_scan_dwell_time = self.DEFAULT_SCAN_DWELL_TIME
        self.snr_threshold_display_var = tk.StringVar(master=self, value=str(self.current_fm_scan_snr_threshold))

        self.indicator_blink_after_id = None
        self.special_op_active_for_blink = False 


        self.set_os_theme()
        self.create_styles()
        
        self.grid_columnconfigure(0, weight=0) 
        self.grid_columnconfigure(1, weight=1) 
        self.grid_columnconfigure(2, weight=0) 
        self.grid_rowconfigure(0, weight=1)    

        self.main_layout_frame = ttk.Frame(self) 
        self.main_layout_frame.grid(row=0, column=1, sticky="nsew", padx=self.PAD_X_MAIN * 4, pady=self.PAD_Y_MAIN) 

        self.main_layout_frame.grid_columnconfigure(0, weight=1) 
        self.main_layout_frame.grid_columnconfigure(1, weight=1) 

        self.main_layout_frame.grid_rowconfigure(0, weight=0)  
        self.main_layout_frame.grid_rowconfigure(1, weight=0)  
        self.main_layout_frame.grid_rowconfigure(2, weight=0)  
        self.main_layout_frame.grid_rowconfigure(3, weight=1)  
        self.main_layout_frame.grid_rowconfigure(4, weight=0)  
        self.main_layout_frame.grid_rowconfigure(5, weight=1)  
        self.main_layout_frame.grid_rowconfigure(6, weight=0)  
        # Row 7 for console (weight=2) is configured in toggle_console

        self.create_widgets() 
        
        self.update_idletasks()
        
        self.bind_arrow_keys() 
        self.after(100, lambda: self.process_serial_queue())
        self.refresh_ports()
        self.protocol("WM_DELETE_WINDOW", self.on_closing); 

    def create_styles(self):
        self.style = ttk.Style(self)
        self.style.configure("EncoderArrow.TButton", 
                             padding=(self.PAD_SMALL, self.PAD_SMALL + 2), 
                             font=('Arial Unicode MS', 14),
                             anchor=tk.CENTER) 
        self.style.configure("Emoji.TButton", padding=(self.PAD_SMALL, self.PAD_SMALL), font=('Arial Unicode MS', 10)) 

    def bind_arrow_keys(self):
        self.bind("<Left>", self.handle_key_press)
        self.bind("<Right>", self.handle_key_press)
        self.bind("<Up>", self.handle_key_press)
        self.bind("<Down>", self.handle_key_press)

    def handle_key_press(self, event):
        if not self.connected: return 
        cmd_to_send = None
        if event.keysym == "Left": 
            cmd_to_send = CMD_ENCODER_DOWN
            self.knob_angle_degrees = (self.knob_angle_degrees - 18 + 360) % 360
            self._draw_knob()
        elif event.keysym == "Right": 
            cmd_to_send = CMD_ENCODER_UP
            self.knob_angle_degrees = (self.knob_angle_degrees + 18) % 360
            self._draw_knob()
        elif event.keysym in ["Up", "Down"]: 
            cmd_to_send = CMD_ENCODER_BTN
        
        if cmd_to_send: self.send_radio_command(cmd_to_send)


    def on_closing(self):
        if self.fm_scan_active: 
            self.fm_scan_stop_requested = True
            time.sleep(self.current_scan_dwell_time + 0.2) 

        if hasattr(self, 'screenshot_window') and self.screenshot_window and self.screenshot_window.winfo_exists(): self.screenshot_window.destroy() 
        if self.memory_viewer_window and self.memory_viewer_window.winfo_exists(): self.memory_viewer_window.destroy()
        if self.connected: self.controller.disconnect()
        self.destroy()

    def set_os_theme(self): 
        self.style = ttk.Style(self)
        try:
            if platform.system() == 'Windows': self.style.theme_use('vista')
            elif platform.system() == 'Darwin': self.style.theme_use('aqua') 
            else: self.style.theme_use('clam') 
        except tk.TclError: print("Failed to set OS theme, using default.")

    def _create_connection_bar_elements(self): 
        return [
            {'type': 'label', 'text': "Port:", 'sticky': "w", 'padx': (0, self.PAD_X_CONN)},
            {'type': 'combobox', 'textvariable_obj': self.port_var, 'width': 10, 'state': "readonly", 'sticky': "w", 'padx': (0, self.PAD_X_CONN), 'tooltip': "Select COM Port for radio connection.", 'name': 'port_combo'},
            {'type': 'label', 'text': "Baud:", 'sticky': "w", 'padx': (0, self.PAD_X_CONN)},
            {'type': 'combobox', 'textvariable_obj': self.baud_var, 'width': 6, 'state': "readonly", 'values': [str(r) for r in self.BAUD_RATES], 'sticky': "w", 'padx': (0, self.PAD_X_CONN), 'tooltip': "Select Baud Rate for serial connection.", 'name': 'baud_combo'}, 
            {'type': 'button', 'text': self.REFRESH_EMOJI, 'command': self.refresh_ports, 'width': self.EMOJI_BUTTON_WIDTH, 'sticky': "w", 'padx': (0, self.PAD_X_CONN), 'tooltip': "Refresh available COM Ports list.", 'name': 'refresh_btn', 'style': 'Emoji.TButton'},
            {'type': 'button', 'text': self.SCREENSHOT_EMOJI, 'command': self.request_screenshot, 'width': self.EMOJI_BUTTON_WIDTH, 'state': tk.DISABLED, 'sticky': "w", 'padx': (0, self.PAD_X_CONN), 'tooltip': "Request Screenshot from Radio (disables log temporarily).", 'name': 'screenshot_btn', 'style': 'Emoji.TButton'},
            {'type': 'button', 'text': self.MEMORY_SLOTS_EMOJI, 'command': self.open_memory_viewer, 'width': self.EMOJI_BUTTON_WIDTH, 'state': tk.DISABLED, 'sticky':"w", 'padx': (0,self.PAD_X_CONN), 'tooltip': "Open Memory Slot Viewer (disables log temporarily).", 'name': 'memory_btn', 'style': 'Emoji.TButton'},
            {'type': 'button', 'text': "Sleep", 'command': self.toggle_sleep, 'width': 6, 'state': tk.DISABLED, 'sticky': "w", 'padx': (0, self.PAD_X_CONN), 'tooltip': "Toggle Radio Sleep/Wake Mode.", 'name': 'sleep_btn'},
            {'type': 'spacer', 'weight': 1}, 
            {'type': 'checkbutton', 'text': "Console", 'variable_obj': self.console_var, 'command': self.toggle_console, 'sticky': "e", 'padx': (self.PAD_X_MAIN, self.PAD_X_CONN), 'tooltip': "Show/Hide Serial Console Log.", 'name': 'console_chk'},
            {'type': 'button', 'text': "Connect", 'command': self.toggle_connection, 'width': 10, 'sticky': "e", 'padx': (0, self.PAD_X_CONN), 'tooltip': "Connect to/Disconnect from Radio.", 'name': 'connect_btn'}, 
            {'type': 'canvas', 'width': 20, 'height': 20, 'highlightthickness': 0, 'sticky': "e", 'padx': (0,0), 'tooltip': "Connection Status:\nRed: Disconnected\nYellow: Connecting/No Data\nGreen: Connected & Receiving Data", 'name': 'connection_status_canvas'}
        ]

    def _get_control_group_configs(self): 
        return [
            {'var_name': 'vol_var', 'cmd_up': CMD_VOLUME_UP, 'cmd_down': CMD_VOLUME_DOWN, 'initial': "Vol: --", 'tip_up': "Increase Volume", 'tip_down': "Decrease Volume"},
            {'var_name': 'band_var', 'cmd_up': CMD_BAND_NEXT, 'cmd_down': CMD_BAND_PREV, 'initial': "Band: --", 'tip_up': "Next Band", 'tip_down': "Previous Band"},
            {'var_name': 'mode_var', 'cmd_up': CMD_MODE_NEXT, 'cmd_down': CMD_MODE_PREV, 'initial': "Mode: --", 'tip_up': "Next Mode", 'tip_down': "Previous Mode"},
            {'var_name': 'step_var', 'cmd_up': CMD_STEP_NEXT, 'cmd_down': CMD_STEP_PREV, 'initial': "Step: --", 'tip_up': "Next Tuning Step", 'tip_down': "Previous Tuning Step"},
            {'var_name': 'bw_var', 'cmd_up': CMD_BW_PREV, 'cmd_down': CMD_BW_NEXT, 'initial': "BW: --", 'tip_up': "Increase Bandwidth", 'tip_down': "Decrease Bandwidth"}, 
            {'var_name': 'agc_var', 'cmd_up': CMD_AGC_ATT_UP, 'cmd_down': CMD_AGC_ATT_DOWN, 'initial': "AGC: --", 'tip_up': "Increase AGC/Attenuator", 'tip_down': "Decrease AGC/Attenuator"},
            {'var_name': 'bl_var', 'cmd_up': CMD_BL_UP, 'cmd_down': CMD_BL_DOWN, 'initial': "Bright: --", 'tip_up': "Increase Backlight", 'tip_down': "Decrease Backlight"},
            {'var_name': 'cal_var', 'cmd_up': CMD_CAL_UP, 'cmd_down': CMD_CAL_DOWN, 'initial': "Cal: --", 'tip_up': "Increase Calibration Offset", 'tip_down': "Decrease Calibration Offset"},
        ]
    
    def _get_status_label_configs(self): 
        return [
            {'var_name': 'freq_var',       'initial': "Frequency: --",    'row': 0, 'col': 0, 'sticky': "w"},
            {'var_name': 'snr_var',        'initial': "SNR: --",          'row': 0, 'col': 2, 'sticky': "w"},
            {'var_name': 'batt_var',       'initial': "Battery: --",      'row': 0, 'col': 3, 'sticky': "w"}, 
            {'var_name': 'agc_status_var', 'initial': "Gain Control: --", 'row': 1, 'col': 0, 'columnspan': 2, 'sticky': "w"}, 
            {'var_name': 'rssi_var',       'initial': "RSSI: --",         'row': 1, 'col': 2, 'sticky': "w"},
            {'var_name': 'fw_var',         'initial': "Firmware: --",     'row': 1, 'col': 3, 'sticky': "w"},
        ]

    def create_widgets(self):
        self.conn_frame = ttk.Frame(self.main_layout_frame) 
        self.conn_frame.grid(row=0, column=0, columnspan=2, padx=0, pady=(0, self.PAD_Y_MAIN), sticky="ew")
        
        conn_elements = self._create_connection_bar_elements()
        current_col_idx = 0
        for config in conn_elements:
            if config['type'] == 'spacer': 
                self.conn_frame.grid_columnconfigure(current_col_idx, weight=config.get('weight', 0))
                current_col_idx +=1
                continue
            
            element = None
            style_to_use = config.get('style', None) 

            if config['type'] == 'frame': 
                pass 
            elif config['type'] == 'label': 
                if 'textvariable_obj' in config: 
                    element = ttk.Label(self.conn_frame, textvariable=config['textvariable_obj'], width=config.get('width'))
                else:
                    element = ttk.Label(self.conn_frame, text=config['text'], style=style_to_use)
            elif config['type'] == 'combobox':
                var_obj = config['textvariable_obj'] 
                element = ttk.Combobox(self.conn_frame, textvariable=var_obj, width=config['width'], state=config['state'], style=style_to_use)
                if 'values' in config: element['values'] = config['values']
            elif config['type'] == 'button': element = ttk.Button(self.conn_frame, text=config['text'], command=config['command'], width=config['width'], state=config.get('state', tk.NORMAL), style=style_to_use)
            elif config['type'] == 'checkbutton':
                var_obj = config['variable_obj'] 
                element = ttk.Checkbutton(self.conn_frame, text=config['text'], variable=var_obj, command=config['command'], style=style_to_use)
            elif config['type'] == 'canvas': element = tk.Canvas(self.conn_frame, width=config['width'], height=config['height'], highlightthickness=config['highlightthickness'])
            
            if element and config['type'] != 'frame': 
                element.grid(row=0, column=current_col_idx, sticky=config['sticky'], padx=config['padx'])
            
            if element and 'tooltip' in config: Tooltip(element, config['tooltip'])
            if element and 'name' in config and config['type'] != 'frame': 
                setattr(self, config['name'], element) 
            current_col_idx +=1
        
        self.update_status_indicator()

        self.ctrl_frame_buttons = []
        control_group_configs = self._get_control_group_configs()
        self.ctrl_frame1 = ttk.Frame(self.main_layout_frame); self.ctrl_frame1.grid(row=1, column=0, columnspan=2, padx=0, pady=self.PAD_Y_MAIN, sticky="ew")
        for i in range(4): self.ctrl_frame1.grid_columnconfigure(i, weight=1, uniform="ctrlgroup1") 
        self.ctrl_frame2 = ttk.Frame(self.main_layout_frame); self.ctrl_frame2.grid(row=2, column=0, columnspan=2, padx=0, pady=(0, self.PAD_Y_MAIN), sticky="ew")
        for i in range(4): self.ctrl_frame2.grid_columnconfigure(i, weight=1, uniform="ctrlgroup2")
        for i, config in enumerate(control_group_configs):
            parent_frame = self.ctrl_frame1 if i < 4 else self.ctrl_frame2; col = i % 4
            text_var_instance = getattr(self, config['var_name']) 
            group_frame, buttons = self._create_control_group_widget(
                parent_frame, 
                text_var_instance, 
                config['cmd_up'], 
                config['cmd_down'], 
                config['initial'], 
                config['tip_up'], 
                config['tip_down']
            )
            group_frame.grid(row=0, column=col, padx=self.PAD_X_CTRL_GROUP, pady=self.PAD_Y_CTRL_GROUP, sticky="nsew")
            self.ctrl_frame_buttons.extend(buttons)
        
        controls_sub_frame = ttk.Frame(self.main_layout_frame)
        controls_sub_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(self.PAD_SMALL, self.PAD_MEDIUM)) 
        controls_sub_frame.grid_columnconfigure(0, weight=1, uniform="control_col_sub") 
        controls_sub_frame.grid_columnconfigure(1, weight=1, uniform="control_col_sub") 

        self.encoder_frame = ttk.Frame(controls_sub_frame, padding=(self.PAD_MEDIUM)) 
        self.encoder_frame.grid(row=0, column=0, sticky="nsew", padx=(0, self.PAD_SMALL))
        self.encoder_frame.grid_rowconfigure(0, weight=1) 
        self.encoder_frame.grid_rowconfigure(1, weight=0) 
        self.encoder_frame.grid_columnconfigure(0, weight=1); self.encoder_frame.grid_columnconfigure(1, weight=0) 
        self.encoder_frame.grid_columnconfigure(2, weight=0); self.encoder_frame.grid_columnconfigure(3, weight=0)
        self.encoder_frame.grid_columnconfigure(4, weight=1)
        
        encoder_buttons_knob_frame = ttk.Frame(self.encoder_frame)
        encoder_buttons_knob_frame.grid(row=0, column=0, columnspan=5, sticky="ew") 
        encoder_buttons_knob_frame.grid_columnconfigure(0, weight=1)
        encoder_buttons_knob_frame.grid_columnconfigure(1, weight=0)
        encoder_buttons_knob_frame.grid_columnconfigure(2, weight=0)
        encoder_buttons_knob_frame.grid_columnconfigure(3, weight=0)
        encoder_buttons_knob_frame.grid_columnconfigure(4, weight=1)
        
        self.encoder_left_btn = ttk.Button(encoder_buttons_knob_frame, text=self.ENCODER_LEFT_EMOJI, command=lambda: self.send_encoder_command(CMD_ENCODER_DOWN, -18), width=self.ENCODER_ARROW_BUTTON_WIDTH, style="EncoderArrow.TButton") 
        self.encoder_left_btn.grid(row=0, column=1, sticky="e", padx=(0, self.PAD_SMALL)) 
        Tooltip(self.encoder_left_btn, "Encoder Down (Counter-Clockwise)")
        
        self.knob_canvas = tk.Canvas(encoder_buttons_knob_frame, width=self.KNOB_SIZE, height=self.KNOB_SIZE, highlightthickness=1, highlightbackground="gray")
        self.knob_canvas.grid(row=0, column=2, padx=self.PAD_SMALL)
        self._draw_knob() 
        self.knob_canvas.bind("<Button-1>", self.handle_knob_click) 
        Tooltip(self.knob_canvas, "Click: Encoder Button\nUse Arrow Keys:\nLeft: Encoder Down\nRight: Encoder Up\nUp/Down: Encoder Button")

        self.encoder_right_btn = ttk.Button(encoder_buttons_knob_frame, text=self.ENCODER_RIGHT_EMOJI, command=lambda: self.send_encoder_command(CMD_ENCODER_UP, 18), width=self.ENCODER_ARROW_BUTTON_WIDTH, style="EncoderArrow.TButton") 
        self.encoder_right_btn.grid(row=0, column=3, sticky="w", padx=(self.PAD_SMALL, 0)) 
        Tooltip(self.encoder_right_btn, "Encoder Up (Clockwise)")
        self.encoder_click_buttons.extend([self.encoder_left_btn, self.encoder_right_btn]) 

        encoder_title_label = ttk.Label(self.encoder_frame, text="Encoder Controls", font=('Helvetica', 10, 'bold'), anchor=tk.CENTER)
        encoder_title_label.grid(row=1, column=0, columnspan=5, pady=(self.PAD_MEDIUM, 0), sticky="ew")


        self.fm_scan_controls_frame = ttk.Frame(controls_sub_frame, padding=(self.PAD_MEDIUM)) 
        self.fm_scan_controls_frame.grid(row=0, column=1, sticky="nsew", padx=(self.PAD_SMALL, 0))
        
        fm_scan_title_label = ttk.Label(self.fm_scan_controls_frame, text="FM Scan", font=('Helvetica', 10, 'bold'), anchor=tk.CENTER)
        fm_scan_title_label.pack(pady=(0, self.PAD_SMALL), fill=tk.X)

        snr_frame = ttk.Frame(self.fm_scan_controls_frame)
        snr_frame.pack(fill=tk.X, pady=self.PAD_SMALL)
        ttk.Label(snr_frame, text="SNR Floor:").pack(side=tk.LEFT)
        self.snr_threshold_scale = ttk.Scale(snr_frame, from_=0, to=24, orient=tk.HORIZONTAL, command=self._update_snr_threshold) 
        self.snr_threshold_scale.set(self.current_fm_scan_snr_threshold)
        self.snr_threshold_scale.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(self.PAD_SMALL,0))
        ttk.Label(snr_frame, textvariable=self.snr_threshold_display_var, width=3).pack(side=tk.LEFT)
        Tooltip(self.snr_threshold_scale, "Set minimum Signal-to-Noise Ratio for FM scan results (0-24 dB).")
        
        self.fm_scan_progress_label = ttk.Label(self.fm_scan_controls_frame, textvariable=self.fm_scan_progress_var, anchor=tk.CENTER)
        self.fm_scan_progress_label.pack(pady=self.PAD_SMALL, fill=tk.X)
        self.fm_scan_progress_var.set("") 

        fm_scan_buttons_actual_frame = ttk.Frame(self.fm_scan_controls_frame)
        fm_scan_buttons_actual_frame.pack(pady=(self.PAD_SMALL, 0), anchor='center') 
        self.fm_scan_button = ttk.Button(fm_scan_buttons_actual_frame, text="FM Scan", command=self.start_fm_scan, width=9)
        self.fm_scan_button.pack(side=tk.LEFT, padx=self.PAD_SMALL)
        Tooltip(self.fm_scan_button, "Scan FM band for stations (uses current SNR Floor). Dwell time is fixed at 0.5s.")
        self.fm_scan_stop_button = ttk.Button(fm_scan_buttons_actual_frame, text="Stop Scan", command=self.stop_fm_scan, width=9)
        Tooltip(self.fm_scan_stop_button, "Stop the current FM scan.")
        self._update_fm_scan_button_state() 

        self.set_control_buttons_state(tk.DISABLED) 
        
        self.status_frame = ttk.LabelFrame(self.main_layout_frame, text="Radio Status") 
        self.status_frame.grid(row=6, column=0, columnspan=2, padx=self.PAD_X_MAIN, pady=(0, self.PAD_X_MAIN * 4), sticky="ew") 
        self.status_frame.grid_columnconfigure(0, weight=1) 
        self.status_frame.grid_columnconfigure(1, weight=0) 
        self.status_frame.grid_columnconfigure(2, weight=1) 
        self.status_frame.grid_columnconfigure(3, weight=1) 
        
        status_label_configs = self._get_status_label_configs()
        for config in status_label_configs:
            var_instance = getattr(self, config['var_name']) 
            label = ttk.Label(self.status_frame, textvariable=var_instance)
            label.grid(row=config['row'], column=config['col'], columnspan=config.get('columnspan', 1), padx=self.PAD_X_MAIN, pady=self.PAD_Y_CONN, sticky=config['sticky'])
            if config['var_name'] == 'freq_var': 
                self.snr_level_indicator = tk.Canvas(self.status_frame, width=10, height=10, highlightthickness=0)
                self.snr_level_indicator.grid(row=config['row'], column=config['col'] + 1, padx=(0, self.PAD_SMALL), pady=self.PAD_Y_CONN, sticky="w")
                Tooltip(self.snr_level_indicator, "Green if SNR >= Floor, Grey otherwise.")
                self._update_snr_indicator() 


        self.console_frame = ttk.LabelFrame(self.main_layout_frame, text="Serial Console") 
        self.console = scrolledtext.ScrolledText(self.console_frame, height=8, width=70, state=tk.DISABLED, relief="sunken", borderwidth=1, padx=self.PAD_X_CONN, pady=self.PAD_Y_CONN) 
        self.console.pack(fill="both", expand=True, padx=self.PAD_X_CONN, pady=self.PAD_Y_CONN)


    def _update_snr_threshold(self, value):
        self.current_fm_scan_snr_threshold = int(float(value))
        self.snr_threshold_display_var.set(f"{self.current_fm_scan_snr_threshold}")
        self._update_snr_indicator() 

    def _update_scan_dwell_time(self, value): 
        pass # Dwell time is now static

    def _update_snr_indicator(self):
        if not hasattr(self, 'snr_level_indicator') or not self.snr_level_indicator.winfo_exists():
            return
        
        snr_text = self.snr_var.get() 
        color_to_set = "#AAAAAA" # Default grey
        try:
            snr_match = re.search(r'(-?\d+)\s*dB', snr_text)
            if snr_match:
                snr_value = int(snr_match.group(1))
                if snr_value >= self.current_fm_scan_snr_threshold:
                    color_to_set = "#00E000" # Bright green
        except (ValueError, TypeError):
            pass 
        
        self.snr_level_indicator.delete("all")
        self.snr_level_indicator.create_oval(0, 0, 10, 10, fill=color_to_set, outline=color_to_set)


    def _draw_knob(self):
        self.knob_canvas.delete("all")
        cx, cy = self.KNOB_SIZE / 2, self.KNOB_SIZE / 2
        r_outer = self.KNOB_SIZE / 2 - 3 
        
        self.knob_canvas.create_oval(cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer, 
                                     outline="black", fill="lightgrey", width=2)
        
        angle_rad = math.radians(self.knob_angle_degrees - 90) 
        
        x_tip = cx + self.KNOB_INDICATOR_LENGTH * math.cos(angle_rad)
        y_tip = cy + self.KNOB_INDICATOR_LENGTH * math.sin(angle_rad)

        x_base_center = cx + (self.KNOB_INDICATOR_LENGTH - self.ARROWHEAD_LENGTH) * math.cos(angle_rad)
        y_base_center = cy + (self.KNOB_INDICATOR_LENGTH - self.ARROWHEAD_LENGTH) * math.sin(angle_rad)
        
        angle_rad_perp = angle_rad + math.pi / 2
        
        x_base1 = x_base_center + (self.ARROWHEAD_WIDTH / 2) * math.cos(angle_rad_perp)
        y_base1 = y_base_center + (self.ARROWHEAD_WIDTH / 2) * math.sin(angle_rad_perp)
        x_base2 = x_base_center - (self.ARROWHEAD_WIDTH / 2) * math.cos(angle_rad_perp)
        y_base2 = y_base_center - (self.ARROWHEAD_WIDTH / 2) * math.sin(angle_rad_perp)
        
        self.knob_canvas.create_polygon(x_tip, y_tip, x_base1, y_base1, x_base2, y_base2, 
                                        fill="black", outline="black")


    def send_encoder_command(self, command, angle_change):
        """Helper to send encoder command and update knob angle."""
        if self.connected:
            self.controller.send_command(command)
            self.knob_angle_degrees = (self.knob_angle_degrees + angle_change + 360) % 360
            self._draw_knob()
        else:
            messagebox.showwarning("Not Connected", "Connect to the radio to send commands.")


    def handle_knob_click(self, event=None):
        if not self.connected: return
        self.send_radio_command(CMD_ENCODER_BTN)

    def _create_control_group_widget(self, parent, text_var_instance, cmd_up, cmd_down, initial_text, tip_up, tip_down): 
        group_frame = ttk.LabelFrame(parent, padding=(self.PAD_X_CTRL_GROUP, self.PAD_Y_CTRL_GROUP)) 
        group_frame.grid_columnconfigure(0, weight=1) 
        up_button = ttk.Button(group_frame, text=self.UP_ARROW_EMOJI, command=lambda: self.send_radio_command(cmd_up), width=self.ENCODER_ARROW_BUTTON_WIDTH, style="EncoderArrow.TButton")
        up_button.grid(row=0, column=0, pady=(self.PAD_Y_CONN,0)); Tooltip(up_button, tip_up)
        
        text_var_instance.set(initial_text) 
        value_label = ttk.Label(group_frame, textvariable=text_var_instance, width=self.LABEL_WIDTH, font=('Helvetica', 9, 'bold'), anchor="center") 
        value_label.grid(row=1, column=0, pady=self.PAD_Y_CONN)
        
        down_button = ttk.Button(group_frame, text=self.DOWN_ARROW_EMOJI, command=lambda: self.send_radio_command(cmd_down), width=self.ENCODER_ARROW_BUTTON_WIDTH, style="EncoderArrow.TButton")
        down_button.grid(row=2, column=0, pady=(0,self.PAD_Y_CONN)); Tooltip(down_button, tip_down)
        return group_frame, [up_button, down_button]

    def open_memory_viewer(self): 
        if not self.connected: messagebox.showwarning("Not Connected", "Connect to the radio to use the memory viewer."); return
        
        if self.memory_viewer_window and self.memory_viewer_window.winfo_exists():
            self.memory_viewer_window.lift(); self.memory_viewer_window.focus_set()
            if not self.controller.expecting_memory_slots: 
                self.special_op_active_for_blink = True 
                self.refresh_memory_slots_from_radio()
            return
        self.special_op_active_for_blink = True 
        self.waiting_for_memory_data_to_build_viewer = True
        self.refresh_memory_slots_from_radio()

    def _build_and_show_memory_viewer(self):
        if self.memory_viewer_window and self.memory_viewer_window.winfo_exists():
            self.memory_viewer_window.destroy() 
        
        self.memory_viewer_window = tk.Toplevel(self); self.memory_viewer_window.title("Memory Slot Viewer")
        self.memory_viewer_window.resizable(False, True) 
        
        editor_frame = ttk.Frame(self.memory_viewer_window, padding=self.PAD_LARGE) 
        editor_frame.pack(fill="both", expand=True)

        refresh_button = ttk.Button(editor_frame, text="Refresh Slots from Radio", command=self.refresh_memory_slots_from_radio)
        refresh_button.pack(pady=(0, self.PAD_MEDIUM)); Tooltip(refresh_button, "Fetch current memory slot data from the radio")

        slots_canvas_frame = ttk.Frame(editor_frame) 
        slots_canvas_frame.pack(fill="both", expand=True)

        slots_canvas = tk.Canvas(slots_canvas_frame)
        slots_scrollbar = ttk.Scrollbar(slots_canvas_frame, orient="vertical", command=slots_canvas.yview)
        slots_scrollable_frame = ttk.Frame(slots_canvas) 

        slots_scrollable_frame.bind("<Configure>", lambda e: slots_canvas.configure(scrollregion=slots_canvas.bbox("all")))
        slots_canvas.create_window((0, 0), window=slots_scrollable_frame, anchor="nw")
        slots_canvas.configure(yscrollcommand=slots_scrollbar.set)

        slots_canvas.pack(side="left", fill="both", expand=True)
        slots_scrollbar.pack(side="right", fill="y")
        
        self.memory_slot_display_vars = {} 
        num_cols = 4
        est_content_width_per_slot_item = 150 

        for i in range(32):
            slot_num = i + 1; row = i // num_cols; col = i % num_cols
            slot_frame = ttk.LabelFrame(slots_scrollable_frame, text=f"Slot {slot_num:02d}", padding=(self.PAD_MEDIUM, self.PAD_SMALL))
            slot_frame.grid(row=row, column=col, padx=self.PAD_X_CTRL_GROUP, pady=self.PAD_Y_CTRL_GROUP, sticky="nsew")
            
            band_var = tk.StringVar(); ttk.Label(slot_frame, text="Band:").grid(row=0, column=0, sticky="nw", pady=1); 
            ttk.Label(slot_frame, textvariable=band_var, anchor="w", wraplength=est_content_width_per_slot_item - 50).grid(row=0, column=1, sticky="ew", padx=self.PAD_X_CONN, pady=1)
            
            freq_var = tk.StringVar(); ttk.Label(slot_frame, text="Freq:").grid(row=1, column=0, sticky="nw", pady=1);  
            ttk.Label(slot_frame, textvariable=freq_var, anchor="w").grid(row=1, column=1, sticky="ew", padx=self.PAD_X_CONN, pady=1)
            
            mode_var = tk.StringVar(); ttk.Label(slot_frame, text="Mode:").grid(row=2, column=0, sticky="nw", pady=1); 
            ttk.Label(slot_frame, textvariable=mode_var, anchor="w").grid(row=2, column=1, sticky="ew", padx=self.PAD_X_CONN, pady=1)
            
            slot_frame.grid_columnconfigure(1, weight=1) 
            self.memory_slot_display_vars[slot_num] = {'band': band_var, 'freq': freq_var, 'mode': mode_var}
        
        self.update_memory_viewer_display() 

        self.memory_viewer_window.update_idletasks() 
        content_width = slots_scrollable_frame.winfo_reqwidth()
        scrollbar_width = slots_scrollbar.winfo_width() if slots_scrollbar.winfo_ismapped() else 20 
        window_width = content_width + scrollbar_width + (self.PAD_LARGE * 2) + 30 
        
        est_row_height = 0
        if self.memory_slot_display_vars and slots_scrollable_frame.winfo_children(): 
            first_slot_frame = slots_scrollable_frame.winfo_children()[0]
            first_slot_frame.update_idletasks()
            est_row_height = first_slot_frame.winfo_reqheight() + self.PAD_Y_CTRL_GROUP
        else: est_row_height = 75 
        
        num_rows_to_show = 8 
        content_height = est_row_height * num_rows_to_show
        window_height = content_height + refresh_button.winfo_reqheight() + (self.PAD_LARGE * 2) + self.PAD_MEDIUM + 40 
        window_height = max(400, min(window_height, 700)) 

        self.memory_viewer_window.geometry(f"{int(window_width)}x{int(window_height)}")
        self.memory_viewer_window.minsize(int(window_width * 0.95), 300) 

        self.memory_viewer_window.lift()
        self.memory_viewer_window.focus_set()


    def refresh_memory_slots_from_radio(self):
        if not self.connected: messagebox.showwarning("Not Connected", "Connect to radio to refresh memory slots."); return
        self.special_op_active_for_blink = True
        print("Requesting memory slot data from radio..."); self.controller.send_command(CMD_SHOW_MEM)

    def update_memory_viewer_display(self): 
        if not (self.memory_viewer_window and self.memory_viewer_window.winfo_exists()): return
        for slot_num_one_based, data in enumerate(self.memory_slots_data, 1):
            if slot_num_one_based in self.memory_slot_display_vars:
                vars_dict = self.memory_slot_display_vars[slot_num_one_based]
                
                band_val = data.get('band', '')
                freq_hz_str = data.get('freq_hz', '')
                mode_val = data.get('mode', '')

                display_band = band_val if band_val else "-"
                display_mode = mode_val if mode_val else "-"
                display_freq = "-"

                if freq_hz_str and freq_hz_str.isdigit():
                    freq_hz = int(freq_hz_str)
                    if freq_hz == 0 and not band_val and not mode_val: 
                        pass 
                    elif mode_val == "FM":
                        freq_mhz = freq_hz / 1000000.0
                        display_freq = f"{freq_mhz:.1f} MHz"
                    else: 
                        freq_khz = freq_hz / 1000.0
                        if freq_hz == 0: 
                             display_freq = "0 kHz"
                        elif freq_khz == int(freq_khz): 
                            display_freq = f"{int(freq_khz)} kHz"
                        else: 
                            display_freq = f"{freq_khz:.3f} kHz"
                elif freq_hz_str: 
                    display_freq = freq_hz_str 
                
                vars_dict['band'].set(display_band)
                vars_dict['freq'].set(display_freq)
                vars_dict['mode'].set(display_mode)


    def request_screenshot(self): 
        if not self.connected: messagebox.showwarning("Not Connected", "Connect to the radio to request a screenshot."); return
        if self.controller.expecting_screenshot_data: messagebox.showinfo("Screenshot In Progress", "Already waiting for screenshot data."); return
        
        self.special_op_active_for_blink = True
        if hasattr(self, 'screenshot_btn'):
            self.screenshot_btn.config(text="📸 Receiving...", state=tk.DISABLED)
        
        self.controller.send_command(CMD_SCREENSHOT)

    def _refresh_screenshot_command(self):
        if self.screenshot_window and self.screenshot_window.winfo_exists():
            self.screenshot_window.destroy() # Destroy existing window
        
        # Reset all screenshot window related attributes
        self.screenshot_window = None
        self.ss_image_label = None
        self.ss_palette_outer_frame = None
        self.theme_palette_frame = None 
        self.ss_button_frame = None
        self.ss_refresh_button = None
        self.ss_save_png_button = None
        self.ss_info_label = None
        self.initial_screenshot_geometry = None 
        self.last_screenshot_rgb565_palette_order = []
            
        print("App: Refreshing screenshot by recreating window...") 
        self.request_screenshot() # This will trigger display_screenshot which creates a new window


    def request_radio_theme(self):
        if not self.connected:
            messagebox.showwarning("Not Connected", "Connect to the radio to get theme data.")
            return
        if self.controller.expecting_theme_string or self.controller.theme_get_sequence_active:
            messagebox.showinfo("In Progress", "Already trying to get theme data.")
            return
        
        self.special_op_active_for_blink = True
        if self.screenshot_window and self.screenshot_window.winfo_exists() and self.theme_palette_frame: 
            for widget in self.theme_palette_frame.winfo_children(): 
                widget.destroy()
            loading_label = ttk.Label(self.theme_palette_frame, text="Fetching theme from radio...") 
            loading_label.pack(pady=5)

        self.controller.request_theme_data()


    def _rgb888_to_rgb565(self, r8, g8, b8):
        r5 = (r8 >> 3) & 0x1F
        g6 = (g8 >> 2) & 0x3F
        b5 = (b8 >> 3) & 0x1F
        return (r5 << 11) | (g6 << 5) | b5

    def _rgb565_to_rgb888(self, rgb565_int):
        r5 = (rgb565_int >> 11) & 0x1F
        g6 = (rgb565_int >> 5) & 0x3F
        b5 = rgb565_int & 0x1F
        
        r8 = (r5 * 255 + 15) // 31
        g8 = (g6 * 255 + 31) // 63
        b8 = (b5 * 255 + 15) // 31
        return (r8, g8, b8)

    def _display_radio_theme_swatches(self, theme_data_x_hex_str):
        if not (self.screenshot_window and self.screenshot_window.winfo_exists()):
            return
        
        if not self.theme_palette_frame or not self.theme_palette_frame.winfo_exists():
            self.theme_palette_frame = ttk.Frame(self.screenshot_window)
            self.theme_palette_frame.pack(pady=self.PAD_SMALL, fill='x')

        for widget in self.theme_palette_frame.winfo_children(): 
            widget.destroy()

        if not theme_data_x_hex_str:
            ttk.Label(self.theme_palette_frame, text="No theme data received.").pack(pady=5)
            return

        theme_title_label = ttk.Label(self.theme_palette_frame, text="Theme Colors", font=('Helvetica', 10, 'bold'), anchor=tk.CENTER)
        theme_title_label.pack(pady=(self.PAD_SMALL, self.PAD_SMALL))

        theme_rgb565_with_indices = []
        raw_theme_colors = [val for val in theme_data_x_hex_str.split('x') if val]
        for i, hex_val_str in enumerate(raw_theme_colors):
            if len(hex_val_str) == 4:
                try:
                    theme_rgb565_with_indices.append({'index': i, 'rgb565': int(hex_val_str, 16), 'hex_str': hex_val_str})
                except ValueError:
                    print(f"App: Invalid theme hex value '{hex_val_str}' at index {i}")
            else:
                print(f"App: Skipping invalid length theme hex value: {hex_val_str}")

        swatch_container = ttk.Frame(self.theme_palette_frame)
        swatch_container.pack() 

        ordered_theme_swatches_to_draw = []
        temp_theme_colors_dict = {tc['rgb565']: [] for tc in theme_rgb565_with_indices}
        for tc in theme_rgb565_with_indices:
            temp_theme_colors_dict[tc['rgb565']].append(tc)
        
        processed_theme_colors = set()

        if hasattr(self, 'last_screenshot_rgb565_palette_order') and self.last_screenshot_rgb565_palette_order:
            for ss_rgb565 in self.last_screenshot_rgb565_palette_order:
                if ss_rgb565 in temp_theme_colors_dict:
                    ordered_theme_swatches_to_draw.extend(temp_theme_colors_dict[ss_rgb565])
                    processed_theme_colors.add(ss_rgb565)
        
        for rgb565_val, entries in temp_theme_colors_dict.items():
            if rgb565_val not in processed_theme_colors:
                ordered_theme_swatches_to_draw.extend(entries)


        color_to_column_frame = {} 
        for theme_entry in ordered_theme_swatches_to_draw[:self.MAX_THEME_SWATCHES]:
            rgb565_int = theme_entry['rgb565']
            original_index = theme_entry['index']
            r8_disp, g8_disp, b8_disp = self._rgb565_to_rgb888(rgb565_int)
            display_hex_color = f"#{r8_disp:02x}{g8_disp:02x}{b8_disp:02x}"
            
            target_column_frame = None
            if rgb565_int not in color_to_column_frame:
                column_frame = ttk.Frame(swatch_container)
                column_frame.pack(side=tk.LEFT, padx=1, anchor=tk.N) 
                color_to_column_frame[rgb565_int] = column_frame
                target_column_frame = column_frame
            else:
                target_column_frame = color_to_column_frame[rgb565_int]

            swatch = tk.Canvas(target_column_frame, width=20, height=20, bg=display_hex_color,
                               highlightthickness=1, highlightbackground='grey')
            swatch.pack(side=tk.TOP, pady=1) 
            Tooltip(swatch, f"RGB565: 0x{rgb565_int:04X}\nIndex: {original_index}")


    def display_screenshot(self, hex_data, transfer_duration=None): 
        local_proc_start_time = time.time()
        image_bytes = b'' 
        pil_image = None 
        self.last_screenshot_rgb565_palette_order = [] 
        
        try:
            if hasattr(self, 'ss_info_label') and self.ss_info_label and self.ss_info_label.winfo_exists():
                self.ss_info_label.destroy()
                self.ss_info_label = None

            try: 
                image_bytes = bytes.fromhex(hex_data)
                if not image_bytes: 
                    messagebox.showerror("Screenshot Error", "No valid image data received after hex conversion.")
                    return 
            except ValueError as e:
                messagebox.showerror("Screenshot HEX Error", f"Invalid HEX data: {e}") 
                return
            
            if image_bytes: 
                try:
                    image_stream = io.BytesIO(image_bytes)
                    pil_image = Image.open(image_stream) 
                except Image.UnidentifiedImageError as e: 
                    messagebox.showerror("Screenshot Image Error", f"Could not identify image from data: {e}")
            else: 
                print("App: Screenshot - image_bytes is empty.")
                return

            if not pil_image: 
                print("App: Screenshot - PIL image is invalid. Window will not be shown.")
                messagebox.showerror("Screenshot Error", "Failed to load image data for display.")
                return 

            if not (hasattr(self, 'screenshot_window') and self.screenshot_window and self.screenshot_window.winfo_exists()):
                self.screenshot_window = tk.Toplevel(self); self.screenshot_window.title("Radio Screenshot")
                self.screenshot_window.resizable(False, True) 
                try: bg_color = self.style.lookup("TFrame", "background")
                except tk.TclError: bg_color = "SystemButtonFace" 
                self.screenshot_window.configure(background=bg_color)
                
                self.ss_image_label = ttk.Label(self.screenshot_window)
                self.ss_image_label.pack(padx=10, pady=10)

                self.ss_palette_outer_frame = ttk.Frame(self.screenshot_window)
                self.ss_palette_outer_frame.pack(pady=self.PAD_SMALL, fill='x')
                
                self.theme_palette_frame = ttk.Frame(self.screenshot_window) 
                self.theme_palette_frame.pack(pady=self.PAD_SMALL, fill='x')

                self.ss_button_frame = ttk.Frame(self.screenshot_window) 
                self.ss_button_frame.pack(pady=10)

                self.ss_refresh_button = ttk.Button(self.ss_button_frame, text="Refresh Screenshot", command=self._refresh_screenshot_command)
                self.ss_refresh_button.pack(side=tk.LEFT, padx=5)
                Tooltip(self.ss_refresh_button, "Request a new screenshot.")

                get_theme_btn = ttk.Button(self.ss_button_frame, text="Get Theme", command=self.request_radio_theme)
                get_theme_btn.pack(side=tk.LEFT, padx=5)
                Tooltip(get_theme_btn, "Fetch and display the radio's current color theme (37 RGB565 colors).")

                save_bmp_button = ttk.Button(self.ss_button_frame, text="Save as BMP", command=lambda data=image_bytes: self.save_screenshot_as_bmp(data))
                save_bmp_button.pack(side=tk.LEFT, padx=5)
                
                self.ss_save_png_button = ttk.Button(self.ss_button_frame, text="Save as PNG") 
                self.ss_save_png_button.pack(side=tk.LEFT, padx=5)
                
                self.screenshot_window.update_idletasks() 
                if self.initial_screenshot_geometry is None: 
                    self.initial_screenshot_geometry = self.screenshot_window.geometry() 
            else: 
                if self.ss_image_label: self.ss_image_label.config(image=None); self.ss_image_label.image = None
                for frame_attr in ['ss_palette_outer_frame', 'theme_palette_frame']: 
                    frame = getattr(self, frame_attr, None)
                    if frame and frame.winfo_exists():
                        for child in frame.winfo_children(): child.destroy()
            
            tk_image = ImageTk.PhotoImage(pil_image)
            self.ss_image_label.config(image=tk_image); self.ss_image_label.image = tk_image 

            if self.ss_palette_outer_frame and self.ss_palette_outer_frame.winfo_exists():
                try:
                    rgb_image = pil_image.convert('RGB')
                    all_colors_data_rgb888 = rgb_image.getcolors(rgb_image.size[0] * rgb_image.size[1])
                    if all_colors_data_rgb888:
                        rgb565_color_counts = {}
                        for count, rgb888_tuple in all_colors_data_rgb888:
                            if isinstance(rgb888_tuple, tuple) and len(rgb888_tuple) == 3:
                                r8, g8, b8 = rgb888_tuple
                                rgb565_val = self._rgb888_to_rgb565(r8, g8, b8)
                                rgb565_color_counts[rgb565_val] = rgb565_color_counts.get(rgb565_val, 0) + count
                        
                        significant_rgb565_colors = [
                            (agg_count, rgb565_val) for rgb565_val, agg_count in rgb565_color_counts.items()
                            if agg_count > self.MIN_COLOR_COUNT_FOR_PALETTE 
                        ]
                        sorted_significant_rgb565 = sorted(significant_rgb565_colors, key=lambda item: item[0], reverse=True)
                        self.last_screenshot_rgb565_palette_order = [item[1] for item in sorted_significant_rgb565] 

                        if sorted_significant_rgb565:
                            palette_inner_frame = ttk.Frame(self.ss_palette_outer_frame) 
                            palette_inner_frame.pack() 
                            for i in range(min(len(sorted_significant_rgb565), self.MAX_SWATCHES_TO_DISPLAY)):
                                agg_count, rgb565_val = sorted_significant_rgb565[i]
                                r8_disp, g8_disp, b8_disp = self._rgb565_to_rgb888(rgb565_val)
                                hex_color_display = f"#{r8_disp:02x}{g8_disp:02x}{b8_disp:02x}"
                                swatch_canvas = tk.Canvas(palette_inner_frame, width=20, height=20, bg=hex_color_display, 
                                                          highlightthickness=1, highlightbackground='grey')
                                swatch_canvas.pack(side=tk.LEFT, padx=1, pady=1)
                                Tooltip(swatch_canvas, f"RGB565: 0x{rgb565_val:04X}\nCount: {agg_count}")
                        else:
                            ttk.Label(self.ss_palette_outer_frame, text=f"No colors with count > {self.MIN_COLOR_COUNT_FOR_PALETTE}.").pack()
                    elif all_colors_data_rgb888 is None: 
                        ttk.Label(self.ss_palette_outer_frame, text="Image has too many distinct colors for palette.").pack()
                    else: 
                        ttk.Label(self.ss_palette_outer_frame, text="No colors found in image.").pack()
                except Exception as e_color:
                    print(f"App: Error generating screenshot color palette: {e_color}")
                    ttk.Label(self.ss_palette_outer_frame, text="Could not generate screenshot palette.").pack()

            if hasattr(self, 'ss_save_png_button'): 
                self.ss_save_png_button.config(command=lambda img=pil_image: self.save_screenshot_as_png(img))
            
            if self.screenshot_window and self.screenshot_window.winfo_exists(): 
                self.screenshot_window.lift() 

        except Exception as e: 
            print(f"App: General exception in display_screenshot: {e}")
            messagebox.showerror("Screenshot Error", f"Failed to display screenshot: {e}")
        finally:
            if hasattr(self, 'screenshot_btn'):
                self.screenshot_btn.config(text=self.SCREENSHOT_EMOJI)
            if hasattr(self, 'ss_refresh_button') and self.ss_refresh_button and self.ss_refresh_button.winfo_exists():
                self.ss_refresh_button.config(state=tk.NORMAL, text="Refresh Screenshot")
            if hasattr(self, 'ss_info_label') and self.ss_info_label and self.ss_info_label.winfo_exists():
                self.ss_info_label.destroy() 
                self.ss_info_label = None
            self.set_control_buttons_state(tk.NORMAL if self.connected else tk.DISABLED)


    def save_screenshot_as_png(self, pil_image_to_save): 
        if not pil_image_to_save: messagebox.showerror("Save Error", "No image data to save as PNG."); return
        file_path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG files", "*.png"), ("All files", "*.*")], title="Save Screenshot As PNG")
        if file_path:
            try: pil_image_to_save.save(file_path, "PNG"); messagebox.showinfo("Save Successful", f"Screenshot saved to:\n{file_path}")
            except Exception as e: messagebox.showerror("Save Error", f"Failed to save screenshot as PNG: {e}")

    def save_screenshot_as_bmp(self, raw_bmp_data):
        if not raw_bmp_data: messagebox.showerror("Save Error", "No raw BMP data to save."); return
        file_path = filedialog.asksaveasfilename(defaultextension=".bmp", filetypes=[("BMP files", "*.bmp"), ("All files", "*.*")], title="Save Screenshot As BMP")
        if file_path:
            try:
                with open(file_path, 'wb') as f:
                    f.write(raw_bmp_data)
                messagebox.showinfo("Save Successful", f"Screenshot saved to:\n{file_path}")
            except Exception as e: messagebox.showerror("Save Error", f"Failed to save screenshot as BMP: {e}")


    def send_radio_command(self, command): 
        if self.connected: self.controller.send_command(command)
        else: messagebox.showwarning("Not Connected", "Connect to the radio to send commands.")

    def send_encoder_command(self, command, angle_change):
        """Helper to send encoder command and update knob angle."""
        if self.connected:
            self.controller.send_command(command)
            self.knob_angle_degrees = (self.knob_angle_degrees + angle_change + 360) % 360
            self._draw_knob()
        else:
            messagebox.showwarning("Not Connected", "Connect to the radio to send commands.")


    def toggle_console(self): 
        self.console_visible = self.console_var.get()
        if self.console_visible:
            self.console_frame.grid(row=7, column=0, columnspan=2, padx=self.PAD_X_MAIN, pady=(self.PAD_Y_CONN, self.PAD_Y_MAIN), sticky="nsew") 
            self.console.config(state=tk.NORMAL) 
            self.main_layout_frame.grid_rowconfigure(7, weight=2) 
        else: 
            self.console_frame.grid_forget()
            self.main_layout_frame.grid_rowconfigure(7, weight=0) 
        

    def auto_detect_port(self): 
        ports = serial.tools.list_ports.comports(); current_port_val = self.port_var.get()
        if current_port_val and any(p.device == current_port_val for p in ports): return
        selected_port = None
        for port in ports:
            desc = (port.description or "").upper(); name = (port.name or "").upper()
            if any(sub in desc or sub in name for sub in ["CH340", "CP210", "FTDI", "USB SERIAL", "SERIAL", "ACM", "USB-SERIAL"]):
                selected_port = port.device; break
        if selected_port: self.port_var.set(selected_port)
        elif ports: self.port_var.set(ports[0].device)
        else: self.port_var.set("")

    def refresh_ports(self): 
        ports_info = serial.tools.list_ports.comports(); port_devices = [p.device for p in ports_info]
        current_selection = self.port_var.get()
        if hasattr(self, 'port_combo'):
            self.port_combo['values'] = port_devices
        
        if current_selection and current_selection in port_devices: 
            self.port_var.set(current_selection)
        elif port_devices: 
            self.auto_detect_port()
            if not self.port_var.get(): 
                self.port_var.set(port_devices[0])
        else: 
            self.port_var.set("")
            messagebox.showinfo("Ports", "No COM ports found.")
        if hasattr(self, 'connect_btn'): 
            self.connect_btn.config(state=tk.NORMAL if self.port_var.get() else tk.DISABLED)


    def clear_status_labels(self): 
        self.freq_var.set("Frequency: --"); self.agc_status_var.set("Gain Control: --"); self.rssi_var.set("RSSI: --"); self.snr_var.set("SNR: --")
        self.batt_var.set("Battery: --"); self.fw_var.set("Firmware: --"); self.vol_var.set("Vol: --"); self.band_var.set("Band: --")
        self.mode_var.set("Mode: --"); self.step_var.set("Step: --"); self.bw_var.set("BW: --"); self.agc_var.set("AGC: --")
        self.bl_var.set("Bright: --"); self.cal_var.set("Cal: --")
        if hasattr(self, 'snr_level_indicator'): 
            self.snr_level_indicator.delete("all")
            self.snr_level_indicator.create_oval(0,0,10,10, fill="grey", outline="grey")


    def set_control_buttons_state(self, state): 
        general_button_state = state if self.connected else tk.DISABLED

        if hasattr(self, 'sleep_btn'): self.sleep_btn.config(state=general_button_state)
        
        if hasattr(self, 'screenshot_btn'):
            if self.fm_scan_active or not self.connected or self.controller.expecting_screenshot_data:
                self.screenshot_btn.config(state=tk.DISABLED)
            else:
                self.screenshot_btn.config(state=tk.NORMAL, text=self.SCREENSHOT_EMOJI)

        if hasattr(self, 'memory_btn'): self.memory_btn.config(state=general_button_state)
        
        for button in self.ctrl_frame_buttons: button.config(state=general_button_state)
        for button in self.encoder_click_buttons: button.config(state=general_button_state) 
        
        self._update_fm_scan_button_state()
        if not self.controller.expecting_screenshot_data and \
           not self.controller.expecting_memory_slots and \
           not self.controller.expecting_theme_string and \
           not self.fm_scan_active:
            self.special_op_active_for_blink = False


    def handle_forced_disconnect(self, error_message): 
        if self.connected: 
            print(f"Forced disconnect due to: {error_message}")
            if self.fm_scan_active: 
                self.fm_scan_stop_requested = True
                self._fm_scan_complete("Connection Lost", original_states=None) 
            messagebox.showerror("Connection Lost", f"Disconnected from radio due to serial error:\n{error_message}\nPlease check the connection and try again.")
            self.controller.disconnect() 
            self.connected = False
            self.controller.data_received = False 
            self.clear_status_labels()
            self.set_control_buttons_state(tk.DISABLED)
            if hasattr(self, 'sleep_btn'): self.sleep_btn.config(text="Sleep")
            self.controller.sleep_mode = False
            self.update_status_indicator()


    def toggle_connection(self): 
        if self.connected:
            print("User initiated disconnect.")
            if self.fm_scan_active: 
                self.fm_scan_stop_requested = True
                self._fm_scan_complete("Disconnected", original_states=None) 
            self.controller.disconnect() 
            self.connected = False; self.controller.data_received = False
            self.clear_status_labels(); self.set_control_buttons_state(tk.DISABLED)
            if hasattr(self, 'sleep_btn'): self.sleep_btn.config(text="Sleep"); 
            self.controller.sleep_mode = False
        else:
            selected_port = self.port_var.get(); selected_baud = self.baud_var.get()
            if not selected_baud: messagebox.showwarning("Connection", "Please select a baud rate."); return
            if selected_port:
                if self.controller.connect(selected_port, selected_baud): self.connected = True; self.set_control_buttons_state(tk.NORMAL)
                else: self.connected = False; self.set_control_buttons_state(tk.DISABLED) 
            else: messagebox.showwarning("Connection", "Please select a valid COM port.")
        self.update_status_indicator()
        self._update_fm_scan_button_state()


    def toggle_sleep(self): 
        if not self.connected: messagebox.showwarning("Not Connected", "Connect to the radio first."); return
        if self.fm_scan_active: messagebox.showwarning("Scan Active", "Cannot change sleep mode during FM scan."); return
        if self.controller.sleep_mode: self.controller.send_command(CMD_SLEEP_OFF); self.controller.sleep_mode = False; self.sleep_btn.config(text="Sleep")
        else: self.controller.send_command(CMD_SLEEP_ON); self.controller.sleep_mode = True; self.sleep_btn.config(text="Wake")

    def _trigger_heartbeat_blink(self):
        if self.indicator_blink_after_id:
            self.after_cancel(self.indicator_blink_after_id)
            self.indicator_blink_after_id = None
        
        if hasattr(self, 'connection_status_canvas') and self.connection_status_canvas.winfo_exists():
            blink_color = "green" 
            on_duration = 250 
            if self.special_op_active_for_blink:
                blink_color = "#00E000" 
                on_duration = 100 
            
            self.connection_status_canvas.itemconfig("status_oval", fill=blink_color, outline=blink_color) 
            self.indicator_blink_after_id = self.after(on_duration, self._reset_heartbeat_color)

    def _reset_heartbeat_color(self):
        self.indicator_blink_after_id = None
        self.update_status_indicator() 


    def update_status_indicator(self): 
        color = "red"; 
        if self.connected: 
            color = "green" if self.controller.data_received else "yellow"
        
        try: bg_color = self.style.lookup("TFrame", "background")
        except tk.TclError: bg_color = "SystemButtonFace" 
        
        if hasattr(self, 'connection_status_canvas'): 
            self.connection_status_canvas.configure(background=bg_color)
            self.connection_status_canvas.delete("status_oval") 
            self.connection_status_canvas.create_oval(2, 2, 18, 18, fill=color, outline=color, tags="status_oval")
        
        if hasattr(self, 'connect_btn'): 
            self.connect_btn.config(text="Disconnect" if self.connected else "Connect")
            if not self.connected: self.connect_btn.config(state=tk.NORMAL if self.port_var.get() else tk.DISABLED)

    def value_to_percentage(self, value, max_value): 
        if max_value == 0: return 0
        return min(self.PERCENTAGE_MULTIPLIER, max(0, round((value / max_value) * self.PERCENTAGE_MULTIPLIER)))

    def voltage_to_percentage(self, voltage): 
        clamped_v = max(self.MIN_BATTERY_VOLTAGE, min(voltage, self.MAX_BATTERY_VOLTAGE))
        denom = self.MAX_BATTERY_VOLTAGE - self.MIN_BATTERY_VOLTAGE
        if denom == 0: return self.PERCENTAGE_MULTIPLIER if clamped_v >= self.MAX_BATTERY_VOLTAGE else 0
        return max(0, min(self.PERCENTAGE_MULTIPLIER, round(((clamped_v - self.MIN_BATTERY_VOLTAGE) / denom) * self.PERCENTAGE_MULTIPLIER)))

    def format_firmware_version(self, v): return f"v{v // 100}.{v % 100:02d}"
    def format_agc_status_display(self, agc_idx): 
        if agc_idx == 0: return ("AGC: On", "Gain Control: Auto (AGC On)")
        return (f"Att: {agc_idx -1}", f"Gain Control: Manual (Att: {agc_idx -1}dB)")
    def format_calibration_display(self, cal): return "Cal: None" if cal == 0 else f"Cal: {cal:+} Hz"

    def _update_fm_scan_button_state(self):
        if not hasattr(self, 'fm_scan_button') or not hasattr(self, 'fm_scan_stop_button'):
            return 

        is_fm_mode = "fm" in self.mode_var.get().lower()
        
        if self.fm_scan_active:
            self.fm_scan_button.pack_forget()
            self.fm_scan_stop_button.pack(side=tk.LEFT, padx=self.PAD_SMALL)
            self.fm_scan_stop_button.config(state=tk.NORMAL)
        else:
            self.fm_scan_stop_button.pack_forget()
            self.fm_scan_button.pack(side=tk.LEFT, padx=self.PAD_SMALL)
            if self.connected and is_fm_mode:
                self.fm_scan_button.config(state=tk.NORMAL)
            else:
                self.fm_scan_button.config(state=tk.DISABLED)

    def start_fm_scan(self):
        if not self.connected:
            messagebox.showwarning("Not Connected", "Connect to the radio to start FM scan.")
            return
        if "fm" not in self.mode_var.get().lower():
            messagebox.showwarning("Incorrect Mode", "FM Scan is only available in FM mode.")
            return
        if self.fm_scan_active:
            messagebox.showinfo("Scan Active", "An FM scan is already in progress.")
            return

        self.fm_scan_active = True
        self.special_op_active_for_blink = True 
        self.fm_scan_stop_requested = False
        self.fm_scan_results = []
        self.fm_scan_start_time = time.monotonic() 
        self.fm_scan_progress_var.set("Scanning: Initializing...") 
        self._update_fm_scan_button_state()
        
        original_states = {}
        controls_to_disable = self.ctrl_frame_buttons + self.encoder_click_buttons
        if hasattr(self, 'screenshot_btn'): controls_to_disable.append(self.screenshot_btn)
        if hasattr(self, 'memory_btn'): controls_to_disable.append(self.memory_btn)
        if hasattr(self, 'sleep_btn'): controls_to_disable.append(self.sleep_btn)
        
        for ctrl in controls_to_disable:
            if hasattr(ctrl, 'cget'): 
                 original_states[ctrl] = ctrl.cget('state')
                 ctrl.config(state=tk.DISABLED)


        threading.Thread(target=self._perform_fm_scan, args=(original_states,), daemon=True).start()

    def stop_fm_scan(self):
        if self.fm_scan_active:
            self.fm_scan_stop_requested = True
            self.fm_scan_progress_var.set("Stopping scan...")
            print("App: FM Scan stop requested.")
        

    def _restore_controls_after_action(self, original_states):
        print("App: Restoring controls after action.")
        if original_states:
            for ctrl, state in original_states.items():
                if hasattr(ctrl, 'winfo_exists') and ctrl.winfo_exists():
                    ctrl.config(state=state if self.connected else tk.DISABLED)
        else: 
            self.set_control_buttons_state(tk.NORMAL if self.connected else tk.DISABLED)
        self._update_fm_scan_button_state()

    def _save_scan_results_to_file(self, text_widget_content):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Save FM Scan Results"
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(text_widget_content)
                messagebox.showinfo("Save Successful", f"Scan results saved to:\n{file_path}")
            except Exception as e:
                messagebox.showerror("Save Error", f"Failed to save scan results: {e}")

    def _fm_scan_complete(self, reason="Completed", original_states=None):
        self.fm_scan_active = False 
        self.special_op_active_for_blink = False 
        self.fm_scan_progress_var.set("") 
        scan_duration = time.monotonic() - self.fm_scan_start_time
        
        total_frequencies_scanned = len(self.fm_scan_results)
        
        results_text_content = f"--- FM Scan {reason} ({total_frequencies_scanned} freqs in {scan_duration:.2f}s) ---\n"
        results_text_content += f"--- Results (SNR >= {self.current_fm_scan_snr_threshold}, sorted by SNR) ---\n"

        if reason == "Completed" or reason == "Max steps reached":
            significant_stations = [res for res in self.fm_scan_results if res['snr'] is not None and res['snr'] >= self.current_fm_scan_snr_threshold]
            sorted_stations = sorted(significant_stations, key=lambda x: x['snr'], reverse=True)
            
            if sorted_stations:
                for station in sorted_stations:
                    results_text_content += f"  {station['freq']}, SNR: {station['snr']}\n"
            else:
                results_text_content += "  No stations found meeting the SNR threshold.\n"
        
        elif self.fm_scan_results: 
            results_text_content = f"--- Interrupted FM Scan Results ({total_frequencies_scanned} freqs in {scan_duration:.2f}s) ---\n"
            for station in self.fm_scan_results:
                 results_text_content += f"  {station['freq']}, SNR: {station['snr']}\n"
        
        results_text_content += "--- End of FM Scan Results ---"
        # print(results_text_content) # Removed to avoid duplicate output

        results_window = tk.Toplevel(self)
        results_window.title("FM Scan Results")
        results_window.geometry("400x300")
        
        text_area = scrolledtext.ScrolledText(results_window, wrap=tk.WORD, height=15, width=50)
        text_area.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
        text_area.insert(tk.END, results_text_content)
        text_area.config(state=tk.DISABLED) 

        save_button = ttk.Button(results_window, text="Save Results", 
                                 command=lambda: self._save_scan_results_to_file(text_area.get("1.0", tk.END)))
        save_button.pack(pady=5)
        results_window.lift()
        
        if reason != "Completed" and hasattr(self, 'scan_cycle_start_freq_str') and self.scan_cycle_start_freq_str:
            if self.freq_var.get() != self.scan_cycle_start_freq_str:
                self.after(100, lambda sf=self.scan_cycle_start_freq_str, os=original_states, r=reason: self._initiate_tune_back(sf, os, r))
            else: 
                self._restore_controls_after_action(original_states)
        else: 
            self._restore_controls_after_action(original_states)
        
        self.fm_scan_results = [] 
        self.scan_cycle_start_freq_str = ""


    def _perform_fm_scan(self, original_states): 
        print("App: Starting FM Scan thread.")
        self.after(0, lambda: self.fm_scan_progress_var.set("Scanning: Setting step..."))
        
        target_step_str_short = self.FM_SCAN_TARGET_STEP_STR.lower()
        step_set_success = False
        
        for attempt in range(15): 
            if self.fm_scan_stop_requested:
                self.after(0, lambda os=original_states: self._fm_scan_complete("Stopped", os))
                return

            current_step_full_text = self.step_var.get()
            step_match = re.search(r'Step:\s*(\S+)', current_step_full_text, re.IGNORECASE)
            current_step_val_str = ""
            if step_match:
                current_step_val_str = step_match.group(1).lower()

            if target_step_str_short == current_step_val_str:
                print(f"App: FM Scan step is correctly '{current_step_full_text}'.")
                step_set_success = True
                break
            else:
                print(f"App: Current step is '{current_step_full_text}', attempting to set to '{target_step_str_short}'.")
                cmd_to_send = None
                num_commands = 0
                if current_step_val_str in self.FM_STEP_CYCLE_STRINGS:
                    try:
                        current_idx = self.FM_STEP_CYCLE_STRINGS.index(current_step_val_str)
                        target_idx = self.FM_STEP_CYCLE_STRINGS.index(target_step_str_short)
                        cycle_len = len(self.FM_STEP_CYCLE_STRINGS)
                        
                        fwd_steps = (target_idx - current_idx + cycle_len) % cycle_len
                        bwd_steps = (current_idx - target_idx + cycle_len) % cycle_len

                        if fwd_steps <= bwd_steps:
                            cmd_to_send = CMD_STEP_NEXT
                            num_commands = fwd_steps
                        else:
                            cmd_to_send = CMD_STEP_PREV
                            num_commands = bwd_steps
                    except ValueError: 
                        print(f"App: Error finding step '{current_step_val_str}' in cycle. Defaulting to CMD_STEP_NEXT.")
                        cmd_to_send = CMD_STEP_NEXT
                        num_commands = 1
                else: 
                    cmd_to_send = CMD_STEP_NEXT
                    num_commands = 1
                
                for _ in range(num_commands):
                    if self.fm_scan_stop_requested: self.after(0, lambda os=original_states: self._fm_scan_complete("Stopped", os)); return
                    self.controller.send_command(cmd_to_send)
                    time.sleep(0.2) 
                time.sleep(0.3) 

        if not step_set_success:
            print(f"App: Failed to set step to '{target_step_str_short}' for FM scan after {attempt+1} attempts.")
            self.after(0, lambda: messagebox.showerror("FM Scan Error", f"Could not set tuning step to '{target_step_str_short}'."))
            self.after(0, lambda os=original_states: self._fm_scan_complete("Error", os))
            return

        time.sleep(0.1) 
        self.scan_cycle_start_freq_str = self.freq_var.get() 
        start_freq_mhz_match = re.search(r'(\d+\.?\d*)\s*MHz', self.scan_cycle_start_freq_str, re.IGNORECASE)
        if not start_freq_mhz_match:
            print(f"App: Could not parse numeric start frequency for scan: {self.scan_cycle_start_freq_str}")
            self.after(0, lambda os=original_states: self._fm_scan_complete("Error", os))
            return
        self.scan_cycle_start_freq_mhz = float(start_freq_mhz_match.group(1))
        print(f"App: FM Scan cycle starting point: {self.scan_cycle_start_freq_str} ({self.scan_cycle_start_freq_mhz:.2f} MHz)")
        
        self.fm_scan_results = []
        
        current_freq_str_for_log = self.scan_cycle_start_freq_str
        snr_str = self.snr_var.get() 
        snr_val = None
        snr_match = re.search(r'(-?\d+)\s*dB', snr_str) 
        if snr_match:
            snr_val = int(snr_match.group(1))
        self.fm_scan_results.append({'freq': current_freq_str_for_log, 'snr': snr_val})
        self.after(0, lambda f=current_freq_str_for_log: self.fm_scan_progress_var.set(f"Scanning: {f.replace('Frequency: ', '')}"))
        if self.console_visible:
            self.after(0, lambda f=current_freq_str_for_log, s=snr_val: self.console.insert(tk.END, f"Scan (Initial): {f}, SNR: {s}\n"))
            self.after(0, lambda: self.console.see(tk.END))
        
        last_recorded_freq_str = current_freq_str_for_log
        has_moved_from_start = False 
        steps_taken = 0

        while steps_taken < self.FM_SCAN_MAX_STEPS:
            if self.fm_scan_stop_requested: break
            
            freq_before_tune_cmd = self.freq_var.get() 
            self.send_encoder_command(CMD_ENCODER_UP, 18) 
            
            time.sleep(self.current_scan_dwell_time / 2) 
            wait_attempts = 0
            max_wait_attempts = int((self.current_scan_dwell_time / 2) / 0.05) + 2 
            freq_has_changed_in_step = False

            for _ in range(max_wait_attempts):
                if self.fm_scan_stop_requested: break
                time.sleep(0.05) 
                new_current_freq_str_after_tune = self.freq_var.get()
                if new_current_freq_str_after_tune != freq_before_tune_cmd:
                    freq_has_changed_in_step = True
                    break 
            
            if self.fm_scan_stop_requested: break

            new_current_freq_str = self.freq_var.get()

            if not freq_has_changed_in_step and new_current_freq_str == freq_before_tune_cmd :
                 if last_recorded_freq_str == new_current_freq_str: 
                    print(f"App: Scan - Freq did not change from {freq_before_tune_cmd} after tune cmd and dwell. Step: {steps_taken+1}.")
                 steps_taken += 1
                 continue


            new_freq_mhz_match = re.search(r'(\d+\.?\d*)\s*MHz', new_current_freq_str, re.IGNORECASE)
            if not new_freq_mhz_match:
                print(f"App: Could not parse current frequency during scan: {new_current_freq_str}. Stopping scan.")
                break 
            new_current_freq_mhz = float(new_freq_mhz_match.group(1))

            if not has_moved_from_start and abs(new_current_freq_mhz - self.scan_cycle_start_freq_mhz) > 0.01:
                has_moved_from_start = True
            
            if has_moved_from_start and abs(new_current_freq_mhz - self.scan_cycle_start_freq_mhz) < 0.01:
                print(f"App: FM Scan completed a full cycle, returning to start frequency ({new_current_freq_str}).")
                break 
            
            snr_str = self.snr_var.get()
            snr_val = None
            snr_match = re.search(r'(-?\d+)\s*dB', snr_str)
            if snr_match: snr_val = int(snr_match.group(1))
            
            if last_recorded_freq_str != new_current_freq_str:
                self.fm_scan_results.append({'freq': new_current_freq_str, 'snr': snr_val})
                last_recorded_freq_str = new_current_freq_str 
                self.after(0, lambda f=new_current_freq_str: self.fm_scan_progress_var.set(f"Scanning: {f.replace('Frequency: ', '')}"))
                if self.console_visible:
                     self.after(0, lambda f=new_current_freq_str, s=snr_val: self.console.insert(tk.END, f"Scan: {f}, SNR: {s}\n"))
                     self.after(0, lambda: self.console.see(tk.END))
            
            steps_taken += 1
        
        completion_reason = "Completed"
        if self.fm_scan_stop_requested:
            completion_reason = "Stopped by user"
        elif steps_taken >= self.FM_SCAN_MAX_STEPS:
            print("App: FM Scan reached maximum steps. Stopping.")
            completion_reason = "Max steps reached"
        
        self.after(0, lambda reason=completion_reason, os=original_states: self._fm_scan_complete(reason, os))


    def _initiate_tune_back(self, target_freq_str, original_states, scan_completion_reason):
        if not self.connected: 
            self._restore_controls_after_action(original_states) 
            return

        print(f"App: Initiating tune-back to {target_freq_str}")
        
        target_freq_mhz_match = re.search(r'(\d+\.?\d*)\s*MHz', target_freq_str, re.IGNORECASE)
        if not target_freq_mhz_match:
            print(f"App: Could not parse target frequency for tune back: {target_freq_str}")
            self._restore_controls_after_action(original_states) 
            return
        
        target_mhz = float(target_freq_mhz_match.group(1))
        
        for ctrl_key in original_states: 
            if hasattr(ctrl_key, 'winfo_exists') and ctrl_key.winfo_exists():
                ctrl_key.config(state=tk.DISABLED)
        self._update_fm_scan_button_state() 

        threading.Thread(target=self._tune_radio_to_frequency_step_thread, 
                         args=(target_mhz, target_freq_str, original_states), 
                         daemon=True).start()

    def _tune_radio_to_frequency_step_thread(self, target_mhz, target_freq_str, original_states):
        print(f"App: Tune-back thread started for {target_freq_str}.")
        max_tune_attempts = 40  
        attempts = 0
        tuned_successfully = False

        while attempts < max_tune_attempts:
            if not self.connected: break 

            current_freq_str_in_tune_back = self.freq_var.get() 
            
            if current_freq_str_in_tune_back == target_freq_str:
                print(f"App: Successfully tuned back to {target_freq_str} (exact string match).")
                tuned_successfully = True
                break

            current_freq_mhz_match_tune = re.search(r'(\d+\.?\d*)\s*MHz', current_freq_str_in_tune_back, re.IGNORECASE)
            if target_mhz and current_freq_mhz_match_tune:
                current_mhz_tune = float(current_freq_mhz_match_tune.group(1))
                if abs(current_mhz_tune - target_mhz) < 0.06: 
                    print(f"App: Successfully tuned back near {target_mhz:.2f} MHz (current: {current_mhz_tune:.2f} MHz).")
                    tuned_successfully = True
                    break
                
                command_to_send = CMD_ENCODER_UP if current_mhz_tune < target_mhz else CMD_ENCODER_DOWN
                self.send_encoder_command(command_to_send, 18 if command_to_send == CMD_ENCODER_UP else -18) 
            else:
                 print(f"App: Tune back: Cannot parse current freq '{current_freq_str_in_tune_back}'. Stopping tune back.")
                 break 
            
            time.sleep(0.25) 
            attempts += 1
        
        if not tuned_successfully:
            print(f"App: Tune-back to {target_freq_str} may not be exact after {max_tune_attempts} attempts. Current: {self.freq_var.get()}")

        self.after(0, self._restore_controls_after_action, original_states)


    def process_serial_queue(self):
        try:
            if not self.controller.data_queue.empty() and self.console_visible:
                 self._trigger_heartbeat_blink() 

            while not self.controller.data_queue.empty():
                queue_item = self.controller.data_queue.get_nowait()
                if isinstance(queue_item, tuple) and len(queue_item) == 2:
                    item_type, item_data = queue_item
                    if item_type == 'screenshot_data':
                        hex_data, transfer_duration = item_data 
                        self.display_screenshot(hex_data, transfer_duration); continue 
                    elif item_type == 'screenshot_error':
                        messagebox.showerror("Screenshot Error", item_data)
                        if self.console_visible: self.console.insert(tk.END, f"Screenshot error: {item_data}\n")
                        if hasattr(self, 'screenshot_btn'):
                            self.screenshot_btn.config(text=self.SCREENSHOT_EMOJI)
                        self.set_control_buttons_state(tk.NORMAL if self.connected else tk.DISABLED)
                        continue
                    elif item_type == 'serial_error_disconnect': 
                        self.handle_forced_disconnect(item_data); continue
                    elif item_type == 'memory_slots_data':
                        for i in range(32): self.memory_slots_data[i].update({'band': '', 'freq_hz': '', 'mode': ''})
                        for line in item_data:
                            match = RadioController.MEMORY_SLOT_PATTERN.match(line.strip()) 
                            if match:
                                try:
                                    slot_num_str, band_val, freq_val, mode_val = [g.strip() for g in match.groups()]
                                    slot_idx = int(slot_num_str) -1 
                                    if 0 <= slot_idx < 32: self.memory_slots_data[slot_idx].update({'band': band_val, 'freq_hz': freq_val, 'mode': mode_val})
                                except (ValueError, IndexError) as e: print(f"App: Error parsing slot line '{line}': {e}")
                        
                        if self.waiting_for_memory_data_to_build_viewer:
                            self._build_and_show_memory_viewer(); self.waiting_for_memory_data_to_build_viewer = False
                        else: self.update_memory_viewer_display()
                        if self.console_visible: self.console.insert(tk.END, "Memory slots updated.\n")
                        self.special_op_active_for_blink = False 
                        self.set_control_buttons_state(tk.NORMAL if self.connected else tk.DISABLED)
                        continue
                    elif item_type == 'memory_slots_error':
                        messagebox.showerror("Memory Slot Error", item_data)
                        if self.console_visible: self.console.insert(tk.END, f"Memory slot error: {item_data}\n")
                        self.special_op_active_for_blink = False 
                        self.set_control_buttons_state(tk.NORMAL if self.connected else tk.DISABLED)
                        continue
                    elif item_type == 'theme_data':
                        self._display_radio_theme_swatches(item_data)
                        self.special_op_active_for_blink = False 
                        self.set_control_buttons_state(tk.NORMAL if self.connected else tk.DISABLED)
                        continue
                    elif item_type == 'theme_data_error':
                        messagebox.showerror("Theme Error", item_data)
                        if self.console_visible: self.console.insert(tk.END, f"Theme data error: {item_data}\n")
                        if self.screenshot_window and self.screenshot_window.winfo_exists() and self.theme_palette_frame:
                             for widget in self.theme_palette_frame.winfo_children(): widget.destroy()
                             if hasattr(self.theme_palette_frame, 'loading_label'): delattr(self.theme_palette_frame, 'loading_label')
                             error_label = ttk.Label(self.theme_palette_frame, text=item_data)
                             error_label.pack(pady=5)
                        self.special_op_active_for_blink = False 
                        self.set_control_buttons_state(tk.NORMAL if self.connected else tk.DISABLED)
                        continue


                data_line = str(queue_item) 
                if self.console_visible and self.console.winfo_exists(): self.console.insert(tk.END, data_line + '\n'); self.console.see(tk.END)
                if RadioController.DATA_LOG_PATTERN.match(data_line): 
                    params = data_line.split(',')
                    if len(params) >= 15: 
                        try:
                            app_v=int(params[0]); raw_f=int(params[1]); bfo=int(params[2]); cal=int(params[3]); band=params[4].strip(); mode=params[5].strip() 
                            step=params[6].strip(); bw=params[7].strip(); agc=int(params[8]); vol=int(params[9]); rssi=int(params[10]); snr=int(params[11]); volt=float(params[13])
                            
                            old_mode_val = self.mode_var.get() 
                            self.mode_var.set(f"Mode: {mode}")
                            if old_mode_val != self.mode_var.get(): 
                                self._update_fm_scan_button_state()

                            self.step_var.set(f"Step: {step}") 

                            if mode in ['LSB','USB']: self.freq_var.set(f"Frequency: {(raw_f*1000+bfo)/1000.0:.3f} kHz")
                            elif mode=='FM': self.freq_var.set(f"Frequency: {raw_f/100.0:.2f} MHz")
                            else: self.freq_var.set(f"Frequency: {raw_f} kHz")
                            agc_s,agc_l=self.format_agc_status_display(agc); self.agc_var.set(agc_s); self.agc_status_var.set(agc_l)
                            self.vol_var.set(f"Vol: {vol} ({self.value_to_percentage(vol,self.MAX_VOLUME)}%)")
                            self.band_var.set(f"Band: {band}"); 
                            self.bw_var.set(f"BW: {bw}")
                            self.cal_var.set(self.format_calibration_display(cal)); self.rssi_var.set(f"RSSI: {rssi} dBuV"); self.snr_var.set(f"SNR: {snr} dB")
                            self.batt_var.set(f"Battery: {volt:.2f}V ({self.voltage_to_percentage(volt)}%)")
                            self.fw_var.set(f"Firmware: {self.format_firmware_version(app_v)}") 
                            if not self.controller.data_received: self.controller.data_received=True; self.update_status_indicator()
                            self._update_snr_indicator() 
                        except (ValueError,IndexError) as e: 
                            log_msg=f"App: Data parsing error for log line: '{data_line}' - {e}\n"; print(log_msg.strip()) 
                            if self.console_visible and self.console.winfo_exists(): self.console.insert(tk.END, log_msg)
                        except Exception as e: 
                            log_msg=f"App: Unexpected error processing log line: '{data_line}' - {e}\n"; print(log_msg.strip())
                            if self.console_visible and self.console.winfo_exists(): self.console.insert(tk.END, log_msg)
                    else: 
                        log_msg=f"App: Line matched DATA_LOG_PATTERN but had {len(params)} fields: '{data_line}'\n"; print(log_msg.strip())
                        if self.console_visible and self.console.winfo_exists(): self.console.insert(tk.END, log_msg)

        except queue.Empty: pass
        finally:
            if self.winfo_exists(): self.after(100, lambda: self.process_serial_queue())

if __name__ == "__main__":
    app = RadioApp()
    app.mainloop()
