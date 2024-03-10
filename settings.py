from dotenv import load_dotenv
from os import environ
from os.path import join, dirname

load_dotenv(join(dirname(__file__), '.env'))

COHOST_COOKIE = environ.get("COHOST_COOKIE")
SCRATCHPAD_HANDLE = environ.get("SCRATCHPAD_HANDLE")
POST_TO = environ.get("POST_TO")
