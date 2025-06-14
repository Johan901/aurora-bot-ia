from flask import Flask, request
import os
import openai
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import psycopg2
import pytesseract
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
import urllib.request
from PIL import Image
import traceback
import requests
import cv2
import numpy as np


app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

import re

esperando_nombre = {}
ultima_referencia = {}

#Detectamos nombre
def detectar_nombre(texto):
    texto = texto.strip().lower()

    # Detecta nombre en frases típicas
    patrones = [
        r"\bme llamo (\w+)",
        r"\bmi nombre es (\w+)",
        r"\bsoy (\w+)"
    ]
    for patron in patrones:
        match = re.search(patron, texto)
        if match:
            nombre = match.group(1)
            if nombre.isalpha():
                return nombre.capitalize()

    # Si solo escribe el nombre
    if texto.isalpha() and len(texto) <= 20:
        saludos_comunes = ["hola", "buenas", "buenosdias", "buenasdias", "buenastardes", "buenosdías", "buenastardes", "buenasnoches"]
        if texto not in saludos_comunes:
            return texto.capitalize()


    # Intento por estructura: "Hola, Juan"
    match = re.search(r"\b(hola|buenas)[^\w]{0,3}(\w+)", texto, re.IGNORECASE)
    if match and match.group(2).isalpha():
        return match.group(2).capitalize()

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


def buscar_por_referencia(ref, nombre_usuario):
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

    if not resultados:
        sugerencias = recomendar_prendas(nombre_usuario)
        return (
            f"Lo siento mucho {nombre_usuario} la referencia *{ref.upper()}* está *agotada* 😔.\n\n"
            "Pero no te preocupes, mira lo que te puedo sugerir en su lugar 💫:\n\n"
            f"{sugerencias}"
        )


    respuesta = f"Sí {nombre_usuario}, tenemos disponible la(s) referencia(s) similar(es) a *{ref.upper()}*💖🥰✨:\n"
    for ref_real, color, detal, mayor in resultados:
        respuesta += f"- *{ref_real}* en color *{color}* – ${detal:,.0f} al detal / ${mayor:,.0f} por mayor\n"
    return respuesta.strip()


# Mostrar prendas en promo mayor a 40.000
def buscar_promociones(nombre_usuario=""):
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
        WHERE precio_al_detal < 40000 AND cantidad > 0
        ORDER BY precio_al_detal ASC
        LIMIT 3
    """)
    resultados = cur.fetchall()
    cur.close()
    conn.close()

    if not resultados:
        return f"Por ahora no tenemos promociones disponibles {nombre_usuario} 🥺, pero pronto vendrán nuevas ofertas. ¿Te gustaría que te recomiende algo especial mientras tanto? 💡"

    respuesta = f"¡Claro {nombre_usuario}! 🥰✨🥳 Estos productos están en *promoción*:\n"
    for ref, color, detal, mayor in resultados:
        respuesta += f"- *{ref}* en color *{color}* – ${detal:,.0f} al detal / ${mayor:,.0f} por mayor\n"

    respuesta += "\n\n¿Te interesa alguno de estos? 🛍️ Puedo ayudarte a hacer el proceso de compra ✨"

    return respuesta.strip()

#Buscar tipo de prendas del cliente
def buscar_por_tipo_prenda(prenda_usuario, nombre_usuario=""):
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
        WHERE UPPER(tipo_prenda) LIKE %s AND cantidad > 0
        ORDER BY precio_al_detal ASC
        LIMIT 5
    """, ('%' + prenda_usuario.upper() + '%',))
    resultados = cur.fetchall()
    cur.close()
    conn.close()

    if not resultados:
        return f"Lo siento {nombre_usuario} 😔, por ahora no tengo disponibles *{prenda_usuario}*. Pero si quieres puedo sugerirte otras prendas hermosas. ¿Te gustaría ver algunas opciones? ✨"

    respuesta = f"¡Claro {nombre_usuario}! 💖 Mira lo que tengo disponible en *{prenda_usuario}s*:\n"
    for ref, color, detal, mayor in resultados:
        respuesta += f"- *{ref}* en color *{color}* – ${detal:,.0f} al detal / ${mayor:,.0f} por mayor\n"

    respuesta += "\n¿Te gusta alguno? Puedo ayudarte a separarlo o mostrarte más opciones 🛍️✨"
    return respuesta.strip()


