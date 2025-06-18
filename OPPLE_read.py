import asyncio
import struct
import datetime
import csv
import os
import sys
from bleak import BleakClient, BleakScanner

# --- Opple Device Configuration (Based on your output) ---
OPPLE_DEVICE_NAME_PREFIX = "LMaster_0d72"
OPPLE_MAC_ADDRESS = "FF:00:16:00:0D:72"
# Data Notification Characteristic (Rx) - Where measurement data is expected
OPPLE_CHARACTERISTIC_UUID_DATA_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
# Command Characteristic (Tx) - Where START/STOP commands are sent, and seemingly some responses are received
OPPLE_CHARACTERISTIC_UUID_COMMAND_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# Commands identified from your output
OPPLE_COMMAND_START_MEASUREMENT = b'\x00\x00\x0e\x00\x13\x00\x00\x02\x00\x00\x00\x00\x0a\x00'
OPPLE_COMMAND_STOP_MEASUREMENT = b'\x00\x00\x0e\x00\x13\x00\x00\x02\x00\x00\x00\x00\x0b\x00'

# Set to True to automatically scan and discover the Opple device by its name prefix.
AUTO_DISCOVER_OPPLE = True

# --- Variables for storing received measurement data ---
current_measurements = []
measurement_session_data = {
    "halogen_reference": [],
    "solar_reference": [],
    "sample_halogen": {},
    "sample_solar": {}
}
current_mode = None
current_sample_name = None

# --- File for raw data logging ---
RAW_DATA_LOG_FILE = "raw_data.txt"

# --- Function to parse raw data from Opple ---
def parse_opple_data(data: bytes, source_uuid: str):
    """
    Parses the raw byte array received from the Opple Light Master Pro.
    This function has been updated with the latest observations but still
    requires app values to fully map raw sensor readings to Lux, CCT, etc.
    """
    parsed = {}
    
    # Handle data from the 'command' characteristic (6e400003)
    if source_uuid.lower() == OPPLE_CHARACTERISTIC_UUID_COMMAND_TX.lower():
        if len(data) == 20:
            parsed['packet_type'] = '20_byte_measurement_or_status'
            parsed['raw_hex'] = data.hex()

            # The last 6 bytes (bytes 14-19) appear to contain the primary measurement data
            # Unpack as three little-endian unsigned shorts
            try:
                # Example: 80001f0000000000020000000a01000bb2108216
                # RawVal_1 from data[14:16] (000b -> 11)
                # RawVal_2 from data[16:18] (b210 -> 42290)
                # RawVal_3 from data[18:20] (8216 -> 57858)
                
                raw_val1 = struct.unpack('<H', data[14:16])[0]
                raw_val2 = struct.unpack('<H', data[16:18])[0]
                raw_val3 = struct.unpack('<H', data[18:20])[0]
                
                # These are raw sensor readings that need to be mapped to Lux, CCT, etc.
                parsed['RawVal_1'] = raw_val1
                parsed['RawVal_2'] = raw_val2
                parsed['RawVal_3'] = raw_val3
                
                # If these values are all zero (as in dark measurements), indicate it
                if raw_val1 == 0 and raw_val2 == 0 and raw_val3 == 0:
                    parsed['measurement_state'] = 'DARK'
                else:
                    parsed['measurement_state'] = 'LIGHT_DETECTED'

                return parsed
            except struct.error as e:
                return f"ERROR: Could not unpack 20-byte data from {source_uuid}: {e}"
            except Exception as e:
                return f"ERROR: Unknown parsing error for 20-byte data from {source_uuid}: {e}"

        elif len(data) == 11:
            parsed['packet_type'] = '11_byte_status_or_battery'
            parsed['raw_hex'] = data.hex()
            
            # The two bytes at index 8 and 9 (data[8:10]) appear to be battery voltage in mV
            # Interpreted as a big-endian unsigned short
            try:
                raw_battery_mv = struct.unpack('>H', data[8:10])[0]
                parsed['RawBattery_mV'] = raw_battery_mv
                
                return parsed
            except struct.error as e:
                return f"ERROR: Could not unpack 11-byte battery data from {source_uuid}: {e}"
            except Exception as e:
                return f"ERROR: Unknown parsing error for 11-byte data from {source_uuid}: {e}"
        else:
            # Any other length from the command characteristic
            parsed['packet_type'] = f'UNKNOWN_LEN_FROM_COMMAND_CHAR ({len(data)}B)'
            parsed['raw_hex'] = data.hex()
            return parsed

    # Handle data from the 'data' characteristic (6e400002) - still not observed with measurements
    elif source_uuid.lower() == OPPLE_CHARACTERISTIC_UUID_DATA_RX.lower():
        parsed['packet_type'] = 'MEASUREMENT_DATA_CANDIDATE_FROM_RX_CHAR'
        parsed['raw_hex'] = data.hex()
        parsed['data_length'] = len(data)
        return parsed
    
    else:
        # Any other UUID not explicitly handled
        return f"INFO: Data from unhandled characteristic {source_uuid} (Length {len(data)}): {data.hex()}"


