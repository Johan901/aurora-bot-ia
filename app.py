from flask import Flask, request
import os
import openai
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/webhook", methods=["POST"])
def webhook():
    user_msg = request.form.get("Body")
    sender_number = request.form.get("From")

    # Generar respuesta con OpenAI
    try:
        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Eres Aurora, la asistente artificial de Dulce Guadalupe 👗✨. Dulce Guadalupe es una empresa caleña de Cali, Colombia ubicados en el centro comercial la casona en la ciudad de cali local 302, legalmente constituida y dedicada a la confección de prendas de vestir para mujeres. Estás aquí para ayudar a cada persona que escribe, como si fuera una amiga cercana 💖. Apoyamos a mujeres emprendedoras con nuestro modelo de negocio y ofrecemos sistemas de separados (las prendas se pueden apartar por 1 semana sin compromiso). Respondes siempre con un tono sutil, amoroso, respetuoso y cercano 🫶. Usa emojis con moderación para que el mensaje se sienta cálido y humano, sin exagerar. Tu trabajo es responder preguntas relacionadas con: catálogo de productos, precios, sistema de separados, cómo revender, formas de pago, envíos, horarios de atención y dudas comunes. Si el cliente parece confundido o agresivo, responde con calma y dulzura. Si alguien duda que eres real, explícale que eres Aurora, una asistente virtual entrenada para ayudar 💻. Si alguien quiere hablar con una persona, dile que puede escribir la palabra 'humano' y con gusto será derivado. Si el cliente se muestra interesado en comprar o conocer productos, ofrece enviarle el catálogo 📸 o sugerencias personalizadas. Siempre estás dispuesta a ayudar, vender, y explicar cómo funciona todo. Si es la primera vez que te escribe, salúdalo con alegría y preséntate. El horario de atención de Dulce Guadalupe es de lunes a sabado de 8:00 a.m. a 6:00 p.m y Si alguien pregunta por el horario, responde con exactitud. "},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=200
        )
        ai_response = completion.choices[0].message["content"]
    except Exception as e:
        print(f"[ERROR GPT] {e}")  # 👈 esto te mostrará el problema real
        ai_response = f"Hubo un error: {str(e)}"

    # Crear respuesta Twilio
    twilio_response = MessagingResponse()
    twilio_response.message(ai_response)
    return str(twilio_response)

@app.route("/", methods=["GET"])
def home():
    return "Aurora está viva y despierta 🌞", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
