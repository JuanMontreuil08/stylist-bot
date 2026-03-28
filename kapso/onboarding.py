"""
Onboarding flow: guides new users through a profile setup via WhatsApp interactive messages.
State is persisted in Supabase (user_profiles.onboarding_step) so server restarts are safe.

Steps:
  STEP_NEW    (0) → send welcome, ask name
  STEP_GENDER (1) → got name, send gender buttons
  STEP_STYLE  (2) → got gender, send style list
  STEP_SIZES  (3) → got style, ask sizes (free text)
  STEP_BUDGET (4) → got sizes, send budget buttons
  STEP_COLORS (5) → got budget, send colors list
  STEP_BRANDS (6) → got colors, ask brands (free text)
  STEP_DONE   (7) → got brands, finalize → onboarding_complete = True
"""
import os
import httpx
from dotenv import load_dotenv
from kapso.config import KAPSO_API_KEY, KAPSO_API_BASE, WHATSAPP_API_VERSION

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

STEP_NEW     = 0
STEP_GENDER  = 1
STEP_STYLE   = 2
STEP_SIZES   = 3
STEP_BUDGET  = 4
STEP_COLORS  = 5
STEP_BRANDS  = 6
STEP_DONE    = 7


# ── Supabase helpers ───────────────────────────────────────────────────────────

def _sb():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _get_row(phone: str) -> dict | None:
    try:
        resp = _sb().table("user_profiles").select("*").eq("user_id", phone).limit(1).execute()
        rows = getattr(resp, "data", None) or []
        return rows[0] if rows else None
    except Exception as e:
        print(f"[onboarding] get_row error: {e}")
        return None


def _upsert(phone: str, updates: dict):
    try:
        _sb().table("user_profiles").upsert({"user_id": phone, **updates}, on_conflict="user_id").execute()
    except Exception as e:
        print(f"[onboarding] upsert error: {e}")


# ── Public helpers ─────────────────────────────────────────────────────────────

def needs_onboarding(phone: str) -> bool:
    """True if the user has not completed onboarding. Returns False on any error (fail open)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return False
    try:
        row = _get_row(phone)
        return not (row or {}).get("onboarding_complete", False)
    except Exception:
        return False


def get_profile(phone: str) -> dict | None:
    """Return the full user_profiles row, or None."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return None
    return _get_row(phone)


# ── WhatsApp message senders ───────────────────────────────────────────────────

def _api_url(phone_number_id: str) -> str:
    return f"{KAPSO_API_BASE}/meta/whatsapp/{WHATSAPP_API_VERSION}/{phone_number_id}/messages"


def _headers() -> dict:
    return {"Content-Type": "application/json", "X-API-Key": KAPSO_API_KEY}


def _send_text(phone_number_id: str, to: str, text: str):
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        r = httpx.post(_api_url(phone_number_id), json=body, headers=_headers(), timeout=15.0)
        print(f"[onboarding] text → {r.status_code}")
    except Exception as e:
        print(f"[onboarding] send_text error: {e}")


def _send_buttons(phone_number_id: str, to: str, body_text: str, buttons: list[tuple[str, str]]):
    """buttons: list of (id, title). Max 3 buttons; title max 20 chars."""
    action_buttons = [
        {"type": "reply", "reply": {"id": bid, "title": title[:20]}}
        for bid, title in buttons[:3]
    ]
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": action_buttons},
        },
    }
    try:
        r = httpx.post(_api_url(phone_number_id), json=body, headers=_headers(), timeout=15.0)
        print(f"[onboarding] buttons → {r.status_code}")
    except Exception as e:
        print(f"[onboarding] send_buttons error: {e}")


def _send_list(
    phone_number_id: str,
    to: str,
    body_text: str,
    button_label: str,
    rows: list[tuple[str, str, str]],  # (id, title, description)
):
    """rows: list of (id, title, description). Title max 24 chars; description max 72 chars."""
    section_rows = [
        {"id": rid, "title": title[:24], "description": desc[:72]}
        for rid, title, desc in rows
    ]
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": button_label[:20],
                "sections": [{"rows": section_rows}],
            },
        },
    }
    try:
        r = httpx.post(_api_url(phone_number_id), json=body, headers=_headers(), timeout=15.0)
        print(f"[onboarding] list → {r.status_code}")
    except Exception as e:
        print(f"[onboarding] send_list error: {e}")


# ── Main processor ─────────────────────────────────────────────────────────────

