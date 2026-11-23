#include <Arduino.h>
#include <NimBLEDevice.h>
#include <XboxControllerNotificationParser.h>

// =============================================================================
// Configuration
// =============================================================================

// Known EvoFox Gamepad MAC Addresses (Android mode & PC/Xbox mode)
#define KNOWN_EVOFOX_MAC_1     "0c:b7:4d:bb:53:11"
#define KNOWN_EVOFOX_MAC_2     "03:25:04:21:19:cc"
#define KNOWN_EVOFOX_MAC_3     "94:a9:90:ce:1c:f9"

// UART pins connected to Retro-Go (ESP32-S3)
#define BRIDGE_UART_TX_PIN     21    // Connect this C3 TX pin to ESP32-S3 RX pin
#define BRIDGE_UART_RX_PIN     20    // Connect this C3 RX pin to ESP32-S3 TX pin
#define BRIDGE_UART_BAUD       115200

// Hardware / Board Definitions
#define STATUS_LED_PIN         8     // Onboard LED on ESP32-C3 SuperMini / DevKit
#define LED_ACTIVE_LOW         true  // SuperMini LED is active LOW
#define BOOT_BUTTON_PIN        9     // Boot button on ESP32-C3 (active LOW)

// =============================================================================
// Packet Definition for Retro-Go (10 Bytes Framed)
// =============================================================================
#pragma pack(push, 1)
struct GamepadPacket {
    uint8_t sync1;       // 0xAA
    uint8_t sync2;       // 0x55
    uint8_t buttons1;    // Bit 0:UP, 1:DOWN, 2:LEFT, 3:RIGHT, 4:A, 5:B, 6:X, 7:Y
    uint8_t buttons2;    // Bit 0:START, 1:SELECT, 2:LB, 3:RB, 4:LT, 5:RT, 6:LS, 7:RS
    uint8_t buttons3;    // Bit 0:HOME/XBOX, 1:SHARE, Bit 7: CONNECTED_FLAG
    uint8_t joy_lx;      // 0..255 (128 is center)
    uint8_t joy_ly;      // 0..255 (128 is center)
    uint8_t joy_rx;      // 0..255 (128 is center)
    uint8_t joy_ry;      // 0..255 (128 is center)
    uint8_t checksum;    // XOR of bytes 2..8
};
#pragma pack(pop)

// Button Masks
#define MASK_UP       (1 << 0)
#define MASK_DOWN     (1 << 1)
#define MASK_LEFT     (1 << 2)
#define MASK_RIGHT    (1 << 3)
#define MASK_A        (1 << 4)
#define MASK_B        (1 << 5)
#define MASK_X        (1 << 6)
#define MASK_Y        (1 << 7)

#define MASK_START    (1 << 0)
#define MASK_SELECT   (1 << 1)
#define MASK_LB       (1 << 2)
#define MASK_RB       (1 << 3)
#define MASK_LT       (1 << 4)
#define MASK_RT       (1 << 5)
#define MASK_LS       (1 << 6)
#define MASK_RS       (1 << 7)

#define MASK_HOME     (1 << 0)
#define MASK_SHARE    (1 << 1)
#define MASK_CONN     (1 << 7)

// =============================================================================
// Unified Gamepad State
// =============================================================================
struct UnifiedGamepadState {
    bool up, down, left, right;
    bool a, b, x, y;
    bool start, select, lb, rb, lt, rt, ls, rs;
    bool home, share;
    uint8_t joy_lx, joy_ly, joy_rx, joy_ry;

    void reset() {
        up = down = left = right = false;
        a = b = x = y = false;
        start = select = lb = rb = lt = rt = ls = rs = false;
        home = share = false;
        joy_lx = joy_ly = joy_rx = joy_ry = 128;
    }
};

static UnifiedGamepadState padState;
static XboxControllerNotificationParser xboxParser;
HardwareSerial BridgeSerial(1);

static NimBLEClient* pConnectedClient = nullptr;
static NimBLEAddress targetAddress;
static NimBLEAdvertisedDevice* pTargetAdvDevice = nullptr;
static bool targetFound = false;
static bool isConnected = false;

static unsigned long lastPacketSendTime = 0;
static unsigned long lastLedBlinkTime = 0;
static bool ledBlinkState = false;

// Forward declarations
void startScanning();
bool connectToAddress(const NimBLEAddress& address);
void setLed(bool on);

