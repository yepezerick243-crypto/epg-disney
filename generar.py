
import json
import os
import re
import time
import requests
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from urllib.parse import quote
from pathlib import Path

# =====================================================
# CONFIGURACIÓN
# =====================================================

BASE = "https://mi.tv"
PAIS = "mx"
CANAL = "DisneyChannel.mx"
NOMBRE = "Disney Channel"
ZONA = ZoneInfo("America/Guayaquil")
OFFSET = -300

ARCHIVO = Path("disney.xml")
MAX_DIAS = 6

SESION = requests.Session()
SESION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://mi.tv/mx/canales/disney/ayer"
})


def obtener_json(url):
    for intento in range(4):
        try:
            respuesta = SESION.get(url, timeout=30)
            respuesta.raise_for_status()
            return respuesta.json()
        except Exception as error:
            print(f"Error de conexión ({intento + 1}/4): {error}")
            time.sleep(3)
    return None


def recorrer(obj):
    if isinstance(obj, dict):
        yield obj
        for valor in obj.values():
            yield from recorrer(valor)
    elif isinstance(obj, list):
        for valor in obj:
            yield from recorrer(valor)


def obtener_canal():
    url = f"{BASE}/api/epg/channels/country/{PAIS}"
    datos = obtener_json(url)

    if not datos:
        raise RuntimeError("No se pudo consultar el listado de canales.")

    for elemento in recorrer(datos):
        nombre = str(
            elemento.get("name")
            or elemento.get("title")
            or elemento.get("displayName")
            or ""
        ).strip()

        if (
            elemento.get("channelId")
            and elemento.get("uri")
            and nombre.casefold() == "disney"
        ):
            return elemento["channelId"]

    # Algunas respuestas pueden identificar el canal como Disney Channel.
    for elemento in recorrer(datos):
        nombre = str(
            elemento.get("name")
            or elemento.get("title")
            or elemento.get("displayName")
            or ""
        ).strip()

        if (
            elemento.get("channelId")
            and elemento.get("uri")
            and nombre.casefold() == "disney channel"
        ):
            return elemento["channelId"]

    raise RuntimeError("No se encontró Disney en el catálogo de mi.tv.")


def obtener_programas(fecha, channel_id):
    url = (
        f"{BASE}/api/epg/guide/{fecha}"
        f"?channelId={quote(str(channel_id))}"
        f"&timeZoneOffset={OFFSET}"
    )

    datos = obtener_json(url)

    if datos is None:
        return []

    programas = []

    for elemento in recorrer(datos):
        if (
            "beginTimeMillis" in elemento
            and "endTimeMillis" in elemento
            and isinstance(elemento.get("program"), dict)
        ):
            programas.append(elemento)

    return programas


def obtener_detalle(uri):
    if not uri:
        return {}

    url = (
        f"{BASE}/api/epg/programs/country/{PAIS}/uri/"
        f"{quote(str(uri), safe='')}"
    )

    return obtener_json(url) or {}


def buscar_valor(datos, *claves):
    for elemento in recorrer(datos):
        for clave in claves:
            valor = elemento.get(clave)
            if valor not in (None, "", [], {}):
                return valor
    return None


def texto(valor):
    if valor is None:
        return ""
    if isinstance(valor, (dict, list)):
        return ""
    return str(valor).strip()


def limpiar(valor):
    return re.sub(r"\s+", " ", texto(valor)).strip()


def fecha_xml(milisegundos):
    instante = datetime.fromtimestamp(
        int(milisegundos) / 1000,
        tz=ZONA
    )
    return instante.strftime("%Y%m%d%H%M%S -0500")


def agregar_texto(padre, etiqueta, valor, idioma="es"):
    valor = limpiar(valor)
    if valor:
        elemento = ET.SubElement(padre, etiqueta)
        elemento.text = valor
        if idioma:
            elemento.set("lang", idioma)
        return elemento
    return None


