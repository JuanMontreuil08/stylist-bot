# main.py — Twilio voice + Grok (LLM) + Cartesia (TTS). Túnel con ngrok.
import os
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
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
VOICE_ID = "b0689631-eee7-4a6c-bb86-195f1d267c2e"
VOICE_CONFIG = {
    "mode": "id",
    "id": VOICE_ID,
    "__experimental_controls": {"emotion": ["positivity:high"], "speed": 0.3},
}

# Use Redis in production
conversations = {}

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
        conversations[call_sid] = {"context": "New upsell call.", "turn": 0}
        initial_pitch = "Hola, aquí Benito de The North Face. Veo que utilizaste tu nueva cortaviento hace unos días. Nos gustaría saber si tienes alguna opinión o duda sobre el producto para poder ayudarte."
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
    """Genera audio con Cartesia y guarda en wav_path (bloqueante)."""
    stream = cartesia_client.tts.bytes(
        model_id="sonic",
        transcript=transcript,
        voice=VOICE_CONFIG,
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