// =============================================================================
// LED Helper
// =============================================================================
void setLed(bool on) {
    digitalWrite(STATUS_LED_PIN, LED_ACTIVE_LOW ? !on : on);
}

// =============================================================================
// Decode HID Notifications (9-byte Android mode & 16/17-byte Xbox mode)
// =============================================================================
static uint8_t prevGamepadReport[20] = {0};
static size_t prevGamepadReportLen = 0;

void processReport(const uint8_t* data, size_t length) {
    // Only track primary gamepad input reports (9-byte generic Android, 10-byte EvoFox Android, 16/17-byte Xbox)
    if (length == 9 || length == 10 || length == 16 || length == 17) {
        bool changed = (length != prevGamepadReportLen) || (memcmp(prevGamepadReport, data, length) != 0);
        if (changed) {
            memcpy(prevGamepadReport, data, length <= sizeof(prevGamepadReport) ? length : sizeof(prevGamepadReport));
            prevGamepadReportLen = length;

            Serial.printf("[REPORT %d-byte]: ", length);
            for (size_t i = 0; i < length; i++) {
                Serial.printf("%02X ", data[i]);
            }
            Serial.println();
        }
    }

    if (length == 16 || length == 17) {
        // Mode 2: PC / Xbox Mode (16 or 17 bytes)
        const uint8_t* pSrc = (length == 17) ? (data + 1) : data;
        xboxParser.update((uint8_t*)pSrc, 16);

        padState.up    = xboxParser.btnDirUp;
        padState.down  = xboxParser.btnDirDown;
        padState.left  = xboxParser.btnDirLeft;
        padState.right = xboxParser.btnDirRight;

        padState.a = xboxParser.btnA;
        padState.b = xboxParser.btnB;
        padState.x = xboxParser.btnX;
        padState.y = xboxParser.btnY;

        padState.start  = xboxParser.btnStart;
        padState.select = xboxParser.btnSelect;
        padState.lb     = xboxParser.btnLB;
        padState.rb     = xboxParser.btnRB;
        padState.lt     = (xboxParser.trigLT > 200);
        padState.rt     = (xboxParser.trigRT > 200);
        padState.ls     = xboxParser.btnLS;
        padState.rs     = xboxParser.btnRS;

        padState.home  = xboxParser.btnXbox;
        padState.share = xboxParser.btnShare;

        padState.joy_lx = (uint8_t)(xboxParser.joyLHori >> 8);
        padState.joy_ly = (uint8_t)(xboxParser.joyLVert >> 8);
        padState.joy_rx = (uint8_t)(xboxParser.joyRHori >> 8);
        padState.joy_ry = (uint8_t)(xboxParser.joyRVert >> 8);
    }
    else if (length == 10) {
        // Mode 1: EvoFox One S Android BLE Mode (10 bytes: LX LY RX RY Hat Btn1 Btn2 RT_Ana LT_Ana Counter)
        // Analog Sticks (Bytes 0-3)
        padState.joy_lx = data[0];
        padState.joy_ly = data[1];
        padState.joy_rx = data[2];
        padState.joy_ry = data[3];

        // D-Pad / Hat Switch (Byte 4: 0=UP, 1=UR, 2=R, 3=DR, 4=D, 5=DL, 6=L, 7=UL, 0xFF=Released)
        uint8_t hat = data[4];
        padState.up    = (hat == 0 || hat == 1 || hat == 7);
        padState.right = (hat == 1 || hat == 2 || hat == 3);
        padState.down  = (hat == 3 || hat == 4 || hat == 5);
        padState.left  = (hat == 5 || hat == 6 || hat == 7);

        // Buttons (Byte 5: A=0x01, B=0x02, X=0x08, Y=0x10, LB=0x40, RB=0x80)
        padState.a  = (data[5] & 0x01) != 0;
        padState.b  = (data[5] & 0x02) != 0;
        padState.x  = (data[5] & 0x08) != 0;
        padState.y  = (data[5] & 0x10) != 0;
        padState.lb = (data[5] & 0x40) != 0;
        padState.rb = (data[5] & 0x80) != 0;

        // Triggers & Navigation (Byte 6: LT=0x01, RT=0x02, SELECT=0x04, START=0x08, HOME=0x10, LS=0x20, RS=0x40)
        padState.lt     = ((data[6] & 0x01) != 0) || (data[8] > 40);
        padState.rt     = ((data[6] & 0x02) != 0) || (data[7] > 40);
        padState.select = (data[6] & 0x04) != 0;
        padState.start  = (data[6] & 0x08) != 0;
        padState.home   = (data[6] & 0x10) != 0;
        padState.ls     = (data[6] & 0x20) != 0;
        padState.rs     = (data[6] & 0x40) != 0;
    }
    else if (length == 9) {
        // Mode 1 generic: Android BLE Mode (9 bytes: 00 00 00 00 00 00 00 81 81)
        // data[0]: Buttons 1 (A, B, X, Y, LB, RB)
        padState.a  = (data[0] & 0x01);
        padState.b  = (data[0] & 0x02);
        padState.x  = (data[0] & 0x08);
        padState.y  = (data[0] & 0x10);
        padState.lb = (data[0] & 0x40);
        padState.rb = (data[0] & 0x80);

        // data[1]: Buttons 2 (Select, Start, Home, LS, RS, Triggers)
        padState.lt     = (data[1] & 0x01);
        padState.rt     = (data[1] & 0x02);
        padState.select = (data[1] & 0x04);
        padState.start  = (data[1] & 0x08);
        padState.home   = (data[1] & 0x10);
        padState.ls     = (data[1] & 0x20);
        padState.rs     = (data[1] & 0x40);

        // data[2]: D-Pad / Hat Switch
        uint8_t hat = data[2] & 0x0F;
        padState.up    = (hat == 1 || hat == 2 || hat == 8);
        padState.right = (hat >= 2 && hat <= 4);
        padState.down  = (hat >= 4 && hat <= 6);
        padState.left  = (hat >= 6 && hat <= 8);

        // Analog Sticks:
        // Bytes 7 & 8 are Left Stick (resting at 0x81 = 129)
        padState.joy_lx = data[7];
        padState.joy_ly = data[8];

        // Bytes 5 & 6 are Right Stick (centered at 128 / 0x80 or signed 0)
        padState.joy_rx = (data[5] != 0) ? data[5] : 128;
        padState.joy_ry = (data[6] != 0) ? data[6] : 128;
    }
}

