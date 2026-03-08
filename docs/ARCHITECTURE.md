# Arquitectura técnica — Solución

Documento de referencia para el pitch deck: descripción técnica y arquitectura del proyecto.

---

## 1. Visión general

La solución es un **asistente de estilo y compras** multicanal que combina:

- **Canal principal**: WhatsApp (mensajes de texto e imágenes).
- **Canal de voz**: llamadas salientes (MVP) para seguimiento post-compra o contacto proactivo.

El cerebro del sistema es un **agente de IA** que decide respuestas y qué herramientas usar (catálogo, búsqueda web, futura iniciación de llamadas). La capa de voz es un servicio separado que maneja la conversación por teléfono (reconocimiento de voz, LLM, síntesis de voz).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           USUARIOS                                          │
├──────────────────────────────┬──────────────────────────────────────────────┤
│  WhatsApp (Kapso)            │  Llamada de voz (Twilio)                      │
└──────────────┬───────────────┴──────────────────┬───────────────────────────┘
               │                                   │
               ▼                                   ▼
┌──────────────────────────────┐     ┌──────────────────────────────────────┐
│  app (FastAPI)               │     │  cartesia/main (FastAPI)              │
│  POST /webhooks/whatsapp      │     │  GET/POST /voice (webhook Twilio)     │
│  → Kapso handler              │     │  → TwiML, Gather, Play, Say          │
└──────────────┬───────────────┘     └──────────────────┬───────────────────┘
               │                                        │
               ▼                                        ▼
┌──────────────────────────────┐     ┌──────────────────────────────────────┐
│  Agente (Strands + Bedrock)   │     │  Voice pipeline                      │
│  • Claude Sonnet 4 (LLM)      │     │  • STT: Twilio (Gather speech)        │
│  • Tools: catálogo, web       │     │  • LLM: Grok (xAI)                   │
│  • Entrada: texto + imagen    │     │  • TTS: Cartesia Sonic                │
└──────────────┬───────────────┘     └──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Herramientas externas                                                       │
│  AWS Bedrock KB (catálogo) · Perplexity (búsqueda web) · S3 (imágenes)       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Canal WhatsApp

### Flujo

1. **Kapso** recibe el mensaje del usuario en WhatsApp y envía un webhook a nuestro backend.
2. **`app.py`** (FastAPI) expone `POST /webhooks/whatsapp`, recibe el payload y delega en `process_webhook_payload`.
3. **`kapso/handler.py`**:
   - Extrae texto y/o URL de imagen del mensaje.
   - Arma un payload `{ "prompt": texto, "image_url": url_imagen? }` y llama al agente.
4. **Agente** (`agentcore/agent.py`): recibe el payload, opcionalmente convierte la imagen a bytes (vía `utils/handle_kapso_image`) y ejecuta el agente con texto y/o imagen.
5. La respuesta del agente puede incluir referencias a imágenes en S3 (`[s3://bucket/key]`). El handler las detecta, genera URLs firmadas y envía por Kapso: primero el texto, luego cada imagen por la API de WhatsApp.

### Tecnologías

- **Backend**: FastAPI (`app.py`).
- **Integración WhatsApp**: Kapso (API/Webhook).
- **Agente**: Strands + Bedrock (ver sección 4).
- **Imágenes**: S3 para catálogo; URLs firmadas para enviar fotos en el chat.

---

## 3. Canal de voz (MVP)

### Flujo

1. Una llamada saliente se inicia con **`start_call(phone_number)`** (p. ej. desde un script o, en el futuro, desde una tool del agente). Twilio marca al número y usa como URL del webhook `BASE_URL/voice`.
2. **Primera petición a `/voice`**: el servidor no tiene `call_sid` en memoria; asume “nueva llamada”, reproduce un pitch inicial (TTS) y devuelve TwiML con `<Gather>` para capturar la voz del usuario.
3. **Siguientes peticiones**: Twilio envía `SpeechResult` (texto transcrito). El servidor:
   - Pasa el texto al **LLM (Grok)** para obtener la respuesta y un intent (`interested`, `objection`, `close`, `exit`).
   - Genera audio con **Cartesia (TTS)** y lo sirve desde `/static/response.wav`.
   - Devuelve TwiML: `<Play>` del audio y de nuevo `<Gather>` para el siguiente turno, o `<Hangup>` si el intent es `close` o `exit`.

### Componentes

| Componente   | Tecnología        | Uso                                                                 |
|-------------|-------------------|---------------------------------------------------------------------|
| Telefonía   | Twilio            | Llamadas salientes, webhook, TwiML (Gather, Play, Say, Hangup).    |
| STT         | Twilio (Gather)   | Reconocimiento de voz en la llamada; resultado en `SpeechResult`.  |
| LLM         | Grok (xAI)        | Respuesta conversacional y clasificación de intent.               |
| TTS         | Cartesia Sonic    | Generación de audio (WAV 8 kHz) para Play en Twilio.              |
| Servidor    | FastAPI           | `cartesia/main.py`: rutas `/voice`, `/static`, lógica de turnos.   |

### Estado

