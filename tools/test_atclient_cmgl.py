import sys, os
sys.path.insert(0, os.path.abspath("."))
from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient

port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
t = SerialTransport(port, 115200)
c = ATClient(t)
c.open()
c.send_command("AT+CMGF=1")
res = c.send_command('AT+CMGL="ALL"')
print("SUCCESS:", res.success)
print("RAW:")
print(repr(res.raw_response))
print("LINES:")
for l in res.lines:
    print(repr(l))
c.close()
