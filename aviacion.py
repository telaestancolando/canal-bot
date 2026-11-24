"""
Canal de aviación: dos vídeos al día (03:00 y 15:00, hora de España).

Calendario semanal (config.json -> "calendario_aviacion"):
  lunes accidentes (serie de 2 partes: parte 1 a las 03:00, parte 2 a las 15:00),
  miércoles aerodinámica (avión a las 03:00, helicóptero a las 15:00), resto de días
  temas generales, aviones legendarios e historias de supervivencia.

Los temas están elegidos a mano en temas.json (calidad controlada). La información sale de
Wikipedia (español + inglés) y TODAS las cifras del guion se verifican contra ese texto.
Reutiliza de bot.py la voz, la verificación, los gráficos, el montaje y la publicación.

Modo prueba: python aviacion.py --prueba
"""
import datetime as dt
import json
import os
import sys
import time

import requests

import bot

CFG = bot.CFG
TEMAS = json.loads((bot.RAIZ / "temas.json").read_text(encoding="utf-8"))
ESTADO = bot.RAIZ / "estado_aviacion.json"
PRUEBA = "--prueba" in sys.argv
log = bot.log
UA = {"User-Agent": "canal-bot/1.0 (divulgacion aviacion; contacto en GitHub)"}


# ---------------------------------------------------------------- Estado (qué se ha publicado)
def leer_estado():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    return {"usados": [], "pendiente": None}


def guardar_estado(e):
    ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")


def turno():
    """'manana' para la ejecución de las 03:00, 'tarde' para la de las 15:00."""
    forzado = os.environ.get("TURNO", "").strip()
    if forzado in ("manana", "tarde"):
        return forzado
    return "manana" if dt.datetime.utcnow().hour < 9 else "tarde"


# ---------------------------------------------------------------- Wikipedia
def wikipedia(ref):
    """ref = 'es:Título' o 'en:Title'. Busca el artículo y devuelve (texto, url)."""
    idioma, consulta = ref.split(":", 1)
    api = f"https://{idioma}.wikipedia.org/w/api.php"
    r = requests.get(api, headers=UA, timeout=60, params={
        "action": "query", "list": "search", "srsearch": consulta, "srlimit": 1, "format": "json"})
    res = r.json().get("query", {}).get("search", [])
    if not res:
        return "", None
    titulo = res[0]["title"]
    r = requests.get(api, headers=UA, timeout=60, params={
        "action": "query", "prop": "extracts", "explaintext": 1, "titles": titulo,
        "format": "json", "redirects": 1})
    paginas = r.json()["query"]["pages"]
    texto = next(iter(paginas.values())).get("extract", "")
    # quitar secciones que no aportan
    for corte in ("\n== Véase también", "\n== Referencias", "\n== See also", "\n== References", "\n== Notes"):
        texto = texto.split(corte)[0]
    url = f"https://{idioma}.wikipedia.org/wiki/{titulo.replace(' ', '_')}"
    return texto, url


def reunir_fuentes(refs):
    textos, urls = [], []
    for ref in refs:
        try:
            t, u = wikipedia(ref)
        except Exception as ex:
            log("Wikipedia falló:", ref, ex)
            continue
        if t:
            textos.append(f"### FUENTE: {u}\n{t[:14000]}")
            urls.append(u)
    return "\n\n".join(textos), urls


# ---------------------------------------------------------------- Aprendizaje (qué funciona mejor)
def ig_base_token():
    token = os.environ.get("IG_TOKEN", "").strip()
    base = (f"https://graph.facebook.com/{CFG['version_api_instagram']}" if token.startswith("EAA")
            else f"https://graph.instagram.com/{CFG['version_api_instagram']}")
    return base, token


def actualizar_metricas(estado):
    """Lee likes y comentarios de los Reels publicados (de más de 36 h) y los guarda en el estado."""
    base, token = ig_base_token()
    if not token:
        return
    ahora = dt.datetime.utcnow()
    for p in estado.get("publicados", []):
        edad = (ahora - dt.datetime.fromisoformat(p["fecha"])).total_seconds() / 3600
        if edad < 36 or edad > 24 * 30:
            continue
        try:
            r = requests.get(f"{base}/{p['id']}", params={"fields": "like_count,comments_count",
                                                         "access_token": token}, timeout=30).json()
            if "like_count" in r or "comments_count" in r:
                # un comentario vale más que un like: indica más interés
                p["score"] = r.get("like_count", 0) + 3 * r.get("comments_count", 0)
        except Exception as ex:
            log("No se pudieron leer métricas:", ex)


def pesos_categorias(estado):
    """Media de engagement por categoría. Devuelve None si aún no hay datos suficientes."""
    sumas, cuentas = {}, {}
    for p in estado.get("publicados", []):
        if "score" in p:
            sumas[p["categoria"]] = sumas.get(p["categoria"], 0) + p["score"]
            cuentas[p["categoria"]] = cuentas.get(p["categoria"], 0) + 1
    con_datos = [c for c in cuentas if cuentas[c] >= 2]
    if len(con_datos) < 3:
        return None
    medias = {c: sumas[c] / cuentas[c] for c in con_datos}
    log("Engagement medio por categoría:", {c: round(m, 1) for c, m in medias.items()})
    return medias


def categoria_por_rendimiento(estado, por_defecto):
    """Más vídeos de lo que mejor funciona, pero siempre un poco de todo (al menos 8 % cada una)."""
    import random
    medias = pesos_categorias(estado)
    if not medias:
        return por_defecto
    cats = list(TEMAS.keys())
    media_global = sum(medias.values()) / len(medias)
    w = {c: medias.get(c, media_global) + 1 for c in cats}
    total = sum(w.values())
    probs = {c: max(w[c] / total, 0.08) for c in cats}
    rnd = random.Random(f"{dt.date.today()}-{turno()}")
    eleccion = rnd.choices(cats, weights=[probs[c] for c in cats])[0]
    log("Categoría elegida por rendimiento:", eleccion, {c: round(p, 2) for c, p in probs.items()})
    return eleccion


def ampliar_temas(estado, categoria):
    """Si una categoría se queda sin temas, se piden nuevos a Gemini y se comprueba que Wikipedia los tiene."""
    existentes = [t["titulo"] for t in TEMAS[categoria]]
    p = f"""Propón 6 temas NUEVOS para vídeos de un canal de aviones y helicópteros, categoría "{categoria}".
Que interesen al gran público de España y Latinoamérica y que tengan artículo en Wikipedia.
No repitas ninguno de estos: {existentes}
Devuelve JSON: {{"temas": [{{"id": "corto-sin-espacios", "titulo": "título atractivo",
"fuentes": ["es:Título exacto en Wikipedia en español", "en:Exact English Wikipedia title"]}}]}}"""
    try:
        nuevos = bot.gemini(p).get("temas", [])
    except Exception as ex:
        log("No se pudieron ampliar temas:", ex)
        return
    ids = {t["id"] for t in TEMAS[categoria]}
    for t in nuevos:
        if t.get("id") in ids or not t.get("fuentes"):
            continue
        texto, _ = reunir_fuentes(t["fuentes"])
        if len(texto) > 3000:
            estado.setdefault("temas_extra", {}).setdefault(categoria, []).append(t)
            TEMAS[categoria].append(t)
            log("Tema nuevo añadido:", t["titulo"])


