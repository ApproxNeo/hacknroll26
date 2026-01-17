# mac_peripheral.py
from Foundation import NSObject, NSRunLoop, NSDate, NSData
import CoreBluetooth as CB

SERVICE_UUID = "12345678-1234-5678-1234-56789ABCDEF0"
CHAR_UUID    = "12345678-1234-5678-1234-56789ABCDEF1"
DEVICE_NAME  = "BLEChat"

def to_nsdata(b: bytes) -> NSData:
    return NSData.dataWithBytes_length_(b, len(b))

class PeripheralDelegate(NSObject):
    def init(self):
        # self = super().init()
        if self is None:
            return None
        self.pm = None
        self.char = None
        self.last_value = b""
        return self

    def peripheralManagerDidUpdateState_(self, peripheral):
        # 5 == poweredOn (CBManagerStatePoweredOn); avoids importing enum symbols
        if int(peripheral.state()) != 5:
            print("Bluetooth not powered on (state=%s)" % peripheral.state())
            return

        self.pm = peripheral

        self.char = CB.CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            CB.CBUUID.UUIDWithString_(CHAR_UUID),
            (CB.CBCharacteristicPropertyRead |
             CB.CBCharacteristicPropertyWrite |
             CB.CBCharacteristicPropertyNotify),
            None,
            (CB.CBAttributePermissionsReadable |
             CB.CBAttributePermissionsWriteable),
        )

        svc = CB.CBMutableService.alloc().initWithType_primary_(
            CB.CBUUID.UUIDWithString_(SERVICE_UUID),
            True
        )
        svc.setCharacteristics_([self.char])

        self.pm.addService_(svc)

    def peripheralManager_didAddService_error_(self, peripheral, service, error):
        if error is not None:
            print("addService error:", error)
            return

        adv = {
            CB.CBAdvertisementDataLocalNameKey: DEVICE_NAME,
            CB.CBAdvertisementDataServiceUUIDsKey: [CB.CBUUID.UUIDWithString_(SERVICE_UUID)],
        }
        peripheral.startAdvertising_(adv)
        print("Advertising as", DEVICE_NAME)

    def peripheralManagerDidStartAdvertising_error_(self, peripheral, error):
        if error is not None:
            print("Advertising error:", error)

    def peripheralManager_didReceiveWriteRequests_(self, peripheral, requests):
        for req in requests:
            data = req.value()
            if data is None:
                continue
            b = bytes(data)
            self.last_value = b
            print("[RX from client]", b.decode("utf-8", errors="replace"))
            peripheral.respondToRequest_withResult_(req, CB.CBATTErrorSuccess)

        peripheral.updateValue_forCharacteristic_onSubscribedCentrals_(
            to_nsdata(self.last_value),
            self.char,
            None
        )

    def peripheralManager_didReceiveReadRequest_(self, peripheral, request):
        request.setValue_(to_nsdata(self.last_value))
        peripheral.respondToRequest_withResult_(request, CB.CBATTErrorSuccess)

def main():
    delegate = PeripheralDelegate.alloc().init()
    pm = CB.CBPeripheralManager.alloc().initWithDelegate_queue_(delegate, None)
    delegate.pm = pm

    rl = NSRunLoop.currentRunLoop()
    while True:
        rl.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))

if __name__ == "__main__":
    main()