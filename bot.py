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
    from bs4 import BeautifulSoup
    import warnings
    warnings.filterwarnings("ignore")

    def url_de(v):
        return v.get("texto") if isinstance(v, dict) else v

    mejor, url_ok = "", None
    for clave in ("url_xml", "url_html"):
        url = url_de(item.get(clave))
        if not url:
            continue
        try:
            r = requests.get(url, headers=UA, timeout=60)
            r.raise_for_status()
        except Exception as ex:
            log("BOE no responde:", url, ex)
            continue
        sopa = BeautifulSoup(r.content, "html.parser")
        cuerpo = sopa.find("texto") or sopa.find(id="textoxslt") or sopa.find(id="DOdocText") or sopa
        texto = re.sub(r"\s+", " ", cuerpo.get_text(" ")).strip()
        if len(texto) > len(mejor):
            mejor, url_ok = texto, url
    return mejor, url_de(item.get("url_html")) or url_ok


def fecha_historica():
    """Recorre en orden cronológico los años de Gobierno (desde 'inicio_gobierno') de forma que
    entre 'repaso_desde' y 'repaso_hasta' se llegue al presente. Fuera de ese periodo, el BOE de hoy."""
    h = CFG.get("repaso_historico")
    if not h:
        return None
    hoy = dt.date.today()
    ini, fin = dt.date.fromisoformat(h["repaso_desde"]), dt.date.fromisoformat(h["repaso_hasta"])
    if not (ini <= hoy <= fin):
        return None
    origen = dt.date.fromisoformat(h["inicio_gobierno"])
    frac = (hoy - ini).days / max((fin - ini).days, 1)
    return origen + dt.timedelta(days=int(frac * (hoy - origen).days))


def candidatos():
    usados = set(json.loads(HISTORIAL.read_text()) if HISTORIAL.exists() else [])
    claves = [k.lower() for k in CFG["palabras_clave_boe"]]
    base = fecha_historica()
    if base:
        log("Repaso histórico: BOE alrededor de", base)
        fechas = [base + dt.timedelta(days=k) for k in range(0, 42, 2)]  # unas seis semanas de BOE
    else:
        hoy = dt.date.today()
        fechas = [hoy - dt.timedelta(days=d) for d in range(CFG["dias_atras_boe"] + 1)]
    lista = []
    for fecha in fechas:
        for it in items_boe(fecha):
            t = it["titulo"]
            ident = it.get("identificador", t)
            if ident in usados:
                continue
            tl = t.lower()
            if any(x.lower() in tl for x in CFG.get("excluir_boe", [])):
                continue
            if any(k in tl for k in claves):
                # prioridad: dinero que sale fuera o va a entidades (lo que más indigna)
                prio = sum(1 for k in CFG.get("prioridad_boe", []) if k.lower() in tl)
                lista.append({"id": ident, "titulo": t, "item": it, "prio": prio})
    lista.sort(key=lambda c: -c["prio"])
    log(f"{len(lista)} candidatos en el BOE ({sum(1 for c in lista if c['prio'])} prioritarios)")
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
def extraer_json(txt):
    txt = txt.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt[txt.find("{"):]
    trozo = txt[txt.find("{"):txt.rfind("}") + 1]
    try:
        return json.loads(trozo)
    except ValueError:
        # JSON con alguna coma o comilla mal puesta: se repara en vez de tirar todo
        from json_repair import repair_json
        datos = repair_json(trozo or txt, return_objects=True)
        if not isinstance(datos, dict) or not datos:
            raise
        return datos


