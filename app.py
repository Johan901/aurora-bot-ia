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
catalogo_enviado = {}  

#Detectamos nombre
def detectar_nombre(texto, sender_number=None):
    texto = texto.strip().lower()
    palabras_invalidas = {
        "hola", "buenas", "tardes", "dias", "noches", "tienes", "quiero", "necesito", "por", "favor",
        "info", "informacion", "información", "me", "puedo", "ver", "gracias", "de", "la", "el", "una",
        "separar", "referencia", "catalogo", "https", "para", "comprar"
    }

    # 1. Frases típicas (me llamo, mi nombre es, soy)
    patrones = [
        r"\bme llamo (\w+)",
        r"\bmi nombre es (\w+)",
        r"\bsoy (\w+)"
    ]
    for patron in patrones:
        for match in re.finditer(patron, texto):
            posible = match.group(1)
            if posible.isalpha() and posible not in palabras_invalidas and len(posible) > 2:
                return posible.capitalize()

    # Si está esperando nombre y es una sola palabra válida
    if sender_number and esperando_nombre.get(sender_number):
        palabras = texto.split()
        if len(palabras) == 1:
            posible = palabras[0]
            if posible.isalpha() and posible not in palabras_invalidas and len(posible) > 2:
                return posible.capitalize()

    # Si dice "Hola Juan", "Buenas Sara", etc.
    if sender_number and not esperando_nombre.get(sender_number):
        match = re.search(r"\b(?:hola|buenas)[\s,]*(\w+)", texto)
        if match:
            posible = match.group(1)
            if posible.isalpha() and posible not in palabras_invalidas and len(posible) > 2:
                return posible.capitalize()

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
        SELECT nombre, ultima_prenda, ultima_talla, tipo_cliente
        FROM clientes_ia
        WHERE phone_number = %s
    """, (phone_number,))
    resultado = cur.fetchone()
    cur.close()
    conn.close()
    return resultado 

# 🔹 Insertar o actualizar cliente en la tabla clientes_ia
def actualizar_cliente(phone_number, nombre=None, prenda=None, talla=None, correo=None, ciudad=None, tipo_cliente=None):
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
        if tipo_cliente:
            campos.append("tipo_cliente = %s")
            valores.append(tipo_cliente)
        if ciudad:
            campos.append("ciudad = %s")
            valores.append(ciudad)
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
            INSERT INTO clientes_ia (phone_number, nombre, ultima_prenda, ultima_talla, correo, ciudad, tipo_cliente)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (phone_number, nombre, prenda, talla, correo, ciudad, tipo_cliente))

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

# Hacer pedido
def mensaje_hacer_pedido(nombre_usuario=""):
    return (
        f"💡 {nombre_usuario} si deseas hacer un pedido, simplemente escribe *Hacer pedido* 🛍️.\n\n"
        "Tu solicitud será enviada y una asesora se pondrá en contacto contigo muy pronto por este mismo chat 💬✨.\n"
        "¡Gracias por confiar en Dulce Guadalupe! 💖"
    )


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
⏳ Puedes separar hasta por *8 días*.
🔁 Si haces compras frecuentes (dentro del mismo mes), ¡te mantenemos el *precio por mayor*!

📥 Mira nuestro catálogo completo con los precios al por mayor aquí:
👉 https://t.me/dulcedguadalupecali

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

# BLOQUEOS
def esta_bloqueado(phone_number):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT bloqueado FROM bloqueos_aurora WHERE phone_number = %s
    """, (phone_number,))
    resultado = cur.fetchone()
    cur.close()
    conn.close()
    return resultado and resultado[0] is True