def crear_programa(elemento, fecha, cache):
    programa = elemento.get("program", {})
    uri = programa.get("uri", "")

    if uri not in cache:
        cache[uri] = obtener_detalle(uri)
        time.sleep(0.15)

    detalle = cache[uri]

    inicio = int(elemento["beginTimeMillis"])
    final = int(elemento["endTimeMillis"])

    if final <= inicio:
        return None

    # Datos de programación y ficha del programa.
    combinado = {
        "programacion": programa,
        "detalle": detalle
    }

    tipo = limpiar(
        buscar_valor(combinado, "programType")
    ).upper()

    nombre_serie = limpiar(
        buscar_valor(combinado, "name")
    )

    titulo_programa = limpiar(
        buscar_valor(combinado, "title")
    )

    titulo_original = limpiar(
        buscar_valor(combinado, "originalTitle")
    )

    sinopsis = limpiar(
        buscar_valor(combinado, "synopsisLong", "synopsis", "description")
    )

    temporada = buscar_valor(
        combinado, "seasonNumber", "season"
    )

    episodio = buscar_valor(
        combinado, "episodeNumber"
    )

    if isinstance(temporada, dict):
        temporada = temporada.get("number")

    # Las series utilizan el nombre de la serie como título.
    if programa.get("seriesId") or tipo == "TV_EPISODE":
        titulo = nombre_serie or titulo_programa
        subtitulo = titulo_programa if titulo != titulo_programa else ""
    else:
        titulo = titulo_programa or nombre_serie
        subtitulo = ""

    if not titulo:
        return None

    # Algunos datos de episodio están dentro de objetos anidados.
    if not temporada:
        for item in recorrer(combinado):
            if isinstance(item.get("number"), (int, str)):
                if item is not programa and item is not elemento:
                    temporada = item["number"]
                    break

    if not episodio:
        episodio = buscar_valor(combinado, "episode")

    categoria = buscar_valor(
        combinado, "genre", "category"
    )

    if isinstance(categoria, list):
        categoria = " / ".join(map(texto, categoria))

    anio = buscar_valor(combinado, "year")

    nodo = ET.Element("programme", {
        "start": fecha_xml(inicio),
        "stop": fecha_xml(final),
        "channel": CANAL
    })

    agregar_texto(nodo, "title", titulo)
    agregar_texto(nodo, "sub-title", subtitulo)
    agregar_texto(nodo, "title", titulo_original, "original")
    agregar_texto(nodo, "desc", sinopsis)

    if categoria:
        agregar_texto(nodo, "category", categoria)

    if anio:
        agregar_texto(nodo, "date", anio, None)

    if temporada or episodio:
        numero = []
        if temporada:
            numero.append(f"{int(temporada):02d}" if str(temporada).isdigit() else str(temporada))
        if episodio:
            numero.append(f"{int(episodio):02d}" if str(episodio).isdigit() else str(episodio))

        agregar_texto(
            nodo,
            "episode-num",
            ".".join(numero),
            None
        )

    return nodo


def cargar_xml():
    if ARCHIVO.exists():
        try:
            return ET.parse(ARCHIVO).getroot()
        except ET.ParseError:
            print("XML anterior inválido. Se creará uno nuevo.")

    root = ET.Element("tv", {
        "generator-info-name": "EPG Disney mi.tv"
    })

    canal = ET.SubElement(root, "channel", {"id": CANAL})
    agregar_texto(canal, "display-name", NOMBRE, None)

    return root


def main():
    ahora = datetime.now(ZONA)
    medianoche = datetime.combine(
        ahora.date(),
        dt_time.min,
        tzinfo=ZONA
    )

    print("=" * 55)
    print("GENERADOR EPG DISNEY - MI.TV MÉXICO")
    print("=" * 55)
    print("Fecha local:", ahora.isoformat())
    print("Inicio:", medianoche.isoformat())

    channel_id = obtener_canal()
    print("ID del canal:", channel_id)

    root = cargar_xml()

    existentes = {
        (p.get("channel"), p.get("start"))
        for p in root.findall("programme")
    }

    cache = {}
    nuevos = 0
    consultados = 0

    for desplazamiento in range(MAX_DIAS + 1):
        fecha = (ahora.date() + timedelta(days=desplazamiento))
        fecha_texto = fecha.strftime("%Y-%m-%d")

        print(f"\nConsultando: {fecha_texto}")

        registros = obtener_programas(fecha_texto, channel_id)
        print("Programas recibidos:", len(registros))

        for registro in registros:
            inicio = int(registro["beginTimeMillis"])

            instante = datetime.fromtimestamp(
                inicio / 1000,
                tz=ZONA
            )

            if instante < medianoche:
                continue

            nodo = crear_programa(registro, fecha_texto, cache)

            if nodo is None:
                continue

            clave = (nodo.get("channel"), nodo.get("start"))

            if clave in existentes:
                continue

            root.append(nodo)
            existentes.add(clave)
            nuevos += 1

        consultados += 1

    # Orden cronológico.
    programas = root.findall("programme")
    for programa in programas:
        root.remove(programa)

    for programa in sorted(
        programas,
        key=lambda p: (p.get("start", ""), p.get("channel", ""))
    ):
        root.append(programa)

    ET.indent(root, space="    ")

    ET.ElementTree(root).write(
        ARCHIVO,
        encoding="utf-8",
        xml_declaration=True
    )

    print("\n" + "=" * 55)
    print("EXTRACCIÓN TERMINADA")
    print("Fechas consultadas:", consultados)
    print("Programas nuevos:", nuevos)
    print("Programas acumulados:", len(root.findall("programme")))
    print("Archivo:", ARCHIVO)
    print("=" * 55)


if __name__ == "__main__":
    main()