# ---------------------------------------------------------------- Elección del tema
def elegir(estado):
    """Devuelve (categoria, tema, numero_de_parte, total_de_partes)."""
    pend = estado.get("pendiente")
    if pend:
        for t in TEMAS[pend["categoria"]]:
            if t["id"] == pend["id"]:
                return pend["categoria"], t, pend["parte"], partes_de(pend["categoria"], t)
    dia = CFG["calendario_aviacion"][dt.date.today().weekday()]
    categoria = dia[turno()] if isinstance(dia, dict) else dia
    categoria = categoria_por_rendimiento(estado, categoria)
    usado = lambda t: t["id"] in estado["usados"] or t["id"].rsplit("-", 1)[0] in estado["usados"]
    libres = [t for t in TEMAS[categoria] if not usado(t)]
    if len(libres) <= 2:
        ampliar_temas(estado, categoria)
        libres = [t for t in TEMAS[categoria] if not usado(t)]
    if not libres:  # se acabó la lista: vuelve a empezar
        estado["usados"] = [u for u in estado["usados"] if u not in {t["id"] for t in TEMAS[categoria]}]
        libres = TEMAS[categoria]
    tema = libres[0]
    return categoria, tema, 1, partes_de(categoria, tema)


def partes_de(categoria, tema):
    if "partes" in tema:
        return len(tema["partes"])
    return 2 if categoria == "accidente" else 1


# ---------------------------------------------------------------- Guion
NORMAS_AV = """
NORMAS (obligatorias):
- Usa SOLO datos que aparezcan en las FUENTES. Nada de memoria, nada inventado. Si un dato
  no está, no lo digas.
- Cifras con dígitos y formato español (1.250 metros, 3,5 toneladas). Si conviertes unidades
  (pies a metros, nudos a km/h) pon la operación en "calculos" usando solo cifras de las
  fuentes y estas constantes: {constantes}. En la narración di "unos" o "aproximadamente".
- Narrador único, hombre. Tono de buen documental contado de tú a tú: cercano, natural,
  seguro y preciso. Serio pero no frío. No intentes ser gracioso ni "guay": nada de jerga
  juvenil, exclamaciones exageradas, frikismo ni frases de youtuber. Nada de "en este vídeo
  vamos a ver", nada de saludos, nada de frases de relleno. El interés lo ponen los datos
  y la historia, no el tono.
- Accidentes: respeto absoluto a las víctimas, sin morbo ni detalles escabrosos, sin
  especular. Las causas, solo las que recoge la investigación oficial según las fuentes.
- No hagas preguntas al público tipo concurso ni le pidas que elija opciones.
- Habla de aviones y técnica; no opines de política.
- No anuncies otros vídeos ni días concretos ("el miércoles veremos..."), salvo el aviso de la parte 2 de una serie.
- Cada vídeo cuenta el tema COMPLETO: no lo dejes en una pregunta o en un "lo veremos otro día".
- El público es España y toda Latinoamérica: español neutro que se entienda en todos los
  países, sin expresiones solo de España (nada de "vale", "guay", "flipar", "vosotros").
  Da las distancias en kilómetros y metros, y la altitud también en pies.
"""


GUIA_CATEGORIA = {
    "accidente": """ESTILO ACCIDENTE: cuéntalo como una historia con tensión, en orden: quiénes eran y adónde iban
(toma "globo" con la ruta), el despegue y el vuelo normal, la primera señal de que algo va mal, la emergencia
(tomas "emergencia", "motor" con estado apagado o fuego, tormenta si la hubo), y en la parte de la investigación
"rayos_x" con la pieza culpable en rojo y tarjetas con las horas clave ("17:42"). Nunca muestres el impacto.""",
    "aerodinamica": """ESTILO PRINCIPIOS DE VUELO: explicación SENCILLA para cualquier persona, sin fórmulas ni palabras
técnicas sin explicar, con comparaciones de la vida diaria (la mano por la ventanilla del coche, un ventilador...).
Corto y claro: 50 a 90 segundos. Usa "ala_flujo", "heli_rotor", "heli_estacionario", "rayos_x" y "comparacion".""",
    "general": "ESTILO CURIOSIDAD: un hecho o historia de la aviación contado con ritmo, con el dato más sorprendente al principio.",
    "legendarios": "ESTILO AVIÓN LEGENDARIO: por qué es especial, su historia y sus récords, con tomas de vuelo espectaculares.",
    "supervivencia": "ESTILO SUPERVIVENCIA: historia real con tensión y final de esperanza, centrada en lo que hicieron bien las personas.",
}


def cuando_siguiente():
    return "hoy a las 15:00" if turno() == "manana" else "mañana a primera hora"


