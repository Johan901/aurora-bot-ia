from flask import Flask, request
import os
import openai
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import psycopg2

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

import re

def detectar_nombre(texto):
    texto = texto.strip()
    texto_lower = texto.lower()

    frases = ["me llamo", "soy", "mi nombre es"]
    for frase in frases:
        if frase in texto_lower:
            partes = texto_lower.split()
            for i, palabra in enumerate(partes):
                if palabra in frase:
                    if i + 1 < len(partes):
                        posible = texto.split()[i + 1]
                        if posible.isalpha():
                            return posible.capitalize()

    if texto.isalpha() and texto.istitle() and len(texto) <= 15:
        return texto

    partes = texto.split(',')
    if len(partes) == 2 and partes[1].strip().istitle():
        posible = partes[1].strip()
        if posible.isalpha():
            return posible

    palabras = texto.split()
    for i, palabra in enumerate(palabras):
        if palabra.lower() in ["gracias", "hola"] and i + 1 < len(palabras):
            posible_nombre = palabras[i + 1]
            if posible_nombre.istitle() and posible_nombre.isalpha():
                return posible_nombre

    return None

def detectar_correo(texto):
    patron = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    coincidencias = re.findall(patron, texto)
    return coincidencias[0] if coincidencias else None


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
def actualizar_cliente(phone_number, nombre=None, prenda=None, talla=None, correo=None):
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
        if correo:
            campos.append("correo = %s")
            valores.append(correo)
        if campos:
            campos.append("fecha_ultima_interaccion = NOW()")
            query = f"UPDATE clientes_ia SET {', '.join(campos)} WHERE phone_number = %s"
            valores.append(phone_number)
            cur.execute(query, valores)
    else:
        cur.execute("""
            INSERT INTO clientes_ia (phone_number, nombre, ultima_prenda, ultima_talla, correo)
            VALUES (%s, %s, %s, %s, %s)
        """, (phone_number, nombre, prenda, talla, correo))

    conn.commit()
    cur.close()
    conn.close()


def buscar_por_referencia(ref, nombre_cliente=None):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT ref, color, precio_al_detal, precio_por_mayor
        FROM inventario
        WHERE ref ILIKE %s AND cantidad > 0
    """, (ref.upper() + '%',))
    resultados = cur.fetchall()
    cur.close()
    conn.close()

    saludo = f"💖 ¡Genial, {nombre_cliente}!" if nombre_cliente else "💖 ¡Genial!"

    if not resultados:
        return f"{saludo} La referencia *{ref.upper()}* está *agotada* 🥺 por el momento . Si deseas te puedo recomendar otras prendas similares o enviarte el catálogo completo 📸."

    respuesta = f"{saludo} Tenemos disponible la(s) referencia(s) similar(es) a *{ref.upper()}*:\n"
    for ref_real, color, detal, mayor in resultados:
        respuesta += f"- *{ref_real}* en color *{color}* – ${detal:,.0f} al detal / ${mayor:,.0f} por mayor\n"
    return respuesta.strip()




# 🔹 Buscar productos en promoción (detal < 40000)
def buscar_promociones():
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT ref, color, precio_al_detal
        FROM inventario
        WHERE precio_al_detal < 40000 AND cantidad > 0
        ORDER BY precio_al_detal ASC
        LIMIT 3
    """)
    resultados = cur.fetchall()
    cur.close()
    conn.close()

    if not resultados:
        return "Por ahora no tenemos promociones disponibles 🥺, pero pronto vendrán nuevas ofertas."

    respuesta = "¡Claro! 🌟💖👗 Estos productos están en *promoción*:\n"
    for ref, color, precio in resultados:
        respuesta += f"- *{ref}* en color *{color}* – solo ${precio:,.0f}\n"
    return respuesta.strip()