void onNotifyCallback(NimBLERemoteCharacteristic* pChara, uint8_t* pData, size_t length, bool isNotify) {
    processReport(pData, length);
}

// =============================================================================
// Client Callbacks
// =============================================================================
class MyClientCallbacks : public NimBLEClientCallbacks {
    void onConnect(NimBLEClient* pClient) override {
        Serial.printf("[BLE] PHY Link Connected: %s\n", pClient->getPeerAddress().toString().c_str());
    }

    void onDisconnect(NimBLEClient* pClient, int reason) override {
        Serial.printf("\n[BLE] Controller Disconnected (Reason: 0x%02X / %d)\n", reason, reason);
        isConnected = false;
        pConnectedClient = nullptr;
        padState.reset();
        setLed(false);

        Serial.println("[BLE] Link dropped cleanly. Resuming background scan...");
        startScanning();
    }

    void onAuthenticationComplete(NimBLEConnInfo& connInfo) override {
        Serial.printf("[BLE] Security Complete: Encrypted=%s, Bonded=%s\n",
                      connInfo.isEncrypted() ? "YES" : "NO",
                      connInfo.isBonded() ? "YES" : "NO");
    }

    void onPassKeyEntry(NimBLEConnInfo& connInfo) override {
        NimBLEDevice::injectPassKey(connInfo, 0);
    }

    void onConfirmPasskey(NimBLEConnInfo& connInfo, uint32_t pass_key) override {
        NimBLEDevice::injectConfirmPasskey(connInfo, true);
    }
};

static MyClientCallbacks clientCallbacks;

// =============================================================================
// Scan Callbacks (Matches both Discovery Mode and Reconnection Mode)
// =============================================================================
static NimBLEAdvertisedDevice lastCandidateDev;
static bool hasCandidateDev = false;