def escribir_guion(categoria, tema, parte, total, texto, urls):
    if total > 1:
        enfoque = tema.get("partes", [None, None])[parte - 1] if "partes" in tema else None
        if categoria == "accidente" and not enfoque:
            enfoque = ("PARTE 1: el contexto, el avión, la tripulación y el vuelo minuto a minuto hasta "
                       "el momento crítico. Termina en suspense, justo antes del desenlace."
                       if parte == 1 else
                       "PARTE 2: qué pasó exactamente, la investigación, las causas oficiales y qué cambió "
                       "en la aviación después. Empieza con un resumen de 15 segundos de la parte 1.")
        serie = (f"Es la PARTE {parte} de {total}. Enfoque: {enfoque}. "
                 + (f"Al final, deja el gancho: 'La parte 2, {cuando_siguiente()}'." if parte == 1 else
                    "Menciona al principio que es la segunda parte."))
        titulo_parte = f" (Parte {parte})"
    else:
        serie, titulo_parte = "Vídeo único, completo.", ""
    p = f"""Escribe el guion de un vídeo vertical de entre 50 segundos y 3 minutos (150-440 palabras de narración:
unas 150-200 si el tema es sencillo, hasta 440 si es una historia con mucho que contar) para el canal
de aviones y helicópteros "{CFG['canal']}". Tema: {tema['titulo']}{titulo_parte}. Categoría: {categoria}.
{serie}
{GUIA_CATEGORIA.get(categoria, '')}
{NORMAS_AV.format(constantes=json.dumps(CFG['constantes']))}
FUENTES:
{texto[:28000]}

Devuelve JSON:
{{
 "titulo_youtube": "máx 70 caracteres, palabra clave al principio, curiosidad, termina en '{titulo_parte.strip()}' si es una serie",
 "fuente_corta": "Wikipedia / informe oficial",
 "palabras_clave": ["5-8 búsquedas reales de la gente sobre este tema"],
 "descripcion": "2 primeras líneas con la palabra clave y el gancho; luego 3-4 frases; sin URL",
 "tags": ["12-15 etiquetas"],
 "hashtags": ["5-8 hashtags de aviación concretos, con #"],
 "pregunta_comentarios": "una pregunta abierta para comentarios (no de elegir opciones), p. ej. '¿Habrías hecho lo mismo en la cabina?'",
 "titulo_post": "gancho para Instagram, 1-2 emojis, máx 80 caracteres",
 "texto_post": "4-6 frases cortas con emojis como viñetas, el dato más impactante, y al final 'Guárdalo y compártelo con alguien que vuele pronto'",
 "paises": ["códigos ISO3 de los países implicados, o vacío"],
 "calculos": [{{"operacion": "35000 * 0.3048", "resultado": 10668}}],
 "aeronave": {{"modelo": "uno de: a320, b737, b757, b777, a330, a340, b747, a380, dc10, md80, rj85, atr72, c130, caza, heli_ligero, heli_medio, heli_militar, heli_tandem (el más parecido al aparato real de la historia)",
              "colores": {{"fuselaje": "#hex", "deriva": "#hex (cola)", "franja": "#hex", "motor": "#hex"}},
              "texto": "nombre de la aerolínea u operador en MAYÚSCULAS para pintarlo en el fuselaje, o CABINA ABIERTA si es un tema general"}},
 "escenario": {{"terreno": "hierba|campo|selva|mar|montana|nieve|desierto|ciudad", "hora": "dia|atardecer|noche", "tormenta": false}},
 "escenas": [
   {{"narracion": "...", "toma": "vuelo", "params": {{"camara": "lateral"}}, "tarjeta": {{"etiqueta": "AERONAVE / 01", "titulo": "Boeing 757"}}, "short": true}},
   {{"narracion": "...", "toma": "despegue", "tarjeta": {{"etiqueta": "VELOCIDAD", "titulo": "250 km/h"}}}},
   {{"narracion": "...", "toma": "emergencia", "params": {{"fuego": true}}, "etiquetas": [{{"parte": "motor", "texto": "motor 2 apagado"}}], "escenario": {{"hora": "noche", "tormenta": true}}}},
   {{"narracion": "...", "toma": "ala_flujo", "params": {{"angulo": 18, "perdida": true}}, "tarjeta": {{"etiqueta": "ALA / 02", "titulo": "Pérdida"}},
     "comparacion": [{{"titulo": "Vuelo normal", "texto": "el aire sigue el ala", "angulo": 5}}, {{"titulo": "Pérdida", "texto": "el aire se despega", "angulo": 18}}]}},
   {{"narracion": "...", "toma": "globo", "puntos": [{{"nombre": "Madrid", "lat": 40.47, "lon": -3.56}}, {{"nombre": "Lima", "lat": -12.02, "lon": -77.11}}]}},
   {{"narracion": "...", "toma": "rayos_x", "params": {{"pieza": "tren"}}, "tarjeta": {{"etiqueta": "TREN PRINCIPAL / 03", "titulo": "Freno multidisco"}}}},
   {{"narracion": "...", "toma": "pista", "cierre": true}}
 ]
}}
El vídeo es una ANIMACIÓN 3D que se genera sola a partir de estas tomas. Cada escena elige una "toma":
- "pista": el aparato parado en la pista y la cámara girando alrededor (presentar el avión).
- "rodaje": rodando por la pista. "despegue": carrera y despegue. "aterrizaje": aproximación y toma de tierra.
- "vuelo": en vuelo. params.camara: lateral | persecucion | frontal | abajo | arriba; params.altitud_m opcional.
- "viraje": girando inclinado. "emergencia": en apuros, picando y balanceándose (params.fuego true/false).
- "ala_flujo": primer plano del ala con las líneas de aire (params.angulo en grados, params.perdida true/false).
- "motor": primer plano del motor (params.estado: normal | apagado | fuego).
- "rayos_x": el aparato en líneas de alambre con una pieza en rojo (params.pieza: motor | tren | ala | cola | rotor | rotor_cola | cabina | fuselaje).
- "globo": la Tierra con la ruta dibujándose entre "puntos" (nombre, lat, lon de aeropuertos o ciudades; 2 a 4).
- "cenital": vista desde arriba. Helicópteros: "heli_estacionario" (quieto en el aire con el flujo del rotor), "heli_vuelo", "heli_rotor" (primer plano del rotor con el aire bajando).
"escenario" global: terreno, hora y tormenta de la historia; una escena puede cambiarlo con su propio "escenario"
(p. ej. la parte de la tormenta). Usa "atardecer" o "noche" cuando la historia ocurre así o para dar ambiente.
"tarjeta" (opcional, en unas 6-9 escenas): etiqueta corta en mayúsculas tipo "ALA / 02", "VELOCIDAD", "17:42"
y un título de 1-3 palabras o una cifra con unidad ("60 toneladas", "Sustentación"). Las cifras, de las fuentes.
"etiquetas" (opcional): 1-2 rótulos que señalan una pieza visible (parte: morro, cabina, ala, flaps, alerones,
motor, tren, cola, estabilizador, fuselaje, rotor, rotor_cola) con un texto de 1-4 palabras.
"comparacion" (opcional, solo aerodinámica): 2 casos con su perfil de ala y ángulo. NO es una pregunta al público.
Varía las tomas: no repitas la misma dos veces seguidas, y alterna planos generales y de detalle.
En accidentes: nada de víctimas; el impacto nunca se muestra, se corta antes (emergencia, humo, tormenta).
5 a 14 escenas de 1 a 3 frases cortas. La primera, con un gancho brutal en los 3 primeros segundos (un dato o
una frase que obligue a seguir viendo). Hacia la mitad, una frase natural invitando a seguir el canal. La última lleva
"cierre": true y es {'el gancho de la parte 2' if total > 1 and parte == 1 else 'la pregunta para comentarios'}."""
    return bot.gemini(p)


def corregir_guion(g, error, texto):
    """En vez de reescribir todo (gasta cupo), se pide arreglar solo las cifras sin respaldo."""
    p = f"""Este guion (JSON) tiene cifras que NO aparecen en las fuentes: {error}
Corrígelo: elimina esas cifras o cámbialas por otras que SÍ estén en las fuentes, sin tocar nada
más. Si una cifra viene de una conversión de unidades, pon la operación en "calculos" usando solo
cifras de las fuentes y estas constantes: {json.dumps(CFG['constantes'])}.
Devuelve el JSON completo corregido, con la misma estructura.
GUION:
{json.dumps(g, ensure_ascii=False)}
FUENTES:
{texto[:20000]}"""
    return bot.gemini(p)


# ---------------------------------------------------------------- Visuales extra
_pintar_original = bot.pintar