- Conversaciones en memoria por `call_sid` (contexto e historial del diálogo). En producción se recomienda Redis (o similar) para estado distribuido y TTL.

---

## 4. Agente central (Strands + Bedrock)

### Responsabilidad

Un único agente que atiende **solo el canal WhatsApp** (el flujo de voz tiene su propio LLM en `cartesia/llm_handler.py`). El agente:

- Recibe prompt de texto y opcionalmente una imagen.
- Usa un system prompt fijo (estilista / asistente de compras, tono WhatsApp, reglas de herramientas).
- Puede invocar herramientas (catálogo, búsqueda web) y devolver texto (+ referencias S3 para imágenes).

### Stack

- **Framework**: Strands (definición del agente y tools).
- **Runtime**: Bedrock Agent Core (`BedrockAgentCoreApp`), compatible con despliegue en AWS.
- **Modelo**: Claude Sonnet 4 (`us.anthropic.claude-sonnet-4-6`) vía Bedrock.

### Entrada / salida

- **Entrada**: `payload` con `prompt` (texto) y opcionalmente `image_url` (se descarga y se convierte a bytes para el modelo).
- **Salida**: texto en `response.message["content"][0]["text"]`; el handler de Kapso interpreta en ese texto las referencias S3 y envía texto + imágenes por WhatsApp.

---

## 5. Herramientas del agente (Tools)

### 5.1 Búsqueda en catálogo (`search_clothing_catalog`)

- **Qué hace**: Búsqueda semántica sobre el catálogo interno de ropa.
- **Cómo**: AWS Bedrock Knowledge Base (KB) con búsqueda híbrida y reranking (Cohere). Los ítems tienen metadatos (tipo, color, estilo, ocasión, etc.) y una URL S3 de imagen.
- **Salida**: Lista de ítems con metadatos e `image_url` (S3). El agente incluye en la respuesta referencias `[s3://bucket/key]` para que el handler envíe las fotos por WhatsApp.

### 5.2 Búsqueda de productos en internet (`search_products_online`)

- **Qué hace**: Recomendaciones y comparaciones de productos que no están en el catálogo (otras marcas, electrónica, skincare, etc.) o cuando el catálogo no tiene resultados útiles.
- **Cómo**: API de Perplexity (Sonar) con búsqueda web, schema JSON fijo (resumen, recomendaciones con pros/cons, precios aproximados, URLs, comparación).
- **Parámetros**: `query` (producto/categoría) y opcionalmente `user_context` (presupuesto, preferencias, país, etc.).
- **Salida**: Texto resumido con productos, pros/cons, precios y URLs para que el agente responda en WhatsApp sin recortar enlaces.

### 5.3 Tool de voz (futuro)

- **Objetivo**: Que el agente pueda “llamar por teléfono” al usuario (p. ej. seguimiento post-compra).
- **Enfoque**: Añadir una tool (p. ej. `initiate_voice_call(phone_number, reason)`) que, o bien llame a un endpoint del servicio de voz hosteado (`POST /api/start-call`), o bien use las credenciales de Twilio desde el entorno del agente. En ambos casos el servicio de voz debe estar hosteado para que Twilio pueda acceder a `BASE_URL/voice`.

---

## 6. Datos y configuración

- **Catálogo**: Ítems indexados en Bedrock Knowledge Base; imágenes en S3; el agente solo ve metadatos + `s3_url` que luego se traducen a URLs firmadas para WhatsApp.
- **Secrets**: Variables de entorno (`.env`): AWS, Kapso, Perplexity, Twilio, xAI, Cartesia, `BASE_URL` del servicio de voz. No se almacenan secretos en código.
- **Estado de voz**: En MVP, diccionario en memoria por `call_sid`; en producción, usar almacén externo (Redis) con clave por llamada y TTL.

---

## 7. Despliegue y hosting

- **WhatsApp + Agente**: El `app` FastAPI (`app.py`) debe estar en un servidor accesible por Kapso (URL del webhook). El agente puede ejecutarse en el mismo proceso o en un runtime Bedrock/Lambda según la configuración de Bedrock Agent Core.
- **Voz**: El servicio `cartesia/main.py` debe estar hosteado con una URL pública estable (`BASE_URL`). Twilio debe tener configurado el webhook de la número de voz a `https://<tu-dominio>/voice`. Opciones típicas: Railway, Render, Fly.io, AWS (Lambda + API Gateway o ECS).
- **Catálogo**: Knowledge Base y S3 en la misma cuenta/región AWS que use el runtime del agente.

---

## 8. Resumen para el pitch

- **Problema**: Reducir fricción en búsqueda, compra y experiencia post-compra en moda.
- **Solución técnica**: Un agente de IA multicanal (WhatsApp + voz) con herramientas de catálogo (Bedrock KB) y búsqueda web (Perplexity), más un pipeline de voz (Twilio + Grok + Cartesia) para llamadas conversacionales.
- **Diferenciación**: Un solo “cerebro” de producto (agente) con múltiples canales; catálogo propio + ampliación con búsqueda en internet; voz como canal adicional listo para integrarse como tool del agente una vez hosteado el servicio.
