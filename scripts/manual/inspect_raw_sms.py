import sys
import os
sys.path.insert(0, os.path.abspath("."))
import serial

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    print(f"Connecting to {port}...")
    s = serial.Serial(port, 115200, timeout=2.0)
    try:
        s.write(b"AT\r\n")
        print("AT:", repr(s.read(100)))
        
        s.write(b"AT+CSCS?\r\n")
        print("CSCS?:", repr(s.read(100)))

        s.write(b"AT+CMGF=1\r\n")
        print("CMGF=1:", repr(s.read(100)))

        s.write(b'AT+CMGL="ALL"\r\n')
        buf = b""
        import time
        t0 = time.time()
        while time.time() - t0 < 3.0:
            c = s.read(s.in_waiting or 1)
            if c:
                buf += c
            else:
                if b"OK\r\n" in buf or b"ERROR\r\n" in buf:
                    break
        print("RAW BYTES:")
        print(buf)
        print("\nDECODED (latin1):")
        print(buf.decode("latin1", errors="replace"))
    finally:
        s.close()

if __name__ == "__main__":
    main()

