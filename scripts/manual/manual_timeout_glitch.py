import sys, os, time
sys.path.insert(0, os.path.abspath("."))
import serial

port = "COM3"
s = serial.Serial(port, 115200, timeout=1.0)
try:
    s.write(b"AT+CMGF=1\r\n")
    time.sleep(0.1)
    s.read(s.in_waiting)
    
    # Test 1: Calling readline() with FIXED timeout (never changing s.timeout)
    s.write(b'AT+CMGL="ALL"\r\n')
    lines_fixed = []
    t0 = time.time()
    while time.time() - t0 < 3.0:
        l = s.readline()
        if not l:
            break
        lines_fixed.append(l)
        if b"OK\r\n" in l:
            break
            
    print("--- FIXED TIMEOUT RESULT ---")
    for l in lines_fixed:
        print(repr(l))

    time.sleep(0.5)

    # Test 2: Calling readline() while modifying s.timeout on EVERY call
    s.write(b'AT+CMGL="ALL"\r\n')
    lines_dynamic = []
    t0 = time.time()
    while time.time() - t0 < 3.0:
        s.timeout = 2.0 - (time.time() - t0)
        l = s.readline()
        s.timeout = 1.0  # reset like SerialTransport does!
        if not l:
            break
        lines_dynamic.append(l)
        if b"OK\r\n" in l:
            break
            
    print("\n--- DYNAMIC TIMEOUT RESULT ---")
    for l in lines_dynamic:
        print(repr(l))

finally:
    s.close()

