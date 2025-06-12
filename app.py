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


# Descargar imagen a disco temporal
def descargar_imagen_twilio(media_url):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    response = requests.get(media_url, auth=(account_sid, auth_token))
    ruta = "/tmp/temp_img.jpg"
    with open(ruta, "wb") as f:
        f.write(response.content)
    return ruta

# Ver ref por links
def extraer_ref_desde_link_catalogo(texto):
    patron_url = r"https:\/\/dulceguadalupe-catalogo\.ecometri\.shop\/\d+\/([a-zA-Z0-9\-]+)"
    match = re.search(patron_url, texto)
    if match:
        ref_completa = match.group(1)  # jg567-cb5a7b
        ref = ref_completa.split('-')[0]  # jg567
        return ref.upper()
    return None

estado_pedidos = {}

# FLUJO DE SEPARADOS
# Nª1 DATOS CLIENTE
def insertar_o_actualizar_cliente(c):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()

    # Comprobar si existe
    cur.execute("SELECT cedula FROM clientes WHERE cedula = %s", (c["cedula"],))
    existe = cur.fetchone()

    if existe:
        cur.execute("""
            UPDATE clientes
            SET nombre = %s, telefono = %s, email = %s, departamento = %s, ciudad = %s, direccion = %s
            WHERE cedula = %s
        """, (c["nombre"], c["telefono"], c["correo"], c["departamento"], c["ciudad"], c["direccion"], c["cedula"]))
    else:
        cur.execute("""
            INSERT INTO clientes (cedula, nombre, telefono, email, departamento, ciudad, direccion)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (c["cedula"], c["nombre"], c["telefono"], c["correo"], c["departamento"], c["ciudad"], c["direccion"]))

    conn.commit()
    cur.close()
    conn.close()

#Nª2 INSERTAR PEDIDOS
def insertar_pedido_y_detalle(cedula, prendas, envio, observaciones, medio_conocimiento):
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()

    from datetime import datetime, timedelta
    fecha_pedido = datetime.now()
    fecha_limite = fecha_pedido + timedelta(days=8)

    total_pedido = sum(p["precio"] for p in prendas)

    cur.execute("""
        INSERT INTO pedidos (
            cliente_cedula, fecha_pedido, total_pedido, asesor, envio,
            fecha_limite, estado, medio_conocimiento, pedido_separado, observaciones
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'activo', %s, TRUE, %s)
        RETURNING id_pedido
    """, (cedula, fecha_pedido, total_pedido, "3104238002", envio, fecha_limite, medio_conocimiento, observaciones))

    id_pedido = cur.fetchone()[0]

    for prenda in prendas:
        cur.execute("""
            INSERT INTO detalle_pedido (id_pedido, referencia, cantidad, precio_unitario)
            VALUES (%s, %s, %s, %s)
        """, (id_pedido, prenda["ref"], prenda["cantidad"], prenda["precio"]))

    conn.commit()
    cur.close()
    conn.close()


# 🔹 Ruta webhook para Twilio
@app.route("/webhook", methods=["POST"])
def webhook():
    user_msg = (request.form.get("Body") or "").strip()
    lower_msg = user_msg.lower()
    sender_number = request.form.get("From")
    num_medias = int(request.form.get("NumMedia", "0"))

    respuestas = []
    ai_response = ""
    

    # 🛒 ACTIVAR FLUJO DE PEDIDO
    if any(palabra in lower_msg for palabra in ["separar", "separado", "quiero hacer un pedido", "quiero apartar", "quiero comprar", "quiero separar", "puedo separar", "deseo hacer pedido", "quiero pedir"]):
        estado_pedidos[sender_number] = {
            "fase": "esperando_datos",
            "datos_cliente": {},
            "prendas": [],
            "observaciones": "",
            "medio_conocimiento": "",
            "tipo_cliente": "",
        }
        return str(MessagingResponse().message(
            "📝 ¡Perfecto! Vamos a registrar tu pedido.\n\nPor favor, envíame los siguientes datos en este formato:\n\n"
            "*Nombre:* ...\n*Cédula:* ...\n*Teléfono:* ...\n*Correo:* ...\n*Departamento:* ...\n*Ciudad:* ...\n*Dirección:* ...\n\n"
            "Puedes enviarlos todos juntos o por partes. 🫶"
        ))

    # 🔄 CONTINUACIÓN DEL FLUJO
    if sender_number in estado_pedidos:
        estado = estado_pedidos[sender_number]
        fase = estado["fase"]
        datos_cliente = estado["datos_cliente"]
        prendas = estado["prendas"]

        if fase == "esperando_datos":
            partes = user_msg.strip().split('\n')
            for parte in partes:
                texto = parte.lower().strip()

                if "cedula" in texto or (texto.isdigit() and 8 <= len(texto) <= 11):
                    datos_cliente["cedula"] = ''.join(filter(str.isdigit, parte))
                elif "nombre" in texto:
                    datos_cliente["nombre"] = parte.split(":")[-1].strip()
                elif "telefono" in texto or ("tel" in texto and any(c.isdigit() for c in texto)):
                    datos_cliente["telefono"] = ''.join(filter(str.isdigit, parte))
                elif "correo" in texto or "email" in texto or "@" in texto:
                    datos_cliente["correo"] = detectar_correo(parte)
                elif "departamento" in texto:
                    datos_cliente["departamento"] = parte.split(":")[-1].strip()
                elif "ciudad" in texto:
                    datos_cliente["ciudad"] = parte.split(":")[-1].strip()
                elif "direccion" in texto:
                    datos_cliente["direccion"] = parte.split(":")[-1].strip()

            faltantes = [k for k in ["cedula", "nombre", "telefono", "correo", "departamento", "ciudad", "direccion"] if k not in datos_cliente]
            if faltantes:
                return str(MessagingResponse().message(f"⚠️ Aún faltan los siguientes datos: {', '.join(faltantes)}. Por favor escríbelos para continuar."))
            else:
                try:
                    insertar_o_actualizar_cliente(datos_cliente)
                    estado["fase"] = "esperando_tipo_cliente"
                    return str(MessagingResponse().message(
                        "🤔 Antes de continuar, ¿compras al *detal* o como *mayorista*?\n\n"
                        "🔸 *IMPORTANTE*: Esta información será verificada antes de confirmar el pedido."
                    ))
                except Exception as e:
                    return str(MessagingResponse().message("❌ Hubo un problema registrando tus datos. Intenta nuevamente."))


        elif fase == "esperando_tipo_cliente":
            tipo = lower_msg.strip()
            if "mayor" in tipo:
                estado["tipo_cliente"] = "mayorista"
            elif "detal" in tipo:
                estado["tipo_cliente"] = "detal"
            else:
                return str(MessagingResponse().message(
                    "❌ No entendí tu respuesta. Por favor indica si eres *mayorista* o compras al *detal*."
                ))
            estado["fase"] = "esperando_prendas"
            return str(MessagingResponse().message(
                "✅ ¡Perfecto! Ahora dime las referencias y cantidades a separar. Por ejemplo:\n*JG456 x2*\n*RR789 x1*"
            ))

        elif fase == "esperando_prendas":
            lineas = user_msg.strip().split('\n')
            nuevas_prendas = []
            for linea in lineas:
                match = re.match(r"([A-Z0-9\-]{3,})\s*[xX]\s*(\d+)", linea.strip(), re.IGNORECASE)
                if match:
                    ref, cantidad = match.groups()
                    cantidad = int(cantidad)

                    # Obtener precios desde BD según ref
                    conn = psycopg2.connect(
                        host=os.getenv("PG_HOST"),
                        dbname=os.getenv("PG_DB"),
                        user=os.getenv("PG_USER"),
                        password=os.getenv("PG_PASSWORD"),
                        port=os.getenv("PG_PORT", "5432")
                    )
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT precio_al_detal, precio_por_mayor FROM inventario
                        WHERE ref = %s
                        LIMIT 1
                    """, (ref.upper(),))
                    resultado = cur.fetchone()
                    cur.close()
                    conn.close()

                    if resultado:
                        precio = resultado[1] if estado["tipo_cliente"] == "mayorista" else resultado[0]
                        nuevas_prendas.append({"ref": ref.upper(), "cantidad": cantidad, "precio": precio})

            if not nuevas_prendas:
                return str(MessagingResponse().message("❌ No detecté prendas válidas. Usa el formato *REF x CANTIDAD*"))

            estado["prendas"].extend(nuevas_prendas)
            estado["fase"] = "esperando_envio"
            return str(MessagingResponse().message(
                "📝 ¿Tienes alguna observación especial para este pedido?\n\nY dime si será *recojo en local* o *envío a domicilio*."
            ))

        elif fase == "esperando_envio":
            estado["observaciones"] = user_msg
            estado["fase"] = "esperando_medio_conocimiento"
            return str(MessagingResponse().message(
                "📣 ¿Cómo nos conociste? Elige una opción:\n\n"
                "- Pauta Publicitaria\n- Redes Sociales\n- Cliente Frecuente\n- Punto Físico\n- Boca a Boca\n- Email Marketing\n- Eventos o Ferias\n- Promociones en Línea\n- Otros"
            ))

        elif fase == "esperando_medio_conocimiento":
            opciones_validas = [
                "Pauta Publicitaria", "Redes Sociales", "Cliente Frecuente", "Punto Físico",
                "Boca a Boca", "Email Marketing", "Eventos o Ferias", "Promociones en Línea", "Otros"
            ]
            medio = user_msg.strip()
            if medio not in opciones_validas:
                return str(MessagingResponse().message("❌ Esa opción no es válida. Responde con una de las opciones mencionadas."))
            estado["medio_conocimiento"] = medio
            estado["fase"] = "confirmacion_final"

            resumen = "\n".join([f"- *{p['ref']}* x{p['cantidad']}" for p in prendas])
            return str(MessagingResponse().message(
                f"✅ ¡Perfecto! Aquí tienes el resumen del pedido:\n\n"
                f"👤 Cliente: *{datos_cliente['nombre']}*\n🧾 Cédula: *{datos_cliente['cedula']}*\n📦 Prendas:\n{resumen}\n\n"
                f"📍 Observaciones: {estado['observaciones']}\n📣 Medio: {medio}\n\n"
                f"¿Deseas confirmar el pedido? Responde *sí* para proceder o *no* para cancelar."
            ))

        elif fase == "confirmacion_final":
            if "sí" in lower_msg:
                insertar_pedido_y_detalle(
                    datos_cliente["cedula"],
                    prendas,
                    envio=estado["observaciones"],
                    observaciones=estado["observaciones"],
                    medio_conocimiento=estado["medio_conocimiento"]
                )
                del estado_pedidos[sender_number]
                return str(MessagingResponse().message("🎉 ¡Tu pedido ha sido registrado exitosamente! Muchas gracias por comprar en Dulce Guadalupe. Te avisaremos cualquier novedad. 💖"))
            else:
                del estado_pedidos[sender_number]
                return str(MessagingResponse().message("🛑 Pedido cancelado. Si deseas volver a intentarlo, solo escríbeme. Estoy aquí para ayudarte 💬"))

        # Fallback si fase no se reconoce
        return str(MessagingResponse().message("⚠️ Estoy procesando tu pedido. Si algo sale mal, escribe *cancelar pedido* para reiniciar."))



    # 🔸 Nuevo manejo para contenido multimedia
    if num_medias > 0:
        datos_cliente = recuperar_cliente_info(sender_number)
        nombre_usuario = f"{datos_cliente[0]}," if datos_cliente and datos_cliente[0] else ""

        twilio_response = MessagingResponse()
        twilio_response.message(
            f"Hola {nombre_usuario} 💖 Para ayudarte con el producto correcto, por favor compárteme el *enlace del catálogo oficial*.\n\n"
            "✅ Ingresa al catálogo:\nhttps://dulceguadalupe-catalogo.ecometri.shop\n"
            "📦 Selecciona la prenda que deseas\n🔗 Presiona *Compartir* y envíame ese link aquí.\n\n"
            "Así podré ver exactamente la referencia y ayudarte a separar o verificar disponibilidad 🛍️✨"
        )
        return str(twilio_response)

    try:
        historial = recuperar_historial(sender_number, limite=15)
        primera_vez = len(historial) == 0

        # 🔍 Verificar si están preguntando por una referencia
        mensaje_limpio = re.sub(r'[^\w\s]', '', lower_msg)
        match_ref = re.search(r'\b[a-z]{2}\d{2,4}\b', mensaje_limpio)
        ref_link_detectada = extraer_ref_desde_link_catalogo(user_msg)


        #Prendas
        posibles_prendas = ["conjunto", "vestido", "body", "blusa", "falda"]
        posibles_tallas = ["xs", "s", "m", "l", "xl"]

        # Detección inteligente
        nombre_detectado = detectar_nombre(user_msg) if esperando_nombre.get(sender_number) else None
        correo_detectado = detectar_correo(user_msg)
        prenda_detectada = next((p for p in posibles_prendas if p in lower_msg), None)
        talla_detectada = next((t.upper() for t in posibles_tallas if f"talla {t}" in lower_msg or f"talla: {t}" in lower_msg), None)

        # Recuperar info previa
        datos_cliente = recuperar_cliente_info(sender_number)
        nombre, prenda, talla = datos_cliente if datos_cliente else (None, None, None)

        nombre_usuario = f"{nombre}," if nombre else ""
        
        # WEBHOOK PEDIDOS
        
        # Actualizar cliente si detectó algo
        if nombre_detectado:
            actualizar_cliente(sender_number, nombre_detectado, prenda_detectada, talla_detectada, correo_detectado)
            esperando_nombre.pop(sender_number, None)
        elif prenda_detectada or talla_detectada or correo_detectado:
            actualizar_cliente(sender_number, None, prenda_detectada, talla_detectada, correo_detectado)



        if match_ref or ref_link_detectada:
            ref_encontrada = match_ref.group().upper() if match_ref else ref_link_detectada
            ultima_referencia[sender_number] = ref_encontrada  # 👉 Guardamos la última ref consultada
            ai_response = buscar_por_referencia(ref_encontrada, nombre_usuario)
            
            # Insertar mensajes al historial
            insertar_mensaje(sender_number, "user", user_msg)
            insertar_mensaje(sender_number, "assistant", ai_response)

            # Consultar precio real desde BD
            conn = psycopg2.connect(
                host=os.getenv("PG_HOST"),
                dbname=os.getenv("PG_DB"),
                user=os.getenv("PG_USER"),
                password=os.getenv("PG_PASSWORD"),
                port=os.getenv("PG_PORT", "5432")
            )
            cur = conn.cursor()
            cur.execute("""
                SELECT precio_al_detal, precio_por_mayor FROM inventario
                WHERE ref = %s
                LIMIT 1
            """, (ref_encontrada.upper(),))
            resultado = cur.fetchone()
            cur.close()
            conn.close()

            if resultado:
                precio = resultado[0]  # Por ahora, al detal. Se ajustará luego según si es mayorista.
            else:
                precio = 0  # Fallback si no encontró nada
            
            # 👉 AÑADIR DETECCIÓN DE INTENCIÓN DE SEPARAR
            # 👉 INTENCIÓN DE SEPARAR
            if any(palabra in lower_msg for palabra in ["separarla", "separar esta", "quiero separarla", "quiero pedirla", "quiero comprarla", "quiero apartarla", "quiero esta"]):
                ref_guardada = ultima_referencia.get(sender_number)
                if ref_guardada:
                    # Consultar precio de la última ref guardada
                    conn = psycopg2.connect(
                        host=os.getenv("PG_HOST"),
                        dbname=os.getenv("PG_DB"),
                        user=os.getenv("PG_USER"),
                        password=os.getenv("PG_PASSWORD"),
                        port=os.getenv("PG_PORT", "5432")
                    )
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT precio_al_detal, precio_por_mayor FROM inventario
                        WHERE ref = %s
                        LIMIT 1
                    """, (ref_guardada,))
                    resultado = cur.fetchone()
                    cur.close()
                    conn.close()

                    if resultado:
                    precio = resultado[0]
                    estado_pedidos[sender_number] = {
                        "fase": "esperando_datos",
                        "datos_cliente": {},
                        "prendas": [{"ref": ref_guardada, "cantidad": 1, "precio": precio}],
                        "observaciones": "",
                        "medio_conocimiento": "",
                    }
                    del ultima_referencia[sender_number]  # ✅ limpiamos la referencia usada
                    return str(MessagingResponse().message(
                        "📝 ¡Genial! Vamos a registrar tu pedido con esta prenda.\n\nPor favor, envíame los siguientes datos en este formato:\n\n"
                        "*Nombre:* ...\n*Cédula:* ...\n*Teléfono:* ...\n*Correo:* ...\n*Departamento:* ...\n*Ciudad:* ...\n*Dirección:* ...\n\n"
                        "Puedes enviarlos todos juntos o por partes. 🫶"
                    ))

                else:
                    return str(MessagingResponse().message("⚠️ Para ayudarte a separar una prenda necesito que primero me indiques la referencia. Envíame el enlace del catálogo o dime el código de la prenda."))


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

        elif any(p in lower_msg for p in ["mayorista", "como puedo comprar al por mayor", "comprar por mayor", "ventas por mayor", "quiero ser mayorista", "mayorista", "por mayor", "revender", "quiero vender", "precio mayor", "quiero comprar varias"]):
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
            historial.append({
                "role": "assistant",
                "content": (
                    f"¡Hola {nombre}! 😊 Soy Aurora, la asistente virtual de Dulce Guadalupe. "
                    "Estoy aquí para ayudarte con nuestros productos, separados y más. "
                    "¿Quieres que te muestre algo de nuestros conjuntos más 🔥 o te ayudo con alguna duda? 💖"
                ) if nombre else (
                    "¡Hola! 😊 Soy Aurora, la asistente virtual de Dulce Guadalupe. "
                    "Estoy aquí para ayudarte con nuestros productos, separados y más. "
                    "¿Quieres que te muestre algo de nuestros conjuntos más 🔥 o te ayudo con alguna duda? 💖"
                )
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

        Si el cliente pregunta cómo comprar al por mayor, cómo revender, o menciona que quiere vender ropa, explícale con emoción y claridad cómo funciona nuestro sistema de venta para mayoristas. Dile que pueden iniciar con mínimo 4 referencias surtidas, que pueden separar hasta por 8 días sin compromiso, y que si compran de forma recurrente en el mismo mes mantienen el precio al por mayor. Ofrécele el catálogo mayorista con este enlace:
        👉 https://dulceguadalupe-catalogo.ecometri.shop/573104238002/collections/conjuntos

        Además, invítalo a unirse a nuestro grupo privado de WhatsApp para conocer promociones y colecciones exclusivas:
        👉 https://chat.whatsapp.com/E0LcsssYLpX4hRuh7cc1zX
        """

        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": system_prompt}] + historial,
            max_tokens=200
        )


        ai_response = completion.choices[0].message["content"]

        # Si no tenemos nombre guardado ni fue detectado
        if not nombre and not nombre_detectado and "tu nombre" not in user_msg.lower():
            ai_response += "\n\n💡 ¿Me podrías decir tu nombre para darte una mejor atención? 🫶"
            
        esperando_nombre[sender_number] = True



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