class MyScanCallbacks : public NimBLEScanCallbacks {
    void onResult(const NimBLEAdvertisedDevice* advertisedDevice) override {
        // Only consider connectable devices
        if (!advertisedDevice->isConnectable()) {
            return;
        }

        String addr = advertisedDevice->getAddress().toString().c_str();
        String name = advertisedDevice->getName().c_str();
        uint16_t appearance = advertisedDevice->getAppearance();
        int rssi = advertisedDevice->getRSSI();
        bool isZeroAddr = (addr == "00:00:00:00:00:00");

        // Immediately filter out non-gamepad devices (Smart TVs, PCs, audio devices)
        String nameLower = name;
        nameLower.toLowerCase();
        if (nameLower.indexOf("tv") >= 0 ||
            nameLower.indexOf("webos") >= 0 ||
            nameLower.indexOf("desktop") >= 0 ||
            nameLower.indexOf("audio") >= 0 ||
            nameLower.indexOf("speaker") >= 0 ||
            nameLower.indexOf("sound") >= 0 ||
            nameLower.indexOf("headphone") >= 0) {
            return;
        }

        bool isMatch = false;

        // 1. Check against ANY bonded peer address in NVS
        if (!isZeroAddr) {
            int numBonds = NimBLEDevice::getNumBonds();
            for (int i = 0; i < numBonds; i++) {
                if (advertisedDevice->getAddress() == NimBLEDevice::getBondedAddress(i)) {
                    Serial.printf("\n[BLE] *** MATCHED BONDED PEER #%d [%s] RSSI:%d ***\n", i, addr.c_str(), rssi);
                    isMatch = true;
                    break;
                }
            }

            // 2. Hardware / NimBLE resolved bond check
            if (!isMatch && NimBLEDevice::isBonded(advertisedDevice->getAddress())) {
                Serial.printf("\n[BLE] *** MATCHED via isBonded(): [%s] RSSI:%d ***\n", addr.c_str(), rssi);
                isMatch = true;
            }

            // 3. Known Controller MACs (Android Mode, PC Mode, RPA history)
            if (!isMatch && (addr.equalsIgnoreCase(KNOWN_EVOFOX_MAC_1) ||
                             addr.equalsIgnoreCase(KNOWN_EVOFOX_MAC_2) ||
                             addr.equalsIgnoreCase(KNOWN_EVOFOX_MAC_3))) {
                Serial.printf("\n[BLE] *** MATCHED KNOWN CONTROLLER MAC [%s] RSSI:%d ***\n", addr.c_str(), rssi);
                isMatch = true;
            }
        }

        // 4. Discovery Mode Name match ("EvoFox", "Xbox", "Gamepad", "Wireless Controller", "Retro-Pad")
        if (!isMatch && (nameLower.indexOf("evofox") >= 0 ||
                         nameLower.indexOf("xbox") >= 0 ||
                         nameLower.indexOf("gamepad") >= 0 ||
                         nameLower.indexOf("wireless controller") >= 0 ||
                         nameLower.indexOf("retro-pad") >= 0)) {
            Serial.printf("\n[BLE] *** MATCHED BY NAME: \"%s\" [%s] RSSI:%d ***\n", name.c_str(), addr.c_str(), rssi);
            isMatch = true;
        }

        // 5. HID Gamepad / Joystick Appearance (960..968)
        if (!isMatch && !isZeroAddr && (appearance >= 960 && appearance <= 968)) {
            Serial.printf("\n[BLE] *** MATCHED BY GAMEPAD APPEARANCE: %u [%s] RSSI:%d ***\n", appearance, addr.c_str(), rssi);
            isMatch = true;
        }

        // 6. Advertises HID Service (0x1812)
        if (!isMatch && !isZeroAddr && advertisedDevice->isAdvertisingService(NimBLEUUID((uint16_t)0x1812))) {
            Serial.printf("\n[BLE] *** MATCHED BY HID SERVICE (0x1812) [%s] RSSI:%d ***\n", addr.c_str(), rssi);
            isMatch = true;
        }

        // Only save candidate devices that actually match gamepad criteria
        if (!isZeroAddr) {
            if (isMatch || (appearance >= 960 && appearance <= 968) ||
                advertisedDevice->isAdvertisingService(NimBLEUUID((uint16_t)0x1812))) {
                lastCandidateDev = *advertisedDevice;
                hasCandidateDev = true;
            }
        }

        if (isMatch && !targetFound) {
            if (isZeroAddr) {
                if (hasCandidateDev) {
                    Serial.printf("[BLE] Scan response resolved to candidate gamepad: [%s]\n",
                                  lastCandidateDev.getAddress().toString().c_str());
                    if (pTargetAdvDevice) delete pTargetAdvDevice;
                    pTargetAdvDevice = new NimBLEAdvertisedDevice(lastCandidateDev);
                    targetFound = true;
                    NimBLEDevice::getScan()->stop();
                } else {
                    int numBonds = NimBLEDevice::getNumBonds();
                    NimBLEAddress fallbackAddr = (numBonds > 0) ? NimBLEDevice::getBondedAddress(0) : NimBLEAddress(std::string(KNOWN_EVOFOX_MAC_1), BLE_ADDR_PUBLIC);
                    Serial.printf("[BLE] Scan response without cached address, attempting fallback [%s]...\n",
                                  fallbackAddr.toString().c_str());
                    targetAddress = fallbackAddr;
                    if (pTargetAdvDevice) delete pTargetAdvDevice;
                    pTargetAdvDevice = new NimBLEAdvertisedDevice();
                    targetFound = true;
                    NimBLEDevice::getScan()->stop();
                }
            } else {
                if (pTargetAdvDevice) delete pTargetAdvDevice;
                pTargetAdvDevice = new NimBLEAdvertisedDevice(*advertisedDevice);
                targetFound = true;
                NimBLEDevice::getScan()->stop();
            }
        }
    }