def recomendar_prendas(nombre_usuario="", excluidas=[]):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()

    query = """
        SELECT ref, color, precio_al_detal, precio_por_mayor
        FROM inventario
        WHERE cantidad > 0
    """
    if excluidas:
        placeholders = ','.join(['%s'] * len(excluidas))
        query += f" AND ref NOT IN ({placeholders})"
    query += " ORDER BY RANDOM() LIMIT 3"

    cur.execute(query, excluidas if excluidas else [])
    resultados = cur.fetchall()
    cur.close()
    conn.close()

    if not resultados:
        return f"No tengo sugerencias en este momento {nombre_usuario} ☹️😥. Pero si quieres, puedo buscar contigo lo que más se ajuste a tu estilo. 💫"

    respuesta = f"Mira lo que encontré para ti {nombre_usuario} 🤩👀✨:\n"
    for ref, color, detal, mayor in resultados:
        respuesta += f"- *{ref}* en color *{color}* – ${detal:,.0f} al detal / ${mayor:,.0f} por mayor\n"

    respuesta += "\n¿Te gusta alguno? Puedo ayudarte a separarlo 🛍️💖"
    return respuesta


def referencias_mostradas(historial):
    patron_ref = re.compile(r'\*\*?([A-Z0-9\-]{2,10})\*\*?')
    refs = set()
    for h in historial:
        if h["role"] == "assistant":
            matches = patron_ref.findall(h["content"])
            refs.update(matches)
    return list(refs)


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

def responder_mayoristas(nombre_usuario=""):
    return f"""¡Hola {nombre_usuario}! 💖 Si estás pensando en emprender o ya tienes un negocio, esto es para ti:

✨ *Atención mayoristas y revendedores* ✨

En Dulce Guadalupe queremos ayudarte a crecer con prendas hermosas, de calidad y a precios pensados para ti. Aquí te contamos cómo funciona nuestro sistema de venta al por mayor:

👗 Compra mínima: *4 referencias surtidas* (pueden ser diferentes tallas y colores).
⏳ Puedes separar hasta por *8 días* sin compromiso.
🔁 Si haces compras frecuentes (dentro del mismo mes), ¡te mantenemos el *precio por mayor*!

📥 Mira nuestro catálogo completo con los precios al por mayor aquí:
👉 https://dulceguadalupe-catalogo.ecometri.shop/573104238002/collections/conjuntos

🎁 Además, si quieres estar entre los primeros en conocer nuestras *nuevas colecciones y promociones exclusivas*,
únete a nuestro grupo privado de WhatsApp:
👉 https://chat.whatsapp.com/E0LcsssYLpX4hRuh7cc1zX

¿Te gustaría que te ayude a hacer tu primer pedido? 🛍️ Estoy aquí para acompañarte. 💫"""


# Leemos mediante OCR REF desde imagen del cliente
def extraer_referencia_desde_imagen(ruta_imagen, nombre_usuario=""):
    try:
        img = cv2.imread(ruta_imagen)
        if img is None:
            raise ValueError("No se pudo leer la imagen")

        h, w = img.shape[:2]
        margen = 180
        offset = 80  # subesquinas

        regiones = {
            "sup_izq": img[0:margen, 0:margen],
            "sup_der": img[0:margen, w-margen:w],
            "inf_izq": img[h-margen:h, 0:margen],
            "inf_der": img[h-margen:h, w-margen:w],
            "sub_sup_izq": img[offset:offset+margen, offset:offset+margen],
            "sub_sup_der": img[offset:offset+margen, w-offset-margen:w-offset],
            "sub_inf_izq": img[h-offset-margen:h-offset, offset:offset+margen],
            "sub_inf_der": img[h-offset-margen:h-offset, w-offset-margen:w-offset],
        }

        posibles_refs = []
        for nombre, region in regiones.items():
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            procesada = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                cv2.THRESH_BINARY_INV, 11, 8
            )
            texto = pytesseract.image_to_string(procesada, lang="eng+spa")
            matches = re.findall(r'\b[A-Z]{2,4}\d{2,4}\b', texto.upper())
            posibles_refs.extend(matches)

        posibles_refs = list(dict.fromkeys(posibles_refs))  # quitar duplicados

        for ref in posibles_refs:
            respuesta = buscar_por_referencia(ref, nombre_usuario)
            if "agotada" not in respuesta.lower():
                respuesta = re.sub(
                r"tenemos disponible la\(s\) referencia\(s\) similar\(es\) a \*\*?([A-Z]{2,4}\d{2,4})\*\*?",
                r"La referencia **\1** está disponible en los siguientes colores:",
                respuesta,
                flags=re.IGNORECASE
            )
            return ref, respuesta


        if posibles_refs:
            ref_agotada = posibles_refs[0]
            mensaje = (
                f"{nombre_usuario} encontré la referencia *{ref_agotada}*, "
                "pero está *agotada* 😞.\n\n"
                "¿Quieres que te recomiende algo igual de hermoso? 💖✨"
            )
            return ref_agotada, mensaje

        return None, (
            f"No encontré referencias claras en la imagen {nombre_usuario} 😕.\n"
            "¿Podrías tomar otra foto enfocando bien la referencia blanca? 💖📸"
        )

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"[ERROR OCR Mejorado] {error_trace}")
        return None, f"⚠️ Ocurrió un error al procesar la imagen 😥:\n```{str(e)}```"


