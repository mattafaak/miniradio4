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
    SCREENSHOT_DATA_INACTIVITY_TIMEOUT = 2.0 
    MEMORY_DATA_INACTIVITY_TIMEOUT = 1.2 
    MEMORY_SLOT_PATTERN = re.compile(r"^#?\s*(\d{1,2})\s*,\s*([^,]*?)\s*,\s*(\d+)\s*,\s*([^,]*?)\s*$")
    DATA_LOG_PATTERN = re.compile(r"^\s*\d+\s*(?:,\s*[^,]*\s*){14}$")


    def __init__(self):
        self.ser = None; self.running = False
        self.data_queue = queue.Queue(); self.data_received = False
        self.sleep_mode = False
        self.expecting_screenshot_data = False; self.screenshot_hex_buffer = ""; self.last_screenshot_hex_byte_time = 0 
        self.screenshot_request_time = 0 
        self.expecting_memory_slots = False; self.memory_slots_buffer = []; self.last_memory_slot_time = 0
        self.expecting_theme_string = False 
        self.log_is_on_before_special_op = False 
        self.line_assembly_buffer_bytes = b"" 

    def connect(self, port, baudrate=115200):
        try:
            print(f"Attempting to connect to {port} at {baudrate} baud.")
            self.ser = Serial(port, int(baudrate), timeout=0.1) 
            self.running = True; self.data_received = False
            self.expecting_screenshot_data = False; self.screenshot_hex_buffer = ""; self.last_screenshot_hex_byte_time = 0
            self.screenshot_request_time = 0
            self.expecting_memory_slots = False; self.memory_slots_buffer = []; self.last_memory_slot_time = 0
            self.expecting_theme_string = False
            self.line_assembly_buffer_bytes = b"" 
            threading.Thread(target=self.read_serial, daemon=True).start()
            self.send_command(CMD_TOGGLE_LOG, is_user_toggle=True) 
            return True
        except ValueError: messagebox.showerror("Baud Rate Error", f"Invalid baud rate: {baudrate}."); return False
        except SerialException as e: messagebox.showerror("Connection Error", f"Failed to connect to {port} at {baudrate} baud: {str(e)}"); return False
        except Exception as e: messagebox.showerror("Error", f"An unexpected error during connection: {str(e)}"); return False

    def disconnect(self):
        self.running = False; time.sleep(0.05) 
        if self.ser and self.ser.is_open: self.ser.close(); print("Serial port closed by disconnect().")
        self.data_received = False; self.expecting_screenshot_data = False; self.expecting_memory_slots = False; self.expecting_theme_string = False
        self.screenshot_hex_buffer = ""; self.memory_slots_buffer = []
        self.last_screenshot_hex_byte_time = 0; self.last_memory_slot_time = 0
        self.line_assembly_buffer_bytes = b""

    def _send_raw_command(self, cmd_char):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(cmd_char.encode() + b'\n')
            except Exception as e:
                print(f"Controller: Error sending raw command '{cmd_char}': {e}")

    def send_command(self, cmd, is_user_toggle=False, is_theme_preview=False, theme_string=""):
        if not (self.ser and self.ser.is_open):
            if cmd in [CMD_SCREENSHOT, CMD_SHOW_MEM, CMD_THEME_EDITOR_TOGGLE, CMD_THEME_GET] or is_theme_preview: 
                messagebox.showwarning("Not Connected", "Connect to radio first.")
            return
        try:
            if cmd in [CMD_SCREENSHOT, CMD_SHOW_MEM, CMD_THEME_GET] or is_theme_preview:
                if self.log_is_on_before_special_op: 
                    self._send_raw_command(CMD_TOGGLE_LOG); time.sleep(0.05) 
            
            if cmd == CMD_SCREENSHOT:
                print("Ctrl: Screenshot requested."); self.expecting_screenshot_data = True
                self.screenshot_hex_buffer = ""; self.last_screenshot_hex_byte_time = time.time() 
                self.screenshot_request_time = time.time() 
                self.ser.write(cmd.encode() + b'\n')
            elif cmd == CMD_SHOW_MEM:
                print("Ctrl: Memory slots requested."); self.expecting_memory_slots = True
                self.memory_slots_buffer = []; self.last_memory_slot_time = time.time()
                self.ser.write(cmd.encode() + b'\n')
            elif cmd == CMD_THEME_GET:
                print("Ctrl: Requesting theme string."); self.expecting_theme_string = True
                self.last_memory_slot_time = time.time() 
                self.ser.write(cmd.encode() + b'\n')
            elif is_theme_preview: 
                full_cmd_str = theme_string + CMD_THEME_SET_SUFFIX 
                print(f"Ctrl: Sending theme preview: {full_cmd_str[:20]}...")
                self.ser.write(full_cmd_str.encode() + b'\n') 
            else: 
                self.ser.write(cmd.encode() + b'\n') 

            if cmd == CMD_TOGGLE_LOG and is_user_toggle: 
                self.log_is_on_before_special_op = not self.log_is_on_before_special_op 
                print(f"Ctrl: Log toggled. Assumed state: {'ON' if self.log_is_on_before_special_op else 'OFF'}")

        except Exception as e: 
            print(f"Ctrl: Error sending '{cmd}': {e}"); self.data_queue.put(('serial_error_disconnect', f"Send error: {e}"))


    def _is_hex_string(self, s): return bool(s) and all(c in "0123456789abcdefABCDEF" for c in s)
    def _is_memory_slot_line(self, line): return bool(self.MEMORY_SLOT_PATTERN.match(line.strip()))

    def _finalize_special_op(self, operation_type):
        print(f"Ctrl: {operation_type} operation complete.") 
        resumed_log = False
        if operation_type == "Screenshot":
            self.expecting_screenshot_data = False; self.last_screenshot_hex_byte_time = 0
            if self.screenshot_hex_buffer: 
                transfer_duration = time.time() - self.screenshot_request_time
                self.data_queue.put(('screenshot_data', (self.screenshot_hex_buffer, transfer_duration) ))
                self.screenshot_hex_buffer = ""
        elif operation_type == "Memory":
            self.expecting_memory_slots = False; self.last_memory_slot_time = 0
            if self.memory_slots_buffer:  
                self.data_queue.put(('memory_slots_data', list(self.memory_slots_buffer)))
                self.memory_slots_buffer = []
        elif operation_type == "ThemeGet": 
             self.expecting_theme_string = False; self.last_memory_slot_time = 0 
        
        if self.log_is_on_before_special_op and operation_type != "ThemeEditorToggle": 
            print(f"Ctrl: Attempting to resume log after {operation_type}.")
            time.sleep(0.1); self._send_raw_command(CMD_TOGGLE_LOG); resumed_log = True
        

    def read_serial(self):
        while self.running and self.ser and self.ser.is_open:
            try:
                new_bytes = self.ser.readline() 
                if new_bytes:
                    self.line_assembly_buffer_bytes += new_bytes
                elif not self.line_assembly_buffer_bytes: 
                    if self.expecting_screenshot_data and self.screenshot_hex_buffer and \
                       self.last_screenshot_hex_byte_time > 0 and \
                       (time.time() - self.last_screenshot_hex_byte_time > self.SCREENSHOT_DATA_INACTIVITY_TIMEOUT):
                        print(f"Ctrl: Screenshot inactivity timeout. Finalizing. Len: {len(self.screenshot_hex_buffer)}")
                        self._finalize_special_op("Screenshot")
                    elif self.expecting_memory_slots and self.memory_slots_buffer and \
                         self.last_memory_slot_time > 0 and \
                         (time.time() - self.last_memory_slot_time > self.MEMORY_DATA_INACTIVITY_TIMEOUT):
                        self._finalize_special_op("Memory")
                    elif self.expecting_theme_string and self.last_memory_slot_time > 0 and \
                         (time.time() - self.last_memory_slot_time > self.MEMORY_DATA_INACTIVITY_TIMEOUT): 
                        print("Ctrl: Timeout waiting for theme string."); self._finalize_special_op("ThemeGet")
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
                                      'memory_slots_error' if op_type_on_error == "Memory" else 'theme_error'
                            current_buffer = self.screenshot_hex_buffer if op_type_on_error == "Screenshot" else self.memory_slots_buffer if op_type_on_error == "Memory" else None
                            msg = f"UnicodeDecodeError at start of {op_type_on_error} data."
                            if current_buffer: msg = f"Unicode corruption after {len(current_buffer)} items."
                            self.data_queue.put((err_key, msg))
                            if op_type_on_error == "Screenshot": self.screenshot_hex_buffer = ""
                            elif op_type_on_error == "Memory": self.memory_slots_buffer = []
                            self._finalize_special_op(op_type_on_error)
                        else: 
                            try: line_str = complete_line_bytes.decode('utf-8').strip()
                            except UnicodeDecodeError: print(f"Ctrl: Persistent UnicodeDecodeError: {complete_line_bytes[:60]}..."); line_str = None
                        if line_str and not (self.expecting_screenshot_data or self.expecting_memory_slots or self.expecting_theme_string): self.data_queue.put(line_str)
                        continue 

                    if not line_str: 
                        if self.expecting_screenshot_data and self.screenshot_hex_buffer: self.last_screenshot_hex_byte_time = time.time() 
                        elif self.expecting_memory_slots and self.memory_slots_buffer: self.last_memory_slot_time = time.time()
                        continue

                    if self.expecting_screenshot_data:
                        is_hex = self._is_hex_string(line_str)
                        is_data_log = self.DATA_LOG_PATTERN.match(line_str)
                        is_simple_resp = line_str.strip().upper() == "OK" or "Error: Expected newline" in line_str

                        if is_hex: 
                            self.screenshot_hex_buffer += line_str; self.last_screenshot_hex_byte_time = time.time() 
                        elif self.screenshot_hex_buffer: 
                            if is_data_log or (line_str and not is_simple_resp): 
                                print(f"Ctrl: Screenshot: Non-HEX/Non-Simple line '{line_str[:30]}' after HEX. Finalizing.")
                                self._finalize_special_op("Screenshot") 
                                if is_data_log: self.data_queue.put(line_str) 
                                elif line_str: print(f"Ctrl: Discarding non-log line after screenshot: {line_str}")
                            elif is_simple_resp: 
                                print(f"Ctrl: Ignoring simple response during screenshot HEX: {line_str}")
                                self.last_screenshot_hex_byte_time = time.time() 
                        elif is_data_log: self.data_queue.put(line_str) 
                        elif line_str: print(f"Ctrl: Discarding other line during screenshot expectation: {line_str}")
                    
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
                                elif line_str: print(f"Ctrl: Discarding post-memory: {line_str}")
                                continue
                            elif is_simple_resp: print(f"Ctrl: Ignoring simple response during memory: {line_str}"); self.last_memory_slot_time = time.time()
                        elif not self.memory_slots_buffer and line_str: 
                            if is_log: self.data_queue.put(line_str)
                            elif not is_simple_resp: print(f"Ctrl: Non-slot line '{line_str[:30]}' while expecting memory start.")
                    
                    elif self.expecting_theme_string:
                        theme_match = self.THEME_STRING_PATTERN.match(line_str)
                        if theme_match:
                            self.data_queue.put(('theme_data', theme_match.group(1)))
                            self._finalize_special_op("ThemeGet"); continue
                        elif line_str : print(f"Ctrl: Non-theme line '{line_str[:30]}' while expecting theme.")
                    
                    elif line_str: 
                        is_set_mem_echo = self.MEMORY_SLOT_PATTERN.match(line_str) and line_str.startswith(CMD_SET_MEM_PREFIX)
                        if is_set_mem_echo: print(f"Ctrl: Radio echo (set memory): {line_str}")
                        elif "Error: Expected newline" in line_str: print(f"Ctrl: Radio status/error: {line_str}")
                        elif line_str.strip().upper() == "OK": print(f"Ctrl: Radio status: {line_str}")
                        elif self.DATA_LOG_PATTERN.match(line_str): self.data_queue.put(line_str)
                        else: print(f"Ctrl: Other radio output (not queued): {line_str}")

            except SerialException as e: print(f"Ctrl: Serial read error: {e}"); self.data_queue.put(('serial_error_disconnect', f"Serial read error: {e}")); self.running = False; break 
            except Exception as e: 
                print(f"Ctrl: Unexpected error in read loop: {e}")
                op_type = "Screenshot" if self.expecting_screenshot_data else "Memory" if self.expecting_memory_slots else "ThemeGet" if self.expecting_theme_string else None
                if op_type: self._finalize_special_op(op_type)
                self.data_queue.put(('serial_error_disconnect', f"Read loop error: {e}")); self.running = False; break

