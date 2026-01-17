import asyncio
from bleak import BleakScanner, BleakClient

# --- CONFIGURATION ---
TARGET_NAME = "Python_BLE_Chat2"
SERVICE_UUID = "A07498CA-AD5B-474E-940D-16F1FBE7E8CD"
CHAR_UUID = "51FF12BB-3ED8-46E5-B4F9-D64E2FEC021B"

async def run_client():
    device = None
    
    # --- RETRY LOOP ---
    print(f"🕵️  Scanning for '{TARGET_NAME}'... (Press Ctrl+C to stop)")
    while True:
        devices = await BleakScanner.discover(timeout=5.0)
        
        # Look for our specific device name
        for d in devices:
            if d.name == TARGET_NAME:
                device = d
                break
        
        if device:
            print(f"✅ Found {device.name}!")
            break
        else:
            print("... Not found yet, retrying scan ...")
            # Loop continues automatically

    # --- CONNECTION ---
    print(f"🔗 Connecting to {device.address}...")
    try:
        async with BleakClient(device.address) as client:
            print(f"🎉 Connected! Type your message.")
            
            while True:
                msg = input("You: ")
                if msg.lower() == 'q':
                    break
                
                try:
                    await client.write_gatt_char(CHAR_UUID, msg.encode('utf-8'))
                    print("   -> Sent")
                except Exception as e:
                    print(f"❌ Connection lost: {e}")
                    break
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        print("💡 TIP: Restart the SERVER script and try again.")

if __name__ == "__main__":
    try:
        asyncio.run(run_client())
    except KeyboardInterrupt:
        print("\nExiting...")