    void onScanEnd(const NimBLEScanResults& results, int reason) override {
        if (!isConnected && !targetFound) {
            startScanning();
        }
    }
};

static MyScanCallbacks scanCallbacks;

// =============================================================================
// Scan Control
// =============================================================================
void startScanning() {
    auto pScan = NimBLEDevice::getScan();
    if (!pScan->isScanning()) {
        pScan->setScanCallbacks(&scanCallbacks, false);
        pScan->setActiveScan(true);
        pScan->setInterval(45);  // 45 ms
        pScan->setWindow(45);    // 45 ms (100% duty cycle continuous search)
        pScan->setFilterPolicy(BLE_HCI_SCAN_FILT_NO_WL_INITA); // Receive connectable directed advertisements!
        pScan->setDuplicateFilter(false);
        Serial.println("[BLE] Scanning for EvoFox gamepad (Supports Home reconnect, A+Home Android, X+Home PC)...");
        pScan->start(0, false);
    }
}

// =============================================================================
// Connect to Device
// =============================================================================
bool connectToDevice(NimBLEAdvertisedDevice* advDevice) {
    NimBLEAddress peerAddr = advDevice->getAddress();
    if (peerAddr.toString() == "00:00:00:00:00:00" && targetAddress.toString() != "00:00:00:00:00:00") {
        peerAddr = targetAddress;
    }

    Serial.printf("\n[BLE] Initiating connection to \"%s\" [%s]...\n",
                  advDevice->getName().c_str(),
                  peerAddr.toString().c_str());

    NimBLEClient* pClient = NimBLEDevice::getClientByPeerAddress(peerAddr);
    if (!pClient) {
        if (NimBLEDevice::getCreatedClientCount() >= NIMBLE_MAX_CONNECTIONS) {
            Serial.println("[BLE] Reclaiming client slot...");
            NimBLEDevice::deleteClient(pConnectedClient);
        }
        pClient = NimBLEDevice::createClient();
        if (!pClient) {
            Serial.println("[BLE] ERROR: Could not create NimBLEClient!");
            return false;
        }
    }

    pClient->setClientCallbacks(&clientCallbacks, false);
    // Low latency parameters: 15-30ms interval, 5.0s supervision timeout
    pClient->setConnectionParams(12, 24, 0, 500);
    pClient->setConnectTimeout(6000); // 6s connection timeout

    bool connOk = false;
    if (peerAddr == advDevice->getAddress() && peerAddr.toString() != "00:00:00:00:00:00") {
        connOk = pClient->connect(advDevice, true);
    } else {
        connOk = pClient->connect(peerAddr, true);
    }

    if (!connOk) {
        Serial.println("[BLE] Connection attempt timed out or was rejected.");
        return false;
    }

    Serial.println("[BLE] Connected! Discovering all services...");

    NimBLERemoteService* pHidService = nullptr;
    for (auto pService : pClient->getServices(true)) {
        Serial.printf("[BLE] Discovered Service: %s\n", pService->getUUID().toString().c_str());
        if (pService->getUUID() == NimBLEUUID((uint16_t)0x1812)) {
            pHidService = pService;
        }
    }

    if (!pHidService) {
        Serial.println("[BLE] ERROR: HID Service 0x1812 not found on device!");
        pClient->disconnect();
        return false;
    }

    bool anySubscribed = false;
    for (auto pChara : pHidService->getCharacteristics(true)) {
        NimBLEUUID uuid = pChara->getUUID();

        // 1. If Protocol Mode (0x2a4e), set to Report Protocol Mode (0x01)
        if (uuid == NimBLEUUID((uint16_t)0x2a4e)) {
            if (pChara->canWrite() || pChara->canWriteNoResponse()) {
                Serial.printf("[BLE] Setting Protocol Mode to Report Mode (0x01) on char %s...\n", uuid.toString().c_str());
                uint8_t mode = 0x01;
                pChara->writeValue(&mode, 1, false);
            }
        }
        // 2. If HID Control Point (0x2a4c), send Exit Suspend (0x01)
        else if (uuid == NimBLEUUID((uint16_t)0x2a4c)) {
            if (pChara->canWrite() || pChara->canWriteNoResponse()) {
                Serial.printf("[BLE] Sending Exit Suspend (0x01) to HID Control Point %s...\n", uuid.toString().c_str());
                uint8_t exitSuspend = 0x01;
                pChara->writeValue(&exitSuspend, 1, false);
            }
        }

        // 3. Read initial value if readable
        if (pChara->canRead()) {
            pChara->readValue();
        }

        // 4. Subscribe to notifications
        if (pChara->canNotify()) {
            Serial.printf("[BLE] Subscribing to Report char %s (handle %d)...\n",
                          uuid.toString().c_str(), pChara->getHandle());
            if (pChara->subscribe(true, onNotifyCallback, true)) {
                Serial.println("[BLE] -> Subscribed successfully!");
                anySubscribed = true;
            } else {
                Serial.println("[BLE] -> Subscribe failed for this char.");
            }
        }
    }

    if (!anySubscribed) {
        Serial.println("[BLE] ERROR: Failed to subscribe to any HID notification characteristic.");
        pClient->disconnect();
        return false;
    }

    pConnectedClient = pClient;
    isConnected = true;
    setLed(true); // Solid Blue ON

    Serial.println("\n=============================================");
    Serial.println("  *** EVOFOX GAMEPAD CONNECTED & READY! ***  ");
    Serial.println("=============================================");
    return true;
}

