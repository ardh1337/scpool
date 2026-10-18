import sys
import os
import time
sys.path.insert(0, os.path.abspath("."))

from app.modem.transport import SerialTransport

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM7"
    t = SerialTransport(port, 115200, timeout=1.0)
    t.open()
    try:
        t.flush()
        # Cancel any active USSD first
        t.write(b"AT+CUSD=2\r\n")
        time.sleep(0.5)
        print("CUSD=2 response:", repr(t._serial.read(t._serial.in_waiting)))
        
        # Send CSCS="IRA"
        t.write(b'AT+CSCS="IRA"\r\n')
        time.sleep(0.3)
        print("CSCS response:", repr(t._serial.read(t._serial.in_waiting)))

        # Send CUSD
        print('Sending AT+CUSD=1,"*808*7*8#" ...')
        t.write(b'AT+CUSD=1,"*808*7*8#"\r\n')
        
        start = time.time()
        buf = b""
        while time.time() - start < 10.0:
            if t._serial.in_waiting:
                chunk = t._serial.read(t._serial.in_waiting)
                buf += chunk
                print(f"[{time.time()-start:.2f}s] received {len(chunk)} bytes: {repr(chunk)}")
                if b"+CUSD:" in buf:
                    # Keep reading until end quote or idle
                    time.sleep(0.8)
                    if t._serial.in_waiting:
                        buf += t._serial.read(t._serial.in_waiting)
                    break
            time.sleep(0.1)

        print("TOTAL BUFFER:")
        print(buf.decode("latin1", errors="replace"))
    finally:
        t.close()

if __name__ == "__main__":
    main()
