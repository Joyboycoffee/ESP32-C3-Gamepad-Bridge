# ESP32-C3-Gamepad-Bridge

ESP32-C3 Bluetooth BLE controller bridge for serial telemetry and input processing.

## Summary

ESP32-C3-Gamepad-Bridge is an embedded bridge that connects Bluetooth Low Energy (BLE) gamepads and controllers (such as Xbox Wireless Controllers and compatible HID devices) to an ESP32-C3 microcontroller. Incoming BLE notifications are parsed into raw stick positions, trigger values, and button states, and relayed over USB serial for desktop applications, robotics control, and telemetry visualizers.

## Features

- Bluetooth LE client powered by NimBLE-Arduino for low latency and automatic reconnection.
- Xbox and standard BLE gamepad report parsing with XboxControllerNotificationParser.
- High-speed USB CDC serial output streaming structured input packets at 115200 baud.
- Desktop graphical visualizer (`visualizer.py`) rendering real-time stick deflection, triggers, and buttons.
- Browser-based web visualizer (`web_visualizer.py`) for websocket-driven remote inspection.

## Hardware Requirements

- Microcontroller: ESP32-C3 development board (ESP32-C3 SuperMini or DevKitM-1)
- Controller: Bluetooth Low Energy compatible gamepad (such as Xbox Wireless Controller, EvoFox One S, or BLE HID gamepad)
- USB connection to host PC

## Build and Flash Steps

1. Open a terminal in the repository root directory.
2. Build the firmware using PlatformIO:
   ```bash
   pio run
   ```
3. Connect the ESP32-C3 board over USB and upload:
   ```bash
   pio run --target upload
   ```
4. Put your BLE gamepad into pairing mode. The ESP32-C3 will automatically discover, bond, and connect.

## Running the Visualizer

1. Install the required Python dependencies:
   ```bash
   pip install pyserial pygame
   ```
2. Run the desktop visualizer:
   ```cmd
   run_visualizer.bat
   ```
   Or execute directly from Python specifying your COM port:
   ```bash
   python visualizer.py --port COM8
   ```
3. Alternatively, launch the browser visualizer:
   ```bash
   python web_visualizer.py
   ```
