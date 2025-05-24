# Agente IA por WhatsApp (Python + GPT-3.5 + Twilio)

Este proyecto conecta Twilio WhatsApp con OpenAI GPT-3.5 para responder automáticamente a mensajes.

## Requisitos

- Cuenta en [OpenAI](https://platform.openai.com)
- Cuenta en [Twilio](https://www.twilio.com/)
- Python 3.7 o superior
- Flask

## Archivos clave

- `app.py`: servidor principal con webhook
- `.env`: variables de entorno (usa `.env.example`)
- `requirements.txt`: librerías necesarias

## Instrucciones

1. Renombra `.env.example` a `.env` y pon tu clave de OpenAI.
2. Instala dependencias:

```
pip install -r requirements.txt
```

3. Ejecuta el servidor:

```
python app.py
```

4. Expón tu servidor (ngrok o deploy en Render) y conecta el webhook en Twilio.

¡Listo! Tu bot responderá automáticamente desde WhatsApp 💬