# 🔹 Verificar si una referencia está agotada (cantidad 0 en todos los colores)
def verificar_agotado(ref):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM inventario
        WHERE ref = %s AND cantidad > 0
    """, (ref.upper(),))
    disponible = cur.fetchone()[0]
    cur.close()
    conn.close()

    return disponible == 0


# 🔹 Ruta webhook para Twilio
@app.route("/webhook", methods=["POST"])
def webhook():
    user_msg = request.form.get("Body")
    sender_number = request.form.get("From")

    try:
        historial = recuperar_historial(sender_number, limite=15)
        primera_vez = len(historial) == 0

        lower_msg = user_msg.lower()
        # 🔍 Verificar si están preguntando por una referencia
        # 🔍 Verificar si están preguntando por una referencia
        lower_msg = user_msg.lower()
        mensaje_limpio = re.sub(r'[^\w\s]', '', lower_msg)
        match_ref = re.search(r'\b[a-z]{2}\d{2,4}\b', mensaje_limpio)

        if match_ref:
            ref_encontrada = match_ref.group().upper()
            ai_response = buscar_por_referencia(ref_encontrada, nombre)
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", ai_response)
            twilio_response = MessagingResponse()
            twilio_response.message(ai_response)
            return str(twilio_response)



        elif any(palabra in lower_msg for palabra in ["promocion", "promoción", "oferta", "barato", "promo"]):
            ai_response = buscar_promociones()
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", ai_response)
            twilio_response = MessagingResponse()
            twilio_response.message(ai_response)
            return str(twilio_response)


        posibles_prendas = ["conjunto", "vestido", "body", "blusa", "falda"]
        posibles_tallas = ["xs", "s", "m", "l", "xl"]

        # Detección inteligente
        nombre_detectado = detectar_nombre(user_msg)
        correo_detectado = detectar_correo(user_msg)
        prenda_detectada = next((p for p in posibles_prendas if p in lower_msg), None)
        talla_detectada = next((t.upper() for t in posibles_tallas if f"talla {t}" in lower_msg or f"talla: {t}" in lower_msg), None)

        # Actualizar cliente si detectó algo
        if nombre_detectado or prenda_detectada or talla_detectada or correo_detectado:
            actualizar_cliente(sender_number, nombre_detectado, prenda_detectada, talla_detectada, correo_detectado)


        # Recuperar info previa
        datos_cliente = recuperar_cliente_info(sender_number)
        nombre, prenda, talla = datos_cliente if datos_cliente else (None, None, None)

        frases = []
        if nombre:
            frases.append(f"Mi nombre es {nombre}.")
        if prenda and talla:
            frases.append(f"La última vez pedí un {prenda} talla {talla}.")
        elif prenda:
            frases.append(f"La última vez pedí un {prenda}.")

        # Mensaje especial si es primera vez
        if primera_vez:
            historial.append({
                "role": "assistant",
                "content": (
                    "¡Hola! 😊 Soy Aurora, la asistente virtual de Dulce Guadalupe. "
                    "Estoy aquí para ayudarte con nuestros productos, separados y más. "
                    "¿En qué puedo asistirte hoy? 💖"
                )
            })

        # Armar historial para GPT
        if frases:
            historial.insert(0, {"role": "user", "content": " ".join(frases)})
        historial.append({"role": "user", "content": user_msg})

        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "Eres Aurora, la asistente artificial de Dulce Guadalupe 👗✨. Dulce Guadalupe es una empresa caleña de Cali, Colombia ubicados en el centro comercial la casona en la ciudad de cali local 302, legalmente constituida y dedicada a la confección de prendas de vestir para mujeres. Estás aquí para ayudar a cada persona que escribe, como si fuera una amiga cercana 💖. Apoyamos a mujeres emprendedoras con nuestro modelo de negocio y ofrecemos sistemas de separados (las prendas se pueden apartar por 1 semana sin compromiso). Respondes siempre con un tono sutil, amoroso, respetuoso y cercano 🫶. Usa emojis con moderación para que el mensaje se sienta cálido y humano, sin exagerar. Tu trabajo es responder preguntas relacionadas con: catálogo de productos, precios, sistema de separados, cómo revender, formas de pago, envíos, horarios de atención y dudas comunes. Si el cliente parece confundido o agresivo, responde con calma y dulzura. Si alguien duda que eres real, explícale que eres Aurora, una asistente virtual entrenada para ayudar 💻. Si alguien quiere hablar con una persona, dile que puede escribir la palabra 'humano' y con gusto será derivado. Si el cliente se muestra interesado en comprar o conocer productos, ofrece enviarle el catálogo 📸 o sugerencias personalizadas. Siempre estás dispuesta a ayudar, vender, y explicar cómo funciona todo. Si es la primera vez que te escribe, salúdalo con alegría y preséntate. El horario de atención de Dulce Guadalupe es de lunes a sábado de 8:00 a.m. a 6:00 p.m y si alguien pregunta por el horario, responde con exactitud."}] + historial,
            max_tokens=200
        )

        ai_response = completion.choices[0].message["content"]

        # Si no tenemos nombre guardado ni fue detectado
        if not nombre and not nombre_detectado:
            ai_response += "\n\n💡 Por cierto, ¿me podrías decir tu nombre para atenderte mejor? 🫶"

    except Exception as e:
        print(f"[ERROR GPT] {e}")
        ai_response = "Lo siento, ocurrió un error al procesar tu mensaje."

    insertar_mensaje(sender_number, "user", user_msg)
    insertar_mensaje(sender_number, "assistant", ai_response)

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