// =============================================================================
// Build and Transmit Gamepad Packet to Retro-Go (ESP32-S3)
// =============================================================================
void sendGamepadPacket(bool connected) {
    GamepadPacket pkt;
    memset(&pkt, 0, sizeof(pkt));

    pkt.sync1 = 0xAA;
    pkt.sync2 = 0x55;

    if (connected) {
        // Face buttons & D-pad
        if (padState.up)    pkt.buttons1 |= MASK_UP;
        if (padState.down)  pkt.buttons1 |= MASK_DOWN;
        if (padState.left)  pkt.buttons1 |= MASK_LEFT;
        if (padState.right) pkt.buttons1 |= MASK_RIGHT;
        if (padState.a)     pkt.buttons1 |= MASK_A;
        if (padState.b)     pkt.buttons1 |= MASK_B;
        if (padState.x)     pkt.buttons1 |= MASK_X;
        if (padState.y)     pkt.buttons1 |= MASK_Y;

        // Shoulder, triggers, thumbstick clicks & menu
        if (padState.start)  pkt.buttons2 |= MASK_START;
        if (padState.select) pkt.buttons2 |= MASK_SELECT;
        if (padState.lb)     pkt.buttons2 |= MASK_LB;
        if (padState.rb)     pkt.buttons2 |= MASK_RB;
        if (padState.lt)     pkt.buttons2 |= MASK_LT;
        if (padState.rt)     pkt.buttons2 |= MASK_RT;
        if (padState.ls)     pkt.buttons2 |= MASK_LS;
        if (padState.rs)     pkt.buttons2 |= MASK_RS;

        // Extra buttons & status
        if (padState.home)  pkt.buttons3 |= MASK_HOME;
        if (padState.share) pkt.buttons3 |= MASK_SHARE;
        pkt.buttons3 |= MASK_CONN;

        // Analog sticks
        pkt.joy_lx = padState.joy_lx;
        pkt.joy_ly = padState.joy_ly;
        pkt.joy_rx = padState.joy_rx;
        pkt.joy_ry = padState.joy_ry;
    } else {
        // Disconnected defaults (centered sticks, all buttons released)
        pkt.joy_lx = 128;
        pkt.joy_ly = 128;
        pkt.joy_rx = 128;
        pkt.joy_ry = 128;
    }

    // Compute simple XOR checksum
    uint8_t* raw = (uint8_t*)&pkt;
    uint8_t chk = 0;
    for (int i = 2; i <= 8; i++) {
        chk ^= raw[i];
    }
    pkt.checksum = chk;

    // Send packet over Hardware UART to ESP32-S3 (Retro-Go)
    BridgeSerial.write((uint8_t*)&pkt, sizeof(pkt));
}

