"""Simulates SDA100 responses on a virtual COM port pair.
Run on one port while steprtool uses the other end of the pair.
"""
import serial

SIM_PORT  = "COM2"    # the port steprtool is NOT using
BAUD      = 9600

# Build a realistic response: 14.300 MHz normal direction
FREQ_KHZ     = 14300
FREQ_TENS_HZ = FREQ_KHZ * 100   # 1_430_000 = 0x15D680
response = bytes([
    0x40,                               # '@'  start
    0x41,                               # 'A'  command echo
    0x00,                               # zero
    (FREQ_TENS_HZ >> 16) & 0xFF,        # Fh  0x15
    (FREQ_TENS_HZ >>  8) & 0xFF,        # Fm  0xD6
     FREQ_TENS_HZ        & 0xFF,        # Fl  0x80
    0x00,                               # ac  no motors active
    #0x00,                               # dir 0x00=normal
    0x02,                               # dir 0x02=180
    0x52,                               # vh  version (any byte)
    0x31,                               # vl  version (any byte)
    0x0D,                               # CR  terminator
])

QUERY_CMD = bytes([0x3F, 0x41, 0x0D])  # "? A CR"

print(f"SDA100 simulator on {SIM_PORT} at {BAUD} baud")
print(f"Will respond with: {FREQ_KHZ} kHz, normal direction")
print(f"Response bytes: {response.hex().upper()}")

reset = True
while reset:
    with serial.Serial(SIM_PORT, BAUD, timeout=10) as ser:
        reset = False
        print("Waiting for query commands...")
        while not reset:
            ser.flush()  # ensure all output is printed before blocking on read
            data = ser.read(3)
            if not data:
                continue
            if data == QUERY_CMD:
                print(f"  Got query ({data.hex().upper()}), sending response")
                ser.write(response)
                ser.flush()
            else:
                print(f"  Got unexpected bytes: {data.hex().upper()}")
                reset = True