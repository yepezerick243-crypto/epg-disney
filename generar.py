import re
import time
import requests
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from pathlib import Path


# =====================================================
# CONFIGURACIÓN
# =====================================================

BASE = "https://mi.tv"
PAIS = "mx"
CANAL_URI = "disney"
CANAL = "DCLA.mx"
NOMBRE = "Disney Channel"

LOGO = "https://upload.wikimedia.org/wikipedia/commons/3/38/Disney_channel_2019.png"

ZONA = ZoneInfo("America/Guayaquil")
OFFSET = -300

ARCHIVO = Path("disney.xml")
MAX_DIAS = 6

SESION = requests.Session()
SESION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://mi.tv/mx/canales/disney",
})


# =====================================================
# DESCARGA Y LECTURA DE MI.TV
# =====================================================

def obtener_html(fecha):
    url = (
        f"{BASE}/{PAIS}/async/channel/{CANAL_URI}/"
        f"{fecha.strftime('%Y-%m-%d')}/{OFFSET}"
    )

    for intento in range(4):
        try:
            respuesta = SESION.get(url, timeout=40)

            # Un 404 significa que mi.tv todavía no ofrece esa fecha.
            # No es necesario repetir cuatro veces la misma consulta.
            if respuesta.status_code == 404:
                print(f"Fecha no disponible en mi.tv: {fecha}")
                return None

            respuesta.raise_for_status()

            if not respuesta.text.strip():
                raise RuntimeError("mi.tv devolvió una respuesta vacía.")

            return respuesta.text

        except Exception as error:
            print(f"Error de conexión ({intento + 1}/4): {error}")

            if intento < 3:
                time.sleep(3)

    return None


def limpiar(valor):
    if not valor:
        return ""

    return re.sub(r"\s+", " ", str(valor)).strip()


def extraer_programas(html, fecha):
    soup = BeautifulSoup(html, "html.parser")
    lista = soup.select_one("ul.broadcasts")

    if lista is None:
        elementos = soup.find_all("li")
    else:
        elementos = lista.find_all("li", recursive=False)

    programas = []
    ultimo_inicio = None
    dia_actual = fecha

    for item in elementos:
        clases = item.get("class", [])

        if "native" in clases:
            continue

        hora_elemento = item.select_one(".time")
        titulo_elemento = item.select_one("h2")

        if hora_elemento is None or titulo_elemento is None:
            continue

        hora_texto = limpiar(
            hora_elemento.get_text(" ", strip=True)
        )

        coincidencia = re.search(
            r"\b(\d{1,2}):(\d{2})\b",
            hora_texto
        )

        if not coincidencia:
            continue

        hora = int(coincidencia.group(1))
        minuto = int(coincidencia.group(2))

        if hora > 23 or minuto > 59:
            continue

        if ultimo_inicio is not None:
            hora_anterior = (
                ultimo_inicio.hour * 60
                + ultimo_inicio.minute
            )

            hora_nueva = hora * 60 + minuto

            if hora_nueva < hora_anterior:
                dia_actual += timedelta(days=1)

        inicio = datetime.combine(
            dia_actual,
            dt_time(hour=hora, minute=minuto),
            tzinfo=ZONA,
        )

        ultimo_inicio = inicio

        titulo = limpiar(
            titulo_elemento.get_text(" ", strip=True)
        )

        subtitulo_elemento = item.select_one(".sub-title")
        sinopsis_elemento = item.select_one(".synopsis")
        enlace = item.select_one("a[href]")

        subtitulo = (
            limpiar(subtitulo_elemento.get_text(" ", strip=True))
            if subtitulo_elemento else ""
        )

        sinopsis = (
            limpiar(sinopsis_elemento.get_text(" ", strip=True))
            if sinopsis_elemento else ""
        )

        url_programa = enlace.get("href", "") if enlace else ""

        temporada = ""
        episodio = ""
        titulo_episodio = ""

        patron = re.search(
            r"temporada\s+(\d+)\s+episodio\s+(\d+)"
            r"(?:\s*[-–:]\s*(.*))?",
            subtitulo,
            flags=re.IGNORECASE,
        )

        if patron:
            temporada = patron.group(1)
            episodio = patron.group(2)
            titulo_episodio = limpiar(patron.group(3) or "")

        programas.append({
            "inicio": inicio,
            "titulo": titulo,
            "subtitulo": titulo_episodio or subtitulo,
            "sinopsis": sinopsis,
            "temporada": temporada,
            "episodio": episodio,
            "url": url_programa,
        })

    return programas


# =====================================================
# CONSTRUCCIÓN XMLTV
# =====================================================

def fecha_xml(instante):
    return instante.strftime("%Y%m%d%H%M%S %z")


def agregar_texto(padre, etiqueta, valor, idioma="es"):
    valor = limpiar(valor)

    if not valor:
        return None

    elemento = ET.SubElement(padre, etiqueta)
    elemento.text = valor

    if idioma:
        elemento.set("lang", idioma)

    return elemento


