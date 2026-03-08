# stt_handler.py — Deepgram live transcription via WebSocket
# Recibe chunks de audio del usuario y expone el texto transcrito (para uso en llamadas de voz).

import os
import queue
import threading
from deepgram import DeepgramClient
from dotenv import load_dotenv

load_dotenv()

dg_client = DeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))

# Opciones por defecto: modelo nova-2, español (ajusta language si necesitas en-US)
DEFAULT_LIVE_OPTIONS = {
    "model": "nova-2",
    "language": "es",
    "interim_results": "true",
    "smart_format": "true",
    "encoding": "linear16",
    "sample_rate": "8000",
    "channels": "1",
}


class LiveTranscriber:
    """
    Transcripción en vivo con Deepgram WebSocket.
    - send_audio(chunk): envía bytes de audio al WebSocket.
    - get_transcript(): devuelve el último transcript final (o None). No bloquea.
    - get_transcript_blocking(timeout): espera hasta recibir un transcript final (útil en loops).
    - stop(): cierra el stream y la conexión.
    """

    def __init__(self, language: str = "es", model: str = "nova-2", sample_rate: str = "8000"):
        self._language = language
        self._model = model
        self._sample_rate = sample_rate
        self._transcript_queue = queue.Queue()
        self._connection = None
        self._thread = None
        self._closed = False

    def _on_message(self, message):
        if getattr(message, "type", None) != "Results":
            return
        if not getattr(message, "channel", None) or not getattr(message.channel, "alternatives", None):
            return
        # Solo encolamos resultados finales (evitar intermedios si no los quieres)
        is_final = getattr(message, "speech_final", None) or getattr(message, "is_final", None)
        if not is_final:
            return
        transcript = (message.channel.alternatives or [{}])[0]
        if isinstance(transcript, dict):
            text = transcript.get("transcript", "").strip()
        else:
            text = getattr(transcript, "transcript", "") or ""
        if text:
            self._transcript_queue.put(text)

    def _listen_thread(self):
        try:
            with dg_client.listen.v1.connect(
                model=self._model,
                language=self._language,
                encoding="linear16",
                sample_rate=self._sample_rate,
                interim_results="true",
                smart_format="true",
            ) as socket:
                self._connection = socket
                # El iterator entrega cada mensaje; procesamos y encolamos transcripts
                for msg in socket:
                    if self._closed:
                        break
                    self._on_message(msg)
        except Exception:
            self._transcript_queue.put(None)  # señal de error

    def start(self):
        """Abre el WebSocket y arranca el hilo que recibe eventos."""
        if self._thread is not None:
            return
        self._closed = False
        self._thread = threading.Thread(target=self._listen_thread, daemon=True)
        self._thread.start()
        # Dar tiempo a que connect() entre
        import time
        time.sleep(0.5)

    def send_audio(self, chunk: bytes) -> None:
        """Envía un chunk de audio (linear16, 8kHz, mono) al WebSocket."""
        if self._connection is None or self._closed:
            return
        try:
            self._connection.send_media(chunk)
        except Exception:
            pass

    def get_transcript(self) -> str | None:
        """Devuelve el siguiente transcript final si hay; si no, None. No bloquea."""
        try:
            return self._transcript_queue.get_nowait()
        except queue.Empty:
            return None

    def get_transcript_blocking(self, timeout: float | None = None) -> str | None:
        """Espera hasta recibir un transcript final (o timeout)."""
        try:
            return self._transcript_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        """Cierra el stream y la conexión."""
        self._closed = True
        if self._connection is not None:
            try:
                self._connection.send_finalize()
                self._connection.send_close_stream()
            except Exception:
                pass
            self._connection = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


# ——— Uso sencillo (archivo o stream simulado) ———
# Para probar con un archivo WAV (linear16 8kHz): lees el archivo en chunks y los pasas a send_audio;
# en otro hilo o con get_transcript_blocking obtienes los transcripts.

def transcribe_audio_file(audio_path: str, language: str = "es") -> str:
    """
    Transcripción de un archivo de audio (prerecorded, no WebSocket).
    Mantiene la misma API que el ejemplo original por si lo usas en algún flujo.
    """
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    response = dg_client.listen.v1.media.transcribe_file(
        request=audio_bytes,
        model="nova-2",
        language=language,
    )
    # ListenV1Response: results.channels[0].alternatives[0].transcript
    if not getattr(response, "results", None):
        return ""
    channels = getattr(response.results, "channels", None) or []
    if not channels:
        return ""
    alts = getattr(channels[0], "alternatives", None) or []
    if not alts:
        return ""
    return getattr(alts[0], "transcript", "") or ""