def pintar_av(escena, g, destino, ancho, alto, subtitulo=None):
    v = escena.get("visual")
    if v not in ("cronologia", "fuerzas"):
        return _pintar_original(escena, g, destino, ancho, alto, subtitulo)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import font_manager as fm
    import textwrap
    c = CFG["colores"]
    negra = fm.FontProperties(fname=bot.fuente_tipo("Black"))
    bold = fm.FontProperties(fname=bot.fuente_tipo("Bold"))
    vertical = alto > ancho
    esc = ancho / 1920 if not vertical else ancho / 1080
    fig = plt.figure(figsize=(ancho / 100, alto / 100), dpi=100, facecolor=c["fondo"])
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(plt.Rectangle((0, 0.965), 1, 0.035, color=c["acento"]))
    ax.text(0.03, 0.982, CFG["canal"].upper(), color="#FFFFFF", fontproperties=bold, fontsize=22 * esc, va="center")
    rot = escena.get("rotulo", "")
    ax.text(0.5, 0.86, "\n".join(textwrap.wrap(rot.upper(), 14 if vertical else 30)), color=c["texto"],
            fontproperties=negra, fontsize=(52 if vertical else 48) * esc, ha="center", va="center")
    if v == "cronologia":
        ev = escena.get("eventos", [])[:5]
        y0, paso = (0.68, 0.11) if vertical else (0.68, 0.12)
        ax.plot([0.12, 0.12], [y0 + 0.02, y0 - paso * (len(ev) - 1) - 0.02], color=c["acento"], lw=4 * esc)
        for k, e in enumerate(ev):
            y = y0 - k * paso
            ax.add_patch(plt.Circle((0.12, y), 0.012, color=c["rojo"]))
            ax.text(0.16, y, str(e.get("hora", "")), color=c["acento"], fontproperties=negra,
                    fontsize=(40 if vertical else 36) * esc, va="center")
            ax.text(0.16, y - (0.045 if vertical else 0.05), "\n".join(textwrap.wrap(str(e.get("texto", "")), 34 if vertical else 70)),
                    color=c["texto"], fontproperties=bold, fontsize=(26 if vertical else 24) * esc, va="center")
    else:  # fuerzas sobre un perfil alar NACA 2412
        bx = fig.add_axes([0.15, 0.25, 0.7, 0.45] if not vertical else [0.05, 0.35, 0.9, 0.3])
        bx.set_facecolor(c["fondo"])
        bx.axis("off")
        x = np.linspace(0, 1, 200)
        m, pp, t = 0.02, 0.4, 0.12
        yt = 5 * t * (0.2969 * np.sqrt(x) - 0.126 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1015 * x**4)
        yc = np.where(x < pp, m / pp**2 * (2 * pp * x - x**2), m / (1 - pp)**2 * ((1 - 2 * pp) + 2 * pp * x - x**2))
        bx.fill(np.r_[x, x[::-1]], np.r_[yc + yt, (yc - yt)[::-1]], color=c["suave"])
        flechas = [((0.4, 0.08), (0, 0.32), "SUSTENTACIÓN", c["acento"]),
                   ((0.4, -0.06), (0, -0.3), "PESO", c["rojo"]),
                   ((-0.05, 0.02), (-0.3, 0), "EMPUJE", "#1E88E5"),
                   ((1.05, 0.02), (0.3, 0), "RESISTENCIA", "#E64A19")]
        for (x0, y0), (dx, dy), et, col in flechas:
            bx.annotate("", xy=(x0 + dx, y0 + dy), xytext=(x0, y0),
                        arrowprops=dict(arrowstyle="-|>", color=col, lw=5 * esc, mutation_scale=40 * esc))
            tx, ty = (x0 + dx * 1.15, y0 + dy * 1.15) if dx == 0 else (x0 + dx * 0.6, y0 + 0.09)
            bx.text(tx, ty, et, color=col, fontproperties=negra,
                    fontsize=(30 if vertical else 28) * esc, ha="center", va="center")
        bx.set_xlim(-0.5, 1.5)
        bx.set_ylim(-0.45, 0.5)
        bx.set_aspect("equal")
    ax.text(0.03, 0.025, f"Fuente: {g.get('fuente_corta', 'Wikipedia')}", color=c["suave"], fontproperties=bold, fontsize=16 * esc)
    if subtitulo:
        t = ax.text(0.5, 0.2 if vertical else 0.09, subtitulo, color="#111111", fontproperties=negra,
                    fontsize=(44 if vertical else 36) * esc, ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.35", fc=c["acento"], ec="none"))
    fig.savefig(destino, facecolor=c["fondo"])
    plt.close(fig)


bot.pintar = pintar_av  # el montaje de bot.py usará estos visuales


def verificar_av(g, texto):
    """Comprueba también las horas y textos de la cronología (sobre una copia)."""
    import copy
    h = copy.deepcopy(g)
    for e in h["escenas"]:
        extra = []
        tj = e.get("tarjeta") or {}
        extra += [tj.get("etiqueta", ""), tj.get("titulo", "")]
        extra += [x.get("texto", "") for x in e.get("etiquetas") or []]
        extra += [f"{x.get('titulo', '')} {x.get('texto', '')}" for x in e.get("comparacion") or []]
        e["rotulo"] = (e.get("rotulo", "") + " " + " ".join(extra)).strip()
        if e.get("visual") == "cronologia":
            e["rotulo"] = e.get("rotulo", "") + " " + " ".join(
                f"{x.get('hora', '')} {x.get('texto', '')}" for x in e.get("eventos", []))
    bot.verificar(h, texto)


def hacer_historia(corto, destino):
    """Story de 15 s: el arranque del corto con un aviso arriba para ir al perfil."""
    import subprocess
    fuente = bot.fuente_tipo("Black")
    texto = "NUEVO VÍDEO - MÍRALO EN EL PERFIL"
    vf = (f"drawbox=x=0:y=120:w=iw:h=150:color=0xE8772E@0.95:t=fill,"
          f"drawtext=fontfile='{fuente}':text='{texto}':fontcolor=white:fontsize=52:"
          f"x=(w-text_w)/2:y=120+(150-text_h)/2")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(corto), "-t", "15", "-vf", vf,
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-movflags", "+faststart", str(destino)], check=True)
    return destino


# ---------------------------------------------------------------- Vídeo animado con ilustraciones IA
ESTILO = ("stylized 3D animated film still, aviation scene, soft cinematic lighting, rich but natural colors, "
          "highly detailed aircraft, depth of field, no text, no letters, no logos, no watermark")


def imagen_ia(prompt, destino, ancho, alto, semilla):
    """Ilustración con Pollinations (gratis). Sin clave: 1 imagen cada ~15 s."""
    import urllib.parse
    from PIL import Image
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(f"{prompt}, {ESTILO}")
    params = {"width": ancho, "height": alto, "model": "flux", "nologo": "true", "seed": semilla, "safe": "true"}
    clave = os.environ.get("POLLINATIONS_TOKEN", "").strip()
    if clave:
        params["token"] = clave
    for intento in range(4):
        try:
            r = requests.get(url, params=params, timeout=180, headers=UA)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                destino.write_bytes(r.content)
                Image.open(destino).convert("RGB").save(destino)
                if not clave:
                    time.sleep(16)
                return True
            log("Imagen IA", r.status_code)
        except Exception as ex:
            log("Imagen IA falló:", str(ex)[:120])
        time.sleep(20 * (intento + 1))
    return False


def fondo_respaldo(ancho, alto):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (ancho, alto))
    d = ImageDraw.Draw(im)
    for y in range(alto):
        t = y / alto
        d.line([(0, y), (ancho, y)], fill=(int(205 + 45 * t), int(228 + 15 * t), int(247 - 10 * t)))
    return im