def llm_respaldo(prompt):
    """Respaldo gratuito y sin clave (Pollinations) cuando se agota el cupo de Gemini."""
    cabeceras = {"Content-Type": "application/json"}
    clave = os.environ.get("POLLINATIONS_TOKEN", "").strip()
    if clave:
        cabeceras["Authorization"] = f"Bearer {clave}"
    body = {"model": "openai", "temperature": 0.7,
            "messages": [{"role": "system", "content": "Responde únicamente con un objeto JSON válido, sin texto adicional."},
                         {"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}}
    for intento in range(4):
        try:
            r = requests.post("https://text.pollinations.ai/openai", json=body, headers=cabeceras, timeout=300)
            if r.status_code == 200:
                return extraer_json(r.json()["choices"][0]["message"]["content"])
            log("Respaldo LLM", r.status_code, r.text[:160])
        except Exception as ex:
            log("Respaldo LLM falló:", str(ex)[:160])
        time.sleep(20 * (intento + 1))
    raise RuntimeError("Ningún modelo de IA responde")


def gemini(prompt):
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7},
    }
    # Plan gratuito: si un modelo da 429 (cupo) o tarda demasiado, se pasa al siguiente.
    # Si el cupo DIARIO está agotado, se salta directamente al respaldo.
    # Cada modelo tiene su propio cupo diario gratuito: se van probando uno tras otro.
    modelos = [CFG["modelo_gemini"], "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-2.5-flash-lite",
               "gemini-2.0-flash", "gemma-3-27b-it"]
    vistos = []
    for modelo in [m for m in modelos if not (m in vistos or vistos.append(m))]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        cuerpo = body
        if modelo.startswith("gemma"):  # Gemma no admite el modo JSON: se pide en el texto
            cuerpo = {"contents": [{"parts": [{"text": prompt + "\n\nResponde SOLO con el JSON, sin nada más."}]}],
                      "generationConfig": {"temperature": 0.7}}
        for intento in range(2):
            try:
                r = requests.post(url, json=cuerpo, timeout=300,
                                  headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
                if r.status_code == 200:
                    return extraer_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                log("Gemini", modelo, r.status_code, r.text[:160])
                if (r.status_code == 429 and "PerDay" in r.text) or r.status_code in (400, 404):
                    break  # cupo diario agotado o modelo no disponible: siguiente modelo
                time.sleep(65 if r.status_code == 429 else 15)
            except (requests.exceptions.RequestException, KeyError, ValueError) as ex:
                log("Gemini", modelo, "falló:", str(ex)[:160])
                time.sleep(10)
    log("Gemini sin cupo: uso el modelo de respaldo")
    return llm_respaldo(prompt)


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
- Si la disposición es de años anteriores, dilo claramente ("en 2019, el Gobierno...") para
  que el espectador vea la evolución de estos años de Gobierno.
- No publiques ni cites encuestas electorales.
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
        criterio = """De esta lista del BOE, elige la disposición con importe que MÁS indigne al
contribuyente medio porque la considere prescindible, ideológica o un regalo: ayudas y
cooperación con otros países (p. ej. 1,5 millones para proyectos en un país lejano),
subvenciones directas a fundaciones, asociaciones, ONG, sindicatos o entidades afines,
propaganda y campañas institucionales, observatorios, chiringuitos, viajes y gastos de
representación. DESCARTA el gasto que la gente ve necesario: defensa, seguridad,
sanidad, infraestructuras, pensiones, emergencias, educación básica. Entre dos
opciones, prefiere la de destino más sorprendente frente a la de cuantía más alta."""
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
{texto[:15000]}

Devuelve JSON con esta forma:
{{
 "titulo_youtube": "máx 70 caracteres. Empieza por la palabra clave principal y añade curiosidad o indignación (cifra + destino + giro). Ej: 'Subvención de 3.200.000 € para... ¿en serio?'",
 "fuente_corta": "BOE o nombre del medio",
 "palabras_clave": ["5-8 búsquedas reales que haría la gente en Google/YouTube sobre este tema"],
 "descripcion": "primeras 2 líneas con la palabra clave principal y el gancho (es lo que se ve en el buscador); luego 3-4 frases explicando; sin URL",
 "tags": ["12-15 etiquetas: palabras clave, nombres de organismos y temas"],
 "hashtags": ["5-8 hashtags concretos del tema, con #"],
 "pregunta_comentarios": "pregunta polémica pero respetuosa para que la gente comente (¿Tú qué harías con ese dinero?)",
 "titulo_post": "primera línea del post de Instagram: gancho brutal con 1-2 emojis, máx 80 caracteres",
 "texto_post": "post ameno y fácil de leer: 4-6 frases cortas, con saltos de línea y emojis como viñetas (📌 💸 🧐), datos clave, la fuente al final y una llamada a guardar y compartir",
 "paises": ["códigos ISO3 de países que salgan, o vacío"],
 "calculos": [{{"operacion": "1250000 / 49000000", "resultado": 0.03}}],
 "escenas": [
   {{"locutor": "A", "narracion": "...", "visual": "portada", "rotulo": "frase corta", "short": true}},
   {{"locutor": "B", "narracion": "¿Cuánto crees que ha costado? Mira las opciones... te lo digo en tres segundos.", "visual": "pregunta", "rotulo": "¿Cuánto crees que ha costado?", "opciones": ["A) ...", "B) ...", "C) ..."], "short": true}},
   {{"locutor": "A", "narracion": "...", "visual": "cifra", "cifra": "1.250.000 €", "rotulo": "...", "short": true}},
   {{"locutor": "A", "narracion": "...", "visual": "barras", "datos": [{{"etiqueta": "...", "valor": 1250000}}], "rotulo": "..."}},
   {{"locutor": "B", "narracion": "...", "visual": "mapa", "rotulo": "..."}},
   {{"locutor": "A", "narracion": "...", "visual": "texto", "rotulo": "..."}},
   {{"locutor": "B", "narracion": "Síguenos para más...", "visual": "cierre", "rotulo": "..."}}
 ]
}}
Reglas de escenas: 10 a 16 escenas, cada una de 1 a 3 frases cortas (ritmo rápido, estilo
TikTok/YouTube Shorts). La primera es "portada": gancho en los 3 primeros segundos (una
pregunta o un dato que indigne), sin saludos. Incluye al menos una escena "pregunta" tipo
concurso justo antes de revelar la cifra principal: las opciones solo aparecen en pantalla,
la narración NO las lee; la respuesta correcta se revela en la escena siguiente. Habla
directamente al espectador ("tú", "tu dinero"), con ironía y frases que se puedan citar.
A mitad del vídeo, una frase pidiendo que se suscriba si le está indignando. La última es
"cierre" con la pregunta para comentarios. Marca "short": true en 4-6 escenas que juntas
cuenten la historia en menos de 60 segundos (incluida la pregunta y la revelación); usa
"mapa" solo si hay países; "barras" solo con cifras del texto."""
    return gemini(p)


# ---------------------------------------------------------------- 3. Verificación
def digitos(s):
    return re.sub(r"[^\d]", "", s)


NUM = r"(?<![A-Za-z\d])\d[\d.,]*\d(?![A-Za-z\d])|(?<![A-Za-z\d.,])\d(?![A-Za-z\d.,])"


def valor_es(n):
    """'1.250.000,50' -> 1250000.5 ; '130.000' -> 130000 ; '0,003' -> 0.003"""
    n = n.strip(".,")
    try:
        if "," in n:
            ent, dec = n.rsplit(",", 1)
            return float(ent.replace(".", "") + "." + dec)
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", n):
            return float(n.replace(".", ""))
        return float(n)
    except ValueError:
        return None


def _num_ingles(n):
    try:
        return float(n.strip(".,").replace(",", ""))
    except ValueError:
        return None


def verificar(g, texto):
    fuente = set()
    for n in re.findall(r"\d[\d.,]*\d|\d", texto):
        v = valor_es(n)
        if v is not None:
            fuente.add(v)
        fuente.add(float(digitos(n) or 0))
        try:  # formato inglés (1,250.5) por si la fuente está en inglés
            fuente.add(float(n.replace(",", "")))
        except ValueError:
            pass
    # "700 millones", "1,2 mil millones", "700 million", "3.5 billion" en la fuente cuentan con su valor completo
    mult_fuente = [(r"mil\s+millones", 1e9), (r"mill(?:ones|ón)", 1e6), (r"billion", 1e9), (r"million", 1e6),
                   (r"thousand", 1e3), (r"mil\b", 1e3)]
    for patron, mult in mult_fuente:
        for m in re.finditer(r"(\d[\d.,]*)\s*" + patron, texto, flags=re.I):
            for v in (valor_es(m.group(1)), _num_ingles(m.group(1))):
                if v is not None:
                    fuente.add(v * mult)
    fuente |= {float(v) for v in CFG["constantes"].values()}
    calculados = []
    for c in g.get("calculos", []):
        op = c["operacion"]
        if not re.fullmatch(r"[\d\s.+\-*/()]+", op):
            raise ValueError(f"Cálculo no permitido: {op}")
        for n in re.findall(r"\d+(?:\.\d+)?", op):
            if float(n) not in fuente:
                raise ValueError(f"Operando {n} no está en la fuente")
        calculados.append(eval(op, {"__builtins__": {}}))

    def respaldado(v):
        if any(abs(v - a) <= 1e-9 * max(1, abs(a)) for a in fuente):
            return True
        # resultados de cálculos: se permite el redondeo ("unos 3 céntimos")
        return any(abs(v - a) <= max(0.06 * abs(a), 0.005) or abs(v - a * 100) <= max(0.06 * abs(a * 100), 0.5)
                   for a in calculados)

    piezas = []
    for e in g["escenas"]:
        piezas += [e.get("narracion", ""), e.get("rotulo", ""), str(e.get("cifra", ""))]
        # las "opciones" de una pregunta tipo concurso son señuelos y no se verifican
        for d in e.get("datos", []):
            piezas.append(str(d["valor"]).replace(".", ","))
    piezas += [g["titulo_youtube"], g["titulo_post"], g["texto_post"], g.get("descripcion", "")]
    errores = []
    multiplicadores = [(r"mil\s+millones", 1e9), (r"mill(?:ones|ón)", 1e6), (r"mil\b", 1e3)]
    for trozo in piezas:
        # "3,2 millones" -> 3.200.000 (se comprueba entero, con margen por el redondeo)
        for patron, mult in multiplicadores:
            for m in re.finditer(r"(\d[\d.,]*)\s*" + patron, trozo):
                v = valor_es(m.group(1))
                if v is None:
                    continue
                v *= mult
                if not (respaldado(v) or any(abs(v - a) <= 0.05 * a for a in fuente if a > 0)):
                    errores.append(m.group(0))
            trozo = re.sub(r"(\d[\d.,]*)\s*" + patron, " ", trozo)
        for n in re.findall(NUM, trozo):
            v = valor_es(n)
            if v is None or v < 10 or respaldado(v):
                continue
            errores.append(n)
    if errores:
        raise ValueError(f"Cifras sin respaldo en la fuente: {sorted(set(errores))}")
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


async def _tts_palabras(texto, voz, destino):
    import edge_tts
    palabras = []
    com = edge_tts.Communicate(texto, voz, rate="+8%", boundary="WordBoundary")
    with open(destino, "wb") as fh:
        async for ch in com.stream():
            if ch["type"] == "audio":
                fh.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                t0 = ch["offset"] / 1e7
                palabras.append([ch["text"], t0, t0 + ch["duration"] / 1e7])
    return palabras


def voz_palabras(texto, destino):
    """Genera la voz y devuelve cada palabra con su momento exacto [(palabra, inicio, fin)].
    Las palabras se toman del texto original (con su puntuación) para los subtítulos."""
    v = CFG["voz_a"]
    pal = None
    for intento in range(3):
        try:
            pal = asyncio.run(_tts_palabras(texto, v, destino))
            break
        except Exception as ex:
            log("TTS reintento", ex)
            time.sleep(5)
    if pal is None:
        raise RuntimeError("No se pudo generar la voz")
    d = duracion(destino)
    originales = texto.split()
    if not pal or abs(len(pal) - len(originales)) > max(2, len(originales) * 0.15):
        # sin marcas fiables: repartir por longitud de palabra
        tot = sum(len(w) + 1 for w in originales) or 1
        t, pal = 0.1, []
        for w in originales:
            dur = (d - 0.2) * (len(w) + 1) / tot
            pal.append([w, t, t + dur]); t += dur
        return pal
    # emparejar con el texto original para recuperar comas, puntos y signos
    import difflib
    norm = lambda w: re.sub(r"[^\wáéíóúüñ]", "", w.lower())
    a = [norm(w) for w in originales]
    b = [norm(p[0]) for p in pal]
    tiempos = [None] * len(originales)
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            tiempos[blk.a + k] = (pal[blk.b + k][1], pal[blk.b + k][2])
    # huecos: interpolar entre vecinos conocidos
    for k in range(len(tiempos)):
        if tiempos[k] is None:
            ant = next((tiempos[x][1] for x in range(k - 1, -1, -1) if tiempos[x]), 0.05)
            sig_i = next((x for x in range(k + 1, len(tiempos)) if tiempos[x]), None)
            sig = tiempos[sig_i][0] if sig_i is not None else d - 0.1
            n_hueco = (sig_i if sig_i is not None else len(tiempos)) - k
            paso = max((sig - ant) / max(n_hueco, 1), 0.08)
            tiempos[k] = (ant, ant + paso)
    return [[w, t[0], t[1]] for w, t in zip(originales, tiempos)]


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


def pintar(escena, g, destino, ancho, alto, subtitulo=None):
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
    ax.text(0.03, 0.982, CFG["canal"].upper(), color="#FFFFFF", fontproperties=bold,
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
            col = c["rojo"] if iso in marcados else "#B9CCDD"
            geo = f["geometry"]
            polis = geo["coordinates"] if geo["type"] == "MultiPolygon" else [geo["coordinates"]]
            for pol in polis:
                mx.add_patch(Polygon(pol[0], closed=True, facecolor=col, edgecolor=c["fondo"], lw=0.4))
        mx.set_xlim(-170, 190)
        mx.set_ylim(-58, 85)
        mx.set_aspect("equal")
        titular(rot, 0.88 if not vertical else 0.84, 44)
    elif v == "pregunta":
        titular(rot, 0.8 if vertical else 0.82, 50, c["acento"])
        ops = escena.get("opciones", [])[:4]
        for k, op in enumerate(ops):
            y = (0.6 if vertical else 0.62) - k * (0.11 if vertical else 0.15)
            ax.add_patch(plt.Rectangle((0.15, y - 0.04), 0.7, 0.08 if vertical else 0.1,
                                       color="#FFFFFF", ec=c["acento"], lw=3))
            t = ax.text(0.5, y + (0.0 if vertical else 0.01), op, color=c["texto"], fontproperties=bold,
                        fontsize=40 * esc, ha="center", va="center")
            ajustar(t, 0.65, 0.07)
    elif v == "cierre":
        titular(rot or "Síguenos", 0.6, 70, c["acento"])
        ax.text(0.5, 0.35, CFG["usuario_instagram"], color=c["texto"], fontproperties=bold,
                fontsize=46 * esc, ha="center")
    else:
        titular(rot, 0.52, 62)
    ax.text(0.03, 0.025, f"Fuente: {g.get('fuente_corta', 'BOE')}", color=c["suave"], fontproperties=bold,
            fontsize=16 * esc)
    if subtitulo:
        ys = 0.2 if vertical else 0.09
        t = ax.text(0.5, ys, subtitulo, color="#111111", fontproperties=negra, fontsize=(44 if vertical else 36) * esc,
                    ha="center", va="center", bbox=dict(boxstyle="round,pad=0.35", fc=c["acento"], ec="none"))
        ajustar(t, 0.9, 0.12)
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


def trozos(texto, n=5):
    palabras = texto.split()
    return [" ".join(palabras[i:i + n]) for i in range(0, len(palabras), n)] or [""]


def clip_subtitulado(e, g, mp3, salida, ancho, alto, pref):
    """Una imagen por cada trozo de frase, con el subtítulo encima, sincronizada por nº de palabras."""
    d = duracion(mp3) + 0.35
    partes = trozos(e["narracion"])
    total = sum(len(x.split()) for x in partes) or 1
    lista = salida.with_suffix(".txt")
    lineas = []
    for k, parte in enumerate(partes):
        png = SALIDA / f"{pref}_{k:02}.png"
        pintar(e, g, png, ancho, alto, subtitulo=parte)
        dur = d * len(parte.split()) / total
        lineas += [f"file '{png.name}'", f"duration {dur:.3f}"]
    lineas.append(f"file '{png.name}'")
    lista.write_text("\n".join(lineas) + "\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista.name,
                    "-i", mp3.name, "-vf", f"scale={ancho}:{alto},fps=25,format=yuv420p",
                    "-t", f"{d:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                    "-ar", "44100", "-b:a", "160k", "-af", "apad", salida.name], check=True, cwd=SALIDA)
    return d


def unir(clips, salida):
    lista = salida.with_suffix(".txt")
    lista.write_text("".join(f"file '{c.name}'\n" for c in clips))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
                    str(lista), "-c", "copy", "-movflags", "+faststart", str(salida)],
                   check=True, cwd=salida.parent)


def montar(g):
    SALIDA.mkdir(exist_ok=True)
    horiz, vert, capitulos, t = [], [], [], 0.0
    for i, e in enumerate(g["escenas"]):
        mp3 = SALIDA / f"v{i:02}.mp3"
        voz(e["narracion"], e.get("locutor", "A"), mp3)
        h = SALIDA / f"h{i:02}.mp4"
        d = clip_subtitulado(e, g, mp3, h, 1920, 1080, f"h{i:02}")
        horiz.append(h)
        capitulos.append((t, e.get("rotulo") or e["visual"]))
        t += d
        if e.get("short") or e["visual"] == "cierre":
            s = SALIDA / f"s{i:02}.mp4"
            clip_subtitulado(e, g, mp3, s, 1080, 1920, f"s{i:02}")
            vert.append(s)
    largo, corto = SALIDA / "video.mp4", SALIDA / "corto.mp4"
    unir(horiz, largo)
    unir(vert, corto)
    pintar({"visual": "portada", "rotulo": g["titulo_youtube"]}, g, SALIDA / "miniatura.png", 1280, 720)
    log(f"Vídeo {duracion(largo):.0f}s, corto {duracion(corto):.0f}s")
    g["_capitulos"] = capitulos_youtube(capitulos)
    return largo, corto


def capitulos_youtube(caps):
    """Capítulos de YouTube: el primero en 0:00, mínimo 10 s cada uno."""
    out = []
    for seg, titulo in caps:
        if out and seg - out[-1][0] < 10:
            continue
        out.append((seg if out else 0, titulo))
    if len(out) < 3:
        return ""
    return "\n".join(f"{int(s // 60)}:{int(s % 60):02d} {tit[:60]}" for s, tit in out)


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


def subir_instagram(video_url, caption, media_type="REELS"):
    token = os.environ["IG_TOKEN"].strip()
    if token.startswith("EAA"):
        # Token de usuario del sistema (inicio de sesión unificado): no caduca, va por graph.facebook.com
        base = f"https://graph.facebook.com/{CFG['version_api_instagram']}"
        uid = os.environ.get("IG_USER_ID", "").strip()
        if not uid:
            raise RuntimeError("Falta el secret IG_USER_ID (ID de la cuenta de Instagram)")
    else:
        token = renovar_token_ig(token)
        base = f"https://graph.instagram.com/{CFG['version_api_instagram']}"
        yo = requests.get(f"{base}/me", params={"fields": "user_id,username", "access_token": token},
                          timeout=60).json()
        uid = yo.get("user_id") or yo["id"]
    datos = {"media_type": media_type, "video_url": video_url, "access_token": token}
    if media_type == "REELS":
        datos.update({"caption": caption[:2200], "share_to_feed": "true"})
    r = requests.post(f"{base}/{uid}/media", data=datos, timeout=120).json()
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
    return pub.get("id")


def comentar_instagram(media_id, mensaje):
    """Deja un comentario del propio canal en una publicación suya (p. ej. aviso de la parte 2)."""
    token = os.environ["IG_TOKEN"].strip()
    base = (f"https://graph.facebook.com/{CFG['version_api_instagram']}" if token.startswith("EAA")
            else f"https://graph.instagram.com/{CFG['version_api_instagram']}")
    r = requests.post(f"{base}/{media_id}/comments", data={"message": mensaje, "access_token": token}, timeout=60)
    log("Comentario en Instagram:", r.status_code, r.text[:200])


# ---------------------------------------------------------------- Principal
def main():
    hoy = dt.date.today().isoformat()
    if hoy in CFG.get("dias_sin_publicar", []):
        log("Hoy no se publica (día bloqueado en config.json):", hoy)
        return
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
    tags_h = " ".join(g.get("hashtags", [])[:8])
    pregunta = g.get("pregunta_comentarios", "")
    descripcion = (f"{g['descripcion']}\n\n💬 {pregunta} Te leo en comentarios.\n\n"
                   f"📌 Capítulos:\n{g.get('_capitulos', '')}\n\n"
                   f"📄 Fuente: {url}\n\n🔔 Suscríbete: un vídeo nuevo cada día sobre tu dinero y sus escándalos.\n"
                   f"📲 Instagram: {CFG['usuario_instagram']}\n\n{tags_h} {CFG['hashtags']}")
    tags_yt = (g.get("palabras_clave", []) + g.get("tags", []))[:20]
    etiqueta = f"dia-{dt.date.today():%Y%m%d}-{int(time.time())}"
    textos = SALIDA / "textos_youtube.txt"
    textos.write_text(f"TITULO:\n{g['titulo_youtube']}\n\nDESCRIPCION:\n{descripcion}\n\n"
                      f"TITULO SHORT:\n{g['titulo_post'][:85]} #Shorts\n", encoding="utf-8")
    # El corto (y, si YouTube aun no esta auditado, todo lo demas) queda en una "release" de GitHub
    caption = (f"{g['titulo_post']}\n\n{g['texto_post']}\n\n💬 {pregunta}\n"
               f"🔁 Compártelo con quien siga pensando que no pasa nada\n📌 Guárdalo para tenerlo a mano\n\n"
               f"Fuente: {g.get('fuente_corta', 'BOE')}\n\n{tags_h} {CFG['hashtags']}")
    with open(textos, "a", encoding="utf-8") as fh:
        fh.write(f"\nTEXTO INSTAGRAM:\n{caption}\n")
    adjuntos = [corto] if (CFG["youtube_auditado"] and os.environ.get("IG_TOKEN")) else [corto, largo, SALIDA / "miniatura.png", textos]
    video_url = url_publica(adjuntos, etiqueta)
    if os.environ.get("IG_TOKEN"):
        subir_instagram(video_url, caption)
    else:
        log("Sin IG_TOKEN: el Reel y su texto quedan en Releases para subirlos a mano")
    if CFG["youtube_auditado"]:
        yt = youtube()
        subir_youtube(yt, largo, g["titulo_youtube"], descripcion, tags_yt, SALIDA / "miniatura.png")
        subir_youtube(yt, corto, f"{g['titulo_post'][:85]} #Shorts", descripcion, tags_yt)
    else:
        log("YouTube pendiente de auditoria: video listo en la pestana Releases de GitHub")
    usados = json.loads(HISTORIAL.read_text()) if HISTORIAL.exists() else []
    HISTORIAL.write_text(json.dumps((usados + [tema["id"]])[-500:], ensure_ascii=False))
    log("Hecho")


if __name__ == "__main__":
    main()
