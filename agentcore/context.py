# Shared per-request WhatsApp context, set by handler.py before each agent call.
# Used by tools that need to send intermediate messages (e.g. search ack).

_current: dict = {}


def set_whatsapp_context(phone_number_id: str, to: str) -> None:
    _current["phone_number_id"] = phone_number_id
    _current["to"] = to


def get_whatsapp_context() -> tuple[str | None, str | None]:
    return _current.get("phone_number_id"), _current.get("to")


def clear_whatsapp_context() -> None:
    _current.clear()


def send_search_ack() -> None:
    """Send 'Buscando...' ack to the current WhatsApp user. No-op if context not set."""
    phone_number_id, to = get_whatsapp_context()
    if not phone_number_id or not to:
        return
    try:
        from kapso.config import KAPSO_API_KEY, KAPSO_API_BASE, WHATSAPP_API_VERSION
        import httpx as _httpx
        _httpx.post(
            f"{KAPSO_API_BASE}/meta/whatsapp/{WHATSAPP_API_VERSION}/{phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "to": to, "type": "text",
                  "text": {"body": "Buscando las mejores opciones para ti... 🔍"}},
            headers={"Content-Type": "application/json", "X-API-Key": KAPSO_API_KEY},
            timeout=5.0,
        )
    except Exception:
        pass
