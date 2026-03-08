"""
Kapso webhook configuration.
"""
import os
from dotenv import load_dotenv

load_dotenv()

KAPSO_API_KEY = os.getenv("KAPSO_API_KEY", "")

WEBHOOK_SECRET = os.getenv("KAPSO_WEBHOOK_SECRET", "")

KAPSO_API_BASE = os.getenv("KAPSO_API_BASE", "https://api.kapso.ai")

WHATSAPP_API_VERSION = "v24.0"
