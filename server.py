import asyncio
import logging
from bless import (
    BlessServer,
    BlessGATTCharacteristic,
    GATTCharacteristicProperties,
    GATTAttributePermissions
)

# --- CONFIGURATION ---
SERVICE_UUID = "A07498CA-AD5B-474E-940D-16F1FBE7E8CD"
CHAR_UUID = "51FF12BB-3ED8-46E5-B4F9-D64E2FEC021B"
SERVER_NAME = "Python_BLE_Chat"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVER_NAME)

def write_request_callback(characteristic: BlessGATTCharacteristic, value: bytearray, **kwargs):
    try:
        message = value.decode('utf-8')
        print(f"\n[📨 RECEIVED MESSAGE]: {message}")
    except Exception as e:
        print(f"\n[!] Received non-text data: {value}")

def read_request_callback(characteristic: BlessGATTCharacteristic, **kwargs):
    # If the client tries to read, we must return bytes
    return b"Hello from Server"

async def run_server():
    server = BlessServer(name=SERVER_NAME)
    
    # We need to handle reads manually now that value is None
    server.read_request_func = read_request_callback
    server.write_request_func = write_request_callback

    await server.add_new_service(SERVICE_UUID)

    await server.add_new_characteristic(
        SERVICE_UUID,
        CHAR_UUID,
        properties=GATTCharacteristicProperties.write | GATTCharacteristicProperties.read,
        permissions=GATTAttributePermissions.writeable | GATTAttributePermissions.readable,
        value=None # Fixed: Must be None for writable characteristics on macOS
    )

    print(f"Starting BLE Server: {SERVER_NAME}...")
    await server.start()
    print(f"Running... Advertising Service: {SERVICE_UUID}")
    
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("Stopping server...")