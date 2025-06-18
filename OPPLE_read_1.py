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
current_measurements = [] # This list will be cleared and refilled for each measurement step
measurement_session_data = {} # Will store aggregated data if needed in future, currently not used as each step saves its own CSV

# --- File for raw data logging (continuous log of all notifications) ---
RAW_DATA_LOG_FILE = "raw_data_full_session_log.txt"

# --- Function to parse raw data from Opple ---
def parse_opple_data(data: bytes, source_uuid: str):
    """
    Parses the raw byte array received from the Opple Light Master Pro.
    """
    parsed = {}
    
    # Handle data from the 'command' characteristic (6e400003)
    if source_uuid.lower() == OPPLE_CHARACTERISTIC_UUID_COMMAND_TX.lower():
        if len(data) == 20:
            parsed['packet_type'] = '20_byte_measurement_or_status'
            parsed['raw_hex'] = data.hex()

            try:
                # Unpack as three little-endian unsigned shorts from bytes 14-19
                raw_val1 = struct.unpack('<H', data[14:16])[0]
                raw_val2 = struct.unpack('<H', data[16:18])[0]
                raw_val3 = struct.unpack('<H', data[18:20])[0]
                
                parsed['RawVal_1'] = raw_val1
                parsed['RawVal_2'] = raw_val2
                parsed['RawVal_3'] = raw_val3
                
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
            
            try:
                # Unpack as big-endian unsigned short from bytes 8-9
                raw_battery_mv = struct.unpack('>H', data[8:10])[0]
                parsed['RawBattery_mV'] = raw_battery_mv
                
                return parsed
            except struct.error as e:
                return f"ERROR: Could not unpack 11-byte battery data from {source_uuid}: {e}"
            except Exception as e:
                return f"ERROR: Unknown parsing error for 11-byte data from {source_uuid}: {e}"
        else:
            parsed['packet_type'] = f'UNKNOWN_LEN_FROM_COMMAND_CHAR ({len(data)}B)'
            parsed['raw_hex'] = data.hex()
            return parsed

    elif source_uuid.lower() == OPPLE_CHARACTERISTIC_UUID_DATA_RX.lower():
        parsed['packet_type'] = 'MEASUREMENT_DATA_CANDIDATE_FROM_RX_CHAR'
        parsed['raw_hex'] = data.hex()
        parsed['data_length'] = len(data)
        return parsed
    
    else:
        return f"INFO: Data from unhandled characteristic {source_uuid} (Length {len(data)}): {data.hex()}"


# --- BLE Notification Handler ---
def notification_handler(sender, data):
    """
    This function is called asynchronously whenever a subscribed characteristic
    sends a BLE notification. It processes raw data and appends to current_measurements.
    """
    global current_measurements 
    current_time_log = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    
    sender_uuid = str(sender.uuid)

    console_log_line = f"[{current_time_log}] Raw data (Length: {len(data)}, Hex: {data.hex()}) FROM {sender_uuid}"
    print(console_log_line)

    try:
        with open(RAW_DATA_LOG_FILE, 'a') as f:
            f.write(f"{console_log_line}\n")
    except IOError as e:
        print(f"Error writing to raw data log file: {e}")
    
    parsed_result = parse_opple_data(data, sender_uuid)
    
    if isinstance(parsed_result, str) and parsed_result.startswith("ERROR"):
        print(f"[{current_time_log}] PARSING ISSUE: {parsed_result}")
    elif isinstance(parsed_result, str) and parsed_result.startswith("INFO"):
        # Suppress verbose INFO messages unless debugging
        pass # print(f"[{current_time_log}] INFO MESSAGE: {parsed_result}")
    elif parsed_result:
        if parsed_result.get('packet_type') == '20_byte_measurement_or_status':
            print(f"[{current_time_log}] Parsed 20-byte data: State='{parsed_result.get('measurement_state')}', "
                  f"RawVal_1={parsed_result.get('RawVal_1')}, RawVal_2={parsed_result.get('RawVal_2')}, "
                  f"RawVal_3={parsed_result.get('RawVal_3')}")
            if parsed_result.get('measurement_state') == 'LIGHT_DETECTED':
                # Create a copy to ensure all fields are initialized for CSV export
                measurement_entry = {
                    'timestamp': current_time_log,
                    'raw_hex': parsed_result.get('raw_hex'),
                    'RawVal_1': parsed_result.get('RawVal_1'),
                    'RawVal_2': parsed_result.get('RawVal_2'),
                    'RawVal_3': parsed_result.get('RawVal_3'),
                    'RawBattery_mV': None, # This will be filled if an 11-byte packet follows shortly
                    'App_Lux': None,        # Placeholder for manual entry
                    'App_CCT': None,
                    'App_Ra': None,
                    'App_x': None,
                    'App_y': None,
                    'App_u': None,
                    'App_v': None,
                    'App_Battery_Percent': None
                }
                current_measurements.append(measurement_entry)
                # print(f"[{current_time_log}] !!! REMEMBER TO RECORD APP VALUES FOR THIS READING !!!")

        elif parsed_result.get('packet_type') == '11_byte_status_or_battery':
            print(f"[{current_time_log}] Parsed 11-byte data: RawBattery_mV={parsed_result.get('RawBattery_mV')}")
            # Try to associate the battery mV with the most recent 20-byte measurement
            if current_measurements and current_measurements[-1].get('RawBattery_mV') is None:
                current_measurements[-1]['RawBattery_mV'] = parsed_result.get('RawBattery_mV')
    else:
        pass # print(f"[{current_time_log}] PARSING RESULT: None (Unhandled data from {sender_uuid})")


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
        print(f"\nData successfully saved to {filename}")
        print(f"**IMPORTANT**: Open '{filename}' and manually fill in the 'App_Lux', 'App_CCT', etc., columns for each row based on your observations from the Opple app.")
    except IOError as e:
        print(f"Error saving data to {filename}: {e}")

