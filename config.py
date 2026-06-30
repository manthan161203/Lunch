import os
import re
from zoneinfo import ZoneInfo
import datetime
from dotenv import load_dotenv
from neonize.client import NewClient

load_dotenv()

DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "yes")
GROUP_JID = os.environ.get("GROUP_JID", "120363419760795427@g.us")
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Kolkata"))

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

ORDERS_CSV_FILE = os.environ.get("ORDERS_CSV_FILE", "orders.csv")
SUMMARY_CSV_FILE = os.environ.get("SUMMARY_CSV_FILE", "summary.csv")
POLLS_DB_FILE = os.environ.get("POLLS_DB_FILE", "polls_db.json")

PATTERN = re.compile(
    r"^\s*(?P<vendor>[^()\-]+?)\s*"
    r"\((?P<menu>.*?)\)\s*-\s*"
    r"(?P<p1>\d+)\s*(?:rupees|rupee|rs\.?|₹)?\s*-\s*"
    r"(?P<p2>\d+)",
    re.IGNORECASE,
)

# Shared global states
TODAYS_MENUS = []
client = NewClient("lunch-drc-bot")
BOOT_TIME = datetime.datetime.now(TZ)
