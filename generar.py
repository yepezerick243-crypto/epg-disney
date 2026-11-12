
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

        hora_texto = limpiar(hora_elemento.get_text(" ", strip=True))
        coincidencia = re.search(r"\b(\d{1,2}):(\d{2})\b", hora_texto)

        if not coincidencia:
            continue

        hora = int(coincidencia.group(1))
        minuto = int(coincidencia.group(2))

        if hora > 23 or minuto > 59:
            continue

        if ultimo_inicio is not None:
            hora_anterior = ultimo_inicio.hour * 60 + ultimo_inicio.minute
            hora_nueva = hora * 60 + minuto

            if hora_nueva < hora_anterior:
                dia_actual += timedelta(days=1)

        inicio = datetime.combine(
            dia_actual,
            dt_time(hour=hora, minute=minuto),
            tzinfo=ZONA,
        )
        ultimo_inicio = inicio

        titulo = limpiar(titulo_elemento.get_text(" ", strip=True))
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
            elemento for elemento in root.findall("channel")
            if elemento.get("id") == CANAL
        ),
        None,
    )

    if canal is None:
        canal = ET.SubElement(root, "channel", {"id": CANAL})

    # Actualizar nombre
    nombres = canal.findall("display-name")

    if nombres:
        nombres[0].text = NOMBRE
        for extra in nombres[1:]:
            canal.remove(extra)
    else:
        agregar_texto(canal, "display-name", NOMBRE, None)

    # Actualizar logo
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
    agregar_texto(nodo, "sub-title", datos["subtitulo"])
    agregar_texto(nodo, "desc", datos["sinopsis"])

    if datos["temporada"] or datos["episodio"]:
        partes = []

        if datos["temporada"]:
            partes.append(f"Temporada {datos['temporada']}")

        if datos["episodio"]:
            partes.append(f"Episodio {datos['episodio']}")

        agregar_texto(
            nodo,
            "episode-num",
            " ".join(partes),
            "onscreen",
        )

        if datos["temporada"].isdigit() and datos["episodio"].isdigit():
            temporada_xml = int(datos["temporada"]) - 1
            episodio_xml = int(datos["episodio"]) - 1

            agregar_texto(
                nodo,
                "episode-num",
                f"{temporada_xml}.{episodio_xml}.",
                "xmltv_ns",
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

    print("=" * 58)
    print("GENERADOR EPG DISNEY CHANNEL - MI.TV")
    print("=" * 58)
    print("Fecha local:", ahora.isoformat())
    print("Zona horaria: Ecuador (UTC-05:00)")
    print("Desde:", medianoche.isoformat())
    print("Hasta:", limite.isoformat())

    todos = []
    fechas_consultadas = 0

    for desplazamiento in range(MAX_DIAS + 2):
        fecha = fecha_inicio + timedelta(days=desplazamiento)
        fecha_texto = fecha.strftime("%Y-%m-%d")

        print(f"\nConsultando: {fecha_texto}")

        html = obtener_html(fecha)

        if html is None:
            print("No se pudo obtener esta fecha.")
            continue

        registros = extraer_programas(html, fecha)
        print("Programas encontrados:", len(registros))

        if registros:
            todos.extend(registros)
            fechas_consultadas += 1

        time.sleep(0.4)

    unicos = {}

    for programa in todos:
        clave = programa["inicio"]

        if clave not in unicos:
            unicos[clave] = programa

    programas = sorted(
        unicos.values(),
        key=lambda p: p["inicio"],
    )

    root = cargar_xml()
    actualizar_canal(root)

    existentes = {
        (p.get("channel"), p.get("start"))
        for p in root.findall("programme")
    }

    nuevos = 0
    sin_final = 0

    for indice, programa in enumerate(programas):
        inicio = programa["inicio"]

        if inicio < medianoche or inicio >= limite:
            continue

        if indice + 1 >= len(programas):
            sin_final += 1
            continue

        final = programas[indice + 1]["inicio"]

        if final <= inicio:
            sin_final += 1
            continue

        nodo = crear_nodo_programa(programa, final)

        if nodo is None:
            continue

        clave = (nodo.get("channel"), nodo.get("start"))

        if clave in existentes:
            continue

        root.append(nodo)
        existentes.add(clave)
        nuevos += 1

    nodos = root.findall("programme")

    for nodo in nodos:
        root.remove(nodo)

    for nodo in sorted(
        nodos,
        key=lambda p: (p.get("start", ""), p.get("channel", "")),
    ):
        root.append(nodo)

    ET.indent(root, space="    ")

    ET.ElementTree(root).write(
        ARCHIVO,
        encoding="utf-8",
        xml_declaration=True,
    )

    print("\n" + "=" * 58)
    print("EXTRACCIÓN TERMINADA")
    print("Fechas consultadas correctamente:", fechas_consultadas)
    print("Programas nuevos:", nuevos)
    print("Programas sin hora final disponible:", sin_final)
    print("Programas acumulados:", len(root.findall("programme")))
    print("Logo del canal:", LOGO)
    print("ID del canal:", CANAL)
    print("Archivo generado:", ARCHIVO)
    print("=" * 58)


if __name__ == "__main__":
    main()
