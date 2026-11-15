"""
Agente diario del canal.
1. Busca en el BOE de los últimos días subvenciones, ayudas, contratos y convenios.
2. Gemini elige el tema más llamativo y escribe un guion SOLO con datos del texto oficial.
3. Se verifican todas las cifras contra el BOE. Si alguna no cuadra, no se publica nada.
4. Voz (dos locutores), gráficas, mapas y montaje: vídeo 16:9 + corto vertical.
5. Sube a YouTube (vídeo + Short) y a Instagram (Reel).

Variables de entorno (Secrets de GitHub):
GEMINI_API_KEY, YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN, IG_TOKEN,
GH_PAT (opcional, para renovar IG_TOKEN solo), GITHUB_REPOSITORY (lo pone GitHub).
Modo prueba: python bot.py --prueba  (no sube nada, deja los vídeos en salida/)
"""
import asyncio
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).parent
CFG = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
SALIDA = RAIZ / "salida"
HISTORIAL = RAIZ / "historial.json"
PRUEBA = "--prueba" in sys.argv
# Calendario: lunes=0 ... domingo=6. Se puede forzar con la variable MODO (dinero / escandalo)
MODO = os.environ.get("MODO") or CFG["calendario"][dt.date.today().weekday()]
if MODO not in ("dinero", "escandalo"):
    MODO = CFG["calendario"][dt.date.today().weekday()]
UA = {"User-Agent": "Mozilla/5.0 (canal-bot)"}


def log(*a):
    print("[bot]", *a, flush=True)


# ---------------------------------------------------------------- 1. BOE
def items_boe(fecha):
    url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{fecha:%Y%m%d}"
    r = requests.get(url, headers={**UA, "Accept": "application/json"}, timeout=60)
    if r.status_code != 200:
        return []
    out = []

    def recorrer(n):
        if isinstance(n, dict):
            if "titulo" in n and ("url_html" in n or "url_xml" in n):
                out.append(n)
            for v in n.values():
                recorrer(v)
        elif isinstance(n, list):
            for v in n:
                recorrer(v)

    recorrer(r.json())
    return out


def texto_boe(item):
    def url_de(v):
        return v.get("texto") if isinstance(v, dict) else v

    url = url_de(item.get("url_xml")) or url_de(item.get("url_html"))
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    from bs4 import BeautifulSoup
    sopa = BeautifulSoup(r.content, "html.parser")
    cuerpo = sopa.find("texto") or sopa.find(id="textoxslt") or sopa
    texto = re.sub(r"\s+", " ", cuerpo.get_text(" ")).strip()
    return texto, url_de(item.get("url_html")) or url


def candidatos():
    usados = set(json.loads(HISTORIAL.read_text()) if HISTORIAL.exists() else [])
    claves = [k.lower() for k in CFG["palabras_clave_boe"]]
    hoy = dt.date.today()
    lista = []
    for d in range(CFG["dias_atras_boe"] + 1):
        for it in items_boe(hoy - dt.timedelta(days=d)):
            t = it["titulo"]
            ident = it.get("identificador", t)
            if ident in usados:
                continue
            if any(k in t.lower() for k in claves):
                lista.append({"id": ident, "titulo": t, "item": it})
    log(f"{len(lista)} candidatos en el BOE")
    return lista[:80]


# ---------------------------------------------------------------- 1b. Prensa (días de escándalo)
def candidatos_prensa():
    import feedparser
    usados = set(json.loads(HISTORIAL.read_text()) if HISTORIAL.exists() else [])
    claves = [k.lower() for k in CFG["palabras_clave_escandalo"]]
    lista = []
    for medio, url in CFG["feeds_prensa"].items():
        try:
            r = requests.get(url, headers=UA, timeout=30)
            feed = feedparser.parse(r.content)
        except Exception as ex:
            log("Feed caído:", medio, ex)
            continue
        for e in feed.entries[:40]:
            t = e.get("title", "")
            resumen = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
            if e.get("link") in usados:
                continue
            if any(k in (t + " " + resumen).lower() for k in claves):
                lista.append({"id": e["link"], "titulo": f"[{medio}] {t}", "link": e["link"], "medio": medio})
    log(f"{len(lista)} noticias candidatas")
    return lista[:80]


