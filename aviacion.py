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
    libres = [t for t in TEMAS[categoria] if t["id"] not in estado["usados"]]
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
- Narrador único, hombre. Tono de piloto veterano que te lo cuenta tomando un café:
  cercano, apasionado, preciso, con alguna frase con gracia. Nada de "en este vídeo
  vamos a ver", nada de saludos, nada de frases de relleno.
- Accidentes: respeto absoluto a las víctimas, sin morbo ni detalles escabrosos, sin
  especular. Las causas, solo las que recoge la investigación oficial según las fuentes.
- No hagas preguntas al público tipo concurso ni le pidas que elija opciones.
- Habla de aviones y técnica; no opines de política.
"""


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
    p = f"""Escribe el guion de un vídeo de 3 a 4 minutos (450-550 palabras de narración) para el canal
de aviones y helicópteros "{CFG['canal']}". Tema: {tema['titulo']}{titulo_parte}. Categoría: {categoria}.
{serie}
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
 "escenas": [
   {{"narracion": "...", "visual": "portada", "rotulo": "frase corta", "short": true}},
   {{"narracion": "...", "visual": "cifra", "cifra": "583 pasajeros", "rotulo": "...", "short": true}},
   {{"narracion": "...", "visual": "cronologia", "eventos": [{{"hora": "17:01", "texto": "..."}}], "rotulo": "..."}},
   {{"narracion": "...", "visual": "barras", "datos": [{{"etiqueta": "...", "valor": 70}}], "rotulo": "..."}},
   {{"narracion": "...", "visual": "fuerzas", "rotulo": "..."}},
   {{"narracion": "...", "visual": "mapa", "rotulo": "..."}},
   {{"narracion": "...", "visual": "texto", "rotulo": "..."}},
   {{"narracion": "...", "visual": "cierre", "rotulo": "..."}}
 ]
}}
Visuales: "cronologia" (hasta 5 eventos con hora o fecha) para accidentes; "fuerzas" (perfil de
ala con sustentación, peso, empuje y resistencia) solo en aerodinámica; "barras" para comparar
cifras de las fuentes (velocidades, pasajeros, alcance...); "mapa" solo si hay países.
10 a 16 escenas de 1 a 3 frases cortas. La primera es "portada" con un gancho brutal en los 3
primeros segundos (un dato o una frase que obligue a seguir viendo). Marca "short": true en 4-6
escenas que juntas cuenten lo esencial en menos de 60 segundos. Hacia la mitad, una frase
natural invitando a seguir el canal. La última es "cierre": {'el gancho de la parte 2' if total > 1 and parte == 1 else 'la pregunta para comentarios'}."""
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


# ---------------------------------------------------------------- Principal
def main():
    estado = leer_estado()
    categoria, tema, parte, total = elegir(estado)
    log(f"Turno {turno()} · {categoria} · {tema['titulo']} · parte {parte}/{total}")
    refs = tema["partes_fuentes"][parte - 1] if "partes_fuentes" in tema else tema["fuentes"]
    texto, urls = reunir_fuentes(refs)
    if len(texto) < 1500:
        raise RuntimeError(f"Fuentes insuficientes para {tema['id']}")
    ultimo = None
    g = escribir_guion(categoria, tema, parte, total, texto, urls)
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
                g = corregir_guion(g, str(ex), texto)
    else:
        raise RuntimeError(f"No publico: {ultimo}")
    bot.SALIDA.mkdir(exist_ok=True)
    (bot.SALIDA / "guion.json").write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
    largo, corto = bot.montar(g)
    if PRUEBA:
        log("Modo prueba: no se sube nada.")
        return
    tags_h = " ".join(g.get("hashtags", [])[:8])
    pregunta = g.get("pregunta_comentarios", "")
    fuentes_txt = "\n".join(urls)
    siguiente = (f"\n▶️ La parte 2, {cuando_siguiente()}. Sigue el canal para no perdértela."
                 if (total > 1 and parte == 1) else "")
    descripcion = (f"{g['descripcion']}{siguiente}\n\n💬 {pregunta}\n\n📌 Capítulos:\n{g.get('_capitulos', '')}\n\n"
                   f"📚 Fuentes:\n{fuentes_txt}\n\n🔔 Dos vídeos de aviación cada día.\n"
                   f"📲 Instagram: {CFG['usuario_instagram']}\n\n{tags_h} {CFG['hashtags']}")
    tags_yt = (g.get("palabras_clave", []) + g.get("tags", []))[:20]
    anterior = "\n⏪ ¿Te perdiste la parte 1? La tienes justo antes en el perfil." if parte > 1 else ""
    caption = (f"{g['titulo_post']}\n\n{g['texto_post']}{siguiente}{anterior}\n\n💬 {pregunta}\n\n"
               f"{tags_h} {CFG['hashtags']}")
    textos = bot.SALIDA / "textos_youtube.txt"
    textos.write_text(f"TITULO:\n{g['titulo_youtube']}\n\nDESCRIPCION:\n{descripcion}\n\n"
                      f"TITULO SHORT:\n{g['titulo_post'][:85]} #Shorts\n\nTEXTO INSTAGRAM:\n{caption}\n", encoding="utf-8")
    etiqueta = f"{dt.date.today():%Y%m%d}-{turno()}-{int(time.time())}"
    historia = hacer_historia(corto, bot.SALIDA / "historia.mp4")
    adjuntos = [corto, largo, bot.SALIDA / "miniatura.png", textos, historia]
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
        bot.subir_youtube(yt, largo, g["titulo_youtube"], descripcion, tags_yt, bot.SALIDA / "miniatura.png")
        bot.subir_youtube(yt, corto, f"{g['titulo_post'][:85]} #Shorts", descripcion, tags_yt)
    # actualizar estado
    if parte < total:
        estado["pendiente"] = {"categoria": categoria, "id": tema["id"], "parte": parte + 1, "ig_parte1": media_id}
    else:
        estado["pendiente"] = None
        estado["usados"].append(tema["id"])
    guardar_estado(estado)
    log("Hecho")


if __name__ == "__main__":
    main()
