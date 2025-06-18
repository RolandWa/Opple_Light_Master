import asyncio
from bleak import BleakClient, BleakScanner
import sys

# --- Opple Device Configuration ---
OPPLE_DEVICE_NAME_PREFIX = "LMaster_0d72"
OPPLE_MAC_ADDRESS = "FF:00:16:00:0D:72" # Replace with your device's MAC address if AUTO_DISCOVER_OPPLE is False

# Set to True to automatically scan and discover the Opple device by its name prefix.
# If False, the script will attempt to connect directly to OPPLE_MAC_ADDRESS.
AUTO_DISCOVER_OPPLE = True

async def find_opple_services():
    """
    Scans for the specified Opple device, connects to it, and prints
    all discovered BLE services and their characteristics.
    """
    target_address = OPPLE_MAC_ADDRESS
    found_device = None

    if AUTO_DISCOVER_OPPLE:
        print(f"Scanning for Opple device ('{OPPLE_DEVICE_NAME_PREFIX}') or address '{OPPLE_MAC_ADDRESS}'...")
        scanner = BleakScanner()
        devices = await scanner.discover(timeout=10) # Scan for 10 seconds

        for d in devices:
            # Prioritize exact MAC address match, then name prefix
            if d.address.upper() == OPPLE_MAC_ADDRESS.upper():
                found_device = d
                print(f"Found Opple by MAC: {d.name} ({d.address})")
                break
            elif d.name and OPPLE_DEVICE_NAME_PREFIX in d.name:
                found_device = d
                print(f"Found Opple by name prefix: {d.name} ({d.address})")
                # Don't break immediately, in case a more precise MAC match is found later if loop continues
        
        if not found_device:
            print(f"No Opple device with name '{OPPLE_DEVICE_NAME_PREFIX}' or address '{OPPLE_MAC_ADDRESS}' found.")
            print("Ensure Opple is powered on and ready to connect (e.g., waiting for connection).")
            print("If still not found, try cycling power on Opple, and Bluetooth on your computer.")
            return
        
        target_address = found_device.address
    else:
        if target_address == "FF:00:16:00:0D:72": # Check if the default placeholder is still there
            print("Warning: AUTO_DISCOVER_OPPLE is False, but OPPLE_MAC_ADDRESS is still the default placeholder.")
            print("Please update OPPLE_MAC_ADDRESS with your device's actual address or set AUTO_DISCOVER_OPPLE to True.")
            return

    print(f"\nAttempting to connect to Opple Light Master Pro ({target_address})...")
    client = None
    try:
        client = BleakClient(target_address)
        await client.connect()

        if client.is_connected:
            print(f"Successfully connected to Opple Light Master Pro!")
            print("\n--- Discovering Services and Characteristics ---")

            for service in client.services:
                print(f"\nService: UUID={service.uuid}, Description={service.description if service.description else 'N/A'}")
                if service.characteristics:
                    print("  Characteristics:")
                    for char in service.characteristics:
                        # Extract characteristic properties for better understanding
                        properties = ", ".join(prop for prop in char.properties)
                        print(f"    - UUID={char.uuid}, Handle={char.handle}, Properties=[{properties}]")
                        
                        # Optionally, read value of readable characteristics
                        if "read" in char.properties:
                            try:
                                value = await client.read_gatt_char(char.uuid)
                                print(f"      Value (read): {value.hex()} (Hex) / {value} (Bytes)")
                            except Exception as e:
                                print(f"      Could not read characteristic {char.uuid}: {e}")
                else:
                    print("  No characteristics for this service.")
        else:
            print("Failed to connect to the device. Ensure Opple is powered on and within range.")

    except Exception as e:
        print(f"An error occurred during BLE communication: {e}")
        print("Please ensure Bluetooth is enabled on your system and you have the necessary permissions.")
        if sys.platform.startswith('linux') and "permission denied" in str(e).lower():
            print("On Linux, you might need to run this script with 'sudo python your_script_name.py' or configure udev rules.")
    finally:
        if client and client.is_connected:
            print("\nDisconnecting from Opple Light Master Pro...")
            await client.disconnect()
            print("Disconnected successfully.")
        else:
            print("\nClient was not connected or already disconnected.")

if __name__ == "__main__":
    try:
        asyncio.run(find_opple_services())
    except KeyboardInterrupt:
        print("\nProgram terminated by user (Ctrl+C).")