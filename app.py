from flask import Flask, request
import os
import openai
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import psycopg2

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

# 🔹 Guardar mensaje en la base de datos
def insertar_mensaje(phone_number, role, message):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO chat_history (phone_number, role, message)
        VALUES (%s, %s, %s)
    """, (phone_number, role, message))
    conn.commit()
    cur.close()
    conn.close()

# 🔹 Recuperar los últimos X mensajes
def recuperar_historial(phone_number, limite=15):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT role, message FROM chat_history
        WHERE phone_number = %s
        ORDER BY timestamp DESC
        LIMIT %s
    """, (phone_number, limite))
    resultados = cur.fetchall()
    cur.close()
    conn.close()
    return [{"role": r, "content": m} for r, m in reversed(resultados)]

# 🔹 Recuperar datos del cliente (nombre, prenda, talla)
def recuperar_cliente_info(phone_number):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT nombre, ultima_prenda, ultima_talla
        FROM clientes_ia
        WHERE phone_number = %s
    """, (phone_number,))
    resultado = cur.fetchone()
    cur.close()
    conn.close()
    return resultado  # (nombre, prenda, talla) o None

# 🔹 Insertar o actualizar cliente en la tabla clientes_ia
def actualizar_cliente(phone_number, nombre=None, prenda=None, talla=None):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    
    # Comprobar si el cliente ya existe
    cur.execute("SELECT id FROM clientes_ia WHERE phone_number = %s", (phone_number,))
    existe = cur.fetchone()

    if existe:
        # Solo actualiza si hay datos nuevos
        campos = []
        valores = []
        if nombre:
            campos.append("nombre = %s")
            valores.append(nombre)
        if prenda:
            campos.append("ultima_prenda = %s")
            valores.append(prenda)
        if talla:
            campos.append("ultima_talla = %s")
            valores.append(talla)
        if campos:
            campos.append("fecha_ultima_interaccion = NOW()")
            query = f"UPDATE clientes_ia SET {', '.join(campos)} WHERE phone_number = %s"
            valores.append(phone_number)
            cur.execute(query, valores)
    else:
        cur.execute("""
            INSERT INTO clientes_ia (phone_number, nombre, ultima_prenda, ultima_talla)
            VALUES (%s, %s, %s, %s)
        """, (phone_number, nombre, prenda, talla))

    conn.commit()
    cur.close()
    conn.close()


# 🔹 Ruta webhook para Twilio
@app.route("/webhook", methods=["POST"])
def webhook():
    user_msg = request.form.get("Body")
    sender_number = request.form.get("From")

    try:
        historial = recuperar_historial(sender_number, limite=15)


        # 🔍 Detectar si el usuario mencionó nombre, prenda o talla
        nombre_detectado = None
        prenda_detectada = None
        talla_detectada = None

        lower_msg = user_msg.lower()

        # Detectar nombre (simplificado)
        if "me llamo" in lower_msg or "mi nombre es" in lower_msg:
            partes = user_msg.split()
            for i, palabra in enumerate(partes):
                if palabra.lower() in ["llamo", "es"]:
                    if i + 1 < len(partes):
                        nombre_detectado = partes[i + 1].capitalize()
                        break

        # Detectar prenda y talla
        posibles_prendas = ["conjunto", "vestido", "body", "blusa", "falda"]
        posibles_tallas = ["xs", "s", "m", "l", "xl"]

        for p in posibles_prendas:
            if p in lower_msg:
                prenda_detectada = p
                break

        for t in posibles_tallas:
            if f"talla {t}" in lower_msg or f"talla: {t}" in lower_msg:
                talla_detectada = t.upper()
                break

        # Actualizar cliente si detectó algo
        if nombre_detectado or prenda_detectada or talla_detectada:
            actualizar_cliente(sender_number, nombre_detectado, prenda_detectada, talla_detectada)



        # 🧠 Insertar memoria previa si existe en clientes_ia
        datos_cliente = recuperar_cliente_info(sender_number)
        if datos_cliente:
            nombre, prenda, talla = datos_cliente
            frases = []
            if nombre:
                frases.append(f"Mi nombre es {nombre}.")
            if prenda and talla:
                frases.append(f"La última vez pedí un {prenda} talla {talla}.")
            elif prenda:
                frases.append(f"La última vez pedí un {prenda}.")
            if frases:
                historial.insert(0, {"role": "user", "content": " ".join(frases)})

        historial.append({"role": "user", "content": user_msg})

        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "Eres Aurora, la asistente artificial de Dulce Guadalupe 👗✨. Dulce Guadalupe es una empresa caleña de Cali, Colombia ubicados en el centro comercial la casona en la ciudad de cali local 302, legalmente constituida y dedicada a la confección de prendas de vestir para mujeres. Estás aquí para ayudar a cada persona que escribe, como si fuera una amiga cercana 💖. Apoyamos a mujeres emprendedoras con nuestro modelo de negocio y ofrecemos sistemas de separados (las prendas se pueden apartar por 1 semana sin compromiso). Respondes siempre con un tono sutil, amoroso, respetuoso y cercano 🫶. Usa emojis con moderación para que el mensaje se sienta cálido y humano, sin exagerar. Tu trabajo es responder preguntas relacionadas con: catálogo de productos, precios, sistema de separados, cómo revender, formas de pago, envíos, horarios de atención y dudas comunes. Si el cliente parece confundido o agresivo, responde con calma y dulzura. Si alguien duda que eres real, explícale que eres Aurora, una asistente virtual entrenada para ayudar 💻. Si alguien quiere hablar con una persona, dile que puede escribir la palabra 'humano' y con gusto será derivado. Si el cliente se muestra interesado en comprar o conocer productos, ofrece enviarle el catálogo 📸 o sugerencias personalizadas. Siempre estás dispuesta a ayudar, vender, y explicar cómo funciona todo. Si es la primera vez que te escribe, salúdalo con alegría y preséntate. El horario de atención de Dulce Guadalupe es de lunes a sábado de 8:00 a.m. a 6:00 p.m y si alguien pregunta por el horario, responde con exactitud."}] + historial,
            max_tokens=200
        )
        ai_response = completion.choices[0].message["content"]

    except Exception as e:
        print(f"[ERROR GPT] {e}")
        ai_response = "Lo siento, ocurrió un error al procesar tu mensaje."

    # Guardar conversación
    insertar_mensaje(sender_number, "user", user_msg)
    insertar_mensaje(sender_number, "assistant", ai_response)

    # Responder por WhatsApp
    twilio_response = MessagingResponse()
    twilio_response.message(ai_response)
    return str(twilio_response)

# 🔹 Home route para verificar que está viva
@app.route("/", methods=["GET"])
def home():
    return "Aurora está viva y despierta 🌞", 200

# 🔹 Ejecutar app
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