def actualizar_canal(root):
    canal = next(
        (
            elemento
            for elemento in root.findall("channel")
            if elemento.get("id") == CANAL
        ),
        None,
    )

    if canal is None:
        canal = ET.SubElement(root, "channel", {"id": CANAL})

    nombres = canal.findall("display-name")

    if nombres:
        nombres[0].text = NOMBRE

        for extra in nombres[1:]:
            canal.remove(extra)
    else:
        agregar_texto(canal, "display-name", NOMBRE, None)

    iconos = canal.findall("icon")

    if iconos:
        iconos[0].set("src", LOGO)

        for extra in iconos[1:]:
            canal.remove(extra)
    else:
        ET.SubElement(canal, "icon", {"src": LOGO})


def crear_nodo_programa(datos, final):
    inicio = datos["inicio"]

    if final <= inicio:
        return None

    nodo = ET.Element("programme", {
        "start": fecha_xml(inicio),
        "stop": fecha_xml(final),
        "channel": CANAL,
    })

    agregar_texto(nodo, "title", datos["titulo"])

    temporada = datos["temporada"]
    episodio = datos["episodio"]
    subtitulo = datos["subtitulo"]

    if temporada and episodio:
        identificacion = (
            f"Temporada {temporada} Episodio {episodio}"
        )

        if subtitulo:
            subtitulo = f"{identificacion} - {subtitulo}"
        else:
            subtitulo = identificacion

    agregar_texto(nodo, "sub-title", subtitulo)
    agregar_texto(nodo, "desc", datos["sinopsis"])

    if temporada or episodio:
        partes = []

        if temporada:
            partes.append(f"Temporada {temporada}")

        if episodio:
            partes.append(f"Episodio {episodio}")

        elemento_onscreen = ET.SubElement(
            nodo,
            "episode-num",
            {"system": "onscreen"},
        )

        elemento_onscreen.text = " ".join(partes)

        if temporada.isdigit() and episodio.isdigit():
            temporada_xml = int(temporada) - 1
            episodio_xml = int(episodio) - 1

            elemento_xmltv = ET.SubElement(
                nodo,
                "episode-num",
                {"system": "xmltv_ns"},
            )

            elemento_xmltv.text = (
                f"{temporada_xml}.{episodio_xml}."
            )

    return nodo


def cargar_xml():
    if ARCHIVO.exists():
        try:
            root = ET.parse(ARCHIVO).getroot()

            if root.tag == "tv":
                actualizar_canal(root)
                return root

        except ET.ParseError:
            print("El XML anterior está dañado. Se creará uno nuevo.")

    root = ET.Element("tv", {
        "generator-info-name": "EPG Disney mi.tv"
    })

    actualizar_canal(root)

    return root


# =====================================================
# FUNCIONES AUXILIARES
# =====================================================

def obtener_inicio_xml(nodo):
    try:
        inicio = datetime.strptime(
            nodo.get("start", ""),
            "%Y%m%d%H%M%S %z",
        )

        return inicio.astimezone(ZONA)

    except (ValueError, TypeError):
        return None


# =====================================================
# PROCESO PRINCIPAL
# =====================================================