# --- BLE Notification Handler ---
def notification_handler(sender, data):
    """
    This function is called asynchronously whenever a subscribed characteristic
    sends a BLE notification.
    """
    global current_measurements 
    current_time_log = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    
    sender_uuid = str(sender.uuid) # Get the UUID of the characteristic that sent the notification

    # Log raw data to console
    console_log_line = f"[{current_time_log}] Raw data (Length: {len(data)}, Hex: {data.hex()}) FROM {sender_uuid}"
    print(console_log_line)

    # Log raw data to file
    try:
        with open(RAW_DATA_LOG_FILE, 'a') as f:
            f.write(f"{console_log_line}\n")
    except IOError as e:
        print(f"Error writing to raw data log file: {e}")
    
    parsed_result = parse_opple_data(data, sender_uuid) # Pass the UUID to the parser
    
    if isinstance(parsed_result, str) and parsed_result.startswith("ERROR"):
        print(f"[{current_time_log}] PARSING ISSUE: {parsed_result}")
    elif isinstance(parsed_result, str) and parsed_result.startswith("INFO"):
        print(f"[{current_time_log}] INFO MESSAGE: {parsed_result}")
    elif parsed_result:
        # Print detailed parsed results
        if parsed_result.get('packet_type') == '20_byte_measurement_or_status':
            print(f"[{current_time_log}] Parsed 20-byte data: State='{parsed_result.get('measurement_state')}', "
                  f"RawVal_1={parsed_result.get('RawVal_1')}, RawVal_2={parsed_result.get('RawVal_2')}, "
                  f"RawVal_3={parsed_result.get('RawVal_3')}")
            # Append to current_measurements if it's a light-detected measurement
            if parsed_result.get('measurement_state') == 'LIGHT_DETECTED':
                parsed_result['timestamp'] = current_time_log
                # Initialize app values to None or placeholders, to be filled manually in CSV
                parsed_result['App_Lux'] = None
                parsed_result['App_CCT'] = None
                parsed_result['App_Ra'] = None
                parsed_result['App_x'] = None
                parsed_result['App_y'] = None
                parsed_result['App_u'] = None
                parsed_result['App_v'] = None
                parsed_result['App_Battery_Percent'] = None # Placeholder for manual battery %
                current_measurements.append(parsed_result)
        elif parsed_result.get('packet_type') == '11_byte_status_or_battery':
            print(f"[{current_time_log}] Parsed 11-byte data: RawBattery_mV={parsed_result.get('RawBattery_mV')}")
            # If we also capture battery % from app, we can map this raw mV value.
        else:
            print(f"[{current_time_log}] Processed packet from {sender_uuid}: {parsed_result}")
    else:
        print(f"[{current_time_log}] PARSING RESULT: None (Unhandled data from {sender_uuid})")


# --- Function to save measurements to a CSV file ---
def save_measurements_to_csv(filename: str, data_list: list):
    """
    Saves a list of measurement dictionaries to a CSV file.
    Includes placeholders for app-reported values for manual entry.
    """
    if not data_list:
        print(f"No data to save to {filename}.")
        return

    # Define all possible fields, including the raw values and placeholders for app values
    fieldnames = [
        'timestamp', 'raw_hex', 'RawVal_1', 'RawVal_2', 'RawVal_3', 'RawBattery_mV',
        'App_Lux', 'App_CCT', 'App_Ra', 'App_x', 'App_y', 'App_u', 'App_v', 'App_Battery_Percent'
    ]
    
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader() 
            for row in data_list:
                # Ensure all fieldnames exist in the row, even if None
                clean_row = {field: row.get(field) for field in fieldnames}
                writer.writerow(clean_row)
        print(f"Data successfully saved to {filename}")
        print(f"**IMPORTANT**: Open '{filename}' and manually fill in the 'App_Lux', 'App_CCT', etc., columns for each row based on your observations from the Opple app.")
    except IOError as e:
        print(f"Error saving data to {filename}: {e}")