// =============================================================================
// Debug Print Input Changes to USB Serial
// =============================================================================
void printInputChanges() {
    if (!isConnected) return;

    static uint8_t prevBtn1 = 0, prevBtn2 = 0, prevBtn3 = 0;
    static uint8_t prevLx = 128, prevLy = 128, prevRx = 128, prevRy = 128;

    uint8_t b1 = 0, b2 = 0, b3 = 0;
    if (padState.a) b1 |= MASK_A;
    if (padState.b) b1 |= MASK_B;
    if (padState.x) b1 |= MASK_X;
    if (padState.y) b1 |= MASK_Y;
    if (padState.up) b1 |= MASK_UP;
    if (padState.down) b1 |= MASK_DOWN;
    if (padState.left) b1 |= MASK_LEFT;
    if (padState.right) b1 |= MASK_RIGHT;

    if (padState.start) b2 |= MASK_START;
    if (padState.select) b2 |= MASK_SELECT;
    if (padState.lb) b2 |= MASK_LB;
    if (padState.rb) b2 |= MASK_RB;
    if (padState.lt) b2 |= MASK_LT;
    if (padState.rt) b2 |= MASK_RT;
    if (padState.ls) b2 |= MASK_LS;
    if (padState.rs) b2 |= MASK_RS;

    if (padState.home) b3 |= MASK_HOME;
    if (padState.share) b3 |= MASK_SHARE;

    uint8_t curLx = padState.joy_lx;
    uint8_t curLy = padState.joy_ly;
    uint8_t curRx = padState.joy_rx;
    uint8_t curRy = padState.joy_ry;

    bool btnChanged = (b1 != prevBtn1 || b2 != prevBtn2 || b3 != prevBtn3);
    bool stickChanged = (abs((int)curLx - (int)prevLx) > 15 || abs((int)curLy - (int)prevLy) > 15 ||
                         abs((int)curRx - (int)prevRx) > 15 || abs((int)curRy - (int)prevRy) > 15);

    if (btnChanged || stickChanged) {
        prevBtn1 = b1; prevBtn2 = b2; prevBtn3 = b3;
        prevLx = curLx; prevLy = curLy;
        prevRx = curRx; prevRy = curRy;

        Serial.printf("[INPUT] Buttons: ");
        bool any = false;
        if (padState.a) { Serial.print("A "); any = true; }
        if (padState.b) { Serial.print("B "); any = true; }
        if (padState.x) { Serial.print("X "); any = true; }
        if (padState.y) { Serial.print("Y "); any = true; }
        if (padState.up) { Serial.print("UP "); any = true; }
        if (padState.down) { Serial.print("DOWN "); any = true; }
        if (padState.left) { Serial.print("LEFT "); any = true; }
        if (padState.right) { Serial.print("RIGHT "); any = true; }
        if (padState.lb) { Serial.print("LB "); any = true; }
        if (padState.rb) { Serial.print("RB "); any = true; }
        if (padState.lt) { Serial.print("LT "); any = true; }
        if (padState.rt) { Serial.print("RT "); any = true; }
        if (padState.ls) { Serial.print("LS "); any = true; }
        if (padState.rs) { Serial.print("RS "); any = true; }
        if (padState.start) { Serial.print("START "); any = true; }
        if (padState.select) { Serial.print("SELECT "); any = true; }
        if (padState.home) { Serial.print("HOME "); any = true; }
        if (!any) Serial.print("NONE ");
        Serial.printf("| Stick L(%d, %d) R(%d, %d)\n", curLx, curLy, curRx, curRy);
    }
}

// =============================================================================
// Handle Serial Commands (e.g. "clear" to wipe bonds)
// =============================================================================
void handleSerialCommands() {
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd.equalsIgnoreCase("clear") || cmd.equalsIgnoreCase("pair")) {
            Serial.println("\n[BLE] *** Clearing all bonded devices from NVS... ***");
            NimBLEDevice::deleteAllBonds();
            Serial.println("[BLE] All bonds deleted! Now turn on your controller with Home+A or Home+X to pair fresh.");
            if (pConnectedClient) {
                pConnectedClient->disconnect();
            }
        } else if (cmd.equalsIgnoreCase("bonds") || cmd.equalsIgnoreCase("status")) {
            int n = NimBLEDevice::getNumBonds();
            Serial.printf("[BLE] Stored Bonds: %d | Connected: %s | Scanning: %s\n",
                          n, isConnected ? "YES" : "NO",
                          NimBLEDevice::getScan()->isScanning() ? "YES" : "NO");
            for (int i = 0; i < n; i++) {
                Serial.printf("  Bond #%d: %s\n", i, NimBLEDevice::getBondedAddress(i).toString().c_str());
            }
        }
    }
}