def texto_articulo(link):
    from bs4 import BeautifulSoup
    r = requests.get(link, headers=UA, timeout=60)
    r.raise_for_status()
    sopa = BeautifulSoup(r.content, "html.parser")
    zona = sopa.find("article") or sopa
    parrafos = [p.get_text(" ", strip=True) for p in zona.find_all("p")]
    texto = " ".join(x for x in parrafos if len(x) > 40)
    return re.sub(r"\s+", " ", texto).strip()


# ---------------------------------------------------------------- 2. Gemini
def gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{CFG['modelo_gemini']}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8},
    }
    for intento in range(4):
        r = requests.post(url, json=body, timeout=180,
                          headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        if r.status_code == 200:
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(txt)
        log("Gemini", r.status_code, r.text[:300])
        time.sleep(30 * (intento + 1))
    raise RuntimeError("Gemini no responde")


NORMAS = """
NORMAS EDITORIALES (obligatorias):
- Usa SOLO datos que aparezcan en el TEXTO FUENTE. Nada de memoria, nada inventado.
- Escribe las cifras con dígitos y EXACTAMENTE con el mismo formato que en el texto fuente
  (si pone 1.250.000 euros, escribe 1.250.000 euros).
- Si quieres un cálculo (p. ej. euros por habitante), ponlo en "calculos" con la operación;
  solo puedes operar con cifras del texto o con estas constantes: {constantes}.
  En la narración usa el resultado redondeado y di "unos" o "aproximadamente".
- Tono: crítico, directo, irónico y entretenido, para un público que quiere saber en qué
  se gasta el dinero de sus impuestos. Humor sí, insultos no.
- Prohibido: atribuir delitos, acusar a personas concretas de ilegalidades, señalar a
  colectivos por nacionalidad, raza o religión, o afirmar algo que el texto no diga.
- Cita siempre la fuente (p. ej. "según el BOE de [fecha]" o "según publica [medio]").
"""

NORMAS_ESCANDALO = """
NORMAS EXTRA PARA ESCÁNDALOS (obligatorias):
- Atribuye todo al medio: "según publica [medio]". Nada que el artículo no diga.
- Presunción de inocencia: "investigado", "presunto", "según la causa". Nunca "culpable"
  si no hay sentencia firme mencionada en el artículo.
- El foco es la gestión del Gobierno y de los cargos públicos: corrupción, contratos,
  enchufes, mentiras, dimisiones, despilfarro, decisiones polémicas.
- Si la noticia trata de inmigración, habla de la política y la gestión del Gobierno
  (cifras oficiales, gasto, decisiones), nunca de la nacionalidad o el origen de
  personas concretas acusadas de delitos.
"""


def elegir_tema(cands):
    lista = "\n".join(f"{i}. {c['titulo']}" for i, c in enumerate(cands))
    if MODO == "escandalo":
        criterio = """De esta lista de noticias, elige el escándalo político o de gestión del
Gobierno que más indignación genere (corrupción, contratos, enchufes, mentiras, despilfarro).
Descarta sucesos sobre personas particulares."""
    else:
        criterio = """De esta lista del BOE, elige la disposición que más indignación o curiosidad
genere al contribuyente (cuantía alta, destino llamativo, gasto exterior, etc.) y que tenga importe."""
    p = f"""Eres el editor de un canal crítico con el Gobierno de España.
{criterio}
{lista}
Responde JSON: {{"indice": n, "motivo": "..."}}"""
    return cands[gemini(p)["indice"]]


def escribir_guion(tema, texto, url):
    extra = NORMAS_ESCANDALO if MODO == "escandalo" else ""
    p = f"""Escribe el guion de un vídeo de 3 a 4 minutos (unas 450-550 palabras de narración)
para el canal "{CFG['canal']}". Dos locutores se alternan: A (hombre) y B (mujer).
{NORMAS.format(constantes=json.dumps(CFG['constantes']))}
{extra}
TÍTULO: {tema['titulo']}
URL: {url}
TEXTO FUENTE:
{texto[:30000]}

Devuelve JSON con esta forma:
{{
 "titulo_youtube": "máx 90 caracteres, gancho fuerte",
 "fuente_corta": "BOE o nombre del medio",
 "descripcion": "3-5 frases + fuente con URL",
 "tags": ["..."],
 "titulo_post": "titular para Instagram con 1-2 emojis",
 "texto_post": "explicación amena de 4-6 frases para Instagram, con la fuente al final",
 "paises": ["códigos ISO3 de países que salgan, o vacío"],
 "calculos": [{{"operacion": "1250000 / 49000000", "resultado": 0.03}}],
 "escenas": [
   {{"locutor": "A", "narracion": "...", "visual": "portada", "rotulo": "frase corta", "short": true}},
   {{"locutor": "B", "narracion": "...", "visual": "cifra", "cifra": "1.250.000 €", "rotulo": "..."}},
   {{"locutor": "A", "narracion": "...", "visual": "barras", "datos": [{{"etiqueta": "...", "valor": 1250000}}], "rotulo": "..."}},
   {{"locutor": "B", "narracion": "...", "visual": "mapa", "rotulo": "..."}},
   {{"locutor": "A", "narracion": "...", "visual": "texto", "rotulo": "..."}},
   {{"locutor": "B", "narracion": "Síguenos para más...", "visual": "cierre", "rotulo": "..."}}
 ]
}}
Reglas de escenas: 10 a 16 escenas; la primera es "portada" con un gancho en los 5 primeros
segundos; marca "short": true en 3-5 escenas que juntas cuenten la historia en menos de
60 segundos; usa "mapa" solo si hay países; "barras" solo con cifras del texto."""
    return gemini(p)


# ---------------------------------------------------------------- 3. Verificación
def digitos(s):
    return re.sub(r"[^\d]", "", s)


def verificar(g, texto):
    fuente = {digitos(n) for n in re.findall(r"\d[\d.,]*\d|\d", texto)}
    fuente |= {str(v) for v in CFG["constantes"].values()}
    resultados = set()
    for c in g.get("calculos", []):
        op = c["operacion"]
        if not re.fullmatch(r"[\d\s.+\-*/()]+", op):
            raise ValueError(f"Cálculo no permitido: {op}")
        for n in re.findall(r"\d+(?:\.\d+)?", op):
            if digitos(n) not in fuente:
                raise ValueError(f"Operando {n} no está en la fuente")
        valor = eval(op, {"__builtins__": {}})
        if abs(valor - float(c["resultado"])) > max(0.02 * abs(valor), 0.01):
            raise ValueError(f"Cálculo mal hecho: {op} = {valor}, no {c['resultado']}")
        resultados.add(digitos(f"{valor:.0f}"))
        resultados.add(digitos(str(c["resultado"])))
    errores = []
    piezas = []
    for e in g["escenas"]:
        piezas += [e.get("narracion", ""), e.get("rotulo", ""), str(e.get("cifra", ""))]
        for d in e.get("datos", []):
            x = float(d["valor"])
            piezas.append(f"{x:.0f}" if x.is_integer() else str(x))
    piezas += [g["titulo_youtube"], g["titulo_post"], g["texto_post"]]
    for trozo in piezas:
        for n in re.findall(r"\d[\d.,]*\d|\d", trozo):
            d = digitos(n)
            if len(d) <= 1 or d in fuente or d in resultados:
                continue
            if any(d.rstrip("0") and d.rstrip("0") == r.rstrip("0") for r in resultados):
                continue
            errores.append(n)
    if errores:
        raise ValueError(f"Cifras sin respaldo en el BOE: {sorted(set(errores))}")
    log("Verificación OK: todas las cifras están en la fuente")


# ---------------------------------------------------------------- 4. Voz
async def _tts(texto, voz, destino):
    import edge_tts
    await edge_tts.Communicate(texto, voz, rate="+8%").save(str(destino))


def voz(texto, locutor, destino):
    v = CFG["voz_a"] if locutor == "A" else CFG["voz_b"]
    for intento in range(3):
        try:
            asyncio.run(_tts(texto, v, destino))
            return
        except Exception as ex:
            log("TTS reintento", ex)
            time.sleep(5)
    raise RuntimeError("No se pudo generar la voz")


def duracion(f):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(f)], capture_output=True, text=True)
    return float(r.stdout.strip())