# --- Function to calculate average values from a list of measurements ---
def calculate_average(measurements: list):
    """
    Calculates the average values for RawVal_1, RawVal_2, RawVal_3, and RawBattery_mV.
    """
    if not measurements:
        return {}
    
    avg_data = {}
    keys_to_average = ['RawVal_1', 'RawVal_2', 'RawVal_3', 'RawBattery_mV'] 
    
    for key in keys_to_average:
        values = [m[key] for m in measurements if key in m and m[key] is not None]
        if values:
            avg_data[key] = sum(values) / len(values)
        else:
            avg_data[key] = None
            
    return avg_data

# --- Main asynchronous function to handle BLE connection and measurement flow ---
async def main():
    global current_measurements, current_mode, current_sample_name

    target_address = OPPLE_MAC_ADDRESS
    if AUTO_DISCOVER_OPPLE:
        print(f"Scanning for Opple device ('{OPPLE_DEVICE_NAME_PREFIX}')...")
        scanner = BleakScanner() 
        devices = await scanner.discover(timeout=10)

        found_device = None
        for d in devices:
            # Use the specific MAC from the log (FF:00:16:00:0D:72)
            if d.address.upper() == OPPLE_MAC_ADDRESS.upper():
                print(f"Found Opple: {d.name} ({d.address})")
                found_device = d
                break
        
        if not found_device:
            print(f"No Opple device with name '{OPPLE_DEVICE_NAME_PREFIX}' or address '{OPPLE_MAC_ADDRESS}' found.")
            print("Ensure Opple is powered on and ready to connect.")
            print("If still not found, try cycling power on Opple, and Bluetooth on your computer.")
            return
        
        target_address = found_device.address
    else:
        if target_address == "XX:XX:XX:XX:XX:XX": 
            print("Error: Please set a valid Opple MAC address or enable AUTO_DISCOVER_OPPLE = True.")
            return

    print(f"Attempting to connect to Opple Light Master Pro ({target_address})...")
    client = None
    try:
        client = BleakClient(target_address)
        await client.connect()

        if client.is_connected:
            print(f"Successfully connected to Opple Light Master Pro!")

            rx_char_found = False
            tx_char_found = False
            for service in client.services:
                for char in service.characteristics:
                    if char.uuid.lower() == OPPLE_CHARACTERISTIC_UUID_DATA_RX.lower():
                        rx_char_found = True
                        print(f"Rx Characteristic Object: {char.uuid} (Handle: {char.handle})")
                        await client.start_notify(char.uuid, notification_handler)
                        print(f"Started subscribing to notifications from characteristic (Rx): {char.uuid}")
                    elif char.uuid.lower() == OPPLE_CHARACTERISTIC_UUID_COMMAND_TX.lower():
                        tx_char_found = True
                        print(f"Tx Characteristic Object: {char.uuid} (Handle: {char.handle})")
                        # We also subscribe to notifications on the TX characteristic as per your output
                        await client.start_notify(char.uuid, notification_handler)
                        print(f"Started subscribing to notifications from characteristic (Tx): {char.uuid}")

            if not rx_char_found:
                print(f"Warning: Data characteristic (Rx) '{OPPLE_CHARACTERISTIC_UUID_DATA_RX}' not found.")
            if not tx_char_found:
                print(f"Warning: Command characteristic (Tx) '{OPPLE_CHARACTERISTIC_UUID_COMMAND_TX}' not found.")

            if not (rx_char_found or tx_char_found):
                print("No relevant characteristics found. Please verify UUIDs.")
                print("Available characteristics (UUID, Properties):")
                for s in client.services:
                    for c in s.characteristics:
                        print(f"  - {c.uuid} (Props: {c.properties})")
                await client.disconnect()
                return

            print("\n--- MEASUREMENT CONTROL ---")
            
            while True:
                print("\n--- Select Measurement Mode ---")
                print("1. Halogen on White Reference Plate (Setup Verification)")
                print("2. Sunlight on White Reference Plate (Sunlight Calibration)")
                print("3. Measure Sample under Halogen")
                print("4. Measure Sample under Sunlight")
                print("5. Exit Program")
                
                choice = input("Enter your choice (1-5): ")

                if choice == '1':
                    current_mode = "halogen_reference"
                    print("\n--- MEASUREMENT MODE: HALOGEN ON REFERENCE PLATE ---")
                    print("Ensure the PTFE plate is in the sphere and the halogen is ON and stable.")
                    print(f"\n--- COLLECTING DATA FOR: HALOGEN REFERENCE ---")
                    num_measurements = 15 # Example: Collect 15 measurements
                    polling_interval = 1 # Poll every 1 second

                    print(f"Starting {num_measurements} measurements, polling every {polling_interval} second(s).")
                    print("\n*** CRITICAL: As EACH raw data line (especially the 20-byte ones with 'LIGHT_DETECTED') appears in the console, IMMEDIATELY look at the Opple app and manually record the FOLLOWING values: ***")
                    print("   - Lux")
                    print("   - CCT")
                    print("   - CRI (Ra)")
                    print("   - Chromaticity Coordinates (x, y)")
                    print("   - Chromaticity Coordinates (u, v)")
                    print("   - Battery Percentage")
                    print("\n   The more precise and numerous these correlations are, the sooner we can find the algorithms!")
                    print("Press Ctrl+C at any time to stop and process collected data.")

                    current_measurements = [] # Clear previous measurements for this session
                    for i in range(num_measurements):
                        print(f"\n--- Polling measurement {i+1}/{num_measurements} ---")
                        # Send START command
                        print(f"Sending START command to {OPPLE_CHARACTERISTIC_UUID_COMMAND_TX} with value {OPPLE_COMMAND_START_MEASUREMENT.hex()}")
                        await client.write_gatt_char(OPPLE_CHARACTERISTIC_UUID_COMMAND_TX, OPPLE_COMMAND_START_MEASUREMENT)
                        print("START command sent successfully.")
                        await asyncio.sleep(polling_interval - 0.1) # Small delay before STOP

                        # Send STOP command
                        print(f"Sending STOP command to {OPPLE_CHARACTERISTIC_UUID_COMMAND_TX} with value {OPPLE_COMMAND_STOP_MEASUREMENT.hex()}")
                        await client.write_gatt_char(OPPLE_CHARACTERISTIC_UUID_COMMAND_TX, OPPLE_COMMAND_STOP_MEASUREMENT)
                        print("STOP command sent successfully.")
                        
                        await asyncio.sleep(0.1) # Short delay after stop command
                        
                    measurement_session_data["halogen_reference"] = current_measurements
                    print("\n--- HALOGEN REFERENCE MEASUREMENT COMPLETE ---")
                    avg_data = calculate_average(current_measurements)
                    print(f"Average Raw Measurements (Halogen Reference): {avg_data}")
                    save_measurements_to_csv("halogen_reference_measurements.csv", current_measurements)

                elif choice == '2':
                    current_mode = "solar_reference"
                    print("Sunlight calibration mode selected. (Functionality to be implemented)")
                    # Similar logic to '1' but for sunlight
                elif choice == '3':
                    current_mode = "sample_halogen"
                    sample_name = input("Enter sample name for Halogen measurement: ")
                    current_sample_name = sample_name
                    print(f"Measuring sample '{sample_name}' under Halogen. (Functionality to be implemented)")
                    # Similar logic to '1' but store in sample_halogen[sample_name]
                elif choice == '4':
                    current_mode = "sample_solar"
                    sample_name = input("Enter sample name for Sunlight measurement: ")
                    current_sample_name = sample_name
                    print(f"Measuring sample '{sample_name}' under Sunlight. (Functionality to be implemented)")
                    # Similar logic to '1' but store in sample_solar[sample_name]
                elif choice == '5':
                    print("Exiting program.")
                    break
                else:
                    print("Invalid choice. Please try again.")

        else:
            print("Failed to connect to the device. Ensure Opple is powered on and within range.")

    except Exception as e:
        print(f"An error occurred during BLE communication: {e}")
        print("Please ensure Bluetooth is enabled on your system and you have the necessary permissions.")
        if sys.platform.startswith('linux') and "permission denied" in str(e).lower():
            print("On Linux, you might need to run this script with 'sudo python your_script_name.py' or configure udev rules.")
    finally:
        if client and client.is_connected:
            print("\nStopping notifications and disconnecting from Opple Light Master Pro...")
            # Stop notifying for both characteristics
            try:
                await client.stop_notify(OPPLE_CHARACTERISTIC_UUID_DATA_RX)
            except Exception as e:
                print(f"Could not stop notify for Rx char: {e}")
            try:
                await client.stop_notify(OPPLE_CHARACTERISTIC_UUID_COMMAND_TX)
            except Exception as e:
                print(f"Could not stop notify for Tx char: {e}")
            
            await client.disconnect()
            print("Disconnected successfully.")
        else:
            print("Client was not connected or already disconnected.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram terminated by user (Ctrl+C).")