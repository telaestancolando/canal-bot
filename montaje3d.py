"""Montaje final de los vídeos 3D: une los segmentos renderizados y dibuja encima, fotograma a
fotograma, la marca, las tarjetas de título, los subtítulos tipo karaoke, las etiquetas que
siguen a cada pieza, la lluvia y los relámpagos, las tarjetas de comparación y el audio."""
import glob
import json
import math
import os
import random
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

RAIZ = Path(__file__).resolve().parent


def fuente(peso, tam):
    for p in (RAIZ / "assets" / f"Inter-{peso}.otf", RAIZ / f"Inter-{peso}.otf",
              Path(f"/usr/share/fonts/opentype/inter/Inter-{peso}.otf"),
              Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        if p.exists():
            return ImageFont.truetype(str(p), int(tam))
    return ImageFont.load_default()


def hexrgb(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


# ---------------------------------------------------------------- capas fijas

def capa_marca(W, H, canal, acento):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    v = H > W
    s = W / (1080 if v else 1920)
    x, y = int(46 * s), int((150 if v else 44) * s)
    f = fuente("Black", 22 * s)
    txt = " ".join(canal.upper())
    d.text((x + 1, y + 1), txt, font=f, fill=(0, 0, 0, 120))
    d.text((x, y), txt, font=f, fill=(255, 255, 255, 235))
    d.rectangle([x, y + int(30 * s), x + int(36 * s), y + int(34 * s)], fill=hexrgb(acento))
    return im


def capa_vineta(W, H, fuerza=0.35):
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    a = np.clip((r - 0.75) / 0.7, 0, 1) ** 1.6 * 255 * fuerza
    arr = np.zeros((H, W, 4), np.uint8)
    arr[..., 3] = a.astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def tarjeta(W, H, etiqueta, titulo, acento, numero=None):
    """Tarjeta crema con etiqueta pequeña y título grande (estilo 'ALA / 02 · Sustentación')."""
    v = H > W
    s = W / (1080 if v else 1920)
    f_e = fuente("Bold", 17 * s)
    f_t = fuente("Black", (58 if v else 52) * s)
    texto = titulo if numero is None else numero
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = tmp.textlength(texto, font=f_t)
    ew = tmp.textlength(etiqueta.upper(), font=f_e) + 24 * s
    w = int(max(tw, ew) + 44 * s)
    h = int((112 if v else 104) * s)
    im = Image.new("RGBA", (w + 12, h + 14), (0, 0, 0, 0))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([6, 8, w + 6, h + 8], radius=int(8 * s), fill=(0, 0, 0, 90))
    im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(5)))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w, h], radius=int(8 * s), fill=hexrgb("#F3EFE4"))
    if etiqueta:
        d.rectangle([int(20 * s), int(20 * s), int(32 * s), int(24 * s)], fill=hexrgb(acento))
        d.text((int(40 * s), int(13 * s)), etiqueta.upper(), font=f_e, fill=hexrgb("#2A2A2A"))
    d.text((int(20 * s), int(36 * s)), texto, font=f_t, fill=hexrgb("#111418"))
    return im


def etiqueta_png(texto, s, color_fondo="#FFFFFF", color_texto="#111418"):
    f = fuente("Bold", 26 * s)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    w = int(tmp.textlength(texto, font=f) + 26 * s)
    h = int(44 * s)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(6 * s), fill=hexrgb(color_fondo), outline=(20, 20, 20, 255), width=max(1, int(2 * s)))
    d.text((w / 2, h / 2), texto, font=f, fill=hexrgb(color_texto), anchor="mm")
    return im


def perfil_naca(n=60, t=0.12, m=0.02, p=0.4):
    b = np.linspace(0, math.pi, n)
    x = (1 - np.cos(b)) / 2
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.126 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 - 0.1036 * x ** 4)
    yc = np.where(x < p, m / p ** 2 * (2 * p * x - x ** 2), m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x ** 2))
    return np.concatenate([np.stack([x, yc + yt], 1)[::-1], np.stack([x, yc - yt], 1)[1:]])