def bloquear_aurora_para(phone_number):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bloqueos_aurora (phone_number, bloqueado)
        VALUES (%s, TRUE)
        ON CONFLICT (phone_number) DO UPDATE SET bloqueado = TRUE
    """, (phone_number,))
    conn.commit()
    cur.close()
    conn.close()


#DESBLOQUEO
def desbloquear_aurora_para(phone_number):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    cur.execute("DELETE FROM bloqueos_aurora WHERE phone_number = %s", (phone_number,))
    conn.commit()
    cur.close()
    conn.close()

#Detectar ciudad
def detectar_ciudad(texto):
    texto = texto.lower()

    patrones_ciudad = [
        r"soy de ([a-záéíóúñ\s]+)",
        r"vivo en ([a-záéíóúñ\s]+)",
        r"desde ([a-záéíóúñ\s]+)",
        r"escribo desde ([a-záéíóúñ\s]+)",
        r"estoy en ([a-záéíóúñ\s]+)",
        r"de ([a-záéíóúñ\s]+)$"
    ]

    # Lista extendida de ciudades en Colombia, centrada en Valle del Cauca y Cali
    ciudades_colombia = {
        "cali", "jamundí", "yumbo", "palmira", "buga", "cerrito", "dapa", "cerrito", "santa helena", "ginebra", "candelaria", "tuluá", "buga", "cartago", "zarzal", "sevilla", "roldanillo", "caicedonia", "la unión", "obando", "el cerrito", "el águila",
        "bogotá", "medellín", "barranquilla", "cartagena", "pereira", "bucaramanga", "cúcuta", "soacha", "ibagué", "neiva", "pasto", "manizales", "villavicencio", "montería", "santa marta",
        "sincelejo", "valledupar", "riohacha", "quibdó", "tunja", "popayán", "florencia", "armenia", "leticia", "mitú", "mocoa", "san andrés", "bello", "envigado", "dosquebradas", "chía", "girardot",
        "fusagasugá", "facatativá", "mosquera", "malambo", "soledad", "ciénaga", "tumaco", "guadalajara de buga", "funza", "zarzal"
    }

    for patron in patrones_ciudad:
        match = re.search(patron, texto)
        if match:
            ciudad_detectada = match.group(1).strip()
            ciudad_normalizada = ciudad_detectada.split(",")[0].strip()
            for ciudad in ciudades_colombia:
                if ciudad_normalizada.startswith(ciudad):
                    return ciudad.title()

    # Fallback si solo mencionan la ciudad sin contexto
    for ciudad in ciudades_colombia:
        if ciudad in texto:
            return ciudad.title()

    return None


# 🔹 Ruta webhook para Twilio
@app.route("/webhook", methods=["POST"])
def webhook():
    user_msg = (request.form.get("Body") or "").strip()
    sender_number = request.form.get("From")
    num_medias = int(request.form.get("NumMedia", "0"))

    # Recuperar info previa
    datos_cliente = recuperar_cliente_info(sender_number)
    nombre, prenda, talla, tipo_cliente = datos_cliente if datos_cliente else (None, None, None, None)

    # 👇 Activar bandera inmediatamente antes de responder
    if nombre is None:
        esperando_nombre[sender_number] = True
    else:
        esperando_nombre[sender_number] = False  # por si ya lo tenía

    nombre_usuario = f"{nombre}," if nombre else ""


    respuestas = []
    ai_response = ""
    
    #Bloqueo
    if esta_bloqueado(sender_number):
        insertar_mensaje(sender_number, "user", user_msg)
        return str(MessagingResponse())  # No responde nada si está bloqueado
    
    # 🔥 Desbloqueo automático si estaba bloqueado pero ya no usa [ASESOR]
    if esta_bloqueado(sender_number) and not user_msg.startswith("[ASESOR]"):
        desbloquear_aurora_para(sender_number)
    
    if user_msg.startswith("[ASESOR]"): 
        # Bloqueo automático
        insertar_mensaje(sender_number, "user", user_msg)
        bloquear_aurora_para(sender_number)
        return str(MessagingResponse())  # no deja que Aurora responda a esto


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
            "👉 https://t.me/dulcedguadalupecali\n\n"
            "Cuando decidas sobre tu pedido, *escríbeme* lo siguiente para remitirte al sector de ventas: *quiero hacer el pedido* 💖🛍️"
        )

        insertar_mensaje(sender_number, "assistant", "Mensaje informativo por imagen/archivo no procesado.")

        return str(twilio_response)


    if num_medias > 0 and "audio" in request.form.get("MediaContentType0", ""):
        twilio_response = MessagingResponse()
        twilio_response.message(
            f"🎧 {nombre_usuario} recibí tu audio.\n\n"
            "¿Deseas hacer un pedido o separar una prenda?\n"
            + mensaje_hacer_pedido(nombre_usuario)
        )
        insertar_mensaje(sender_number, "assistant", "Mensaje por audio recibido.")
        return str(twilio_response)


    try:
        historial = recuperar_historial(sender_number, limite=15)
        primera_vez = len(historial) == 0

        lower_msg = user_msg.lower()
        # Detectar intención de separación o compra inmediata
        intencion_separar = any(p in lower_msg for p in [
            "quiero hacer el pedido"
        ])

        if intencion_separar:
            datos_cliente = recuperar_cliente_info(sender_number)
            nombre_usuario = datos_cliente[0] if datos_cliente and datos_cliente[0] else "💖"

            mensaje = (
                f"Gracias por tu interés {nombre_usuario} 🥰.\n\n"
                "🛍️ Hemos recibido tu solicitud para separar o comprar esta prenda. "
                "En unos instantes una asesora se pondrá en contacto contigo directamente por aquí 💬.\n\n"
                "Mientras tanto, puedes seguir viendo nuestro catálogo completo aquí:\n"
                "👉 https://t.me/dulcedguadalupecali"
            )

            # Guardar en historial
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", mensaje)

            try:
                conn = psycopg2.connect(
                    host=os.getenv("PG_HOST"),
                    dbname=os.getenv("PG_DB"),
                    user=os.getenv("PG_USER"),
                    password=os.getenv("PG_PASSWORD"),
                    port=os.getenv("PG_PORT", "5432")
                )
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO alertas_pendientes (phone_number, nombre, mensaje, fecha, respondido)
                    VALUES (%s, %s, %s, NOW(), FALSE)
                """, (sender_number, nombre_usuario, user_msg))
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                print("[ERROR insertando alerta]", e)

            # 👇 ESTA ES LA LÍNEA QUE FALTABA
            bloquear_aurora_para(sender_number)

            twilio_response = MessagingResponse()
            twilio_response.message(mensaje)
            return str(twilio_response)

        # Si viene de Meta Ads
        if "quiero más información" in lower_msg or "quiero mas información" in lower_msg or "¡Hola! Quiero más información." in lower_msg: 
            if tipo_cliente is None:  # No hay tipo_cliente aún
                pregunta_tipo = (
                    f"{nombre_usuario}¡Hola! Soy Aurora la Asesora de Dulce Guadalupe 🌸 Qué alegría tenerte por aquí.\n\n"
                    "¿Estás interesad@ en nuestras prendas *al por mayor* o *al detal*?\n"
                    "Así podré mostrarte el catálogo ideal para ti y ayudarte en lo que necesites 🛍️✨"
                )
                insertar_mensaje(sender_number, "user", user_msg)
                insertar_mensaje(sender_number, "assistant", pregunta_tipo)
                twilio_response = MessagingResponse()
                twilio_response.message(pregunta_tipo)
                return str(twilio_response)
            
        if lower_msg in ["al por mayor", "mayor", "por mayor", "mayorista"]:
            actualizar_cliente(sender_number, tipo_cliente="mayorista")
            respuesta = (
                f"{nombre_usuario}¡Perfecto! Te comparto el catálogo exclusivo para compras al por mayor 🛒:\n"
                "👉 https://t.me/dulcedguadalupecali\n\n"
                "💖 Si estás pensando en emprender o ya tienes un negocio, esto es para ti:\n\n"
                "En Dulce Guadalupe queremos ayudarte a crecer con prendas hermosas, de calidad y a precios pensados para ti.\n\n"
                "📌 *¿Cómo funciona nuestro sistema mayorista?*\n"
                "👗 Compra mínima: *4 referencias surtidas* (pueden ser diferentes tallas y colores).\n"
                "⏳ Puedes separar hasta por *8 días*.\n"
                "🔁 Si haces compras frecuentes (dentro del mismo mes), ¡te mantenemos el *precio por mayor*! 🥰\n\n"
                "🎁 Además, si quieres estar entre los primeros en conocer nuestras *nuevas colecciones y promociones exclusivas*,\n"
                "únete a nuestro grupo privado de WhatsApp:\n"
                "👉 https://chat.whatsapp.com/E0LcsssYLpX4hRuh7cc1zX\n\n"
                "📲 *Cuéntame*, ¿pudiste abrir el catálogo sin problema? 😊"
            )

            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", respuesta)
            catalogo_enviado[sender_number] = True
            twilio_response = MessagingResponse()
            twilio_response.message(respuesta)
            return str(twilio_response)

        elif lower_msg in ["al detal", "detal", "comprar una", "comprar unidad"]:
            actualizar_cliente(sender_number, tipo_cliente="detal")
            respuesta = (
                f"{nombre_usuario}¡Listo! Para compras al detal te comparto nuestro canal de Telegram 📲:\n"
                "👉 https://t.me/dulcedguadalupecali\n\n"
                "Allí encontrarás todos los precios al detal y también los de mayorista.\n\n"
                "¿Pudiste abrir el catálogo sin problema? 💬"\n\n
                "¿Te gustaría que te ayude a hacer tu primer pedido? 🛍️ Estoy aquí para acompañarte. 💫"
            )
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", respuesta)
            catalogo_enviado[sender_number] = True
            twilio_response = MessagingResponse()
            twilio_response.message(respuesta)
            return str(twilio_response)
   


        #Prendas
        posibles_prendas = ["conjunto", "vestido", "body", "blusa", "falda"]
        posibles_tallas = ["xs", "s", "m", "l", "xl"]

        # Detección inteligente
        nombre_detectado = detectar_nombre(user_msg, sender_number)
        correo_detectado = detectar_correo(user_msg)
        ciudad_detectada = detectar_ciudad(user_msg)
        prenda_detectada = next((p for p in posibles_prendas if p in lower_msg), None)
        talla_detectada = next((t.upper() for t in posibles_tallas if f"talla {t}" in lower_msg or f"talla: {t}" in lower_msg), None)

        # Actualizar cliente si detectó algo
        if nombre_detectado and (nombre is None or nombre.strip() == ""):
            actualizar_cliente(sender_number, nombre_detectado, prenda_detectada, talla_detectada, correo_detectado, ciudad_detectada)
            esperando_nombre.pop(sender_number, None)
        elif prenda_detectada or talla_detectada or correo_detectado or ciudad_detectada:
            actualizar_cliente(sender_number, None, prenda_detectada, talla_detectada, correo_detectado, ciudad_detectada)

        
        # Detectar catalogos
        if any(p in lower_msg for p in ["catálogo", "catalogo", "ver ropa", "link", "link de ropa", "quiero ver", "catálogo por favor", "link del catálogo", "dónde está el catálogo", "muestrame", "que tienes", "quiero ver"]):
            if not catalogo_enviado.get(sender_number):
                catalogo_enviado[sender_number] = True  # marcar como enviado
                ai_response = (
                    f"{nombre_usuario} aquí te dejo el catálogo 📲 por nuestro canal de Telegram:\n"
                    "👉 https://t.me/dulcedguadalupecali\n\n"
                    "¿Pudiste abrirlo correctamente? 💬 Recuerda que necesitas tener *la app de Telegram* instalada en tu celular 📱."
                )
                insertar_mensaje(sender_number, "user", user_msg)
                insertar_mensaje(sender_number, "assistant", ai_response)
                twilio_response = MessagingResponse()
                twilio_response.message(ai_response)
                return str(twilio_response)

        if catalogo_enviado.get(sender_number) and any(p in lower_msg for p in ["no abre", "no pude", "no tengo telegram", "no me abre", "no se puede", "no carga", "no funcionó", "no funciona", "no me deja", "no funciono", "no me deja"]):
            ai_response = (
                f"No te preocupes {nombre_usuario} 💖. A veces el catálogo de Telegram no abre si no tienes la app instalada.\n\n"
                "Aquí te dejo un link alternativo que es más fácil de abrir desde el navegador:\n"
                "👉 https://dulceguadalupe-catalogo.ecometri.shop/573104238002/collections/conjuntos\n\n"
                "¡Espero que ahora sí puedas verlo sin problema! 🛍️✨"
            )
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", ai_response)
            twilio_response = MessagingResponse()
            twilio_response.message(ai_response)
            return str(twilio_response)



        match_ref = re.search(r'\b[A-Z]{2,4}\d{2,4}\b', user_msg.upper())

        if match_ref:
            ref_encontrada = match_ref.group().upper()
            ai_response = buscar_por_referencia(ref_encontrada, nombre_usuario)
            ai_response += "\n\n" + mensaje_hacer_pedido(nombre_usuario)
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
        
        elif any(p in lower_msg for p in ["recomiéndame", "que me recomiendas", "recomendar", "mostrarme", "mostrar ref", "recomiendame algo", "que me quedaria bien", "recomienda", "sugiere", "sugerencia", "qué me ofreces", "tienes algo bonito", "algo que me quede bien"]):
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
            actualizar_cliente(sender_number, tipo_cliente="mayorista")
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
        tipos_consultables = ["conjunto", "conjuntos", "blusa", "blusas", "body", "bodys", "pantalón", "pantalon", "short", "shorts", "falda", "faldas", "vestido", "vestidos", "ROPA INTERIOR", "interior", "ropa interior", "sudadera", "sudaderas", "pijama", "pijamas", "piyama", "piyamas", "pantaloneta", "pantalonetas", "jeans", "jean", "malla", "mallas", "licra", "licras", "leggins", "leggin", "legin", "legins", "falda short", "enterizo", "enterizos", "enterizo short", "chaqueta", "chaquetas", "chaleco", "chalecos", "camisa", "camisas", "camisetas", "camisera", "camiseras", "buzo", "buso", "buzos", "busos", "blusa jeans", "blusa ", "blusas", "blusa", "bikini", "bikinis"]
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

        Si el cliente pregunta cómo comprar al por mayor, cómo revender, o menciona que quiere vender ropa, explícale con emoción y claridad cómo funciona nuestro sistema de venta para mayoristas. Dile que pueden iniciar con mínimo 4 referencias surtidas, que pueden separar hasta por 8 días sin compromiso, y que si compran de forma recurrente en el mismo mes mantienen el precio al por mayor. Ofrécele el catálogo mayorista con este enlace explicale que es por este link:
        👉 https://t.me/dulcedguadalupecali

         Además, si el cliente te dice SOLO QUIERE COMPRAR AL DETAL, UNA UNIDAD, POCAS UNIDADES O ALGO DIFERENTE AL POR MAYOR enviale este siguiente LINK DE TELEGRAM, INDICALE QUE AHI TIENE QUE TENER DESCARGADA LA APLICACION DE TELEGRAM
        👉  https://t.me/dulcedguadalupecali
        Si el cliente dice que no tiene telegram, enviale el link de ecomtri; https://dulceguadalupe-catalogo.ecometri.shop/573104238002/collections/conjuntos , PERO SOLO SI TE DICE QUE NO TIENE TELEGRAM

        También Si el cliente dice que no tiene telegram o no pudo abrir el enlace , enviale el link de ecomtri; https://dulceguadalupe-catalogo.ecometri.shop/573104238002/collections/conjuntos 

        Y cuando mandes los links de los catalogos preguntar siempre de nuevo si los pudo abrir, si el de telegram no le abre mandar el link de ecometri https://dulceguadalupe-catalogo.ecometri.shop/573104238002/collections/conjuntos
                
        Además, invítalo a unirse a nuestro grupo privado de WhatsApp para conocer promociones y colecciones exclusivas:
        👉 https://chat.whatsapp.com/E0LcsssYLpX4hRuh7cc1zX

        Si te preguntan por el instagram dales el link e invitalos a ver todas las publicaciones y todo lo que publicamos:
        👉 https://www.instagram.com/dulceguadalupe_empresa?igsh=MTJqbzJpZWo3bHlyMg==

        También si cualquier persona te pregunta por tallas, diles que en su mayoría se maneja talla unica ok.

        Siempre que el cliente te escriba, ofrecele un gran servicio al cliente, siempre responde con otra pregunta abierta, si le gustan las prendas, si hay algo mas en lo que le puede aydar y siempre serivicial.

        Cuando le mandes la información al cliente sobre mayorista y demas, al final le preguntarás si quiere que le ayudes ha realizar su pedido, entonces mandale SIEMPRE el catalogo del telegram:  https://t.me/dulcedguadalupecali
        Luego le dirás que revise el catalogo, escoja las preguntas y que cuando esté listo que te escriba la siguiente frase: "*quiero hacer el pedido*" y dire que será remitido al area de ventas, que en pocos instantes una asesora de ventas se comunicará directamente por esta misma conversacion.
        Luego agradece su conversacion y que estaras encatanda de volverle a atender en el futuro, agradece su preferencia por Dulce Guadalupe.

        Recuerda siempre preguntarle siempre si quiere algo mas, trata de enviar siempre el catalogo de telegram, pregunta siempre si puede abrir, si no, ya sabes reconocer y que mande el otro catalogo de ecometri, ya sabes.
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
                ai_response += "\n\n💡 ¿Podrías decirme tu *nombre* y desde qué *ciudad* nos escribes para darte una atención más personalizada? 🫶"




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