# ---------------------------------------------------------------- 5. Visuales
def fuente_tipo(peso="Black"):
    for p in [str(RAIZ / "assets" / f"Inter-{peso}.otf"),
              f"/usr/share/fonts/opentype/inter/Inter-{peso}.otf",
              f"/usr/share/fonts/truetype/inter/Inter-{peso}.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p):
            return p
    return None


MAPA = None


def mundo():
    global MAPA
    if MAPA is None:
        url = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
               "geojson/ne_110m_admin_0_countries.geojson")
        MAPA = requests.get(url, timeout=60).json()
    return MAPA


def pintar(escena, g, destino, ancho, alto):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    from matplotlib.patches import Polygon
    from matplotlib.ticker import FuncFormatter

    c = CFG["colores"]
    negra = fm.FontProperties(fname=fuente_tipo("Black"))
    bold = fm.FontProperties(fname=fuente_tipo("Bold"))
    vertical = alto > ancho
    esc = ancho / 1920 if not vertical else ancho / 1080
    fig = plt.figure(figsize=(ancho / 100, alto / 100), dpi=100, facecolor=c["fondo"])
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(plt.Rectangle((0, 0.965), 1, 0.035, color=c["acento"]))
    ax.text(0.03, 0.982, CFG["canal"].upper(), color=c["fondo"], fontproperties=bold,
            fontsize=22 * esc, va="center")
    rot = escena.get("rotulo", "")
    import textwrap
    anchura = 14 if vertical else 26
    v = escena["visual"]

    rend = fig.canvas.get_renderer()

    def ajustar(t, max_ancho=0.9, max_alto=0.45):
        # reduce la letra hasta que el texto quepa en pantalla
        while t.get_fontsize() > 10:
            bb = t.get_window_extent(renderer=rend)
            if bb.width <= max_ancho * ancho and bb.height <= max_alto * alto:
                break
            t.set_fontsize(t.get_fontsize() * 0.92)

    def titular(txt, y, tam, color=c["texto"]):
        t = ax.text(0.5, y, "\n".join(textwrap.wrap(txt.upper(), anchura)), color=color,
                    fontproperties=negra, fontsize=tam * esc, ha="center", va="center",
                    linespacing=1.05)
        ajustar(t)

    if v == "portada":
        titular(rot or g["titulo_youtube"], 0.55, 78)
        ax.add_patch(plt.Rectangle((0.3, 0.22), 0.4, 0.012, color=c["rojo"]))
    elif v == "cifra":
        t = ax.text(0.5, 0.58, str(escena.get("cifra", "")), color=c["acento"], fontproperties=negra,
                    fontsize=150 * esc, ha="center", va="center")
        ajustar(t, 0.9, 0.25)
        titular(rot, 0.3, 48)
    elif v == "barras" and escena.get("datos"):
        datos = escena["datos"][:6]
        bx = fig.add_axes([0.12, 0.12, 0.8, 0.6] if not vertical else [0.18, 0.15, 0.72, 0.55])
        bx.set_facecolor(c["fondo"])
        etiquetas = [textwrap.fill(d["etiqueta"], 18) for d in datos]
        valores = [float(d["valor"]) for d in datos]
        colores = [c["acento"] if x == max(valores) else c["suave"] for x in valores]
        bx.barh(etiquetas[::-1], valores[::-1], color=colores[::-1])
        bx.tick_params(colors=c["texto"], labelsize=20 * esc)
        bx.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}".replace(",", ".")))
        for s in bx.spines.values():
            s.set_visible(False)
        titular(rot, 0.84, 44)
    elif v == "mapa" and g.get("paises"):
        mx = fig.add_axes([0.02, 0.1, 0.96, 0.7] if not vertical else [0.0, 0.3, 1, 0.45])
        mx.set_facecolor(c["fondo"])
        mx.axis("off")
        marcados = set(g["paises"])
        for f in mundo()["features"]:
            iso = f["properties"].get("ADM0_A3")
            col = c["rojo"] if iso in marcados else "#2A3F5F"
            geo = f["geometry"]
            polis = geo["coordinates"] if geo["type"] == "MultiPolygon" else [geo["coordinates"]]
            for pol in polis:
                mx.add_patch(Polygon(pol[0], closed=True, facecolor=col, edgecolor=c["fondo"], lw=0.4))
        mx.set_xlim(-170, 190)
        mx.set_ylim(-58, 85)
        mx.set_aspect("equal")
        titular(rot, 0.88 if not vertical else 0.84, 44)
    elif v == "cierre":
        titular(rot or "Síguenos", 0.6, 70, c["acento"])
        ax.text(0.5, 0.35, CFG["usuario_instagram"], color=c["texto"], fontproperties=bold,
                fontsize=46 * esc, ha="center")
    else:
        titular(rot, 0.52, 62)
    ax.text(0.03, 0.025, f"Fuente: {g.get('fuente_corta', 'BOE')}", color=c["suave"], fontproperties=bold,
            fontsize=16 * esc)
    fig.savefig(destino, facecolor=c["fondo"])
    plt.close(fig)


