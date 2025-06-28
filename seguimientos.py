import os
from datetime import datetime, timedelta
import psycopg2
from twilio.rest import Client
from dotenv import load_dotenv
import time

load_dotenv()

# Conexión a la base de datos
def get_connection():
    return psycopg2.connect(
        host=os.getenv("PG_HOST"),
        dbname=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        port=os.getenv("PG_PORT", "5432")
    )

# Enviar mensaje y registrar en chat_history
def enviar_mensaje_y_registrar(phone, texto):
    client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    message = client.messages.create(
        from_=os.getenv("TWILIO_NUMBER"),
        to=phone,
        body=texto
    )

    # Registrar en chat_history
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO chat_history (phone_number, role, message, timestamp, quoted_sid)
        VALUES (%s, 'assistant', %s, NOW(), %s)
    """, (phone, texto, message.sid))
    conn.commit()
    cur.close()
    conn.close()

# Obtener nombre si está disponible
def obtener_nombre(cur, phone_number):
    cur.execute("SELECT nombre FROM clientes_ia WHERE phone_number = %s", (phone_number,))
    resultado = cur.fetchone()
    if resultado and resultado[0]:
        return resultado[0].split()[0].capitalize()
    return None

# Ejecutar revisión de seguimientos
def revisar_seguimientos():
    conn = get_connection()
    cur = conn.cursor()

    ahora = datetime.utcnow()
    hace_2h = ahora - timedelta(hours=2)
    hace_24h = ahora - timedelta(hours=24)

    # 2 horas
    cur.execute("""
        SELECT phone_number FROM seguimientos
        WHERE ultima_respuesta <= %s AND enviado_2h = FALSE
    """, (hace_2h,))
    for (phone,) in cur.fetchall():
        nombre = obtener_nombre(cur, phone)
        saludo = f"{nombre}, " if nombre else ""
        mensaje = f"🌸 {saludo}¿Pudiste ver el catálogo? Tenemos descuentos increíbles esperándote 🛍️✨"
        enviar_mensaje_y_registrar(phone, mensaje)
        cur.execute("UPDATE seguimientos SET enviado_2h = TRUE WHERE phone_number = %s", (phone,))

    # 24 horas
    cur.execute("""
        SELECT phone_number FROM seguimientos
        WHERE ultima_respuesta <= %s AND enviado_24h = FALSE
    """, (hace_24h,))
    for (phone,) in cur.fetchall():
        nombre = obtener_nombre(cur, phone)
        saludo = f"{nombre}, " if nombre else ""
        mensaje = f"💖 {saludo}¿Aún estás interesad@ en comprar? Hoy tenemos nuevos estilos que te encantarán 😍. ¡Escríbeme para ayudarte!"
        enviar_mensaje_y_registrar(phone, mensaje)
        cur.execute("UPDATE seguimientos SET enviado_24h = TRUE WHERE phone_number = %s", (phone,))

    conn.commit()
    cur.close()
    conn.close()

if __name__ == "__main__":
    while True:
        revisar_seguimientos()
        time.sleep(600)  # 600 segundos = 10 minutos