def main():
    ahora = datetime.now(ZONA)
    fecha_inicio = ahora.date()

    medianoche = datetime.combine(
        fecha_inicio,
        dt_time.min,
        tzinfo=ZONA,
    )

    limite = medianoche + timedelta(days=MAX_DIAS + 1)

    # Los días que queremos actualizar.
    fechas_objetivo = [
        fecha_inicio + timedelta(days=desplazamiento)
        for desplazamiento in range(MAX_DIAS + 1)
    ]

    # El día adicional solo sirve para calcular el final
    # del último programa del período.
    fecha_adicional = fecha_inicio + timedelta(
        days=MAX_DIAS + 1
    )

    fechas_consulta = fechas_objetivo + [fecha_adicional]

    print("=" * 58)
    print("GENERADOR EPG DISNEY CHANNEL - MI.TV")
    print("=" * 58)
    print("Fecha local:", ahora.isoformat())
    print("Zona horaria: Ecuador (UTC-05:00)")
    print("Desde:", medianoche.isoformat())
    print("Hasta:", limite.isoformat())

    todos = []
    fechas_actualizables = set()
    fechas_vacias = []
    fechas_error = []

    # Consultar cada día de forma independiente.
    for fecha in fechas_consulta:
        fecha_texto = fecha.strftime("%Y-%m-%d")

        print(f"\nConsultando: {fecha_texto}")

        html = obtener_html(fecha)

        if html is None:
            fechas_error.append(fecha_texto)
            print("Fecha no disponible. Se conservarán sus datos anteriores.")
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")

            if (
                soup.select_one("ul.broadcasts") is None
                and not soup.find("li")
            ):
                raise RuntimeError(
                    "La respuesta no contiene la estructura de programación."
                )

            registros = extraer_programas(html, fecha)

            print("Programas encontrados:", len(registros))

            if registros:
                todos.extend(registros)

                # Solo se actualizan fechas que realmente
                # tienen programación recibida.
                if fecha in fechas_objetivo:
                    fechas_actualizables.add(fecha)

            else:
                fechas_vacias.append(fecha_texto)

                print(
                    "No hay programas publicados. "
                    "Se conservará la programación anterior de ese día."
                )

        except Exception as error:
            fechas_error.append(fecha_texto)
            print("Error al interpretar la programación:", error)

        time.sleep(0.4)

    # Si no se obtuvo programación de ningún día objetivo,
    # no se modifica el XML.
    if not fechas_actualizables:
        print("\n" + "=" * 58)
        print("ACTUALIZACIÓN CANCELADA")
        print("No se encontró programación nueva para actualizar.")
        print("El XML anterior se conserva sin modificaciones.")
        print("=" * 58)
        return

    # Eliminar duplicados por hora de inicio.
    unicos = {}

    for programa in todos:
        clave = programa["inicio"]

        if clave not in unicos:
            unicos[clave] = programa

    programas = sorted(
        unicos.values(),
        key=lambda p: p["inicio"],
    )

    # Cargar el XML anterior para conservar los días
    # que mi.tv todavía no ha publicado.
    root = cargar_xml()

    # Identificar los inicios de programas antiguos que
    # pertenecen a fechas que NO vamos a reemplazar.
    inicios_conservados = []

    for nodo in root.findall("programme"):
        if nodo.get("channel") != CANAL:
            continue

        inicio = obtener_inicio_xml(nodo)

        if inicio is None:
            continue

        if inicio.date() not in fechas_actualizables:
            inicios_conservados.append(inicio)

    # Preparar todos los inicios nuevos.
    inicios_nuevos = [
        programa["inicio"]
        for programa in programas
    ]

    nuevos_nodos = []
    sin_final = 0

    # Crear los programas nuevos.
    for programa in programas:
        inicio = programa["inicio"]

        if inicio < medianoche or inicio >= limite:
            continue

        # Buscar el siguiente programa nuevo.
        siguientes_nuevos = [
            valor
            for valor in inicios_nuevos
            if valor > inicio
        ]

        final_nuevo = (
            min(siguientes_nuevos)
            if siguientes_nuevos
            else None
        )

        # Si no existe otro programa nuevo, buscar el siguiente
        # programa antiguo de una fecha que se conservará.
        siguientes_conservados = [
            valor
            for valor in inicios_conservados
            if valor > inicio
        ]

        final_conservado = (
            min(siguientes_conservados)
            if siguientes_conservados
            else None
        )

        candidatos = [
            valor
            for valor in (final_nuevo, final_conservado)
            if valor is not None
        ]

        if not candidatos:
            sin_final += 1
            continue

        final = min(candidatos)

        nodo = crear_nodo_programa(programa, final)

        if nodo is not None:
            nuevos_nodos.append(nodo)

    if not nuevos_nodos:
        print("\nNo se generaron programas con horarios completos.")
        print("El XML anterior se conserva.")
        return

    # Eliminar únicamente los registros antiguos de DCLA.mx
    # correspondientes a días que sí tienen programación nueva.
    eliminados = 0

    for nodo in list(root.findall("programme")):
        if nodo.get("channel") != CANAL:
            continue

        inicio = obtener_inicio_xml(nodo)

        if inicio is None:
            continue

        if inicio.date() in fechas_actualizables:
            root.remove(nodo)
            eliminados += 1

    # Insertar los registros nuevos.
    for nodo in nuevos_nodos:
        root.append(nodo)

    # Actualizar el canal y conservar su logo.
    actualizar_canal(root)

    # Ordenar cronológicamente todos los programas.
    nodos = root.findall("programme")

    for nodo in nodos:
        root.remove(nodo)

    for nodo in sorted(
        nodos,
        key=lambda p: (
            p.get("start", ""),
            p.get("channel", ""),
        ),
    ):
        root.append(nodo)

    ET.indent(root, space="    ")

    # Guardar mediante archivo temporal.
    temporal = ARCHIVO.with_suffix(
        ARCHIVO.suffix + ".tmp"
    )

    ET.ElementTree(root).write(
        temporal,
        encoding="utf-8",
        xml_declaration=True,
    )

    temporal.replace(ARCHIVO)

    # Resumen.
    print("\n" + "=" * 58)
    print("EXTRACCIÓN TERMINADA")
    print("Fechas con programación nueva:", len(fechas_actualizables))

    for fecha in sorted(fechas_actualizables):
        print("  Actualizada:", fecha)

    print("Fechas sin programación:", len(fechas_vacias))

    for fecha in fechas_vacias:
        print("  Sin datos:", fecha)

    print("Fechas con errores:", len(fechas_error))

    for fecha in fechas_error:
        print("  Error:", fecha)

    print("Programas nuevos:", len(nuevos_nodos))
    print("Registros antiguos reemplazados:", eliminados)
    print("Programas sin hora final disponible:", sin_final)
    print("Programas acumulados:", len(root.findall("programme")))
    print("Logo del canal:", LOGO)
    print("ID del canal:", CANAL)
    print("Archivo generado:", ARCHIVO)
    print("=" * 58)


if __name__ == "__main__":
    main()