class RadioApp(tk.Tk):
    MIN_BATTERY_VOLTAGE = 3.2; MAX_BATTERY_VOLTAGE = 4.2; MAX_VOLUME = 63; MAX_RSSI_SNR = 127
    PERCENTAGE_MULTIPLIER = 100; LABEL_WIDTH = 14; EMOJI_BUTTON_WIDTH = 2 
    UP_ARROW_EMOJI = "⬆️"; DOWN_ARROW_EMOJI = "⬇️"; REFRESH_EMOJI = "🔃"; SCREENSHOT_EMOJI = "📸"; MEMORY_SLOTS_EMOJI = "💾"; THEME_EDITOR_EMOJI = "🎨"
    ENCODER_LEFT_EMOJI = "⬅️"; ENCODER_RIGHT_EMOJI = "➡️"; ENCODER_ARROW_BUTTON_WIDTH = 4 
    BAUD_RATES = [9600, 19200, 38400, 57600, 115200]; DEFAULT_BAUD_RATE = 115200
    PAD_X_CONN = 2; PAD_Y_CONN = 2; PAD_X_CTRL_GROUP = 5; PAD_Y_CTRL_GROUP = 5 
    PAD_X_MAIN = 5; PAD_Y_MAIN = 5; PAD_LARGE = 10; PAD_MEDIUM = 5; PAD_SMALL = 2
    MODES = ["AM", "FM", "LSB", "USB", "CW"]; BANDS = ["VHF", "ALL", "LW", "MW", "SW", "160M", "80M", "60M", "40M", "30M", "20M", "17M", "15M", "12M", "10M", "6M", "CB"] 
    KNOB_SIZE = 50; KNOB_INDICATOR_LENGTH = 20 

    def __init__(self):
        super().__init__(); self.title("ATS-Mini Radio Controller")
        self.controller = RadioController(); self.connected = False; self.console_visible = False
        self.fixed_window_width = 0; 
        self.set_os_theme(); self.create_styles()
        self.grid_columnconfigure(0, weight=1); self.grid_columnconfigure(1, weight=0); self.grid_columnconfigure(2, weight=1)
        
        self.memory_slots_data = [{'slot_num': i, 'band': '', 'freq_hz': '', 'mode': ''} for i in range(1, 33)]
        self.memory_viewer_window = None; self.memory_slot_display_vars = {}; self.waiting_for_memory_data_to_build_viewer = False 
        self.screenshot_window = None; self.theme_editor_window = None; self.theme_string_var = tk.StringVar()
        self.encoder_click_buttons = [] 

        self.create_widgets() 
        
        self.console_frame.grid(row=5, column=0, padx=self.PAD_X_MAIN, pady=(self.PAD_Y_CONN, self.PAD_Y_MAIN), sticky="ew") 
        self.update_idletasks(); self.fixed_window_width = self.winfo_reqwidth()
        self.console_frame.grid_forget(); self.update_idletasks() 
        initial_height_without_console = self.winfo_reqheight()
        self.geometry(f"{self.fixed_window_width}x{initial_height_without_console}") 
        self.resizable(False, True)
        self.bind_arrow_keys() 
        self.after(100, lambda: self.process_serial_queue())
        self.refresh_ports()
        self.protocol("WM_DELETE_WINDOW", self.on_closing); 

    def create_styles(self):
        self.style = ttk.Style(self)
        self.style.configure("EncoderArrow.TButton", padding=(self.PAD_SMALL, self.PAD_SMALL + 2), font=('Arial Unicode MS', 14)) 
        self.style.configure("Emoji.TButton", padding=(self.PAD_SMALL, self.PAD_SMALL), font=('Arial Unicode MS', 10))

    def bind_arrow_keys(self):
        self.bind("<Left>", self.handle_key_press)
        self.bind("<Right>", self.handle_key_press)
        self.bind("<Up>", self.handle_key_press)
        self.bind("<Down>", self.handle_key_press)

    def handle_key_press(self, event):
        if not self.connected: return 
        cmd_to_send = None
        if event.keysym == "Left": cmd_to_send = CMD_ENCODER_DOWN
        elif event.keysym == "Right": cmd_to_send = CMD_ENCODER_UP
        elif event.keysym in ["Up", "Down"]: cmd_to_send = CMD_ENCODER_BTN
        if cmd_to_send: self.send_radio_command(cmd_to_send)


    def on_closing(self):
        if hasattr(self, 'screenshot_window') and self.screenshot_window and self.screenshot_window.winfo_exists(): self.screenshot_window.destroy() 
        if self.memory_viewer_window and self.memory_viewer_window.winfo_exists(): self.memory_viewer_window.destroy()
        if self.theme_editor_window and self.theme_editor_window.winfo_exists(): self.theme_editor_window.destroy()
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
            {'type': 'combobox', 'textvariable': 'port_var', 'width': 12, 'state': "readonly", 'sticky': "w", 'padx': (0, self.PAD_X_MAIN), 'tooltip': "Select COM Port", 'name': 'port_combo'},
            {'type': 'label', 'text': "Baud:", 'sticky': "w", 'padx': (0, self.PAD_X_CONN)},
            {'type': 'combobox', 'textvariable': 'baud_var', 'width': 7, 'state': "readonly", 'values': [str(r) for r in self.BAUD_RATES], 'default': str(self.DEFAULT_BAUD_RATE), 'sticky': "w", 'padx': (0, self.PAD_X_MAIN), 'tooltip': "Select Baud Rate", 'name': 'baud_combo'},
            {'type': 'button', 'text': self.REFRESH_EMOJI, 'command': self.refresh_ports, 'width': self.EMOJI_BUTTON_WIDTH, 'sticky': "w", 'padx': (0, self.PAD_X_CONN), 'tooltip': "Refresh COM Port List", 'name': 'refresh_btn', 'style': 'Emoji.TButton'},
            {'type': 'button', 'text': self.SCREENSHOT_EMOJI, 'command': self.request_screenshot, 'width': self.EMOJI_BUTTON_WIDTH, 'state': tk.DISABLED, 'sticky': "w", 'padx': (0, self.PAD_X_CONN), 'tooltip': "Request Screenshot from Radio", 'name': 'screenshot_btn', 'style': 'Emoji.TButton'},
            {'type': 'button', 'text': self.MEMORY_SLOTS_EMOJI, 'command': self.open_memory_viewer, 'width': self.EMOJI_BUTTON_WIDTH, 'state': tk.DISABLED, 'sticky':"w", 'padx': (0,self.PAD_X_CONN), 'tooltip': "Open Memory Viewer", 'name': 'memory_btn', 'style': 'Emoji.TButton'},
            {'type': 'button', 'text': self.THEME_EDITOR_EMOJI, 'command': self.open_theme_editor, 'width': self.EMOJI_BUTTON_WIDTH, 'state': tk.DISABLED, 'sticky':"w", 'padx': (0,self.PAD_X_CONN), 'tooltip': "Open Theme Editor", 'name': 'theme_editor_btn', 'style': 'Emoji.TButton'},
            {'type': 'button', 'text': "Sleep", 'command': self.toggle_sleep, 'width': 6, 'state': tk.DISABLED, 'sticky': "w", 'padx': (0, self.PAD_X_CONN), 'tooltip': "Toggle Radio Sleep/Wake Mode", 'name': 'sleep_btn'},
            {'type': 'spacer', 'weight': 1}, 
            {'type': 'checkbutton', 'text': "Console", 'variable': 'console_var', 'command': self.toggle_console, 'sticky': "e", 'padx': (self.PAD_X_MAIN, self.PAD_X_CONN), 'tooltip': "Show/Hide Serial Console Log", 'name': 'console_chk'},
            {'type': 'button', 'text': "Connect", 'command': self.toggle_connection, 'width': 10, 'sticky': "e", 'padx': (0, self.PAD_X_CONN), 'tooltip': "Connect to/Disconnect from Radio", 'name': 'connect_btn'}, 
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
            {'var_name': 'freq_var', 'initial': "Frequency: --", 'row': 0, 'col': 0},
            {'var_name': 'agc_status_var', 'initial': "Gain Control: --", 'row': 1, 'col': 0},
            {'var_name': 'rssi_var', 'initial': "RSSI: --", 'row': 0, 'col': 1},
            {'var_name': 'snr_var', 'initial': "SNR: --", 'row': 1, 'col': 1},
            {'var_name': 'batt_var', 'initial': "Battery: --", 'row': 0, 'col': 2},
            {'var_name': 'fw_var', 'initial': "Firmware: --", 'row': 1, 'col': 2},
        ]

    def create_widgets(self):
        self.main_layout_frame = ttk.Frame(self) 
        self.main_layout_frame.grid(row=0, column=1, sticky="nsew", padx=self.PAD_X_MAIN, pady=self.PAD_Y_MAIN)
        self.main_layout_frame.grid_columnconfigure(0, weight=1)

        self.conn_frame = ttk.Frame(self.main_layout_frame) 
        self.conn_frame.grid(row=0, column=0, padx=0, pady=(0, self.PAD_Y_MAIN), sticky="ew")
        
        conn_elements = self._create_connection_bar_elements()
        for col_idx, config in enumerate(conn_elements):
            if config['type'] == 'spacer': self.conn_frame.grid_columnconfigure(col_idx, weight=config.get('weight', 0)); continue
            element = None
            style_to_use = config.get('style', None) 
            if config['type'] == 'label': element = ttk.Label(self.conn_frame, text=config['text'], style=style_to_use)
            elif config['type'] == 'combobox':
                var = tk.StringVar(); setattr(self, config['textvariable'], var) 
                element = ttk.Combobox(self.conn_frame, textvariable=var, width=config['width'], state=config['state'], style=style_to_use)
                if 'values' in config: element['values'] = config['values']
                if 'default' in config: var.set(config['default'])
            elif config['type'] == 'button': element = ttk.Button(self.conn_frame, text=config['text'], command=config['command'], width=config['width'], state=config.get('state', tk.NORMAL), style=style_to_use)
            elif config['type'] == 'checkbutton':
                var = tk.BooleanVar(value=getattr(self, config['variable'], False)); setattr(self, config['variable'], var)
                element = ttk.Checkbutton(self.conn_frame, text=config['text'], variable=var, command=config['command'], style=style_to_use)
            elif config['type'] == 'canvas': element = tk.Canvas(self.conn_frame, width=config['width'], height=config['height'], highlightthickness=config['highlightthickness'])
            if element:
                element.grid(row=0, column=col_idx, sticky=config['sticky'], padx=config['padx'])
                if 'tooltip' in config: Tooltip(element, config['tooltip'])
                if 'name' in config: setattr(self, config['name'], element) 
        self.update_status_indicator()

        self.ctrl_frame_buttons = []
        control_group_configs = self._get_control_group_configs()
        self.ctrl_frame1 = ttk.Frame(self.main_layout_frame); self.ctrl_frame1.grid(row=1, column=0, padx=0, pady=self.PAD_Y_MAIN, sticky="ew")
        for i in range(4): self.ctrl_frame1.grid_columnconfigure(i, weight=1, uniform="ctrlgroup1") 
        self.ctrl_frame2 = ttk.Frame(self.main_layout_frame); self.ctrl_frame2.grid(row=2, column=0, padx=0, pady=(0, self.PAD_Y_MAIN), sticky="ew")
        for i in range(4): self.ctrl_frame2.grid_columnconfigure(i, weight=1, uniform="ctrlgroup2")
        for i, config in enumerate(control_group_configs):
            parent_frame = self.ctrl_frame1 if i < 4 else self.ctrl_frame2; col = i % 4
            var = tk.StringVar(); setattr(self, config['var_name'], var) 
            group_frame, buttons = self._create_control_group_widget(parent_frame, var, config['cmd_up'], config['cmd_down'], config['initial'], config['tip_up'], config['tip_down'])
            group_frame.grid(row=0, column=col, padx=self.PAD_X_CTRL_GROUP, pady=self.PAD_Y_CTRL_GROUP, sticky="nsew")
            self.ctrl_frame_buttons.extend(buttons)
        
        self.encoder_frame = ttk.Frame(self.main_layout_frame, padding=(0, self.PAD_MEDIUM, 0, self.PAD_MEDIUM))
        self.encoder_frame.grid(row=3, column=0, sticky="ew", pady=(self.PAD_SMALL, self.PAD_MEDIUM))
        self.encoder_frame.grid_columnconfigure(0, weight=1) 
        self.encoder_frame.grid_columnconfigure(1, weight=0) 
        self.encoder_frame.grid_columnconfigure(2, weight=0) 
        self.encoder_frame.grid_columnconfigure(3, weight=0) 
        self.encoder_frame.grid_columnconfigure(4, weight=1) 

        ttk.Label(self.encoder_frame, text="Encoder Control", font=('Helvetica', 10, 'bold')).grid(row=0, column=0, columnspan=5, pady=(0, self.PAD_SMALL))
        
        self.encoder_left_btn = ttk.Button(self.encoder_frame, text=self.ENCODER_LEFT_EMOJI, command=lambda: self.send_radio_command(CMD_ENCODER_DOWN), width=self.ENCODER_ARROW_BUTTON_WIDTH, style="EncoderArrow.TButton") 
        self.encoder_left_btn.grid(row=1, column=1, sticky="e", padx=(0, self.PAD_SMALL)) 
        Tooltip(self.encoder_left_btn, "Encoder Down (Counter-Clockwise)")
        
        self.knob_canvas = tk.Canvas(self.encoder_frame, width=self.KNOB_SIZE, height=self.KNOB_SIZE, highlightthickness=1, highlightbackground="gray")
        self.knob_canvas.grid(row=1, column=2, padx=self.PAD_SMALL)
        self._draw_knob() 
        self.knob_canvas.bind("<Button-1>", self.handle_knob_click) 
        Tooltip(self.knob_canvas, "Click: Encoder Button\nUse Arrow Keys:\nLeft: Encoder Down\nRight: Encoder Up\nUp/Down: Encoder Button")

        self.encoder_right_btn = ttk.Button(self.encoder_frame, text=self.ENCODER_RIGHT_EMOJI, command=lambda: self.send_radio_command(CMD_ENCODER_UP), width=self.ENCODER_ARROW_BUTTON_WIDTH, style="EncoderArrow.TButton") 
        self.encoder_right_btn.grid(row=1, column=3, sticky="w", padx=(self.PAD_SMALL, 0)) 
        Tooltip(self.encoder_right_btn, "Encoder Up (Clockwise)")

        self.encoder_click_buttons.extend([self.encoder_left_btn, self.encoder_right_btn]) 

        self.set_control_buttons_state(tk.DISABLED) 
        
        self.status_frame = ttk.LabelFrame(self.main_layout_frame, text="Radio Status") 
        self.status_frame.grid(row=4, column=0, padx=self.PAD_X_MAIN, pady=(0, self.PAD_Y_CONN), sticky="ew") 
        self.status_frame.grid_columnconfigure([0,1,2], weight=1, uniform="statusgroup") 
        status_label_configs = self._get_status_label_configs()
        for config in status_label_configs:
            var = tk.StringVar(value=config['initial']); setattr(self, config['var_name'], var)
            ttk.Label(self.status_frame, textvariable=var).grid(row=config['row'], column=config['col'], padx=self.PAD_X_MAIN, pady=self.PAD_Y_CONN, sticky="w")
        
        self.console_frame = ttk.LabelFrame(self.main_layout_frame, text="Serial Console") 
        self.console = scrolledtext.ScrolledText(self.console_frame, height=8, width=70, state=tk.DISABLED, relief="sunken", borderwidth=1, padx=self.PAD_X_CONN, pady=self.PAD_Y_CONN) 
        self.console.pack(fill="both", expand=True, padx=self.PAD_X_CONN, pady=self.PAD_Y_CONN)

    def _draw_knob(self):
        self.knob_canvas.delete("all")
        cx, cy, r = self.KNOB_SIZE/2, self.KNOB_SIZE/2, self.KNOB_SIZE/2 - 5
        self.knob_canvas.create_oval(cx-r, cy-r, cx+r, cy+r, outline="black", fill="lightgrey", width=2)
        self.knob_canvas.create_line(cx, cy, cx, cy - self.KNOB_INDICATOR_LENGTH, width=2, fill="black")

    def handle_knob_click(self, event=None):
        if not self.connected: return
        self.send_radio_command(CMD_ENCODER_BTN)

    def _create_control_group_widget(self, parent, text_var, cmd_up, cmd_down, initial_text, tip_up, tip_down):
        group_frame = ttk.LabelFrame(parent, padding=(self.PAD_X_CTRL_GROUP, self.PAD_Y_CTRL_GROUP)) 
        group_frame.grid_columnconfigure(0, weight=1) 
        up_button = ttk.Button(group_frame, text=self.UP_ARROW_EMOJI, command=lambda: self.send_radio_command(cmd_up), width=self.EMOJI_BUTTON_WIDTH, style="Emoji.TButton")
        up_button.grid(row=0, column=0, pady=(self.PAD_Y_CONN,0)); Tooltip(up_button, tip_up)
        text_var.set(initial_text)
        value_label = ttk.Label(group_frame, textvariable=text_var, width=self.LABEL_WIDTH, font=('Helvetica', 9, 'bold'), anchor="center") 
        value_label.grid(row=1, column=0, pady=self.PAD_Y_CONN)
        down_button = ttk.Button(group_frame, text=self.DOWN_ARROW_EMOJI, command=lambda: self.send_radio_command(cmd_down), width=self.EMOJI_BUTTON_WIDTH, style="Emoji.TButton")
        down_button.grid(row=2, column=0, pady=(0,self.PAD_Y_CONN)); Tooltip(down_button, tip_down)
        return group_frame, [up_button, down_button]

    def open_memory_viewer(self): 
        if not self.connected: messagebox.showwarning("Not Connected", "Connect to the radio to use the memory viewer."); return
        
        if self.memory_viewer_window and self.memory_viewer_window.winfo_exists():
            self.memory_viewer_window.lift(); self.memory_viewer_window.focus_set()
            if not self.controller.expecting_memory_slots: self.refresh_memory_slots_from_radio()
            return

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
        self.controller.send_command(CMD_SCREENSHOT)
        if self.console_visible: self.console.insert(tk.END, "Requesting screenshot...\n"); self.console.see(tk.END)

    def display_screenshot(self, hex_data, transfer_duration=None): 
        local_proc_start_time = time.time()
        image_bytes = b'' 
        try:
            existing_ss_window = getattr(self, 'screenshot_window', None)
            if existing_ss_window is not None:
                try:
                    if existing_ss_window.winfo_exists():
                        existing_ss_window.destroy()
                except tk.TclError as e_winfo:
                    print(f"App: Error checking/destroying existing screenshot window: {e_winfo}")
            self.screenshot_window = None 

            try: image_bytes = bytes.fromhex(hex_data)
            except ValueError as e:
                err_msg = str(e); position_str = "position "; context_msg = ""
                if "non-hexadecimal number found" in err_msg and position_str in err_msg:
                    try:
                        pos_start = err_msg.find(position_str) + len(position_str)
                        pos_end_candidate = err_msg.find(" ", pos_start); 
                        if pos_end_candidate == -1: pos_end_candidate = len(err_msg)
                        err_pos = int(err_msg[pos_start:pos_end_candidate])
                        start_ctx = max(0, err_pos - 20); end_ctx = min(len(hex_data), err_pos + 21)
                        problem_context = hex_data[start_ctx:end_ctx]
                        pointer = " " * (err_pos - start_ctx) + "^"
                        context_msg = f"\nContext (char at pos {err_pos}):\n'{problem_context}'\n {pointer}"
                    except Exception as e_parse: context_msg = f"\n(Could not parse error position: {e_parse})"
                messagebox.showerror("Screenshot HEX Error", f"Invalid HEX data: {e}{context_msg}")
                if self.console_visible: self.console.insert(tk.END, f"Screenshot HEX data error: {e}{context_msg}\n")
                return
            image_stream = io.BytesIO(image_bytes); pil_image = Image.open(image_stream) 
            
            local_proc_duration = time.time() - local_proc_start_time
            bits_transferred = len(image_bytes) * 8
            bps = (bits_transferred / transfer_duration) if transfer_duration and transfer_duration > 0 else 0
            
            print(f"App: Screenshot. Bits: {bits_transferred}, Transfer Time: {transfer_duration:.2f}s, bps: {bps:.0f}")


            self.screenshot_window = tk.Toplevel(self); self.screenshot_window.title("Radio Screenshot")
            self.screenshot_window.resizable(False, False)
            try: bg_color = self.style.lookup("TFrame", "background")
            except tk.TclError: bg_color = "SystemButtonFace" 
            self.screenshot_window.configure(background=bg_color)
             
            tk_image = ImageTk.PhotoImage(pil_image)
            img_label = ttk.Label(self.screenshot_window, image=tk_image); img_label.image = tk_image 
            img_label.pack(padx=10, pady=10)
            save_button = ttk.Button(self.screenshot_window, text="Save as PNG", command=lambda img=pil_image: self.save_screenshot_as_png(img))
            save_button.pack(pady=10)
            self.screenshot_window.lift() 
        except Image.UnidentifiedImageError as e: 
             messagebox.showerror("Screenshot Error", f"Could not identify image (truncated or corrupt BMP?): {e}")
             if self.console_visible: self.console.insert(tk.END, f"Screenshot Image.UnidentifiedImageError: {e}\n")
        except AttributeError as ae: 
            print(f"App: Caught AttributeError in display_screenshot: {ae}. self was: {type(self)}")
            messagebox.showerror("Screenshot Error", f"Failed to display screenshot (AttributeError): {ae}")
            if hasattr(self, 'console_visible') and self.console_visible and hasattr(self, 'console') and self.console.winfo_exists():
                self.console.insert(tk.END, f"Error displaying screenshot (AttributeError): {ae}\n")
        except Exception as e:
            print(f"App: General exception in display_screenshot. self: {self}, type(self): {type(self)}, Error: {type(e)} - {e}")
            messagebox.showerror("Screenshot Error", f"Failed to display screenshot: {e}")
            if hasattr(self, 'console_visible') and self.console_visible and hasattr(self, 'console') and self.console.winfo_exists():
                self.console.insert(tk.END, f"Error displaying screenshot: {e}\n")

    def save_screenshot_as_png(self, pil_image_to_save): 
        if not pil_image_to_save: messagebox.showerror("Save Error", "No image data to save."); return
        file_path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG files", "*.png"), ("All files", "*.*")], title="Save Screenshot As")
        if file_path:
            try: pil_image_to_save.save(file_path, "PNG"); messagebox.showinfo("Save Successful", f"Screenshot saved to:\n{file_path}")
            except Exception as e: messagebox.showerror("Save Error", f"Failed to save screenshot: {e}")

    def send_radio_command(self, command): 
        if self.connected: self.controller.send_command(command)
        else: messagebox.showwarning("Not Connected", "Connect to the radio to send commands.")

    def toggle_console(self): 
        self.console_visible = self.console_var.get()
        if self.console_visible:
            self.console_frame.grid(row=5, column=0, padx=self.PAD_X_MAIN, pady=(self.PAD_Y_CONN, self.PAD_Y_MAIN), sticky="ew"); self.console.config(state=tk.NORMAL) 
        else: self.console_frame.grid_forget(); self.console.config(state=tk.DISABLED)
        self.update_idletasks(); self.geometry(f"{self.fixed_window_width}x{self.winfo_reqheight()}")

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

    def set_control_buttons_state(self, state): 
        if hasattr(self, 'sleep_btn'): self.sleep_btn.config(state=state)
        if hasattr(self, 'screenshot_btn'): self.screenshot_btn.config(state=state if self.connected else tk.DISABLED) 
        if hasattr(self, 'memory_btn'): self.memory_btn.config(state=state if self.connected else tk.DISABLED)
        if hasattr(self, 'theme_editor_btn'): self.theme_editor_btn.config(state=state if self.connected else tk.DISABLED)
        for button in self.ctrl_frame_buttons: button.config(state=state)
        for button in self.encoder_click_buttons: button.config(state=state) 

    def handle_forced_disconnect(self, error_message): 
        if self.connected: 
            print(f"Forced disconnect due to: {error_message}")
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


    def toggle_sleep(self): 
        if not self.connected: messagebox.showwarning("Not Connected", "Connect to the radio first."); return
        if self.controller.sleep_mode: self.controller.send_command(CMD_SLEEP_OFF); self.controller.sleep_mode = False; self.sleep_btn.config(text="Sleep")
        else: self.controller.send_command(CMD_SLEEP_ON); self.controller.sleep_mode = True; self.sleep_btn.config(text="Wake")

    def update_status_indicator(self): 
        color = "red"; 
        if self.connected: color = "green" if self.controller.data_received else "yellow"
        try: bg_color = self.style.lookup("TFrame", "background")
        except tk.TclError: bg_color = "SystemButtonFace" 
        if hasattr(self, 'connection_status_canvas'): 
            self.connection_status_canvas.configure(background=bg_color)
            self.connection_status_canvas.delete("all"); self.connection_status_canvas.create_oval(2, 2, 18, 18, fill=color, outline=color)
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

    def open_theme_editor(self):
        if not self.connected: messagebox.showwarning("Not Connected", "Connect to radio for Theme Editor."); return
        if self.theme_editor_window and self.theme_editor_window.winfo_exists():
            self.theme_editor_window.lift(); self.theme_editor_window.focus_set(); return

        self.theme_editor_window = tk.Toplevel(self); self.theme_editor_window.title("Theme Editor")
        self.theme_editor_window.geometry("550x220"); self.theme_editor_window.resizable(False, False) 

        main_frame = ttk.Frame(self.theme_editor_window, padding=self.PAD_LARGE)
        main_frame.pack(fill="both", expand=True)

        ttk.Button(main_frame, text="Enable Theme Editor Mode on Radio (T)", command=lambda: self.send_radio_command(CMD_THEME_EDITOR_TOGGLE)).pack(pady=self.PAD_SMALL, fill="x")
        Tooltip(main_frame.winfo_children()[-1], "Toggles special display mode on radio for easier theme editing.")
        
        ttk.Button(main_frame, text="Get Current Theme from Radio (@)", command=lambda: self.send_radio_command(CMD_THEME_GET)).pack(pady=self.PAD_SMALL, fill="x")
        Tooltip(main_frame.winfo_children()[-1], "Fetches the current theme string from the radio.")
        
        ttk.Label(main_frame, text="Theme String (e.g., 'Color theme Default: x0000...'):").pack(anchor="w", pady=(self.PAD_MEDIUM, 0))
        theme_entry = ttk.Entry(main_frame, textvariable=self.theme_string_var, width=75) 
        theme_entry.pack(fill="x", pady=self.PAD_SMALL)
        Tooltip(theme_entry, "Paste the full theme string here (e.g., 'Color theme ...: xFFFF...')")
        
        ttk.Button(main_frame, text="Preview Theme on Radio (Paste string + !)", command=self.preview_theme_on_radio).pack(pady=self.PAD_MEDIUM, fill="x")
        Tooltip(main_frame.winfo_children()[-1], "Sends the theme string (with '!' appended by the app) to the radio for preview.")

    def preview_theme_on_radio(self):
        if not self.connected: messagebox.showerror("Error", "Not connected."); return
        
        full_theme_line = self.theme_string_var.get().strip()
        if not full_theme_line:
            messagebox.showwarning("Input Error", "Theme string is empty."); return

        match = RadioController.THEME_STRING_PATTERN.match(full_theme_line)
        if not match:
            messagebox.showerror("Input Error", "Invalid theme string format.\nExpected format: 'Color theme ...: xHHHHxHHHH...'")
            return
        
        theme_data_part = match.group(1) 

        if not theme_data_part.startswith('x'):
            messagebox.showerror("Input Error", "Theme data part must start with 'x'."); return
        
        hex_colors = theme_data_part[1:] 
        if len(hex_colors) % 4 != 0:
            messagebox.showerror("Input Error", "Hex color data length must be a multiple of 4."); return
        if not all(c in "0123456789abcdefABCDEF" for c in hex_colors):
            messagebox.showerror("Input Error", "Hex color data contains invalid characters."); return
        
        num_colors = len(hex_colors) // 4
        if num_colors != 32: 
             messagebox.showwarning("Input Warning", f"Theme string has {num_colors} colors, expected 32. Previewing anyway.")
        
        self.controller.send_command(theme_data_part, is_theme_preview=True, theme_string=theme_data_part)


    def process_serial_queue(self):
        try:
            while not self.controller.data_queue.empty():
                queue_item = self.controller.data_queue.get_nowait()
                if isinstance(queue_item, tuple) and len(queue_item) == 2:
                    item_type, item_data = queue_item
                    if item_type == 'screenshot_data':
                        hex_data, transfer_duration = item_data 
                        if self.console_visible: self.console.insert(tk.END, f"Received screenshot (HEX len: {len(hex_data)}). Processing...\n")
                        self.display_screenshot(hex_data, transfer_duration); continue 
                    elif item_type == 'screenshot_error':
                        messagebox.showerror("Screenshot Error", item_data)
                        if self.console_visible: self.console.insert(tk.END, f"Screenshot error: {item_data}\n"); continue
                    elif item_type == 'serial_error_disconnect': 
                        self.handle_forced_disconnect(item_data); continue
                    elif item_type == 'memory_slots_data':
                        print(f"App: Received memory slot data: {len(item_data)} lines")
                        for i in range(32): self.memory_slots_data[i].update({'band': '', 'freq_hz': '', 'mode': ''})
                        for line in item_data:
                            match = RadioController.MEMORY_SLOT_PATTERN.match(line.strip()) 
                            if match:
                                try:
                                    slot_num_str, band_val, freq_val, mode_val = [g.strip() for g in match.groups()]
                                    slot_idx = int(slot_num_str) -1 
                                    if 0 <= slot_idx < 32: self.memory_slots_data[slot_idx].update({'band': band_val, 'freq_hz': freq_val, 'mode': mode_val})
                                    else: print(f"App: Slot index {slot_idx + 1} out of range: {line}")
                                except (ValueError, IndexError) as e: print(f"App: Error parsing slot line '{line}': {e}")
                        
                        if self.waiting_for_memory_data_to_build_viewer:
                            self._build_and_show_memory_viewer(); self.waiting_for_memory_data_to_build_viewer = False
                        else: self.update_memory_viewer_display()
                        if self.console_visible: self.console.insert(tk.END, "Memory slots updated.\n")
                        continue
                    elif item_type == 'memory_slots_error':
                        messagebox.showerror("Memory Slot Error", item_data)
                        if self.console_visible: self.console.insert(tk.END, f"Memory slot error: {item_data}\n"); continue
                    elif item_type == 'theme_data': 
                        full_theme_display_string = f"Color theme: {item_data}" 
                        self.theme_string_var.set(full_theme_display_string)
                        if self.console_visible: self.console.insert(tk.END, f"Theme data received: {item_data[:50]}...\n")
                        if self.theme_editor_window and self.theme_editor_window.winfo_exists(): self.theme_editor_window.lift()
                        continue
                    elif item_type == 'theme_error':
                        messagebox.showerror("Theme Error", item_data)
                        if self.console_visible: self.console.insert(tk.END, f"Theme error: {item_data}\n"); continue


                data_line = str(queue_item) 
                if self.console_visible and self.console.winfo_exists(): self.console.insert(tk.END, data_line + '\n'); self.console.see(tk.END)
                if RadioController.DATA_LOG_PATTERN.match(data_line): 
                    params = data_line.split(',')
                    if len(params) >= 15: 
                        try:
                            app_v=int(params[0]); raw_f=int(params[1]); bfo=int(params[2]); cal=int(params[3]); band=params[4]; mode=params[5] 
                            step=params[6]; bw=params[7]; agc=int(params[8]); vol=int(params[9]); rssi=int(params[10]); snr=int(params[11]); volt=float(params[13])
                            
                            if mode in ['LSB','USB']: self.freq_var.set(f"Frequency: {(raw_f*1000+bfo)/1000.0:.3f} kHz")
                            elif mode=='FM': self.freq_var.set(f"Frequency: {raw_f/100.0:.2f} MHz")
                            else: self.freq_var.set(f"Frequency: {raw_f} kHz")
                            agc_s,agc_l=self.format_agc_status_display(agc); self.agc_var.set(agc_s); self.agc_status_var.set(agc_l)
                            self.vol_var.set(f"Vol: {vol} ({self.value_to_percentage(vol,self.MAX_VOLUME)}%)")
                            self.band_var.set(f"Band: {band}"); self.mode_var.set(f"Mode: {mode}"); self.step_var.set(f"Step: {step}"); self.bw_var.set(f"BW: {bw}")
                            self.cal_var.set(self.format_calibration_display(cal)); self.rssi_var.set(f"RSSI: {rssi} dBuV"); self.snr_var.set(f"SNR: {snr} dB")
                            self.batt_var.set(f"Battery: {volt:.2f}V ({self.voltage_to_percentage(volt)}%)")
                            self.fw_var.set(f"Firmware: {self.format_firmware_version(app_v)}") 
                            if not self.controller.data_received: self.controller.data_received=True; self.update_status_indicator()
                        except (ValueError,IndexError) as e: 
                            log_msg=f"App: Data parsing error for log line: '{data_line}' - {e}\n"; print(log_msg.strip()) 
                            if self.console_visible and self.console.winfo_exists(): self.console.insert(tk.END, log_msg)
                        except Exception as e: 
                            log_msg=f"App: Unexpected error processing log line: '{data_line}' - {e}\n"; print(log_msg.strip())
                            if self.console_visible and self.console.winfo_exists(): self.console.insert(tk.END, log_msg)
                    else: 
                        log_msg=f"App: Line matched DATA_LOG_PATTERN but had {len(params)} fields: '{data_line}'\n"; print(log_msg.strip())
                        if self.console_visible and self.console.winfo_exists(): self.console.insert(tk.END, log_msg)

                elif data_line: 
                    if not (RadioController.MEMORY_SLOT_PATTERN.match(data_line) or "Error:" in data_line or data_line.upper() == "OK"):
                        log_msg=f"App: Unhandled data line from queue: '{data_line}'\n"; print(log_msg.strip())
                        if self.console_visible and self.console.winfo_exists(): self.console.insert(tk.END, log_msg)
        except queue.Empty: pass
        finally:
            if self.winfo_exists(): self.after(100, lambda: self.process_serial_queue())

if __name__ == "__main__":
    app = RadioApp()
    app.mainloop()