def fuente_pil(peso, tam):
    from PIL import ImageFont
    return ImageFont.truetype(bot.fuente_tipo(peso), int(tam))


def texto_con_sombra(d, xy, texto, fuente, relleno="#FFFFFF", anchor="mm"):
    x, y = xy
    for dx, dy in ((3, 3), (2, 2)):
        d.text((x + dx, y + dy), texto, font=fuente, fill=(0, 0, 0, 160), anchor=anchor)
    d.text((x, y), texto, font=fuente, fill=relleno, anchor=anchor)


def componer_base(e, g, img, ancho, alto, tmp):
    """Fotograma base de la escena (sin subtítulos)."""
    import textwrap
    from PIL import Image, ImageDraw, ImageFilter
    c = CFG["colores"]
    bg = Image.open(img).convert("RGB") if img and img.exists() else fondo_respaldo(ancho, alto)
    bg = bg.resize((ancho, alto), Image.LANCZOS)
    v = e.get("visual", "escena")
    vertical = alto > ancho
    if v in ("cifra", "cronologia", "barras", "mapa", "fuerzas"):
        # gráfico de datos en una tarjeta sobre la ilustración desenfocada
        fondo = bg.filter(ImageFilter.GaussianBlur(18))
        fondo = Image.blend(fondo, Image.new("RGB", fondo.size, (255, 255, 255)), 0.25)
        tarjeta_png = tmp.with_name(tmp.stem + "_tarjeta.png")
        pintar_av(e, g, tarjeta_png, ancho, alto)
        tarjeta = Image.open(tarjeta_png).convert("RGB")
        f = 0.86
        tw, th = int(ancho * f), int(alto * f)
        tarjeta = tarjeta.resize((tw, th), Image.LANCZOS)
        mascara = Image.new("L", (tw, th), 0)
        ImageDraw.Draw(mascara).rounded_rectangle([0, 0, tw, th], radius=int(28 * ancho / 1920), fill=255)
        sombra = Image.new("RGBA", fondo.size, (0, 0, 0, 0))
        ImageDraw.Draw(sombra).rounded_rectangle([(ancho - tw) // 2 + 8, (alto - th) // 2 + 12,
                                                  (ancho + tw) // 2 + 8, (alto + th) // 2 + 12],
                                                 radius=int(28 * ancho / 1920), fill=(0, 0, 0, 90))
        fondo = Image.alpha_composite(fondo.convert("RGBA"), sombra.filter(ImageFilter.GaussianBlur(14)))
        fondo.paste(tarjeta, ((ancho - tw) // 2, (alto - th) // 2), mascara)
        return fondo.convert("RGB")
    # escena ilustrada: imagen a pantalla completa + rótulo arriba
    lienzo = bg.convert("RGBA")
    capa = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    alto_degradado = int(alto * (0.30 if vertical else 0.34))
    for y in range(alto_degradado):
        d.line([(0, y), (ancho, y)], fill=(8, 25, 45, int(170 * (1 - y / alto_degradado))))
    lienzo = Image.alpha_composite(lienzo, capa)
    d = ImageDraw.Draw(lienzo)
    esc = ancho / (1080 if vertical else 1920)
    d.text((int(90 * esc), int(70 * esc)), CFG["canal"].upper(), font=fuente_pil("Bold", 30 * esc),
           fill=c["acento"], anchor="lm")
    rot = (e.get("rotulo") or "").upper()
    if rot:
        lineas = textwrap.wrap(rot, 16 if vertical else 30)[:3]
        tam = (78 if v == "portada" else 62) * esc
        y0 = int(alto * (0.12 if vertical else 0.13))
        for k, linea in enumerate(lineas):
            texto_con_sombra(d, (ancho // 2, y0 + int(k * tam * 1.1)), linea, fuente_pil("Black", tam))
    if v == "cierre":
        texto_con_sombra(d, (ancho // 2, int(alto * 0.62)), CFG["usuario_instagram"], fuente_pil("Black", 58 * esc),
                         relleno=c["acento"])
    return lienzo.convert("RGB")


def subtitulo_png(texto, ancho, alto, destino):
    from PIL import Image, ImageDraw
    c = CFG["colores"]
    vertical = alto > ancho
    esc = ancho / (1080 if vertical else 1920)
    im = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = fuente_pil("Black", (56 if vertical else 46) * esc)
    while d.textlength(texto, font=f) > ancho * 0.86 and f.size > 20:
        f = fuente_pil("Black", f.size * 0.92)
    w = d.textlength(texto, font=f)
    y = int(alto * (0.78 if vertical else 0.86))
    pad = int(18 * esc)
    d.rounded_rectangle([(ancho - w) / 2 - pad, y - f.size * 0.75, (ancho + w) / 2 + pad, y + f.size * 0.75],
                        radius=int(16 * esc), fill=c["acento"])
    d.text((ancho / 2, y), texto, font=f, fill="#111111", anchor="mm")
    im.save(destino)


def clip_animado(e, g, mp3, salida, ancho, alto, pref, img, idx):
    """Ilustración con movimiento de cámara + subtítulos sincronizados por palabras."""
    import subprocess
    S = bot.SALIDA
    d = bot.duracion(mp3) + 0.35
    frames = int(d * 25)
    base = S / f"{pref}_base.png"
    componer_base(e, g, img, ancho, alto, base).save(base)
    mov = e.get("movimiento") or ["acercar", "derecha", "alejar", "izquierda"][idx % 4]
    es_dato = e.get("visual") in ("cifra", "cronologia", "barras", "mapa", "fuerzas")
    zmax = 1.04 if es_dato else 1.15
    inc = (zmax - 1) / max(frames, 1)
    if mov == "alejar" and not es_dato:
        z = f"if(eq(on,0),{zmax},max(zoom-{inc:.6f},1.0))"
    else:
        z = f"min(zoom+{inc:.6f},{zmax})"
    if mov == "derecha" and not es_dato:
        x = f"(iw-iw/zoom)*on/{frames}"
    elif mov == "izquierda" and not es_dato:
        x = f"(iw-iw/zoom)*(1-on/{frames})"
    else:
        x = "iw/2-(iw/zoom/2)"
    filtro = [f"[0:v]scale={ancho * 2}:{alto * 2},zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s={ancho}x{alto}:fps=25[v0]"]
    entradas = ["-i", base.name]
    partes = bot.trozos(e["narracion"])
    total = sum(len(p.split()) for p in partes) or 1
    t = 0.0
    for k, parte in enumerate(partes):
        png = S / f"{pref}_sub{k:02}.png"
        subtitulo_png(parte, ancho, alto, png)
        dur = d * len(parte.split()) / total
        entradas += ["-loop", "1", "-i", png.name]
        filtro.append(f"[v{k}][{k + 1}:v]overlay=0:0:enable='between(t,{t:.2f},{t + dur:.2f})'[v{k + 1}]")
        t += dur
    entradas += ["-i", mp3.name]
    n_audio = len(partes) + 1
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *entradas, "-filter_complex", ";".join(filtro),
                    "-map", f"[v{len(partes)}]", "-map", f"{n_audio}:a", "-t", f"{d:.2f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-ar", "44100", "-b:a", "160k", "-af", "apad", salida.name], check=True, cwd=S)
    return d


def montar_animado(g):
    import random
    S = bot.SALIDA
    S.mkdir(exist_ok=True)
    semilla = random.randint(1, 10_000_000)
    horiz, vert, capitulos, t = [], [], [], 0.0
    for i, e in enumerate(g["escenas"]):
        prompt = e.get("imagen") or "airliner flying above the clouds at sunset"
        img_h = S / f"ia_h{i:02}.jpg"
        if not imagen_ia(prompt, img_h, 1344, 768, semilla + i):
            img_h = None
        mp3 = S / f"v{i:02}.mp3"
        bot.voz(e["narracion"], "A", mp3)
        h = S / f"h{i:02}.mp4"
        d = clip_animado(e, g, mp3, h, 1920, 1080, f"h{i:02}", img_h, i)
        horiz.append(h)
        capitulos.append((t, e.get("rotulo") or e.get("visual", "")))
        t += d
        if e.get("short") or e.get("visual") == "cierre":
            img_v = S / f"ia_v{i:02}.jpg"
            if not imagen_ia(prompt, img_v, 768, 1344, semilla + i):
                img_v = None
            s = S / f"s{i:02}.mp4"
            clip_animado(e, g, mp3, s, 1080, 1920, f"s{i:02}", img_v, i)
            vert.append(s)
    largo, corto = S / "video.mp4", S / "corto.mp4"
    bot.unir(horiz, largo)
    bot.unir(vert, corto)
    # miniatura: la portada ilustrada
    from PIL import Image
    Image.open(S / "h00_base.png").resize((1280, 720)).save(S / "miniatura.png")
    log(f"Vídeo {bot.duracion(largo):.0f}s, corto {bot.duracion(corto):.0f}s")
    g["_capitulos"] = bot.capitulos_youtube(capitulos)
    return largo, corto


# ---------------------------------------------------------------- Animación 3D
FPS = 24
RESOLUCION = {"h": (1280, 720), "v": (720, 1280)}
TOMAS_VALIDAS = {"pista", "rodaje", "despegue", "aterrizaje", "vuelo", "viraje", "emergencia", "ala_flujo", "motor",
                 "globo", "heli_estacionario", "heli_vuelo", "heli_rotor", "cenital", "rayos_x"}


def preparar_tareas(g, semilla_base):
    """Voz con tiempos por palabra para cada escena y lista de tomas 3D a renderizar."""
    import math
    S = bot.SALIDA
    S.mkdir(exist_ok=True)
    tareas = []
    aer = g.get("aeronave") or {"modelo": "a320"}
    for i, e in enumerate(g["escenas"]):
        mp3 = S / f"v{i:02}.mp3"
        e["_palabras"] = bot.voz_palabras(e["narracion"], mp3)
        d = bot.duracion(mp3)
        e["_frames"] = int(math.ceil((d + 0.45) * FPS))
    esc = g["escenas"]
    esc[-1]["cierre"] = True
    log(f"Vídeo de {sum(e['_frames'] for e in esc) / FPS:.0f} s")
    for i, e in enumerate(esc):
        toma = e.get("toma") if e.get("toma") in TOMAS_VALIDAS else "vuelo"
        escv = dict(g.get("escenario") or {})
        escv.update(e.get("escenario") or {})
        params = dict(e.get("params") or {})
        if toma == "globo":
            params["puntos"] = [p for p in (e.get("puntos") or []) if "lat" in p and "lon" in p]
        for o in ["v"]:  # un solo vídeo vertical: el mismo para Instagram y YouTube
            ancho, alto = RESOLUCION[o]
            tareas.append({"id": f"e{i:02}{o}", "toma": toma, "params": params, "aeronave": aer, "escenario": escv,
                           "frames": e["_frames"], "fps": FPS, "ancho": ancho, "alto": alto, "muestras": 8,
                           "semilla": (semilla_base + i * 7) % 100000})
    (S / "tareas.json").write_text(json.dumps(tareas, ensure_ascii=False), encoding="utf-8")
    log(f"{len(tareas)} tomas 3D, {sum(t['frames'] for t in tareas)} fotogramas")
    return tareas


def montar_3d(g, dir_frames):
    import montaje3d
    S = bot.SALIDA
    def lista(orient):
        res = []
        for i, e in enumerate(g["escenas"]):
            res.append({"tarea_id": f"e{i:02}{orient}", "frames": e["_frames"], "audio": S / f"v{i:02}.mp3",
                        "palabras": e.get("_palabras"), "tarjeta": e.get("tarjeta"), "etiquetas": e.get("etiquetas"),
                        "tormenta": bool(((g.get("escenario") or {}) | (e.get("escenario") or {})).get("tormenta")),
                        "puntos": e.get("puntos") if e.get("toma") == "globo" else None,
                        "comparacion": e.get("comparacion"), "cierre": bool(e.get("cierre"))})
        return res
    import shutil
    largo, corto = S / "video.mp4", S / "corto.mp4"
    montaje3d.montar(lista("v"), "v", dir_frames, corto, CFG, S / "tmp_montaje", FPS)
    shutil.copy(corto, largo)  # el mismo vídeo para YouTube e Instagram
    montaje3d.miniatura(corto, g.get("titulo_youtube", ""), S / "miniatura.png", CFG["colores"]["acento"])
    capitulos, t = [], 0.0
    for e in g["escenas"]:
        nombre = (e.get("tarjeta") or {}).get("titulo") or e.get("toma", "")
        capitulos.append((t, nombre))
        t += e["_frames"] / FPS
    g["_capitulos"] = bot.capitulos_youtube(capitulos)
    log(f"Vídeo {bot.duracion(largo):.0f}s, corto {bot.duracion(corto):.0f}s")
    return largo, corto


# ---------------------------------------------------------------- Principal
MIN_ESCENAS, MIN_PALABRAS, MAX_PALABRAS = 4, 130, 470


def normalizar_guion(g):
    """La IA a veces devuelve el JSON con otra forma (escenas como texto, todo dentro de otra clave...).
    Se arregla aquí para que el resto del bot reciba siempre lo mismo."""
    if isinstance(g, list):
        g = {"escenas": g}
    if not isinstance(g, dict):
        raise ValueError("La IA no devolvió un guion válido")
    if "escenas" not in g:
        for v in g.values():
            if isinstance(v, dict) and "escenas" in v:
                g = {**g, **v}
                break
    escenas = []
    for e in g.get("escenas") or []:
        if isinstance(e, str) and e.strip():
            escenas.append({"narracion": e.strip(), "toma": "vuelo"})
        elif isinstance(e, dict):
            texto = e.get("narracion") or e.get("texto") or e.get("narration") or ""
            if isinstance(texto, list):
                texto = " ".join(map(str, texto))
            if str(texto).strip():
                e["narracion"] = str(texto).strip()
                if not isinstance(e.get("tarjeta"), dict):
                    e.pop("tarjeta", None)
                if not isinstance(e.get("etiquetas"), list):
                    e.pop("etiquetas", None)
                else:
                    e["etiquetas"] = [x for x in e["etiquetas"] if isinstance(x, dict)]
                if not isinstance(e.get("params"), dict):
                    e.pop("params", None)
                escenas.append(e)
    g["escenas"] = escenas
    for k in ("titulo_youtube", "titulo_post", "texto_post", "descripcion", "pregunta_comentarios"):
        if not isinstance(g.get(k), str):
            g[k] = " ".join(map(str, g[k])) if isinstance(g.get(k), list) else str(g.get(k) or "")
    if not g["titulo_youtube"]:
        g["titulo_youtube"] = g["titulo_post"] or "Cabina Abierta"
    if not g["titulo_post"]:
        g["titulo_post"] = g["titulo_youtube"]
    for k in ("hashtags", "tags", "palabras_clave", "calculos"):
        if not isinstance(g.get(k), list):
            g[k] = []
    if not isinstance(g.get("aeronave"), dict):
        g["aeronave"] = {"modelo": "a320"}
    if not isinstance(g.get("escenario"), dict):
        g["escenario"] = {}
    return g


def palabras_guion(g):
    return sum(len((e.get("narracion") or "").split()) for e in g.get("escenas", []))


def longitud_ok(g):
    return len(g.get("escenas", [])) >= MIN_ESCENAS and MIN_PALABRAS <= palabras_guion(g) <= MAX_PALABRAS


def ampliar_guion(g, texto):
    """Si el guion ha salido corto (o la IA lo ha recortado), se pide completarlo."""
    largo = palabras_guion(g) > MAX_PALABRAS
    p = f"""Este guion (JSON) es DEMASIADO {'LARGO' if largo else 'CORTO'}: tiene {len(g.get('escenas', []))} escenas y {palabras_guion(g)} palabras.
Tiene que durar entre 50 segundos y 3 minutos: entre 150 y 440 palabras de narración en total, en 5 a 14 escenas.
{'Acórtalo quitando lo menos importante' if largo else 'Amplíalo contando la historia completa y con más detalle'}, usando SOLO datos de las FUENTES.
Mantén el mismo formato JSON y todos los campos (aeronave, escenario, tomas variadas, tarjetas, la última con
"cierre": true). No anuncies vídeos futuros ni días concretos.
GUION:
{json.dumps(g, ensure_ascii=False)}
FUENTES:
{texto[:24000]}
Devuelve el JSON completo."""
    return normalizar_guion(bot.gemini(p))


def recortar_o_aceptar(g):
    """Último recurso antes de no publicar: si es largo se quitan escenas del final (manteniendo el cierre);
    si es algo corto pero tiene contenido, se acepta (mejor un vídeo de 40 s que ninguno)."""
    esc = g["escenas"]
    while palabras_guion(g) > MAX_PALABRAS and len(esc) > MIN_ESCENAS:
        esc.pop(-2 if len(esc) > 1 else -1)
    if esc:
        esc[-1]["cierre"] = True
    if palabras_guion(g) >= 90 and len(esc) >= 3:
        log(f"Guion aceptado con {palabras_guion(g)} palabras y {len(esc)} escenas")
        return g
    raise RuntimeError(f"No publico: guion demasiado corto ({palabras_guion(g)} palabras)")


def asegurar_longitud(g, texto, previo=None):
    for _ in range(2):
        if longitud_ok(g):
            return g
        log(f"Guion fuera de medida ({len(g.get('escenas', []))} escenas, {palabras_guion(g)} palabras): lo ajusto")
        try:
            g = ampliar_guion(g, texto)
        except Exception as ex:
            log("No se pudo ampliar:", ex)
    if longitud_ok(g):
        return g
    if previo is not None and longitud_ok(previo):
        return previo
    return recortar_o_aceptar(g)


def quitar_cifras(g, error):
    """Elimina las frases, tarjetas y rótulos que contienen las cifras que no se han podido verificar."""
    import re
    malas = re.findall(r"'([^']+)'", error.split(":", 1)[-1])
    if not malas:
        raise RuntimeError(f"No publico: {error}")
    tiene = lambda txt: any(m in (txt or "") for m in malas)
    def limpiar(txt):
        frases = re.split(r"(?<=[.!?…])\s+", txt or "")
        return " ".join(f for f in frases if not tiene(f)).strip()
    for e in g["escenas"]:
        e["narracion"] = limpiar(e.get("narracion", ""))
        if tiene((e.get("tarjeta") or {}).get("titulo")) or tiene((e.get("tarjeta") or {}).get("etiqueta")):
            e.pop("tarjeta", None)
        e["etiquetas"] = [x for x in e.get("etiquetas") or [] if not tiene(x.get("texto"))]
        e["comparacion"] = [x for x in e.get("comparacion") or [] if not tiene(x.get("titulo")) and not tiene(x.get("texto"))] or None
        if tiene(e.get("rotulo")):
            e["rotulo"] = ""
    g["escenas"] = [e for e in g["escenas"] if e["narracion"]]
    g["escenas"][-1]["cierre"] = True
    for k in ("descripcion", "texto_post"):
        g[k] = limpiar(g.get(k, ""))
    for k in ("titulo_youtube", "titulo_post"):
        if tiene(g.get(k)):
            for m in malas:
                g[k] = g[k].replace(m, "").replace("  ", " ").strip()
    log(f"Quitadas las frases con cifras sin respaldo: {malas}")
    if palabras_guion(g) < 90:
        raise RuntimeError(f"No publico: sin esas cifras el guion queda demasiado corto ({error})")
    return g


def fase_guion():
    """Elige tema, escribe y verifica el guion, genera las voces y la lista de tomas 3D."""
    estado = leer_estado()
    for cat, lista in estado.get("temas_extra", {}).items():
        conocidos = {t["id"] for t in TEMAS.get(cat, [])}
        TEMAS.setdefault(cat, []).extend(t for t in lista if t["id"] not in conocidos)
    actualizar_metricas(estado)
    categoria, tema, parte, total = elegir(estado)
    log(f"Turno {turno()} · {categoria} · {tema['titulo']} · parte {parte}/{total}")
    refs = tema["partes_fuentes"][parte - 1] if "partes_fuentes" in tema else tema["fuentes"]
    texto, urls = reunir_fuentes(refs)
    if len(texto) < 1500:
        raise RuntimeError(f"Fuentes insuficientes para {tema['id']}")
    ultimo = None
    g = normalizar_guion(escribir_guion(categoria, tema, parte, total, texto, urls))
    g = asegurar_longitud(g, texto)
    for intento in range(3):
        for e in g["escenas"]:
            e["locutor"] = "A"  # siempre voz masculina
        try:
            verificar_av(g, texto)
            break
        except ValueError as ex:
            ultimo = ex
            log("Verificación fallida, corrigiendo solo esas cifras:", ex)
            if intento < 2:
                anterior = g
                g = normalizar_guion(corregir_guion(g, str(ex), texto))
                # a veces la corrección devuelve el guion recortado: se recupera la longitud
                g = asegurar_longitud(g, texto, anterior)
                if palabras_guion(g) < palabras_guion(anterior) * 0.8:
                    log("La corrección recortó el guion; se vuelve a intentar sobre el anterior")
                    g = anterior
    else:
        # último recurso: quitar las frases con cifras sin respaldo en vez de no publicar
        g = quitar_cifras(g, str(ultimo))
        verificar_av(g, texto)
    if not longitud_ok(g):
        g = recortar_o_aceptar(g)
    bot.SALIDA.mkdir(exist_ok=True)
    (bot.SALIDA / "guion.json").write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
    preparar_tareas(g, abs(hash(tema["id"])) + parte * 1000)
    plan = {"g": g, "categoria": categoria, "tema": tema, "parte": parte, "total": total, "urls": urls, "estado": estado}
    (bot.SALIDA / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    log("Guion y voces listos")


def fase_render(worker=0, total=1):
    import subprocess
    subprocess.run([sys.executable, str(bot.RAIZ / "render3d.py"), str(bot.SALIDA / "tareas.json"),
                    str(bot.SALIDA / "fotogramas"), "--worker", str(worker), "--total", str(total)], check=True)


def fase_publicar(dir_frames=None):
    plan = json.loads((bot.SALIDA / "plan.json").read_text(encoding="utf-8"))
    g, categoria, tema, parte, total, urls, estado = (plan[k] for k in ("g", "categoria", "tema", "parte", "total", "urls", "estado"))
    largo, corto = montar_3d(g, dir_frames or str(bot.SALIDA / "fotogramas"))
    if PRUEBA:
        log("Modo prueba: no se sube nada.")
        return
    tags_h = " ".join(g.get("hashtags", [])[:8])
    pregunta = g.get("pregunta_comentarios", "")
    fuentes_txt = "\n".join(urls)
    siguiente = (f"\n▶️ La parte 2, {cuando_siguiente()}. Sigue el canal para no perdértela."
                 if (total > 1 and parte == 1) else "")
    descripcion = (f"{g['descripcion']}{siguiente}\n\n💬 {pregunta}\n\n"
                   f"📚 Fuentes:\n{fuentes_txt}\n\n🔔 Dos vídeos de aviación cada día.\n"
                   f"📲 Instagram: {CFG['usuario_instagram']}\n\n{tags_h} {CFG['hashtags']}")
    tags_yt = (g.get("palabras_clave", []) + g.get("tags", []))[:20]
    anterior = "\n⏪ ¿Te perdiste la parte 1? La tienes justo antes en el perfil." if parte > 1 else ""
    caption = (f"{g['titulo_post']}\n\n{g['texto_post']}{siguiente}{anterior}\n\n💬 {pregunta}\n\n"
               f"🎬 Recreación en animación 3D\n\n"
               f"{tags_h} {CFG['hashtags']}")
    textos = bot.SALIDA / "textos_youtube.txt"
    textos.write_text(f"TITULO:\n{g['titulo_youtube']}\n\nDESCRIPCION:\n{descripcion}\n\n"
                      f"(Súbelo como Short: el vídeo es corto.mp4)\n\nTEXTO INSTAGRAM:\n{caption}\n", encoding="utf-8")
    etiqueta = f"{dt.date.today():%Y%m%d}-{turno()}-{int(time.time())}"
    historia = hacer_historia(corto, bot.SALIDA / "historia.mp4")
    adjuntos = [corto, bot.SALIDA / "miniatura.png", textos, historia]
    video_url = bot.url_publica(adjuntos, etiqueta)
    historia_url = video_url.rsplit("/", 1)[0] + "/historia.mp4"
    media_id = None
    if os.environ.get("IG_TOKEN"):
        media_id = bot.subir_instagram(video_url, caption)
        # Parte 2: avisar en la publicación de la parte 1
        p1 = (estado.get("pendiente") or {}).get("ig_parte1")
        if parte > 1 and p1:
            try:
                bot.comentar_instagram(p1, f"🎬 ¡La parte {parte} ya está publicada! La tienes en el perfil 👉 {CFG['usuario_instagram']}")
            except Exception as ex:
                log("No se pudo comentar la parte 1:", ex)
        try:  # Story con el arranque del vídeo para llevar gente al Reel
            bot.subir_instagram(historia_url, "", media_type="STORIES")
        except Exception as ex:
            log("No se pudo publicar la story:", ex)
    if CFG.get("youtube_auditado"):
        yt = bot.youtube()
        bot.subir_youtube(yt, corto, f"{g['titulo_youtube'][:90]} #Shorts", descripcion, tags_yt)
    # actualizar estado
    if media_id:
        estado.setdefault("publicados", []).append(
            {"id": media_id, "categoria": categoria, "tema": tema["id"], "fecha": dt.datetime.utcnow().isoformat()})
        estado["publicados"] = estado["publicados"][-200:]
    if parte < total:
        estado["pendiente"] = {"categoria": categoria, "id": tema["id"], "parte": parte + 1, "ig_parte1": media_id}
    else:
        estado["pendiente"] = None
        estado["usados"].append(tema["id"])
    guardar_estado(estado)
    log("Hecho")


def main():
    fase = sys.argv[sys.argv.index("--fase") + 1] if "--fase" in sys.argv else "todo"
    if fase in ("guion", "todo"):
        fase_guion()
    if fase == "render":
        w = int(os.environ.get("WORKER", "0")); n = int(os.environ.get("WORKERS", "1"))
        fase_render(w, n)
    if fase == "todo":
        fase_render(0, 1)
    if fase in ("publicar", "todo"):
        fase_publicar(os.environ.get("DIR_FOTOGRAMAS"))


if __name__ == "__main__":
    main()