def tarjeta_comparacion(W, H, filas, acento):
    """Panel oscuro con 2 filas: título + perfil de ala con su ángulo (sin preguntas al público)."""
    v = H > W
    s = W / (1080 if v else 1920)
    w = int(W * (0.88 if v else 0.6))
    hf = int(190 * s)
    im = Image.new("RGBA", (w, hf * len(filas) + int(20 * s) * (len(filas) - 1)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    letras = "ABCD"
    for i, fila in enumerate(filas[:3]):
        y0 = i * (hf + int(20 * s))
        d.rectangle([0, y0, w - 1, y0 + hf], fill=(14, 18, 24, 215), outline=(235, 238, 242, 200), width=max(1, int(2 * s)))
        d.text((int(28 * s), y0 + hf / 2), letras[i], font=fuente("Black", 110 * s), fill=(240, 240, 240, 255), anchor="lm")
        tit = fila.get("titulo", "")
        d.text((int(150 * s), y0 + int(26 * s)), tit, font=fuente("Black", 34 * s), fill=(245, 245, 245, 255))
        if fila.get("texto"):
            d.text((int(150 * s), y0 + int(72 * s)), fila["texto"], font=fuente("Bold", 24 * s), fill=(170, 178, 188, 255))
        ang = math.radians(float(fila.get("angulo", 0)))
        pts = perfil_naca()
        cx, cy, cl = w - int(250 * s), y0 + hf * 0.68, 200 * s
        poly = []
        for x, y in pts:
            xr, yr = (x - 0.25) * cl, -y * cl
            poly.append((cx + xr * math.cos(ang) - yr * math.sin(ang), cy + xr * math.sin(ang) + yr * math.cos(ang)))
        for k in range(0, int(w - 300 * s), int(14 * s)):  # línea del viento
            d.line([(int(150 * s) + k, cy), (int(150 * s) + k + int(7 * s), cy)], fill=(120, 128, 138, 255), width=max(1, int(2 * s)))
        d.polygon(poly, fill=(240, 240, 236, 255))
        if fila.get("angulo") is not None:
            d.text((cx + 110 * s, cy - 50 * s), f"{fila['angulo']}°", font=fuente("Bold", 26 * s), fill=hexrgb(acento), anchor="mm")
    return im


def capas_lluvia(W, H, n=8, semilla=4):
    rng = random.Random(semilla)
    capas = []
    gotas = [(rng.uniform(0, W), rng.uniform(0, H), rng.uniform(0.5, 1.0)) for _ in range(int(W * H / 5000))]
    for k in range(n):
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for (x, y, z) in gotas:
            yy = (y + k * H / n * 0.9 * z) % H
            xx = (x + k * W / n * 0.12 * z) % W
            L = 28 * z * W / 1080
            d.line([(xx, yy), (xx - L * 0.22, yy + L)], fill=(210, 220, 235, int(90 * z)), width=max(1, int(2 * z)))
        capas.append(im)
    return capas


# ---------------------------------------------------------------- subtítulos karaoke

def grupos_palabras(palabras, max_car=24, max_pal=5):
    grupos, act = [], []
    for w in palabras:
        txt = " ".join(x[0] for x in act + [w])
        if act and (len(txt) > max_car or len(act) >= max_pal or re.search(r"[.!?;:]$", act[-1][0])):
            grupos.append(act); act = []
        act.append(w)
    if act:
        grupos.append(act)
    return grupos


def dibujar_karaoke(lienzo, grupo, t, acento):
    W, H = lienzo.size
    v = H > W
    s = W / (1080 if v else 1920)
    f = fuente("Black", (52 if v else 46) * s)
    d = ImageDraw.Draw(lienzo)
    esp = d.textlength(" ", font=f)
    anchos = [d.textlength(w[0], font=f) for w in grupo]
    total = sum(anchos) + esp * (len(grupo) - 1)
    if total > W * 0.9:
        f = fuente("Black", f.size * W * 0.9 / total)
        esp = d.textlength(" ", font=f)
        anchos = [d.textlength(w[0], font=f) for w in grupo]
        total = sum(anchos) + esp * (len(grupo) - 1)
    x = (W - total) / 2
    y = H * (0.66 if v else 0.86)
    for (w, t0, t1), aw in zip(grupo, anchos):
        if t >= t1:
            col = (255, 255, 255, 255)
        elif t >= t0:
            col = (255, 255, 255, 255)
        else:
            col = (165, 172, 182, 255)
        for dx, dy in ((0, 3), (2, 3), (-2, 3)):
            d.text((x + dx, y + dy), w, font=f, fill=(0, 0, 0, 150), anchor="ls")
        d.text((x, y), w, font=f, fill=col, anchor="ls")
        if t0 <= t < t1 + 0.05:
            d.rectangle([x, y + 8 * s, x + aw, y + 13 * s], fill=hexrgb(acento))
        x += aw + esp


# ---------------------------------------------------------------- montaje

def unir_segmentos(dir_frames, tarea_id, destino):
    segs = sorted(glob.glob(os.path.join(dir_frames, f"seg_{tarea_id}_*.mp4")))
    if not segs:
        raise RuntimeError(f"Faltan los segmentos de {tarea_id}")
    lista = destino.with_suffix(".txt")
    lista.write_text("".join(f"file '{os.path.abspath(s)}'\n" for s in segs))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lista),
                    "-c", "copy", str(destino)], check=True)
    anclas = {}
    for a in sorted(glob.glob(os.path.join(dir_frames, f"anclas_{tarea_id}_*.json"))):
        anclas.update({int(k): v for k, v in json.load(open(a)).items()})
    return anclas