# ---------------------------------------------------------------- 6. Montaje
def clip(png, mp3, salida, ancho, alto):
    d = duracion(mp3) + 0.35
    frames = int(d * 25)
    vf = (f"scale={ancho * 2}:{alto * 2},zoompan=z='min(zoom+0.0006,1.08)':d={frames}:"
          f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={ancho}x{alto}:fps=25")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(png), "-i", str(mp3),
                    "-vf", vf, "-t", f"{d:.2f}", "-c:v", "libx264", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-b:a", "160k",
                    "-af", "apad", str(salida)], check=True)


def unir(clips, salida):
    lista = salida.with_suffix(".txt")
    lista.write_text("".join(f"file '{c.name}'\n" for c in clips))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
                    str(lista), "-c", "copy", "-movflags", "+faststart", str(salida)],
                   check=True, cwd=salida.parent)


def montar(g):
    SALIDA.mkdir(exist_ok=True)
    horiz, vert = [], []
    escenas = g["escenas"]
    for i, e in enumerate(escenas):
        mp3 = SALIDA / f"v{i:02}.mp3"
        voz(e["narracion"], e.get("locutor", "A"), mp3)
        h_png, v_png = SALIDA / f"h{i:02}.png", SALIDA / f"s{i:02}.png"
        pintar(e, g, h_png, 1920, 1080)
        h = SALIDA / f"h{i:02}.mp4"
        clip(h_png, mp3, h, 1920, 1080)
        horiz.append(h)
        if e.get("short") or e["visual"] == "cierre":
            pintar(e, g, v_png, 1080, 1920)
            s = SALIDA / f"s{i:02}.mp4"
            clip(v_png, mp3, s, 1080, 1920)
            vert.append(s)
    largo, corto = SALIDA / "video.mp4", SALIDA / "corto.mp4"
    unir(horiz, largo)
    unir(vert, corto)
    pintar({"visual": "portada", "rotulo": g["titulo_youtube"]}, g, SALIDA / "miniatura.png", 1280, 720)
    log(f"Vídeo {duracion(largo):.0f}s, corto {duracion(corto):.0f}s")
    return largo, corto