def process_onboarding(
    phone: str,
    phone_number_id: str,
    to: str,
    text: str,
    interactive_id: str | None = None,
) -> None:
    """
    Process one incoming message within the onboarding flow.
    Saves the answer for the current step, then sends the next question.
    On the final step sets onboarding_complete=True so the agent takes over next time.
    """
    row = _get_row(phone)
    step = int((row or {}).get("onboarding_step") or STEP_NEW)

    if (row or {}).get("onboarding_complete"):
        return  # should not be called when complete

    # ── STEP 0: new user — send welcome, ask name ──────────────────────────────
    if step == STEP_NEW:
        _upsert(phone, {"onboarding_step": STEP_GENDER})
        _send_text(phone_number_id, to,
            "¡Hola! 👋 Soy tu estilista personal.\n\n"
            "Antes de empezar quiero conocerte un poco para darte recomendaciones "
            "perfectas para ti. Solo son unas preguntas rápidas.\n\n"
            "¿Cómo te llamas?")
        return

    # ── STEP 1: got name — send gender buttons ─────────────────────────────────
    if step == STEP_GENDER:
        name = (text or "").strip().split()[0].capitalize() if (text or "").strip() else "tú"
        _upsert(phone, {"name": name, "onboarding_step": STEP_STYLE})
        _send_buttons(phone_number_id, to,
            f"Mucho gusto, {name}! 😊 ¿Cómo te identificas?",
            [
                ("gender_h", "Hombre"),
                ("gender_m", "Mujer"),
                ("gender_o", "No especifico"),
            ]
        )
        return

    # ── STEP 2: got gender — send style list ───────────────────────────────────
    if step == STEP_STYLE:
        gender_map = {"gender_h": "hombre", "gender_m": "mujer", "gender_o": "otro"}
        gender = gender_map.get(interactive_id or "", (text or "").lower()[:15])
        _upsert(phone, {"gender": gender, "onboarding_step": STEP_SIZES})
        _send_list(phone_number_id, to,
            "¿Cuál es tu estilo principal?",
            "Ver estilos",
            [
                ("style_casual",  "Casual",         "Cómodo y del día a día"),
                ("style_sport",   "Deportivo",      "Gym, running, activewear"),
                ("style_formal",  "Formal/Oficina", "Business, reuniones de trabajo"),
                ("style_street",  "Streetwear",     "Urban, hype, sneakers"),
                ("style_elegant", "Elegante/Noche", "Eventos y salidas especiales"),
                ("style_mixed",   "Variado",        "Depende del día y la ocasión"),
            ]
        )
        return

    # ── STEP 3: got style — ask sizes (free text) ──────────────────────────────
    if step == STEP_SIZES:
        style_map = {
            "style_casual": "casual", "style_sport": "deportivo",
            "style_formal": "formal", "style_street": "streetwear",
            "style_elegant": "elegante", "style_mixed": "variado",
        }
        style = style_map.get(interactive_id or "", (text or interactive_id or "casual").lower()[:20])
        _upsert(phone, {"style_tags": [style], "onboarding_step": STEP_BUDGET})
        _send_text(phone_number_id, to,
            "¿Cuáles son tus tallas? Escríbelas así:\n\n"
            "Camiseta M, Pantalón 32, Zapato 42\n\n"
            "(solo las que apliquen, o escribe 'no sé' si no estás seguro/a)")
        return

    # ── STEP 4: got sizes — send budget buttons ────────────────────────────────
    if step == STEP_BUDGET:
        _upsert(phone, {"sizes_text": (text or "").strip(), "onboarding_step": STEP_COLORS})
        _send_buttons(phone_number_id, to,
            "¿Cuál es tu presupuesto habitual por prenda?",
            [
                ("budget_low",  "Hasta S/150"),
                ("budget_mid",  "S/150 – S/400"),
                ("budget_high", "S/400+"),
            ]
        )
        return

    # ── STEP 5: got budget — send colors list ──────────────────────────────────
    if step == STEP_COLORS:
        budget_map = {
            "budget_low": "hasta_150",
            "budget_mid": "150_400",
            "budget_high": "mas_400",
        }
        budget = budget_map.get(interactive_id or "", (text or "").strip()[:20])
        _upsert(phone, {"budget_range": budget, "onboarding_step": STEP_BRANDS})
        _send_list(phone_number_id, to,
            "¿Cuáles son tus colores favoritos para ropa?",
            "Ver colores",
            [
                ("color_neutral", "Neutros",         "Negro, blanco, gris, beige"),
                ("color_blues",   "Azules y verdes", "Navy, celeste, oliva, verde"),
                ("color_earth",   "Tonos tierra",    "Marrón, ocre, tostado, camel"),
                ("color_vivid",   "Colores vivos",   "Rojo, amarillo, naranja, rosa"),
                ("color_pastel",  "Pasteles",        "Lila, menta, rosa pálido"),
                ("color_any",     "Sin preferencia", "Me adapto a cualquier color"),
            ]
        )
        return

    # ── STEP 6: got colors — ask brands (free text) ────────────────────────────
    if step == STEP_BRANDS:
        color_map = {
            "color_neutral": "neutros",
            "color_blues":   "azules y verdes",
            "color_earth":   "tonos tierra",
            "color_vivid":   "colores vivos",
            "color_pastel":  "pasteles",
            "color_any":     "sin preferencia",
        }
        color_val = color_map.get(interactive_id or "", (text or interactive_id or "").lower()[:30])
        _upsert(phone, {"favorite_colors": [color_val], "onboarding_step": STEP_DONE})
        _send_text(phone_number_id, to,
            "¿Tienes marcas favoritas? Escríbelas separadas por coma.\n\n"
            "Ej: Nike, Zara, Lacoste\n\n"
            "(o escribe 'ninguna' si no tienes preferencia)")
        return

    # ── STEP 7: got brands — finalize ─────────────────────────────────────────
    if step == STEP_DONE:
        raw = (text or "").strip()
        brands: list[str] = []
        if raw.lower() not in ("ninguna", "no", "n/a", ""):
            brands = [b.strip().capitalize() for b in raw.split(",") if b.strip()]
        name = (row or {}).get("name") or "amigo/a"
        _upsert(phone, {"favorite_brands": brands, "onboarding_complete": True})
        _send_text(phone_number_id, to,
            f"¡Listo, {name}! 🎉 Ya te conozco.\n\n"
            "Ahora puedo ayudarte a encontrar ropa perfecta para ti, "
            "hacer pruebas virtuales, y mucho más.\n\n"
            "¿En qué te ayudo hoy? 👗")