// =============================================================================
// Setup
// =============================================================================
void setup() {
    pinMode(STATUS_LED_PIN, OUTPUT);
    setLed(false);

    pinMode(BOOT_BUTTON_PIN, INPUT_PULLUP);

    padState.reset();

    Serial.begin(115200);
    delay(1000);

    Serial.println("\n=============================================");
    Serial.println("   ESP32-C3 BLE Gamepad Bridge for Retro-Go   ");
    Serial.println("  EvoFox One S (Tri-Mode: Android / PC / Dongle) ");
    Serial.println("=============================================");

    BridgeSerial.begin(BRIDGE_UART_BAUD, SERIAL_8N1, BRIDGE_UART_RX_PIN, BRIDGE_UART_TX_PIN);

    // Initialize NimBLE with RPA Privacy support
    NimBLEDevice::init("ESP32-Gamepad-Bridge");
    NimBLEDevice::setOwnAddrType(BLE_OWN_ADDR_RPA_PUBLIC_DEFAULT);
    NimBLEDevice::setPower(ESP_PWR_LVL_P9); /* +9dBm maximum RF power */

    // Security & Pairing configuration (Just Works, Bonding enabled, Secure Connections)
    NimBLEDevice::setSecurityAuth(true, false, true); // bonding=true, mitm=false, sc=true
    NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
    NimBLEDevice::setSecurityInitKey(BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID);
    NimBLEDevice::setSecurityRespKey(BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID);

    int numBonds = NimBLEDevice::getNumBonds();
    Serial.printf("[BLE] Stored Bonds in NVS: %d\n", numBonds);
    for (int i = 0; i < numBonds; i++) {
        Serial.printf("  Bonded device #%d: %s\n", i, NimBLEDevice::getBondedAddress(i).toString().c_str());
    }

    Serial.println("[BLE] Ready! Searching for gamepad...");
    startScanning();
}

// =============================================================================
// Main Loop
// =============================================================================
void loop() {
    // 1. Connection and Scanning Management
    if (!isConnected) {
        // Blink LED to indicate searching (250 ms toggle)
        if (millis() - lastLedBlinkTime >= 250) {
            lastLedBlinkTime = millis();
            ledBlinkState = !ledBlinkState;
            setLed(ledBlinkState);
        }

        // Target found by scanner -> connect
        if (targetFound && pTargetAdvDevice != nullptr) {
            targetFound = false;
            NimBLEAdvertisedDevice devCopy = *pTargetAdvDevice;
            delete pTargetAdvDevice;
            pTargetAdvDevice = nullptr;

            if (connectToDevice(&devCopy)) {
                // Successfully connected!
            } else {
                delay(500);
                startScanning();
            }
        } else if (!NimBLEDevice::getScan()->isScanning()) {
            startScanning();
        }
    } else {
        // Solid LED when connected
        setLed(true);
    }

    // 2. Transmit framed gamepad packet to ESP32-S3 (Retro-Go) at ~60 Hz (every 16 ms)
    if (millis() - lastPacketSendTime >= 16) {
        lastPacketSendTime = millis();
        sendGamepadPacket(isConnected);
        printInputChanges();
    }

    // 3. Check for Serial commands ("clear", "status", "bonds")
    handleSerialCommands();

    // 4. Check Boot Button: Hold for 3 seconds to clear all bonds
    static unsigned long buttonPressStart = 0;
    if (digitalRead(BOOT_BUTTON_PIN) == LOW) {
        if (buttonPressStart == 0) buttonPressStart = millis();
        if (millis() - buttonPressStart > 3000) {
            buttonPressStart = millis() + 10000; // prevent re-triggering
            Serial.println("\n[BTN] Boot button held! Clearing all BLE bonds from flash...");
            NimBLEDevice::deleteAllBonds();
            Serial.println("[BTN] Bonds cleared! Put gamepad in pairing mode (A+Home or X+Home).");
            if (pConnectedClient) pConnectedClient->disconnect();
        }
    } else {
        buttonPressStart = 0;
    }

    delay(2);
}
