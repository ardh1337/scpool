import sys
import os
sys.path.insert(0, os.path.abspath("."))
from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM7"
    print(f"--- Diagnosing {port} ---")
    transport = SerialTransport(port, 115200, timeout=1.0)
    client = ATClient(transport)
    try:
        client.open()
        for cmd in [
            "AT",
            "AT+CMEE=2",
            "AT+CPIN?",
            "AT+CSQ",
            "AT+CREG?",
            "AT+COPS?",
            'AT+CUSD=1,"*808*7*8#"'
        ]:
            res = client.send_command(cmd, timeout=8.0)
            print(f"[{cmd}] -> success={res.success}, raw={repr(res.raw_response)}")
    finally:
        client.close()

if __name__ == "__main__":
    main()
