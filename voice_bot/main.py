# main.py — Twilio voice + Grok (LLM) + Cartesia (TTS). Túnel con ngrok.
import os
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from dotenv import load_dotenv
from cartesia import Cartesia

from .llm_handler import generate_response

load_dotenv()

app = FastAPI()
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
# URL pública del servidor (ngrok). Ej: https://abc123.ngrok-free.app
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

# Carpeta donde Cartesia guarda los WAV para que Twilio los reproduzca
OUTPUT_VOICES = Path(__file__).parent / "output_voices"
OUTPUT_VOICES.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(OUTPUT_VOICES)), name="static")

cartesia_client = Cartesia(api_key=os.getenv("CARTESIA_API_KEY"))
# Sonic 3: voz estable recomendada para voice agents (Kiefer, inglés; natural en español con language="es")
VOICE_ID = "228fca29-3a0a-435c-8728-5cb483251068"  # Kiefer
VOICE_CONFIG = {"mode": "id", "id": VOICE_ID}
# Menos latencia (speed 1.15) y naturalidad (emotion content) — solo aplica con model_id sonic-3
GENERATION_CONFIG = {"speed": 1.15, "emotion": "content"}

# Use Redis in production
conversations = {}
# Mensaje de apertura por número: lo envía el agente y se usa en el primer POST /voice.
pending_opening_message: dict[str, str] = {}


class StartCallBody(BaseModel):
    phone_number: str
    opening_message: str  # Obligatorio: mensaje que el bot dirá al conectar (generado por el agente).


@app.post("/api/start-call")
async def api_start_call(body: StartCallBody):
    """Inicia una llamada saliente. Usado por la tool del agente. opening_message es la frase que el bot dirá al conectar (generada por el agente)."""
    phone = (body.phone_number or "").strip()
    if not phone:
        return {"ok": False, "error": "phone_number required"}
    msg = (body.opening_message or "").strip()
    if not msg:
        return {"ok": False, "error": "opening_message required"}
    if not phone.startswith("+"):
        phone = "+" + phone
    pending_opening_message[phone] = msg
    try:
        call_sid = start_call(phone)
        return {"ok": True, "call_sid": call_sid}
    except Exception as e:
        pending_opening_message.pop(phone, None)
        return {"ok": False, "error": str(e)}


@app.get("/voice")
async def voice_webhook_get():
    """Para que Twilio no reciba 405 al hacer GET; devuelve TwiML mínimo."""
    r = VoiceResponse()
    r.say("Bienvenido, un momento por favor.")
    return Response(content=str(r), media_type="application/xml")


@app.post("/voice")
async def voice_webhook(request: Request):
    response = VoiceResponse()
    form = await request.form()
    call_sid = form["CallSid"]
    intent = ""

    if call_sid not in conversations:
        to_number = (form.get("To") or "").strip()
        initial_pitch = pending_opening_message.pop(to_number, None)
        if not initial_pitch:
            response.say("Disculpa, no se pudo cargar el mensaje de inicio. Por favor intenta de nuevo más tarde.")
            response.hangup()
            return Response(content=str(response), media_type="application/xml")
        conversations[call_sid] = {"context": initial_pitch, "turn": 0}
        audio_url = await generate_tts_url(initial_pitch)
        response.play(audio_url)
    else:
        user_speech = form.get("SpeechResult", "")
        print(f"[voice] Segundo turno. SpeechResult={repr(user_speech)[:80]}") 
        context = conversations[call_sid]["context"]
        try:
            llm_output = generate_response(user_speech, context)
            bot_response_text = llm_output['response']
            intent = llm_output['intent']
            conversations[call_sid]["context"] += f" User: {user_speech} Bot: {bot_response_text}"
            audio_url = await generate_tts_url(bot_response_text)
            response.play(audio_url)
            if intent == "close":
                response.say("Perfecto, te envío los detalles por WhatsApp. ¡Que tengas un gran día!")
                response.hangup()
            elif intent == "exit":
                response.say("Sin problema, gracias por tu tiempo.")
                response.hangup()
        except Exception as e:
            print(f"[voice] Error cuando hablaste: {e}")
            import traceback
            traceback.print_exc()
            response.say("Disculpa, un momento. ¿Puedes repetir?")

    if intent not in ["close", "exit"]:
        gather = Gather(
            input="speech",
            action=f"{BASE_URL}/voice",
            method="POST",
            speechTimeout="3",
            timeout="15",
            speechModel="phone_call",
            language="es-ES",
        )
        response.append(gather)
    
    return Response(content=str(response), media_type="application/xml")

def _generate_tts_sync(transcript: str, wav_path: Path) -> None:
    """Genera audio con Cartesia Sonic 3 y guarda en wav_path (bloqueante)."""
    stream = cartesia_client.tts.bytes(
        model_id="sonic-3",
        transcript=transcript,
        voice=VOICE_CONFIG,
        language="es",
        generation_config=GENERATION_CONFIG,
        output_format={
            "container": "wav",
            "encoding": "pcm_s16le",
            "sample_rate": 8000,
        },
    )
    with open(wav_path, "wb") as f:
        for chunk in stream:
            f.write(chunk)


async def generate_tts_url(transcript: str) -> str:
    """Genera TTS con Cartesia, guarda en output_voices/response.wav y devuelve URL pública."""
    print(f"Generating audio for: {transcript[:50]}...")
    wav_path = OUTPUT_VOICES / "response.wav"
    await asyncio.to_thread(_generate_tts_sync, transcript, wav_path)
    return f"{BASE_URL}/static/response.wav"


def start_call(phone_number: str):
    """Inicia una llamada saliente de Twilio al número; el webhook es BASE_URL/voice."""
    call = twilio_client.calls.create(
        to=phone_number,
        from_=os.getenv("TWILIO_PHONE_NUMBER"),
        url=f"{BASE_URL}/voice",
    )
    return call.sid

if __name__ == "__main__":
    import uvicorn
    # Example: start_call("+15551234567")
    uvicorn.run(app, host="0.0.0.0", port=8000)