# --- Helper function to run a measurement phase ---
async def run_measurement_phase(client: BleakClient, phase_name: str, num_measurements: int = 15, polling_interval: int = 1):
    global current_measurements
    current_measurements = [] # Clear measurements for this new phase
    
    print(f"\n--- Starting Measurement Phase: {phase_name.upper()} ---")
    if "NO FILTER" in phase_name.upper():
        input("ENSURE NO PAPER FILTER IS PRESENT. Press Enter to begin...")
    elif "2 LAYERS" in phase_name.upper():
        input("PLACE 2 LAYERS OF PAPER FILTER. Press Enter to begin...")
    elif "4 LAYERS" in phase_name.upper():
        input("PLACE 4 LAYERS OF PAPER FILTER. Press Enter to begin...")
    
    print(f"\nCollecting {num_measurements} measurements for '{phase_name}', polling every {polling_interval} second(s).")
    print("\n*** CRITICAL: As EACH raw data line (especially the 20-byte ones with 'LIGHT_DETECTED') appears, ***")
    print("   IMMEDIATELY look at the Opple app and manually record the FOLLOWING values for that timestamp:")
    print("   - Lux, CCT, CRI (Ra), Chromaticity Coordinates (x, y, u, v), and Battery Percentage.")
    print("   You will fill these into the generated CSV file later.")
    print("Press Ctrl+C at any time to stop this phase (data collected so far will be saved).")

    for i in range(num_measurements):
        print(f"\n--- Polling measurement {i+1}/{num_measurements} for {phase_name} ---")
        try:
            # Send START command
            await client.write_gatt_char(OPPLE_CHARACTERISTIC_UUID_COMMAND_TX, OPPLE_COMMAND_START_MEASUREMENT)
            # print("START command sent.")
            await asyncio.sleep(polling_interval - 0.1) # Small delay before STOP

            # Send STOP command
            await client.write_gatt_char(OPPLE_CHARACTERISTIC_UUID_COMMAND_TX, OPPLE_COMMAND_STOP_MEASUREMENT)
            # print("STOP command sent.")
            await asyncio.sleep(0.1) # Short delay after stop command
        except Exception as e:
            print(f"Error during command send for {phase_name} measurement {i+1}: {e}")
            break # Exit measurement loop if sending commands fails
            
    print(f"\n--- {phase_name.upper()} MEASUREMENT PHASE COMPLETE ---")
    output_filename = f"measurements_{phase_name.replace(' ', '_').lower()}.csv"
    save_measurements_to_csv(output_filename, current_measurements)


# --- Main asynchronous function to handle BLE connection and measurement flow ---
async def main():
    target_address = OPPLE_MAC_ADDRESS
    if AUTO_DISCOVER_OPPLE:
        print(f"Scanning for Opple device ('{OPPLE_DEVICE_NAME_PREFIX}')...")
        scanner = BleakScanner() 
        devices = await scanner.discover(timeout=10)

        found_device = None
        for d in devices:
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

            # Subscribe to both Rx and Tx characteristics for notifications
            # The Tx characteristic (6e400003) is where the measurement data seems to come from.
            # The Rx characteristic (6e400002) is where other data might come from, keep it subscribed.
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
                        await client.start_notify(char.uuid, notification_handler)
                        print(f"Started subscribing to notifications from characteristic (Tx): {char.uuid}")

            if not (rx_char_found and tx_char_found):
                print("Warning: One or both key characteristics not found. Check UUIDs or device services.")
                print("Available characteristics (UUID, Properties):")
                for s in client.services:
                    for c in s.characteristics:
                        print(f"  - {c.uuid} (Props: {c.properties})")
                # Decide if to continue or exit based on severity
                # For now, let's assume if Tx is found, we can proceed.
                if not tx_char_found:
                    print("Error: Command (Tx) characteristic not found. Cannot send commands or receive primary measurements. Exiting.")
                    await client.disconnect()
                    return

            print("\n--- BEGINNING AUTOMATED MEASUREMENT SESSION ---")
            print("Please ensure your Opple Light Master Pro is ready and connected.")
            print("You will be prompted to adjust paper filters between measurement phases.")
            
            # Step 1: No Filter
            await run_measurement_phase(client, "No Filter")

            # Step 2: 2 Layers of Paper
            await run_measurement_phase(client, "2 Layers")

            # Step 3: 4 Layers of Paper
            await run_measurement_phase(client, "4 Layers")

            print("\n--- ALL MEASUREMENT PHASES COMPLETE ---")
            print("Please check the generated CSV files (e.g., 'measurements_no_filter.csv')")
            print("and manually fill in the app-reported values for each raw data entry.")

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
            try:
                if tx_char_found: await client.stop_notify(OPPLE_CHARACTERISTIC_UUID_COMMAND_TX)
                if rx_char_found: await client.stop_notify(OPPLE_CHARACTERISTIC_UUID_DATA_RX)
            except Exception as e:
                print(f"Error stopping notifications: {e}")
            await client.disconnect()
            print("Disconnected successfully.")
        else:
            print("Client was not connected or already disconnected.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram terminated by user (Ctrl+C).")