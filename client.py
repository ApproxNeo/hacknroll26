import asyncio
from bleak import BleakScanner, BleakClient

# --- CONFIGURATION ---
# MUST match the Server's UUIDs
SERVICE_UUID = "A07498CA-AD5B-474E-940D-16F1FBE7E8CD"
CHAR_UUID = "51FF12BB-3ED8-46E5-B4F9-D64E2FEC021B"

async def run_client():
    print("Searching for BLE Server...")
    
    # 1. Scan for the device offering our specific Service UUID
    device = next((d for d in await BleakScanner.discover() 
                   if SERVICE_UUID.lower() in d.metadata.get('uuids', [])), None)

    if not device:
        print(f"Could not find device with Service UUID: {SERVICE_UUID}")
        return

    print(f"Found Device: {device.name} ({device.address})")

    # 2. Connect to the device
    async with BleakClient(device) as client:
        print("Connected!")
        
        while True:
            # 3. Get user input
            msg = input("Enter message to send (or 'q' to quit): ")
            if msg.lower() == 'q':
                break
            
            # 4. Write the message to the characteristic
            # We must encode the string to bytes
            await client.write_gatt_char(CHAR_UUID, msg.encode('utf-8'))
            print("Message sent.")

if __name__ == "__main__":
    asyncio.run(run_client())