# ---------------------------------------------------------------- 7. YouTube
def youtube():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    cred = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                       client_id=os.environ["YT_CLIENT_ID"],
                       client_secret=os.environ["YT_CLIENT_SECRET"],
                       token_uri="https://oauth2.googleapis.com/token")
    return build("youtube", "v3", credentials=cred, cache_discovery=False)


def subir_youtube(yt, archivo, titulo, descripcion, tags, miniatura=None):
    from googleapiclient.http import MediaFileUpload
    body = {
        "snippet": {"title": titulo[:100], "description": descripcion, "tags": tags[:15],
                    "categoryId": "25", "defaultLanguage": "es"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(archivo), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    log("YouTube subido:", vid)
    if miniatura:
        try:
            yt.thumbnails().set(videoId=vid, media_body=str(miniatura)).execute()
        except Exception as ex:
            log("Miniatura no puesta (canal sin verificar?):", ex)
    return vid


# ---------------------------------------------------------------- 8. Instagram
def url_publica(archivos, etiqueta):
    repo = os.environ["GITHUB_REPOSITORY"]
    subprocess.run(["gh", "release", "create", etiqueta, *map(str, archivos), "--title", etiqueta,
                    "--notes", "Video del dia"], check=True)
    return f"https://github.com/{repo}/releases/download/{etiqueta}/{archivos[0].name}"


def renovar_token_ig(token):
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=60)
    if r.status_code == 200 and os.environ.get("GH_PAT"):
        nuevo = r.json()["access_token"]
        subprocess.run(["gh", "secret", "set", "IG_TOKEN", "--body", nuevo],
                       env={**os.environ, "GH_TOKEN": os.environ["GH_PAT"]}, check=False)
        log("Token de Instagram renovado")
        return nuevo
    return token


def subir_instagram(video_url, caption):
    token = renovar_token_ig(os.environ["IG_TOKEN"])
    base = f"https://graph.instagram.com/{CFG['version_api_instagram']}"
    yo = requests.get(f"{base}/me", params={"fields": "user_id,username", "access_token": token},
                      timeout=60).json()
    uid = yo.get("user_id") or yo["id"]
    r = requests.post(f"{base}/{uid}/media", data={
        "media_type": "REELS", "video_url": video_url, "caption": caption[:2200],
        "share_to_feed": "true", "access_token": token}, timeout=120).json()
    if "id" not in r:
        raise RuntimeError(f"Instagram contenedor: {r}")
    cid = r["id"]
    for _ in range(60):
        st = requests.get(f"{base}/{cid}", params={"fields": "status_code", "access_token": token},
                          timeout=60).json().get("status_code")
        if st == "FINISHED":
            break
        if st == "ERROR":
            raise RuntimeError("Instagram no pudo procesar el vídeo")
        time.sleep(10)
    pub = requests.post(f"{base}/{uid}/media_publish",
                        data={"creation_id": cid, "access_token": token}, timeout=120).json()
    log("Instagram publicado:", pub)


# ---------------------------------------------------------------- Principal
def main():
    log("Modo de hoy:", MODO)
    cands = candidatos_prensa() if MODO == "escandalo" else candidatos()
    if not cands:
        raise RuntimeError("No hay temas nuevos hoy")
    for _ in range(4):
        tema = elegir_tema(cands)
        log("Tema:", tema["titulo"])
        try:
            if MODO == "escandalo":
                texto, url = texto_articulo(tema["link"]), tema["link"]
            else:
                texto, url = texto_boe(tema["item"])
        except Exception as ex:
            log("No se pudo leer la fuente:", ex)
            texto = ""
        if len(texto) > 1200:
            break
        log("Fuente demasiado corta o de pago, pruebo otra")
        cands = [c for c in cands if c is not tema]
    else:
        raise RuntimeError("No encontré una fuente legible")
    ultimo_error = None
    for intento in range(3):
        g = escribir_guion(tema, texto, url)
        try:
            verificar(g, texto)
            break
        except ValueError as ex:
            ultimo_error = ex
            log("Verificación fallida, reescribiendo:", ex)
    else:
        raise RuntimeError(f"No publico: {ultimo_error}")
    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "guion.json").write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
    largo, corto = montar(g)
    if PRUEBA:
        log("Modo prueba: no se sube nada. Revisa la carpeta salida/")
        return
    descripcion = f"{g['descripcion']}\n\nFuente: {url}\n\n{CFG['hashtags']}"
    etiqueta = f"dia-{dt.date.today():%Y%m%d}-{int(time.time())}"
    textos = SALIDA / "textos_youtube.txt"
    textos.write_text(f"TITULO:\n{g['titulo_youtube']}\n\nDESCRIPCION:\n{descripcion}\n\n"
                      f"TITULO SHORT:\n{g['titulo_post'][:85]} #Shorts\n", encoding="utf-8")
    # El corto (y, si YouTube aun no esta auditado, todo lo demas) queda en una "release" de GitHub
    adjuntos = [corto] if CFG["youtube_auditado"] else [corto, largo, SALIDA / "miniatura.png", textos]
    video_url = url_publica(adjuntos, etiqueta)
    caption = f"{g['titulo_post']}\n\n{g['texto_post']}\n\nFuente: {g.get('fuente_corta', 'BOE')}\n{url}\n\n{CFG['hashtags']}"
    subir_instagram(video_url, caption)
    if CFG["youtube_auditado"]:
        yt = youtube()
        subir_youtube(yt, largo, g["titulo_youtube"], descripcion, g.get("tags", []), SALIDA / "miniatura.png")
        subir_youtube(yt, corto, f"{g['titulo_post'][:85]} #Shorts", descripcion, g.get("tags", []))
    else:
        log("YouTube pendiente de auditoria: video listo en la pestana Releases de GitHub")
    usados = json.loads(HISTORIAL.read_text()) if HISTORIAL.exists() else []
    HISTORIAL.write_text(json.dumps((usados + [tema["id"]])[-500:], ensure_ascii=False))
    log("Hecho")


if __name__ == "__main__":
    main()