def montar(escenas, orient, dir_frames, salida, cfg, tmp, fps=24, W=None, H=None):
    """escenas: lista de dicts con tarea_id, frames, audio, palabras [(w,t0,t1)], tarjeta,
    etiquetas, tormenta, puntos, comparacion, cierre. Devuelve la ruta del mp4."""
    tmp = Path(tmp); tmp.mkdir(parents=True, exist_ok=True)
    if W is None:
        W, H = (1920, 1080) if orient == "h" else (1080, 1920)
    acento = cfg["colores"]["acento"]
    s = W / (1080 if H > W else 1920)
    marca = capa_marca(W, H, cfg["canal"], acento)
    vineta = capa_vineta(W, H)
    lluvia = None
    video_mudo = tmp / f"mudo_{orient}.mp4"
    enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium",
                            "-crf", "19", "-pix_fmt", "yuv420p", str(video_mudo)], stdin=subprocess.PIPE)
    tarjeta_prev = None
    rng = random.Random(7)
    for i, e in enumerate(escenas):
        base = tmp / f"base_{e['tarea_id']}.mp4"
        anclas = unir_segmentos(dir_frames, e["tarea_id"], base)
        nf = e["frames"]
        dec = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", str(base), "-vf",
                                f"scale={W}:{H}:flags=lanczos", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                               stdout=subprocess.PIPE)
        tj = e.get("tarjeta") or {}
        clave_tj = (tj.get("etiqueta"), tj.get("titulo"))
        animar_tj = clave_tj != tarjeta_prev and bool(tj.get("titulo"))
        tarjeta_prev = clave_tj
        m = re.match(r"^\s*([\d.,]+)(.*)$", tj.get("titulo", "") or "")
        img_tj = tarjeta(W, H, tj.get("etiqueta", ""), tj["titulo"], acento) if tj.get("titulo") else None
        grupos = grupos_palabras(e.get("palabras") or [])
        etiq = [(x, etiqueta_png(x["texto"], s)) for x in (e.get("etiquetas") or []) if x.get("texto")]
        puntos = [(f"p{k}", etiqueta_png(p.get("nombre", ""), s, "#E8772E", "#FFFFFF"))
                  for k, p in enumerate(e.get("puntos") or []) if p.get("nombre")]
        comp = tarjeta_comparacion(W, H, e["comparacion"], acento) if e.get("comparacion") else None
        if e.get("tormenta") and lluvia is None:
            lluvia = capas_lluvia(W, H)
        rayos = set()
        if e.get("tormenta"):
            for _ in range(max(1, nf // 120)):
                f0 = rng.randrange(10, max(11, nf - 10))
                rayos |= {f0, f0 + 1, f0 + 4}
        for f in range(nf):
            raw = dec.stdout.read(W * H * 3)
            if len(raw) < W * H * 3:
                raw = ultimo if f > 0 else bytes(W * H * 3)
            ultimo = raw
            t = f / fps
            fr = Image.frombuffer("RGB", (W, H), raw, "raw", "RGB", 0, 1).convert("RGBA")
            if f in rayos:
                fr = Image.blend(fr, Image.new("RGBA", (W, H), (230, 235, 255, 255)), 0.45)
            if lluvia and e.get("tormenta"):
                fr.alpha_composite(lluvia[f % len(lluvia)])
            fr.alpha_composite(vineta)
            fr.alpha_composite(marca)
            # etiquetas sobre piezas
            a = anclas.get(f + 1, {})
            for k, (x, im) in enumerate(etiq):
                pos = a.get(x.get("parte", ""))
                if not pos or not pos[2] or t < 0.6 + 0.25 * k:
                    continue
                alpha = ease((t - 0.6 - 0.25 * k) / 0.3)
                px, py = pos[0] * W, pos[1] * H
                lado = -1 if px > W * 0.55 else 1
                lx = px + lado * 110 * s - (im.width if lado < 0 else 0)
                ly = py - 150 * s - k * 60 * s
                d = ImageDraw.Draw(fr)
                d.ellipse([px - 7 * s, py - 7 * s, px + 7 * s, py + 7 * s], fill=(255, 255, 255, int(255 * alpha)), outline=(20, 20, 20, 255))
                ex = lx + (im.width if lado < 0 else 0)
                d.line([(px, py), (ex, ly + im.height / 2)], fill=(255, 255, 255, int(230 * alpha)), width=max(2, int(3 * s)))
                if alpha >= 0.99:
                    fr.alpha_composite(im, (int(lx), int(ly)))
                else:
                    tmpim = im.copy(); tmpim.putalpha(tmpim.getchannel("A").point(lambda v: int(v * alpha)))
                    fr.alpha_composite(tmpim, (int(lx), int(ly)))
            for (nombre, im) in puntos:
                pos = a.get(nombre)
                if pos and pos[2] and t > 0.5:
                    px, py = pos[0] * W, pos[1] * H
                    fr.alpha_composite(im, (int(px - im.width / 2), int(py - im.height - 18 * s)))
            # tarjeta de título
            if img_tj is not None:
                x0, y0 = int(46 * s), int((205 if H > W else 100) * s)
                if animar_tj and t < 0.5:
                    k = ease(t / 0.45)
                    x0 = int(-img_tj.width + (x0 + img_tj.width) * k)
                if m and animar_tj and t < 1.1 and t >= 0.0:
                    # cifra que cuenta hasta el valor
                    try:
                        val = float(m.group(1).replace(".", "").replace(",", "."))
                        dec_n = len(m.group(1).split(",")[1]) if "," in m.group(1) else 0
                        cur = val * ease(t / 1.0)
                        txt = (f"{cur:,.{dec_n}f}").replace(",", "X").replace(".", ",").replace("X", ".") + m.group(2)
                        img = tarjeta(W, H, tj.get("etiqueta", ""), tj["titulo"], acento, numero=txt)
                    except ValueError:
                        img = img_tj
                else:
                    img = img_tj
                fr.alpha_composite(img, (max(x0, -img.width), y0))
            if comp is not None and t > 0.3:
                k = ease((t - 0.3) / 0.4)
                ci = comp if k >= 0.99 else comp.copy()
                if k < 0.99:
                    ci.putalpha(ci.getchannel("A").point(lambda v: int(v * k)))
                fr.alpha_composite(ci, ((W - comp.width) // 2, int(H * (0.24 if H > W else 0.18))))
            if e.get("cierre"):
                d = ImageDraw.Draw(fr)
                d.text((W / 2, H * 0.45), cfg.get("usuario_instagram", ""), font=fuente("Black", 64 * s),
                       fill=hexrgb(acento), anchor="mm")
            for g in grupos:
                if g[0][1] - 0.05 <= t < (g[-1][2] + 0.25):
                    dibujar_karaoke(fr, g, t, acento)
                    break
            enc.stdin.write(fr.convert("RGB").tobytes())
        dec.stdout.close(); dec.wait()
    enc.stdin.close(); enc.wait()
    # audio: cada escena rellenada con silencio hasta su duración exacta
    entradas, filtros = [], []
    for i, e in enumerate(escenas):
        entradas += ["-i", str(e["audio"])]
        filtros.append(f"[{i}:a]aresample=44100,apad,atrim=0:{e['frames'] / fps:.4f}[a{i}]")
    filtros.append("".join(f"[a{i}]" for i in range(len(escenas))) + f"concat=n={len(escenas)}:v=0:a=1[voz]")
    # fondo: rumor grave de cabina muy bajito, para que el vídeo no suene "vacío"
    total = sum(e["frames"] for e in escenas) / fps
    filtros.append(f"anoisesrc=color=brown:amplitude=0.7:r=44100,lowpass=f=300,highpass=f=40,volume=0.15,"
                   f"atrim=0:{total:.3f},afade=t=in:d=1,afade=t=out:st={max(total - 1.5, 0):.3f}:d=1.5[ruido]")
    filtros.append("[voz][ruido]amix=inputs=2:duration=first:normalize=0[aout]")
    audio = tmp / f"audio_{orient}.m4a"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *entradas, "-filter_complex", ";".join(filtros),
                    "-map", "[aout]", "-c:a", "aac", "-b:a", "160k", str(audio)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_mudo), "-i", str(audio), "-c:v", "copy",
                    "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(salida)], check=True)
    return salida


def miniatura(video, titulo, destino, acento):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.5", "-i", str(video), "-frames:v", "1",
                    str(destino)], check=True)
    im = Image.open(destino).convert("RGBA")
    if im.height > im.width:  # vídeo vertical: miniatura 9:16
        im = im.resize((720, 1280))
    else:
        im = im.resize((1280, 720))
    W, H = im.size
    capa = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=(5, 10, 20, int(190 * max(0, (y - H * 0.45) / (H * 0.55)))))
    im = Image.alpha_composite(im, capa)
    d = ImageDraw.Draw(im)
    import textwrap
    lineas = textwrap.wrap(titulo.upper(), 22 if W > H else 14)[:4]
    f = fuente("Black", 78 if W > H else 70)
    y = H - 20 - 92 * len(lineas)
    for ln in lineas:
        d.text((52, y + 4), ln, font=f, fill=(0, 0, 0, 200))
        d.text((48, y), ln, font=f, fill=(255, 255, 255, 255))
        y += 92
    d.rectangle([48, y - 8, 160, y], fill=hexrgb(acento))
    im.convert("RGB").save(destino)
    return destino
