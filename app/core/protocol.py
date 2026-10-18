"""Konstanta protokol agent <-> server (dipisah supaya mudah diuji dan dipakai ulang)."""

import re

VALID_CPIN = {"READY", "EMPTY", "SIM_ERROR", "PORT_ERROR"}
PORT_NAME_PATTERN = re.compile(r"^(?!.*\.\.)[A-Za-z0-9_./:\-]{1,50}\Z")
MAX_MESSAGE_BYTES = 2_000_000
MAX_BATCH_MESSAGES = 300
MAX_BODY_CHARS = 2000

GROUP_KEY_PATTERN = re.compile(r"^[A-Za-z0-9:._-]{1,40}\Z")
