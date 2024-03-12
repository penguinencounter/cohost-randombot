import os.path

from dotenv import load_dotenv
from os import environ
from os.path import join, dirname

BANS = set()
if os.path.exists("banlist.txt"):
    with open("banlist.txt") as f:
        read = list(f.read().splitlines())
        for line in read:
            BANS.add(line.strip())

load_dotenv(join(dirname(__file__), ".env"))

COHOST_COOKIE = environ.get("COHOST_COOKIE")
SCRATCHPAD_HANDLE = environ.get("SCRATCHPAD_HANDLE")
POST_TO = environ.get("POST_TO")
