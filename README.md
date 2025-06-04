# Mini Radio Controller - User Guide

**Version:** Based on `MiniRadio4.py` script changes up to June 4, 2025.

### 1. Introduction

The Mini Radio Controller provides a graphical interface for your ATS-Mini Si4732-based radio receiver via a serial connection. It allows viewing and changing radio settings, monitoring status, and using features like screenshots, memory management, theme exploration, and FM band scanning.

### 2. Getting Started

**Prerequisites:**
* Python 3 installed.
* Python libraries: `pyserial` and `Pillow`. Install using:
    ```bash
    pip install pyserial Pillow
    ```
* The `MiniRadio4.py` script file.

**Connecting the Radio:**
1.  Connect your radio to your computer with a data-capable USB cable.
2.  Ensure necessary USB-to-serial drivers are installed (usually automatic).
3.  Power on your radio.

### 3. Main Application Window

The window includes:
* **Connection Bar (Top):** For serial connection and global functions.
* **Control Groups (Upper Middle):** Buttons for common radio parameters.
* **Encoder & FM Scan Controls (Lower Middle):** Visual encoder and FM scan settings.
* **Radio Status Display (Bottom):** Real-time radio information.
* **Serial Console (Optional):** Toggled via a checkbox to show raw serial data.

### 4. Detailed GUI Sections

#### 4.1. Connection Bar

* **Port Selection (`Port:`):** Dropdown for COM port selection.
* **Baud Rate (`Baud:`):** Dropdown for baud rate (default 9600).
* **Refresh Ports Button (🔃):** Rescans for COM ports.
* **Screenshot Button (📸):** Captures the radio's display. Log is temporarily disabled. Button shows "📸 Receiving..." during operation.
* **Memory Slots Button (💾):** Opens memory slot viewer. Log is temporarily disabled.
* **Sleep Button:** Toggles radio sleep/wake mode.
* **Console Checkbox:** Shows/hides the Serial Console and toggles the radio's log output.
* **Connect/Disconnect Button:** Establishes or terminates the serial connection.
* **Connection Status Indicator (Dot):**
    * **Red:** Disconnected.
    * **Yellow:** Connecting/No Data.
    * **Green:** Connected & Receiving Data.

#### 4.2. Control Groups (Shared Controls)

Adjust radio parameters using Up (⬆️) / Down (⬇️) buttons. Labels display current settings.
* **Vol:** Volume (0-63) and percentage.
* **Band:** Cycles frequency bands.
* **Mode:** Cycles demodulation modes (AM, FM, LSB, USB, CW).
* **Step:** Tuning step size.
* **BW:** Filter bandwidth.
* **AGC/Attn:** AGC and manual attenuator levels.
* **Bright:** Display backlight brightness.
* **Cal:** Calibration offset.

#### 4.3. Encoder & FM Scan Controls

**Encoder Controls:**
* **Visual Knob:** Rotates to indicate encoder turns.
* **Arrow Buttons (⬅️, ➡️):** Simulate encoder rotation.
* **Knob Click (Canvas):** Simulates encoder button press.
* **Keyboard Arrows:** Left/Right for encoder down/up; Up/Down for encoder button.
* **"Encoder Controls" Label:** Identifies this section.

**FM Scan Controls:**
* **"FM Scan" Label:** Identifies this section.
* **SNR Floor Slider & Display:** Sets minimum SNR for scan results (0-24 dB, default 12 dB).
* **FM Scan Progress Label:** Shows "Scanning: [frequency]" during scan.
* **FM Scan / Stop Scan Buttons:** Starts or stops the FM band scan. (Dwell time is fixed at 0.5s).

#### 4.4. Radio Status Display

Shows real-time information:
* **Frequency Label:** Current frequency.
* **SNR Indicator (Dot):** Bright green if current SNR ≥ SNR Floor; grey otherwise.
* **SNR Label:** Signal-to-Noise Ratio (dB).
* **Battery Label:** Voltage and percentage.
* **Gain Control Label:** AGC status or manual attenuator level.
* **RSSI Label:** Received Signal Strength (dBuV).
* **Firmware Label:** Radio's firmware version.

#### 4.5. Serial Console

Optional panel (toggled by "Console" checkbox) displaying raw serial data and application messages.

### 5. Special Features

#### 5.1. Screenshot Function (📸 Button)

1.  Click the **Screenshot (📸)** button.
2.  A "Radio Screenshot" window appears if data is valid.
3.  **Screenshot Window Features:**
    * **Image Display:** Shows the captured screenshot.
    * **Screenshot Color Palette:** Displays significant colors (count > 16) from the screenshot.
    * **"Get Theme" Button:** Fetches and displays the radio's internal color theme (37 colors). Theme swatches attempt to align with screenshot palette colors.
    * **"Refresh Screenshot" Button:** Closes the current screenshot window and requests a new one.
    * **"Save as BMP" / "Save as PNG" Buttons:** Saves the screenshot.
4.  The main screenshot button is re-enabled after the operation.

#### 5.2. Memory Slot Viewer (💾 Button)

1.  Click the **Memory Slots (💾)** button.
2.  A window opens, displaying data for 32 memory slots (Band, Frequency, Mode).
3.  **"Refresh Slots from Radio" Button:** Updates the displayed data.

#### 5.3. FM Scan

1.  Ensure connection and FM mode. Set desired **SNR Floor** (0-24 dB).
2.  Click **"FM Scan"**. The button changes to "Stop Scan". Progress is shown.
3.  Scan proceeds in 100kHz steps. Dwell time per step is 0.5 seconds.
4.  **Completion/Interruption:** Scan stops on full cycle, user stop, max steps (500), or error. If interrupted, attempts to tune back to the scan's start frequency.
5.  **Results Window:** A new window appears with:
    * Scan summary (total frequencies, duration).
    * List of stations meeting the SNR Floor, sorted by SNR.
    * **"Save Results" Button:** Saves content to a `.txt` file.

### 6. Tips & Troubleshooting

* **Connection:** Verify COM port, baud rate (9600 default), and USB cable.
* **Radio Log:** Temporarily disabled by the app for screenshot, memory, and theme operations.
* **Screenshot Timeout:** Set to 10 seconds.

---
