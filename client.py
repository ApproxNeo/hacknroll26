import asyncio
from bleak import BleakClient, BleakScanner

SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0"
CHAR_UUID    = "12345678-1234-5678-1234-56789abcdef1"
TARGET_NAME  = "BLEChat"

def on_notify(_, data: bytearray):
    print("[NOTIFY from server]", data.decode("utf-8", errors="replace"))

async def pick_device():
    devices = await BleakScanner.discover(timeout=5.0)
    for d in devices:
        if d.name == TARGET_NAME:
            return d
    return None

async def main():
    dev = await pick_device()
    if not dev:
        raise SystemExit(f"Did not find device named '{TARGET_NAME}'. Move closer and try again.")

    print("Connecting to:", dev.address, dev.name)
    async with BleakClient(dev.address) as client:
        await client.start_notify(CHAR_UUID, on_notify)
        print("Connected. Type messages to WRITE to server (client -> server). Ctrl+C to exit.\n")

        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, input, "")
            if not line:
                continue
            await client.write_gatt_char(CHAR_UUID, line.encode("utf-8"), response=True)

if __name__ == "__main__":
    asyncio.run(main())