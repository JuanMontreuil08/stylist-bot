# Cómo probar la voz Twilio + ngrok

## 1. Instalar ngrok (si no lo tienes)

- Descarga: https://ngrok.com/download  
- O con Homebrew: `brew install ngrok`  
- Opcional: crea cuenta en ngrok.com y configura tu auth token: `ngrok config add-authtoken TU_TOKEN`

## 2. Configurar BASE_URL

En el `.env` (en la raíz del proyecto) pon la URL que te dará ngrok. Al inicio puedes dejar el placeholder y actualizarlo en el paso 4:

```bash
BASE_URL=https://REEMPLAZA-CON-TU-URL.ngrok-free.app
```

## 3. Arrancar el servidor FastAPI

Desde la raíz del proyecto, con el venv activado:

```bash
cd "/Users/juanmontreuil/Desktop/Startup v2"
source venv/bin/activate
uvicorn cartesia.main:app --host 0.0.0.0 --port 8000
```

O desde la carpeta `cartesia`:

```bash
cd cartesia
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Deja esta terminal abierta.

## 4. Arrancar ngrok

En **otra terminal**:

```bash
ngrok http 8000
```

Verás una línea tipo:

```
Forwarding   https://abc123.ngrok-free.app -> http://localhost:8000
```

- Copia esa URL (ej: `https://abc123.ngrok-free.app`).
- En tu `.env` actualiza:
  ```bash
  BASE_URL=https://abc123.ngrok-free.app
  ```
- **Reinicia** el servidor uvicorn (Ctrl+C y volver a ejecutar el comando del paso 3) para que cargue la nueva `BASE_URL`.

## 5. Configurar Twilio

1. Entra en https://console.twilio.com → Phone Numbers → Manage → Active numbers.  
2. Elige tu número de Twilio.  
3. En **Voice & Fax**, en "A call comes in":
   - Webhook: **HTTP POST**
   - URL: `https://TU-URL-NGROK.ngrok-free.app/voice` (la misma que `BASE_URL` + `/voice`)
4. Guarda.

Para **llamadas salientes** (que Twilio llame a un teléfono), tu app usa esa misma URL en `start_call()` porque `BASE_URL` ya apunta a ngrok.

## 6. Probar

**Opción A – Llamada entrante**  
Llama desde tu móvil al número de Twilio. Debería contestar el bot (saludo en español y Gather de voz).

**Opción B – Llamada saliente**  
En una consola Python (con el servidor y ngrok en marcha):

```python
from cartesia.main import start_call
start_call("+34TU_NUMERO")  # número con código de país
```

Twilio llamará a ese número y al atender se reproducirá el flujo de voz (Grok + Cartesia).

## 7. Comprobar que todo responde

- Salud del servidor: en el navegador abre `https://TU-URL-NGROK.ngrok-free.app/docs` (debería cargar la API de FastAPI).  
- Webhook de voz: la URL que usa Twilio es `https://TU-URL-NGROK.ngrok-free.app/voice` (método POST).

## Notas

- Cada vez que reinicies ngrok (plan gratuito) la URL cambia; actualiza `BASE_URL` en `.env` y reinicia uvicorn.  
- Si Twilio no llega al webhook, revisa que la URL en Twilio sea exactamente `BASE_URL + "/voice"` y que ngrok esté apuntando al puerto 8000.
