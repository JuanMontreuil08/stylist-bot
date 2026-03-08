import os
import subprocess
from cartesia import Cartesia
from dotenv import load_dotenv
from pathlib import Path


load_dotenv()
client = Cartesia(api_key=os.getenv("CARTESIA_API_KEY"))
transcript = "Hola, aquí Benito de The North Face. Veo que utilizaste tu nueva cortaviento hace unos días. Nos gustaría saber si tienes alguna opinión o duda sobre el producto para poder ayudarte."
voice_id = "b0689631-eee7-4a6c-bb86-195f1d267c2e"  # test mexican voice

# Add emotion with a voice configuration
voice_config = {
    "mode": "id",
    "id": voice_id,
    "__experimental_controls": {
        "emotion": ["positivity:high"],  # Enthusiastic tone
        "speed": 0.3  # Speak slightly slower for persuasion
    }
}

# Generate the audio bytes (streaming chunks)
audio_stream = client.tts.bytes(
    model_id="sonic",
    transcript=transcript,
    voice=voice_config,
    output_format={
        "container": "wav",
        "encoding": "pcm_s16le",  # 16-bit PCM for Twilio
        "sample_rate": 8000,      # 8kHz for telephony
    },
)

# Save and play
output_path = Path("cartesia/output_voices/pitch.wav")
output_path.parent.mkdir(parents=True, exist_ok=True)

with open(output_path, "wb") as f:
    for chunk in audio_stream:
        f.write(chunk)

print("Playing generated audio...")
subprocess.run(["ffplay", "-autoexit", "-nodisp", str(output_path)])