# Descargar imagen a disco temporal
def descargar_imagen_twilio(media_url):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    response = requests.get(media_url, auth=(account_sid, auth_token))
    ruta = "/tmp/temp_img.jpg"
    with open(ruta, "wb") as f:
        f.write(response.content)
    return ruta

# 🔹 Ruta webhook para Twilio
@app.route("/webhook", methods=["POST"])
def webhook():
    user_msg = (request.form.get("Body") or "").strip()
    sender_number = request.form.get("From")
    num_medias = int(request.form.get("NumMedia", "0"))

    respuestas = []
    ai_response = ""

    # Si el cliente envía una imagen o archivo adjunto
    if num_medias > 0:
        datos_cliente = recuperar_cliente_info(sender_number)
        nombre_usuario = f"{datos_cliente[0]}," if datos_cliente and datos_cliente[0] else ""

        media_url = request.form.get("MediaUrl0")
        mensaje_completo = user_msg if user_msg else "Imagen sin texto"

        # ✅ Guarda ambos: texto + imagen (si existen)
        if media_url:
            mensaje_con_media = f"{mensaje_completo}\n\n[Imagen recibida]({media_url})"
            insertar_mensaje(sender_number, "user", mensaje_con_media)
        else:
            insertar_mensaje(sender_number, "user", mensaje_completo)

        twilio_response = MessagingResponse()
        twilio_response.message(
            f"📸 {nombre_usuario} recibí tu imagen.\n\n"
            "💡 Si deseas *separar una prenda* o *hacer un pedido*, por favor revisa nuestro catálogo:\n"
            "👉 https://dulceguadalupe-catalogo.ecometri.shop\n\n"
            "Cuando decidas sobre tu pedido, *escríbeme para remitirte con una asesora* 💖🛍️"
        )

        insertar_mensaje(sender_number, "assistant", "Mensaje informativo por imagen/archivo no procesado.")

        return str(twilio_response)




    try:
        historial = recuperar_historial(sender_number, limite=15)
        primera_vez = len(historial) == 0

        lower_msg = user_msg.lower()
        # 🔍 Verificar si están preguntando por una referencia
        mensaje_limpio = re.sub(r'[^\w\s]', '', lower_msg)
        match_ref = re.search(r'\b[a-z]{2}\d{2,4}\b', mensaje_limpio)

        #Prendas
        posibles_prendas = ["conjunto", "vestido", "body", "blusa", "falda"]
        posibles_tallas = ["xs", "s", "m", "l", "xl"]

        # Detección inteligente
        nombre_detectado = detectar_nombre(user_msg)
        correo_detectado = detectar_correo(user_msg)
        prenda_detectada = next((p for p in posibles_prendas if p in lower_msg), None)
        talla_detectada = next((t.upper() for t in posibles_tallas if f"talla {t}" in lower_msg or f"talla: {t}" in lower_msg), None)

        # Recuperar info previa
        datos_cliente = recuperar_cliente_info(sender_number)
        nombre, prenda, talla = datos_cliente if datos_cliente else (None, None, None)

        nombre_usuario = f"{nombre}," if nombre else ""
        
        # 👇 Activar bandera si no tiene nombre registrado
        if not nombre:
            esperando_nombre[sender_number] = True

        # Actualizar cliente si detectó algo
        if esperando_nombre.get(sender_number) and nombre_detectado and not nombre:
            actualizar_cliente(sender_number, nombre_detectado, prenda_detectada, talla_detectada, correo_detectado)
            esperando_nombre.pop(sender_number, None)  # Limpiar bandera después de guardar
        elif prenda_detectada or talla_detectada or correo_detectado:
            actualizar_cliente(sender_number, None, prenda_detectada, talla_detectada, correo_detectado)


        if match_ref:
            ref_encontrada = match_ref.group().upper()
            ai_response = buscar_por_referencia(ref_encontrada, nombre_usuario)
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", ai_response)
            twilio_response = MessagingResponse()
            twilio_response.message(ai_response)
            return str(twilio_response)



        elif any(palabra in lower_msg for palabra in ["promocion", "promoción", "oferta", "barato", "promo"]):
            ai_response = buscar_promociones(nombre_usuario)
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", ai_response)
            twilio_response = MessagingResponse()
            twilio_response.message(ai_response)
            return str(twilio_response)
        
        elif any(p in lower_msg for p in ["recomiéndame", "que me recomiendas", "recomendar", "mostrarme" "mostrar ref" "recomiendame algo", "que me quedaria bien", "recomienda", "sugiere", "sugerencia", "qué me ofreces", "tienes algo bonito", "algo que me quede bien"]):
            ai_response = recomendar_prendas(nombre_usuario)
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", ai_response)
            twilio_response = MessagingResponse()
            twilio_response.message(ai_response)
            return str(twilio_response)
        
        elif any(f in lower_msg for f in ["otros", "más opciones", "muéstrame más", "algo diferente", "diferente", "otras referencias", "otra opción", "más referencias"]):
            ya_mostradas = referencias_mostradas(historial)
            ai_response = recomendar_prendas(nombre_usuario, excluidas=ya_mostradas)
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", ai_response)
            twilio_response = MessagingResponse()
            twilio_response.message(ai_response)
            return str(twilio_response)

        elif any(p in lower_msg for p in ["mayorista", "como puedo comprar al por mayor", "comprar por mayor", "ventas por mayor", "quiero ser mayorista", "comprar al por mayor", "emprender", "emprender con nosotros", "mayorista", "mayorista" "por mayor", "revender", "quiero vender", "precio mayor", "quiero comprar varias"]):
            ai_response = responder_mayoristas(nombre_usuario)
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", ai_response)
            twilio_response = MessagingResponse()
            twilio_response.message(ai_response)
            return str(twilio_response)


        frases = []

        if nombre:
            frases.append(f"Hola {nombre}, ¿cómo estás? 🌸")
        if prenda and talla:
            frases.append(f"La última vez pediste un {prenda} talla {talla}.")
        elif prenda:
            frases.append(f"La última vez pediste un {prenda}.")


        # Mensaje especial si es primera vez
        if primera_vez:
            if nombre:
                historial.insert(0, {
                    "role": "assistant",
                    "content": f"¡Hola {nombre}! 😊 Soy Aurora, la asistente virtual de Dulce Guadalupe. "
                    "Estoy aquí para ayudarte con nuestros productos, separados y más. "
                    "¿Quieres que te muestre algo de nuestros conjuntos más 🔥 o te ayudo con alguna duda? 💖"
                })
            else:
                esperando_nombre[sender_number] = True
                historial.insert(0, {
                    "role": "assistant",
                    "content": "¡Hola! 😊 Soy Aurora, la asistente virtual de Dulce Guadalupe. "
                    "Estoy aquí para ayudarte con nuestros productos, separados y más. "
                    "¿Quieres que te muestre algo de nuestros conjuntos más 🔥 o te ayudo con alguna duda? 💖"
                })


        # Buscar prendas por tipo (conjunto, blusa, body, etc.)
        tipos_consultables = ["conjunto", "conjuntos", "blusa", "blusas", "body", "bodys", "pantalón", "pantalon", "short", "shorts", "falda", "faldas", "vestido", "vestidos" "ROPA INTERIOR" "interior" "ropa interior" "sudadera" "sudaderas" "pijama" "pijamas" "piyama" "piyamas" "pantaloneta" "pantalonetas" "jeans" "jean" "malla" "mallas" "licra" "licras" "leggins" "leggin" "legin" "legins" "falda short" "enterizo" "enterizos" "enterizo short" "chaqueta" "chaquetas" "chaleco" "chalecos" "camisa" "camisas" "camisetas" "camisera" "camiseras" "buzo" "buso" "buzos" "busos" "blusa jeans" "blusa " "blusa" "bikini" "bikinis"]
        for tipo in tipos_consultables:
            if tipo in lower_msg:
                prenda_estandar = tipo.rstrip('s')  # quitar plural simple
                ai_response = buscar_por_tipo_prenda(prenda_estandar, nombre_usuario)
                insertar_mensaje(sender_number, "user", user_msg)
                insertar_mensaje(sender_number, "assistant", ai_response)
                twilio_response = MessagingResponse()
                twilio_response.message(ai_response)
                return str(twilio_response)


        # Armar historial para GPT
        if frases:
            historial.insert(0, {"role": "user", "content": " ".join(frases)})
        historial.append({"role": "user", "content": user_msg})

        if nombre:
            historial.insert(0, {"role": "user", "content": f"Por favor, respóndeme usando mi nombre: {nombre}."})

        system_prompt = """
        Eres Aurora, la asistente artificial de Dulce Guadalupe 👗✨. Dulce Guadalupe es una empresa caleña de Cali, Colombia ubicados en el centro comercial la casona en la ciudad de cali local 302, legalmente constituida y dedicada a la confección de prendas de vestir para mujeres. Estás aquí para ayudar a cada persona que escribe, como si fuera una amiga cercana 💖. Apoyamos a mujeres emprendedoras con nuestro modelo de negocio y ofrecemos sistemas de separados (las prendas se pueden apartar por 1 semana sin compromiso). Respondes siempre con un tono sutil, amoroso, respetuoso y cercano 🫶. Usa emojis con moderación para que el mensaje se sienta cálido y humano, sin exagerar. Tu trabajo es responder preguntas relacionadas con: catálogo de productos, precios, sistema de separados, cómo revender, formas de pago, envíos, horarios de atención y dudas comunes. Si el cliente parece confundido o agresivo, responde con calma y dulzura. Si alguien duda que eres real, explícale que eres Aurora, una asistente virtual entrenada para ayudar 💻. Si alguien quiere hablar con una persona, dile que puede escribir la palabra 'humano' y con gusto será derivado. Si el cliente se muestra interesado en comprar o conocer productos, ofrece enviarle el catálogo 📸 o sugerencias personalizadas. Siempre estás dispuesta a ayudar, vender, y explicar cómo funciona todo. Si es la primera vez que te escribe, salúdalo con alegría y preséntate. El horario de atención de Dulce Guadalupe es de lunes a sábado de 8:00 a.m. a 6:00 p.m y si alguien pregunta por el horario, responde con exactitud. Nunca inventes referencias o productos. Siempre responde basándote en los datos reales disponibles. Usa nuestra base de datos para dar la información de las referencias, y recomienda referencias de alli. Siempre que conozcas el nombre de la persona, debes usarlo al inicio de tu respuesta como parte del saludo. Si ya sabes el nombre del cliente, siempre debes iniciar tu respuesta con algo como: 'Hola Juan,' o '¡Hola María querida!' para crear conexión cercana.

        Si el cliente pregunta cómo comprar al por mayor, cómo revender, o menciona que quiere vender ropa, explícale con emoción y claridad cómo funciona nuestro sistema de venta para mayoristas. Dile que pueden iniciar con mínimo 4 referencias surtidas, que pueden separar hasta por 8 días sin compromiso, y que si compran de forma recurrente en el mismo mes mantienen el precio al por mayor. Ofrécele el catálogo mayorista con este enlace explicale que es por telegram:
        👉 https://t.me/dulcedguadalupecali

         Además, si el cliente te dice que no tiene la aplicación de telegram ofrecele este otro catalogo facil de aceder
         https://dulceguadalupe-catalogo.ecometri.shop/573104238002/collections/conjuntos
        
        
        Además, invítalo a unirse a nuestro grupo privado de WhatsApp para conocer promociones y colecciones exclusivas:
        👉 https://chat.whatsapp.com/E0LcsssYLpX4hRuh7cc1zX
        """

        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": system_prompt}] + historial,
            max_tokens=200
        )


        ai_response = completion.choices[0].message["content"]

       # Si aún no tenemos el nombre del cliente registrado ni fue detectado ahora
        if not nombre and not nombre_detectado:
            esperando_nombre[sender_number] = True

            if "tu nombre" not in lower_msg and not re.search(r"\b(me llamo|mi nombre es|soy)\b", lower_msg):
                ai_response += "\n\n💡 ¿Podrías decirme tu nombre para darte una atención más personalizada? 🫶"




    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"[ERROR GPT] {error_trace}")
        ai_response = "Lo siento, ocurrió un error interno procesando tu mensaje 😥."

    insertar_mensaje(sender_number, "user", user_msg)
    insertar_mensaje(sender_number, "assistant", ai_response)

    twilio_response = MessagingResponse()
    if respuestas:
        twilio_response.message("\n\n".join(respuestas))
    if ai_response:
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
