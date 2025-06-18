import asyncio
import struct
import datetime
import csv
import os
import sys

# --- Opple Device Configuration (YOU MUST CUSTOMIZE THESE VALUES!) ---
# These values are EXAMPLE placeholders.
# Use a BLE scanner app (e.g., LightBlue, nRF Connect) to find the correct data for your specific Opple device.
OPPLE_DEVICE_NAME_PREFIX = "OPPLELM" # The device name broadcast by your Opple (e.g., "OPPLELM Pro")
OPPLE_MAC_ADDRESS = "XX:XX:XX:XX:XX:XX" # Replace with your Opple's actual Bluetooth MAC address.
                                      # If you leave this as "XX:XX..." AND set AUTO_DISCOVER_OPPLE to True,
                                      # the script will attempt to find the device by its name prefix.

# The UUID of the Bluetooth Low Energy (BLE) characteristic from which Opple sends
# its measurement notifications. THIS IS CRITICAL AND WILL LIKELY BE UNIQUE TO YOUR DEVICE.
# Look for a characteristic with the 'NOTIFY' property when scanning your Opple's services.
OPPLE_CHARACTERISTIC_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb" # Example UUID – YOU MUST VERIFY/CHANGE THIS!

# Set to True to automatically scan and discover the Opple device by its name prefix.
# Set to False if you prefer to use a fixed MAC address.
AUTO_DISCOVER_OPPLE = True

# --- Variables for storing received measurement data ---
current_measurements = [] # Stores measurements for the current active session
measurement_session_data = {
    "halogen_reference": [],    # Measurements for halogen light on the white reference plate
    "solar_reference": [],      # Measurements for sunlight on the white reference plate
    "sample_halogen": {},       # Dictionary: Key = sample name, Value = list of measurements under halogen
    "sample_solar": {}          # Dictionary: Key = sample name, Value = list of measurements under sunlight
}
current_mode = None         # Tracks the current measurement mode (e.g., "halogen_reference")
current_sample_name = None  # Stores the name of the sample being measured

# --- Function to parse raw data from Opple (Likely requires YOUR customization!) ---
def parse_opple_data(data: bytes):
    """
    Parses the raw byte array received from the Opple Light Master Pro into readable measurement values.
    This function is SPECULATIVE and requires empirical verification and potential adjustment
    based on the actual byte structure transmitted by your Opple device.
    The Opple's packet format is not publicly documented, so reverse engineering may be needed.
    """
    # Basic check for minimum expected packet length. Adjust if your device sends shorter/longer packets.
    if len(data) < 20: 
        # Uncomment the line below for debugging raw, short data packets
        # print(f"Received data too short ({len(data)} bytes). Raw hex: {data.hex()}")
        return None

    parsed = {}
    try:
        # These are HYPOTHETICAL BYTE OFFSETS and DATA TYPES.
        # Common packing patterns for Opple-like devices often use little-endian byte order ('<').
        # 'H' for unsigned short (2 bytes), 'f' for float (4 bytes).
        
        # Example: Correlated Color Temperature (CCT) - typically 2 bytes
        parsed['CCT'] = struct.unpack('<H', data[2:4])[0]
        
        # Example: Color Rendering Index (CRI) - typically 2 bytes
        parsed['CRI'] = struct.unpack('<H', data[4:6])[0]
        
        # Example: Illuminance (Lux) - often 4 bytes, float or scaled integer
        parsed['Lux'] = struct.unpack('<f', data[6:10])[0]
        
        # Example: Duv (Delta uv) - often 4 bytes, float or scaled integer
        parsed['Duv'] = struct.unpack('<f', data[10:14])[0]

        # Apply scaling factors if values appear unusually large.
        # This is common for Lux (e.g., raw value is Lux * 100) or Duv (raw value is Duv * 1000).
        if parsed['Lux'] > 100000: # Arbitrary threshold if Lux seems too high (e.g., for 100 Lux it might send 10000)
             parsed['Lux'] /= 100.0 # Adjust this divisor if your observed data differs
        
        if abs(parsed['Duv']) > 0.1: # Duv is typically a very small number (e.g., 0.003)
             parsed['Duv'] /= 1000.0 # Adjust this divisor if your observed data differs
        
        # Optional: Add other parameters like R9 (Special Color Rendering Index for Red) if found.
        # For example, if R9 is at bytes 14-15 as an unsigned short:
        # parsed['R9'] = struct.unpack('<H', data[14:16])[0] 

        return parsed

    except struct.error as e:
        # Uncomment for debugging specific byte parsing errors
        # print(f"Struct parsing error: {e} for data: {data.hex()}")
        return None
    except Exception as e:
        # Uncomment for debugging any other parsing exceptions
        # print(f"Unknown parsing error: {e} for data: {data.hex()}")
        return None

# --- BLE Notification Handler ---
def notification_handler(sender, data):
    """
    This function is called asynchronously whenever the Opple device sends a BLE notification
    containing new measurement data.
    """
    global current_measurements # Allow modification of the global list to store measurements
    current_time = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3] # Get current time (ms precision)
    
    parsed_data = parse_opple_data(data)
    if parsed_data:
        parsed_data['timestamp'] = current_time # Add a timestamp to the parsed data
        current_measurements.append(parsed_data) # Store the parsed data
        print(f"[{current_time}] CCT={parsed_data.get('CCT', 'N/A')}K, "
              f"CRI={parsed_data.get('CRI', 'N/A')}, "
              f"Lux={parsed_data.get('Lux', 'N/A'):.2f}, "
              f"Duv={parsed_data.get('Duv', 'N/A'):.4f}")
    else:
        print(f"[{current_time}] Failed to parse data or data was incomplete. Raw hex: {data.hex()}")

# --- Function to save measurements to a CSV file ---
def save_measurements_to_csv(filename: str, data_list: list):
    """
    Saves a list of measurement dictionaries to a CSV file.
    """
    if not data_list:
        print(f"No data to save to {filename}.")
        return

    # Determine CSV header fields from the keys of the first measurement dictionary
    fieldnames = list(data_list[0].keys())
    
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader() # Write the header row
            writer.writerows(data_list) # Write all measurement rows
        print(f"Data successfully saved to {filename}")
    except IOError as e:
        print(f"Error saving data to {filename}: {e}")

# --- Function to calculate average values from a list of measurements ---
def calculate_average(measurements: list):
    """
    Calculates the average values for CCT, CRI, Lux, and Duv from a list of measurement dictionaries.
    """
    if not measurements:
        return {}
    
    avg_data = {}
    # List of keys for which to calculate the average. Extend if you parse more values.
    keys_to_average = ['CCT', 'CRI', 'Lux', 'Duv'] 
    
    for key in keys_to_average:
        # Filter out None values and ensure the key exists in the dictionary
        values = [m[key] for m in measurements if key in m and m[key] is not None]
        if values:
            avg_data[key] = sum(values) / len(values)
        else:
            avg_data[key] = None # Set to None if no valid values were found for this key
            
    return avg_data

# --- Main asynchronous function to handle BLE connection and measurement flow ---
async def main():
    global current_measurements, current_mode,