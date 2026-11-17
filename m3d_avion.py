"""Generador paramétrico de aviones de línea para Blender (bpy).
Morro en x=0, cola en x=L: el avión avanza hacia -X. Unidades: metros."""
import math, os
import numpy as np
import bpy, bmesh
from PIL import Image, ImageDraw, ImageFont

FUENTE = None
for f in ("assets/Inter-Black.otf", "assets/Inter-Bold.otf",
          "/usr/share/fonts/opentype/inter/Inter-Black.otf",
          "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
    if os.path.exists(f):
        FUENTE = f
        break


def hex2rgb(h, a=1.0):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return (*c, a)


def malla(nombre, verts, caras, uvs=None, suave=True, padre=None):
    me = bpy.data.meshes.new(nombre)
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in caras])
    if uvs is not None:
        uvl = me.uv_layers.new(name="UVMap")
        for poly in me.polygons:
            for li, vi in zip(poly.loop_indices, poly.vertices):
                uvl.data[li].uv = uvs[vi]
    me.validate()
    me.update()
    ob = bpy.data.objects.new(nombre, me)
    bpy.context.scene.collection.objects.link(ob)
    if suave:
        for p in me.polygons:
            p.use_smooth = True
    if padre:
        ob.parent = padre
    bm = bmesh.new(); bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me); bm.free()
    return ob


def material(nombre, color, rough=0.4, metal=0.0, coat=0.0, emis=None, emis_f=0.0):
    m = bpy.data.materials.new(nombre)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = hex2rgb(color) if isinstance(color, str) else color
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    p.inputs["Coat Weight"].default_value = coat
    if emis:
        p.inputs["Emission Color"].default_value = hex2rgb(emis)
        p.inputs["Emission Strength"].default_value = emis_f
    return m


def material_imagen(nombre, ruta, rough=0.32, coat=0.25):
    m = bpy.data.materials.new(nombre)
    m.use_nodes = True
    nt = m.node_tree
    p = nt.nodes["Principled BSDF"]
    t = nt.nodes.new("ShaderNodeTexImage")
    t.image = bpy.data.images.load(ruta)
    t.interpolation = "Cubic"
    nt.links.new(t.outputs["Color"], p.inputs["Base Color"])
    p.inputs["Roughness"].default_value = rough
    p.inputs["Coat Weight"].default_value = coat
    return m


def asignar(ob, mat):
    ob.data.materials.clear()
    ob.data.materials.append(mat)


# ---------------------------------------------------------------- perfiles

def naca(cod="2412", n=40):
    m = int(cod[0]) / 100; p = int(cod[1]) / 10; t = int(cod[2:]) / 100
    beta = np.linspace(0, math.pi, n)
    x = (1 - np.cos(beta)) / 2
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.126 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 - 0.1036 * x ** 4)
    yc = np.where(x < p, m / max(p, 1e-6) ** 2 * (2 * p * x - x ** 2),
                  m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x ** 2)) if p > 0 else 0 * x
    sup = list(zip(x[::-1], (yc + yt)[::-1]))
    inf = list(zip(x[1:-1], (yc - yt)[1:-1]))
    return sup + inf  # anillo cerrado, empieza en el borde de salida


def superficie(nombre, secciones, eje="z", perfil="2412", espejo=True, padre=None):
    """secciones: lista de (x_ba, y, z, cuerda, escala_espesor). eje: normal del perfil."""
    af = naca(perfil)
    n = len(af)
    vs, fs = [], []
    for (x0, y0, z0, c, e) in secciones:
        for (ax, ay) in af:
            if eje == "z":
                vs.append((x0 + ax * c, y0, z0 + ay * c * e))
            else:
                vs.append((x0 + ax * c, ay * c * e + y0, z0))
    ns = len(secciones)
    for i in range(ns - 1):
        for j in range(n):
            a = i * n + j; b = i * n + (j + 1) % n
            fs.append((a, b, b + n, a + n))
    # tapas
    c0 = len(vs); vs.append(tuple(np.mean(vs[0:n], axis=0)))
    c1 = len(vs); vs.append(tuple(np.mean(vs[(ns - 1) * n:ns * n], axis=0)))
    for j in range(n):
        fs.append((j, (j + 1) % n, c0))
        a = (ns - 1) * n
        fs.append((a + j, a + (j + 1) % n, c1))
    obs = [malla(nombre, vs, fs, padre=padre)]
    if espejo:
        vs2 = [(x, -y, z) for (x, y, z) in vs]
        obs.append(malla(nombre + "_R", vs2, fs, padre=padre))
    return obs


def torno(nombre, perfil, nseg=48, padre=None, centro=(0, 0, 0)):
    """Revoluciona un perfil [(x, r)] alrededor del eje X."""
    vs, fs = [], []
    n = len(perfil)
    for i, (x, r) in enumerate(perfil):
        for k in range(nseg):
            a = 2 * math.pi * k / nseg
            vs.append((centro[0] + x, centro[1] + r * math.cos(a), centro[2] + r * math.sin(a)))
    for i in range(n - 1):
        for k in range(nseg):
            a = i * nseg + k; b = i * nseg + (k + 1) % nseg
            fs.append((a, b, b + nseg, a + nseg))
    ob = malla(nombre, vs, fs, padre=padre)
    return ob


# ---------------------------------------------------------------- librea

def librea(ruta, L, R, nariz, cfg):
    W, H = 4096, 1024
    blanco = cfg.get("fuselaje", "#F4F5F7")
    img = Image.new("RGB", (W, H), blanco)
    d = ImageDraw.Draw(img)
    def px(u): return int(u / L * W)
    def py(th): return int((1 - th / (2 * math.pi)) * H)
    # panza
    panza = cfg.get("panza", "#C9CED6")
    d.rectangle([0, py(0.62), W, H], fill=panza)
    d.rectangle([0, 0, W, py(2 * math.pi - 0.62)], fill=panza)
    # franja (cheatline) a ambos lados
    franja = cfg.get("franja", "#14365A")
    acento = cfg.get("acento", "#E8772E")
    for th0 in (1.25, 2 * math.pi - 1.25):
        d.rectangle([px(nariz * 0.9), py(th0 + 0.035), px(L * 0.82), py(th0 - 0.035)], fill=franja)
    for th0 in (1.36, 2 * math.pi - 1.36):
        d.rectangle([px(nariz * 1.1), py(th0 + 0.012), px(L * 0.78), py(th0 - 0.012)], fill=acento)
    # ventanillas
    paso = 0.53
    x = nariz * 1.35
    while x < L * 0.80:
        for th in (1.78, 2 * math.pi - 1.78):
            cx, cy = px(x), py(th)
            d.rounded_rectangle([cx - 11, cy - 15, cx + 11, cy + 15], 9, fill="#2B3440")
        x += paso
    # puertas
    for xd in (nariz * 1.12, L * 0.80):
        for th in (1.75, 2 * math.pi - 1.75):
            cx, cy = px(xd), py(th)
            d.rounded_rectangle([cx - 14, cy - 60, cx + 14, cy + 75], 8, outline="#9AA3AE", width=3)
    # cabina: parabrisas
    # parabrisas: banda oscura de 4 paneles por lado sobre el morro
    for lado in (1, -1):
        bordes = [0.03, 0.46, 0.92, 1.30, 1.56]
        for k in range(4):
            a0, a1 = bordes[k] + 0.03, bordes[k + 1] - 0.03
            u_f0 = nariz * (0.43 + 0.05 * k)
            u_b0 = nariz * (0.58 + 0.07 * k)
            pts = [(px(u_f0), py(math.pi + lado * a0)), (px(u_b0 - nariz * 0.03), py(math.pi + lado * a0)),
                   (px(u_b0), py(math.pi + lado * a1)), (px(u_f0 + nariz * 0.03), py(math.pi + lado * a1))]
            d.polygon(pts, fill="#1A2029")
    # texto
    txt = cfg.get("texto", "CABINA ABIERTA")
    if txt and FUENTE:
        f = ImageFont.truetype(FUENTE, 92)
        bb = d.textbbox((0, 0), txt, font=f)
        tw, th_ = bb[2] - bb[0], bb[3] - bb[1]
        capa = Image.new("RGBA", (tw + 20, th_ + 30), (0, 0, 0, 0))
        dc = ImageDraw.Draw(capa)
        dc.text((10 - bb[0], 10 - bb[1]), txt, font=f, fill=franja)
        u_c = cfg.get("texto_x", 0.40) * L
        # lado -y (v=0.75 aprox): voltear vertical. lado +y: voltear horizontal
        for th, cap in ((2 * math.pi - 2.02, capa.transpose(Image.FLIP_TOP_BOTTOM)),
                        (2.02, capa.transpose(Image.FLIP_LEFT_RIGHT))):
            img.paste(cap, (px(u_c) - cap.width // 2, py(th) - cap.height // 2), cap)
    img.save(ruta)


def librea_deriva(ruta, cfg):
    W = H = 1024
    img = Image.new("RGB", (W, H), cfg.get("deriva", "#E8772E"))
    d = ImageDraw.Draw(img)
    # simple emblema: avión estilizado en blanco
    d.ellipse([380, 380, 640, 640], outline="#FFFFFF", width=26)
    d.polygon([(510, 420), (530, 470), (600, 500), (600, 520), (530, 510), (525, 580), (550, 600),
               (550, 612), (510, 600), (470, 612), (470, 600), (495, 580), (490, 510), (420, 520),
               (420, 500), (490, 470)], fill="#FFFFFF")
    img.save(ruta)


# ---------------------------------------------------------------- avión

TIPOS = {
    # L, diámetro, envergadura, motores, posición ala, cola en T, hélices
    "a320": dict(L=37.6, D=3.95, S=35.8, motores=2, ala="baja", colaT=False, helice=False),
    "b737": dict(L=39.5, D=3.76, S=35.9, motores=2, ala="baja", colaT=False, helice=False),
    "b757": dict(L=47.3, D=3.76, S=38.0, motores=2, ala="baja", colaT=False, helice=False),
    "b777": dict(L=63.7, D=6.2, S=64.8, motores=2, ala="baja", colaT=False, helice=False),
    "a330": dict(L=58.8, D=5.64, S=60.3, motores=2, ala="baja", colaT=False, helice=False),
    "b747": dict(L=70.6, D=6.5, S=64.4, motores=4, ala="baja", colaT=False, helice=False, joroba=True),
    "a340": dict(L=63.7, D=5.64, S=63.5, motores=4, ala="baja", colaT=False, helice=False),
    "rj85": dict(L=28.6, D=3.56, S=26.3, motores=4, ala="alta", colaT=True, helice=False),
    "atr72": dict(L=27.2, D=2.9, S=27.1, motores=2, ala="alta", colaT=True, helice=True),
    "md80": dict(L=45.1, D=3.34, S=32.9, motores=2, ala="baja", colaT=True, helice=False, motor_cola=True),
    "dc10": dict(L=55.5, D=6.0, S=50.4, motores=2, ala="baja", colaT=False, helice=False, motor_deriva=True),
    "c130": dict(L=29.8, D=4.3, S=40.4, motores=4, ala="alta", colaT=False, helice=True),
    "a380": dict(L=72.7, D=7.1, S=79.8, motores=4, ala="baja", colaT=False, helice=False, joroba=True),
}


def crear_avion(tipo="a320", cfg=None, tren=True, dir_tmp="/tmp/m3d"):
    cfg = cfg or {}
    os.makedirs(dir_tmp, exist_ok=True)
    p = dict(TIPOS.get(tipo, TIPOS["a320"]))
    L, D, S = p["L"], p["D"], p["S"]
    R = D / 2
    raiz = bpy.data.objects.new("AVION", None)
    bpy.context.scene.collection.objects.link(raiz)

    # ---- fuselaje
    nariz = 1.55 * D
    cola = 0.30 * L
    nseg, nr = 140, 64
    vs, uvs, fs = [], [], []
    ts = np.linspace(0, 1, nseg) ** 1.0
    xs = []
    for t in np.linspace(0, 1, nseg):
        # más densidad en el morro
        xs.append(L * (t ** 1.35 if t < 0.25 else t) if False else L * t)
    xs = sorted(set([nariz * (1 - math.cos(a)) for a in np.linspace(0, math.pi / 2, 40)] +
                    list(np.linspace(nariz, L, nseg - 40))))
    joroba = p.get("joroba", False)
    for i, x in enumerate(xs):
        if x <= nariz:
            s = x / nariz
            z0 = -0.32 * R
            gt = (1 - (1 - s) ** 1.7) ** 0.75
            gb = (1 - (1 - s) ** 2.6) ** 0.5
            top = z0 + (R - z0) * gt
            bot = z0 - (R + z0) * gb
            zc = (top + bot) / 2; rz = (top - bot) / 2
            ry = R * (1 - (1 - s) ** 2.0) ** 0.6
        elif x >= L - cola:
            s = (x - (L - cola)) / cola
            top = R * (1 - 0.30 * s ** 1.8)
            bot = -R + (1.55 * R) * s ** 1.25
            zc = (top + bot) / 2
            rz = (top - bot) / 2
            ry = R * (1 - 0.86 * s ** 1.15)
            rz = max(rz, 0.05); ry = max(ry, 0.05)
        else:
            ry = rz = R; zc = 0
        top_extra = 0
        if joroba and x < L * 0.33:
            s = x / (L * 0.33)
            top_extra = R * 0.45 * math.sin(min(1, s * 1.15) * math.pi) ** 0.6 if s > 0.05 else 0
        for k in range(nr):
            th = 2 * math.pi * k / nr
            z = zc - rz * math.cos(th)
            if top_extra and math.cos(th) < 0:
                z += top_extra * (-math.cos(th)) ** 2
            vs.append((x, ry * math.sin(th), z))
            uvs.append((x / L, th / (2 * math.pi)))
        # costura UV: duplicamos el primero al final
        vs.append(vs[-nr]); uvs.append((x / L, 1.0))
    nrr = nr + 1
    for i in range(len(xs) - 1):
        for k in range(nr):
            a = i * nrr + k
            fs.append((a, a + 1, a + 1 + nrr, a + nrr))
    # cierre de cola
    cz = len(vs); last = (len(xs) - 1) * nrr
    vs.append((L + 0.05, 0, np.mean([v[2] for v in vs[last:last + nr]])))
    uvs.append((1.0, 0.5))
    for k in range(nr):
        fs.append((last + k, last + k + 1, cz))
    fus = malla("fuselaje", vs, fs, uvs, padre=raiz)
    tex = os.path.join(dir_tmp, f"librea_{tipo}.png")
    librea(tex, L, R, nariz, cfg)
    asignar(fus, material_imagen("pintura_fus", tex))

    blanco = material("blanco", cfg.get("fuselaje", "#F2F3F5"), rough=0.33, coat=0.2)
    gris_ala = material("ala", "#D9DDE2", rough=0.38, metal=0.15)
    acento = material("acento", cfg.get("motor", cfg.get("acento", "#E8772E")), rough=0.3, coat=0.3)
    metal = material("metal", "#B8BDC4", rough=0.22, metal=1.0)
    oscuro = material("oscuro", "#1B1F25", rough=0.5)
    goma = material("goma", "#141414", rough=0.85)
    deriva_m = None

    # ---- alas
    semi = S / 2
    alta = p["ala"] == "alta"
    z_ala = R * 0.72 if alta else -R * 0.62
    x_ala = L * (0.40 if not alta else 0.42)
    c_raiz = 0.16 * L if not alta else 0.115 * L
    flecha = math.radians(27 if not alta else 15)
    dih = math.radians(5.5 if not alta else -2)
    def sec(y, c, e=1.0):
        return (x_ala + (y - R * 0.6) * math.tan(flecha) + (c_raiz - c) * 0.25,
                y, z_ala + (y - R * 0.6) * math.tan(dih), c, e)
    secs = [sec(R * 0.4, c_raiz, 1.15), sec(R * 0.9, c_raiz * 0.97, 1.1),
            sec(semi * 0.36, c_raiz * 0.62), sec(semi * 0.98, c_raiz * 0.24, 0.9)]
    alas = superficie("ala", secs, perfil="2414", padre=raiz)
    for a in alas:
        asignar(a, gris_ala)
    # sharklets
    if not p["helice"] and not alta:
        xt, yt, zt, ct, _ = secs[-1]
        sh = [(xt + 0.05 * ct, yt, zt, ct * 0.9, 0.0), (xt + 0.6 * ct, yt + 0.25, zt + 1.6, ct * 0.35, 0)]
        sh = superficie("sharklet", [(a, b, c, d, 1) for a, b, c, d, _ in sh], eje="y", perfil="0010", padre=raiz)
        for s_ in sh:
            asignar(s_, blanco)
    info = {"L": L, "R": R, "semi": semi, "ala_x": x_ala, "ala_z": z_ala, "c_raiz": c_raiz,
            "flecha": flecha, "dih": dih, "secs": secs}

    # ---- estabilizadores
    x_ht = L * 0.86
    colaT = p["colaT"]
    zt_top = R * 0.6
    alt_der = 0.16 * L if not colaT else 0.17 * L
    c_der = 0.17 * L
    fsecs = [(L - cola * 0.62, 0, zt_top - 0.4, c_der, 1),
             (L - cola * 0.62 + alt_der * math.tan(math.radians(38)), 0, zt_top + alt_der, c_der * 0.42, 1)]
    if colaT:
        fsecs[1] = (fsecs[0][0] + alt_der * math.tan(math.radians(30)), 0, zt_top + alt_der, c_der * 0.55, 1)
    der = superficie("deriva", fsecs, eje="y", perfil="0012", espejo=False, padre=raiz)[0]
    # UV plana para la deriva
    tex2 = os.path.join(dir_tmp, f"deriva_{tipo}.png")
    librea_deriva(tex2, cfg)
    me = der.data
    uvl = me.uv_layers.new(name="UVMap")
    xmin = min(v.co.x for v in me.vertices); xmax = max(v.co.x for v in me.vertices)
    zmin = min(v.co.z for v in me.vertices); zmax = max(v.co.z for v in me.vertices)
    for poly in me.polygons:
        for li, vi in zip(poly.loop_indices, poly.vertices):
            co = me.vertices[vi].co
            uvl.data[li].uv = ((co.x - xmin) / (xmax - xmin), (co.z - zmin) / (zmax - zmin))
    asignar(der, material_imagen("pintura_deriva", tex2))
    if colaT:
        xh, zh = fsecs[1][0] - 0.3, fsecs[1][2] - 0.2
        hsecs = [(xh, 0, zh, c_der * 0.6, 1), (xh + 0.18 * L * math.tan(math.radians(25)), 0.17 * L, zh, c_der * 0.25, 1)]
    else:
        xh, zh = x_ht - 1.0, R * 0.15
        hsecs = [(xh, 0.2, zh, c_der * 0.8, 1), (xh + 0.17 * L * math.tan(math.radians(32)), 0.17 * L, zh + 0.6, c_der * 0.3, 1)]
    for h in superficie("estab", hsecs, perfil="0010", padre=raiz):
        asignar(h, gris_ala)

    # ---- motores
    motores = []
    ventiladores = []
    if p.get("motor_cola"):
        posiciones = [(L * 0.72, s_ * (R + 1.2), R * 0.35) for s_ in (1, -1)]
        rmot, lmot = R * 0.42, L * 0.16
    else:
        if p["motores"] == 2:
            ys = [semi * 0.34]
        else:
            ys = [semi * 0.30, semi * 0.62]
        posiciones = []
        rmot = (R * 0.52 if not p["helice"] else R * 0.30)
        lmot = (L * 0.11 if not p["helice"] else L * 0.13)
        for y in ys:
            xa = x_ala + (y - R * 0.6) * math.tan(flecha)
            za = z_ala + (y - R * 0.6) * math.tan(dih)
            if alta or p["helice"]:
                posiciones += [(xa - lmot * 0.35, s_ * y, za - rmot * 0.2) for s_ in (1, -1)]
            else:
                posiciones += [(xa - lmot * 0.75, s_ * y, za - rmot * 1.25) for s_ in (1, -1)]
    for i, (mx, my, mz) in enumerate(posiciones):
        prof = [(0.0, rmot * 0.86), (0.02 * lmot, rmot * 0.97), (0.08 * lmot, rmot), (0.45 * lmot, rmot * 0.98),
                (0.8 * lmot, rmot * 0.82), (1.0 * lmot, rmot * 0.62), (1.0 * lmot, rmot * 0.55),
                (0.5 * lmot, rmot * 0.80), (0.06 * lmot, rmot * 0.82), (0.0, rmot * 0.86)]
        nac = torno(f"motor{i}", prof, padre=raiz, centro=(mx, my, mz))
        nac.data.materials.append(acento)
        nac.data.materials.append(metal)
        for poly in nac.data.polygons:
            if poly.center.x - mx < 0.05 * lmot or poly.center.x - mx > 0.9 * lmot:
                poly.material_index = 1
        # disco de fan y cono
        fan = torno(f"fan{i}", [(0.12 * lmot, 0.0), (0.12 * lmot, rmot * 0.83)], padre=raiz, centro=(mx, my, mz))
        asignar(fan, oscuro)
        cono = torno(f"cono{i}", [(0.02 * lmot, 0.0), (0.06 * lmot, rmot * 0.18), (0.12 * lmot, rmot * 0.26)],
                     padre=raiz, centro=(mx, my, mz))
        asignar(cono, metal)
        # álabes
        bl = []
        nb = 22 if not p["helice"] else 0
        for k in range(nb):
            a = 2 * math.pi * k / nb
            r0, r1 = rmot * 0.25, rmot * 0.82
            w = 0.05 * rmot
            vv = []
            for rr in (r0, r1):
                for dw, dx in ((-w, -0.02), (w, 0.02)):
                    aa = a + dw / rr * 3
                    vv.append((mx + 0.11 * lmot + dx * lmot, my + rr * math.cos(aa), mz + rr * math.sin(aa)))
            bl.append(vv)
        if bl:
            vs_b = [(a - mx, b - my, c - mz) for bb in bl for (a, b, c) in bb]
            fs_b = [(4 * k, 4 * k + 1, 4 * k + 3, 4 * k + 2) for k in range(len(bl))]
            ob = malla(f"alabes{i}", vs_b, fs_b, suave=False, padre=raiz)
            ob.location = (mx, my, mz)
            asignar(ob, metal)
            ventiladores.append(ob)
        # pilón
        if not p.get("motor_cola"):
            if alta or p["helice"]:
                pass
            else:
                pv = [(mx + 0.25 * lmot, my - 0.12, mz + rmot * 0.6), (mx + 1.0 * lmot, my - 0.12, mz + rmot * 0.5),
                      (mx + 1.2 * lmot, my - 0.12, mz + rmot * 1.35), (mx + 0.55 * lmot, my - 0.12, mz + rmot * 1.4)]
                pv2 = [(a, b + 0.24, c) for a, b, c in pv]
                ob = malla(f"pilon{i}", pv + pv2, [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)],
                           suave=False, padre=raiz)
                asignar(ob, gris_ala)
        if p["helice"]:
            for k in range(6):
                a = 2 * math.pi * k / 6
                r1 = rmot * 4.2
                vv = [(mx - 0.02, my, mz), (mx + 0.02, my, mz)]
                vv += [(mx - 0.05, my + r1 * math.cos(a + 0.05), mz + r1 * math.sin(a + 0.05)),
                       (mx + 0.05, my + r1 * math.cos(a - 0.05), mz + r1 * math.sin(a - 0.05))]
                vv = [(a - mx, b - my, c - mz) for (a, b, c) in vv]
                ob = malla(f"pala{i}_{k}", vv, [(0, 1, 3, 2)], suave=False, padre=raiz)
                ob.location = (mx, my, mz)
                asignar(ob, oscuro)
                ventiladores.append(ob)
        motores.append((mx, my, mz, rmot, lmot))
    if p.get("motor_deriva"):
        mx, mz = L * 0.80, R * 0.6 + 1.6
        rm, lm = R * 0.42, L * 0.14
        prof = [(0.0, rm * 0.86), (0.08 * lm, rm), (0.7 * lm, rm * 0.95), (1.0 * lm, rm * 0.7), (1.0 * lm, rm * 0.6), (0.06 * lm, rm * 0.82), (0.0, rm * 0.86)]
        nac = torno("motor_cola", prof, padre=raiz, centro=(mx, 0, mz))
        asignar(nac, acento)
        fan = torno("fan_cola", [(0.12 * lm, 0.0), (0.12 * lm, rm * 0.83)], padre=raiz, centro=(mx, 0, mz))
        asignar(fan, oscuro)
        motores.append((mx, 0, mz, rm, lm))
    info["motores"] = motores
    info["ventiladores"] = ventiladores
    info["fus_nariz"] = nariz

    # ---- tren de aterrizaje
    suelo = -R - 1.9
    info["altura_tren"] = -suelo
    if tren:
        def rueda(nombre, cx, cy, cz, r, w):
            prof = [(-w / 2, r * 0.0), (-w / 2, r * 0.9), (-w * 0.45, r), (w * 0.45, r), (w / 2, r * 0.9), (w / 2, 0)]
            ob = torno(nombre, [(0, 0)] and prof, padre=raiz, nseg=36)
            ob.rotation_euler = (0, 0, math.pi / 2)
            ob.location = (cx, cy, cz)
            asignar(ob, goma)
            hub = torno(nombre + "_l", [(-w / 2 - 0.01, 0), (-w / 2 - 0.01, r * 0.55), (w / 2 + 0.01, r * 0.55), (w / 2 + 0.01, 0)],
                        padre=raiz, nseg=24)
            hub.rotation_euler = (0, 0, math.pi / 2); hub.location = (cx, cy, cz)
            asignar(hub, metal)
        def pata(nombre, x, y, ztop, zbot, r=0.13):
            ob = torno(nombre, [(0, r), (ztop - zbot, r)], padre=raiz, nseg=16)
            ob.rotation_euler = (0, -math.pi / 2, 0)
            ob.location = (x, y, zbot)
            asignar(ob, metal)
        rr = 0.6 if L > 30 else 0.45
        xm = x_ala + c_raiz * 0.62
        for s_ in (1, -1):
            ym = s_ * (R * 1.35 if not alta else R * 0.9)
            zt = (z_ala if not alta else -R * 0.6)
            pata(f"pata{s_}", xm, ym, zt, suelo + rr)
            for dy in (-0.45, 0.45):
                rueda(f"rueda{s_}{dy}", xm, ym + dy, suelo + rr, rr, 0.42)
        pata("pata_n", nariz * 0.75, 0, -R * 0.7, suelo + rr * 0.6, 0.1)
        for dy in (-0.28, 0.28):
            rueda(f"ruedan{dy}", nariz * 0.75, dy, suelo + rr * 0.6, rr * 0.6, 0.3)
    raiz["suelo"] = suelo
    info["tipo"] = tipo
    info["p"] = p
    return raiz, info


# ---------------------------------------------------------------- cuerpos genéricos

def cuerpo(nombre, xs, perfil, nr=48, padre=None, cerrar=True):
    """perfil(x) -> (ry, z_bajo, z_alto). UV: u = posición relativa, v = ángulo."""
    vs, uvs, fs = [], [], []
    L = xs[-1] - xs[0]
    for x in xs:
        ry, zb, za = perfil(x)
        zc, rz = (za + zb) / 2, max((za - zb) / 2, 0.01)
        ry = max(ry, 0.01)
        for k in range(nr):
            th = 2 * math.pi * k / nr
            vs.append((x, ry * math.sin(th), zc - rz * math.cos(th)))
            uvs.append(((x - xs[0]) / L, th / (2 * math.pi)))
        vs.append(vs[-nr]); uvs.append(((x - xs[0]) / L, 1.0))
    n1 = nr + 1
    for i in range(len(xs) - 1):
        for k in range(nr):
            a = i * n1 + k
            fs.append((a, a + 1, a + 1 + n1, a + n1))
    if cerrar:
        for idx, x in ((0, xs[0]), (len(xs) - 1, xs[-1])):
            c = len(vs)
            base = idx * n1
            vs.append((x, 0, np.mean([v[2] for v in vs[base:base + nr]]))); uvs.append(((x - xs[0]) / L, 0.5))
            for k in range(nr):
                fs.append((base + k, base + k + 1, c))
    return malla(nombre, vs, fs, uvs, padre=padre)


def textura_heli(ruta, cfg, militar=False):
    W, H = 2048, 1024
    base = cfg.get("fuselaje", "#4A5A3C" if militar else "#E8772E")
    img = Image.new("RGB", (W, H), base)
    d = ImageDraw.Draw(img)
    def py(th): return int((1 - th / (2 * math.pi)) * H)
    def px(u): return int(u * W)
    if not militar:
        # franja blanca y panza
        for th0 in (1.45, 2 * math.pi - 1.45):
            d.rectangle([px(0.05), py(th0 + 0.10), px(0.98), py(th0 - 0.10)], fill=cfg.get("franja", "#FFFFFF"))
        d.rectangle([0, py(0.5), W, H], fill=cfg.get("panza", "#14365A"))
        d.rectangle([0, 0, W, py(2 * math.pi - 0.5)], fill=cfg.get("panza", "#14365A"))
    # cristalera delantera: banda oscura que envuelve el morro
    vid = "#1C2733"
    d.polygon([(px(0.0), py(2.2)), (px(0.15), py(2.2)), (px(0.20), py(math.pi)), (px(0.15), py(2 * math.pi - 2.2)),
               (px(0.0), py(2 * math.pi - 2.2))], fill=vid)
    d.rectangle([0, py(2 * math.pi - 1.0), px(0.10), py(1.0)], fill=vid)
    # ventanas laterales de cabina
    for th0 in (1.85, 2 * math.pi - 1.85):
        for u0 in (0.20, 0.29):
            d.rounded_rectangle([px(u0), py(th0 + 0.35), px(u0 + 0.07), py(th0 - 0.25)], 12, fill=vid)
    txt = cfg.get("texto", "CABINA ABIERTA")
    if txt and FUENTE and not militar:
        f = ImageFont.truetype(FUENTE, 40)
        bb = d.textbbox((0, 0), txt, font=f)
        capa = Image.new("RGBA", (bb[2] - bb[0] + 10, bb[3] - bb[1] + 16), (0, 0, 0, 0))
        ImageDraw.Draw(capa).text((5 - bb[0], 5 - bb[1]), txt, font=f, fill=cfg.get("panza", "#14365A"))
        for th, cap in ((2 * math.pi - 1.45, capa.transpose(Image.FLIP_TOP_BOTTOM)), (1.45, capa.transpose(Image.FLIP_LEFT_RIGHT))):
            img.paste(cap, (px(0.62) - cap.width // 2, py(th) - cap.height // 2), cap)
    img.save(ruta)


def crear_helicoptero(tipo="medio", cfg=None, dir_tmp="/tmp/m3d"):
    """tipo: ligero (patines), medio (ruedas), militar (verde, ruedas), tandem (dos rotores)."""
    cfg = cfg or {}
    os.makedirs(dir_tmp, exist_ok=True)
    militar = tipo in ("militar", "tandem") or cfg.get("militar", False)
    L = {"ligero": 10.5, "medio": 14.5, "militar": 15.5, "tandem": 15.5}.get(tipo, 14.5)
    raiz = bpy.data.objects.new("AVION", None)
    bpy.context.scene.collection.objects.link(raiz)
    H_c = L * 0.16  # altura de cabina
    W_c = L * 0.12
    xb = L * (0.48 if tipo != "tandem" else 0.95)
    def perfil(x):
        s = x / L
        if tipo == "tandem":
            if s < 0.08:
                f = (1 - (1 - s / 0.08) ** 2) ** 0.5
                return W_c * 1.15 * f, -H_c * 0.5 * f + 0.0, H_c * (0.2 + 0.8 * f) * 0.85
            return W_c * 1.15, -H_c * 0.5, H_c * 0.85
        if s < 0.18:  # morro redondo
            f = (1 - (1 - s / 0.18) ** 2.2) ** 0.5
            return W_c * f, -H_c * 0.42 * f ** 0.8, -H_c * 0.42 + H_c * (0.15 + 0.85 * f ** 0.7)
        if s < 0.40:
            return W_c, -H_c * 0.42, H_c * 0.58
        if s < 0.55:  # transición al botalón
            t = (s - 0.40) / 0.15
            k = 0.5 - 0.5 * math.cos(t * math.pi)
            return W_c * (1 - 0.72 * k), -H_c * 0.42 + H_c * 0.62 * k, H_c * 0.58 - H_c * 0.18 * k
        t = (s - 0.55) / 0.45  # botalón de cola
        r = W_c * 0.28 * (1 - 0.45 * t)
        zc = H_c * 0.30 + t * H_c * 0.05
        return r, zc - r, zc + r
    xs = list(np.linspace(0, L, 120))
    fus = cuerpo("fuselaje", xs, perfil, padre=raiz)
    tex = os.path.join(dir_tmp, f"heli_{tipo}.png")
    textura_heli(tex, cfg, militar)
    asignar(fus, material_imagen("pintura_heli", tex, rough=0.38 if not militar else 0.7, coat=0.15 if not militar else 0))
    base_col = cfg.get("fuselaje", "#4A5A3C" if militar else "#E8772E")
    pint = material("heli_base", base_col, rough=0.4 if not militar else 0.7)
    metal = material("metal_h", "#9EA4AB", rough=0.3, metal=1.0)
    oscuro = material("oscuro_h", "#1E2226", rough=0.5)
    palam = material("pala", "#2A2E33", rough=0.45)
    info = {"L": L, "rotores": [], "tipo": tipo}
    # deriva y estabilizador
    if tipo != "tandem":
        fsecs = [(L * 0.90, 0, H_c * 0.30, L * 0.09, 1), (L * 0.95, 0, H_c * 0.30 + L * 0.13, L * 0.05, 1)]
        for o in superficie("deriva_h", fsecs, eje="y", perfil="0012", espejo=False, padre=raiz):
            asignar(o, pint)
        hs = [(L * 0.80, 0, H_c * 0.33, L * 0.05, 1), (L * 0.81, L * 0.09, H_c * 0.33, L * 0.04, 1)]
        for o in superficie("estab_h", hs, perfil="0012", padre=raiz):
            asignar(o, pint)
    # motores (carenado sobre la cabina)
    mot = cuerpo("carenado", list(np.linspace(L * 0.22, L * 0.50, 30)),
                 lambda x: (W_c * 0.55 * (1 - ((x - L * 0.36) / (L * 0.14)) ** 4) ** 0.5,
                            H_c * 0.50, H_c * 0.50 + H_c * 0.42 * (1 - ((x - L * 0.36) / (L * 0.14)) ** 4) ** 0.5),
                 padre=raiz) if tipo != "tandem" else None
    if mot:
        asignar(mot, pint)

    def rotor(nombre, cx, cz, radio, palas, eje="z", cy=0.0):
        piv = bpy.data.objects.new(nombre, None)
        bpy.context.scene.collection.objects.link(piv)
        piv.parent = raiz
        piv.location = (cx, cy, cz)
        cuerda = radio * 0.055
        for k in range(palas):
            a = 2 * math.pi * k / palas
            vs, fs_ = [], []
            for rr in (radio * 0.08, radio):
                for dc in (-cuerda / 2, cuerda / 2):
                    for dz in (-cuerda * 0.06, cuerda * 0.06):
                        px_ = rr * math.cos(a) - dc * math.sin(a)
                        py_ = rr * math.sin(a) + dc * math.cos(a)
                        vs.append((px_, py_, dz) if eje == "z" else (px_, dz, py_))
            fs_ = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            ob = malla(f"{nombre}_pala{k}", vs, fs_, suave=False, padre=piv)
            asignar(ob, palam)
        hub = torno(f"{nombre}_buje", [(-radio * 0.05, 0), (-radio * 0.05, radio * 0.06), (radio * 0.05, radio * 0.06), (radio * 0.05, 0)],
                    padre=piv, nseg=16)
        hub.rotation_euler = (0, -math.pi / 2, 0) if eje == "z" else (0, 0, math.pi / 2)
        asignar(hub, oscuro)
        info["rotores"].append((piv, eje))
        return piv

    if tipo == "tandem":
        for cx, cz in ((L * 0.08, H_c * 1.25), (L * 0.88, H_c * 1.75)):
            mast = torno("mastil", [(0, 0.35), (H_c * 0.6, 0.25)], padre=raiz, nseg=16)
            mast.rotation_euler = (0, -math.pi / 2, 0); mast.location = (cx, 0, cz - H_c * 0.6)
            asignar(mast, pint)
            rotor("ROTOR", cx, cz, L * 0.61, 3)
        # torre trasera
        tor = superficie("torre", [(L * 0.75, 0, H_c * 0.7, L * 0.22, 1), (L * 0.82, 0, H_c * 1.4, L * 0.14, 1)],
                         eje="y", perfil="0030", espejo=False, padre=raiz)[0]
        asignar(tor, pint)
    else:
        mast = torno("mastil", [(0, 0.18), (H_c * 0.28, 0.14)], padre=raiz, nseg=16)
        mast.rotation_euler = (0, -math.pi / 2, 0); mast.location = (L * 0.36, 0, H_c * 0.88)
        asignar(mast, metal)
        rotor("ROTOR", L * 0.36, H_c * 1.16, L * 0.58, 4 if tipo != "ligero" else 2)
        rotor("ROTOR_COLA", L * 0.965, H_c * 0.30 + L * 0.06, L * 0.085, 4 if tipo != "ligero" else 2, eje="y", cy=-W_c * 0.30)
    # tren
    if tipo == "ligero":
        for s_ in (1, -1):
            sk = torno(f"patin{s_}", [(0, 0.06), (L * 0.42, 0.06)], padre=raiz, nseg=12)
            sk.location = (L * 0.10, s_ * W_c * 1.1, -H_c * 0.42 - 0.75)
            asignar(sk, metal)
            for xx in (L * 0.18, L * 0.40):
                vs = [(xx, s_ * W_c * 1.1, -H_c * 0.42 - 0.75), (xx, s_ * W_c * 0.6, -H_c * 0.40),
                      (xx + 0.12, s_ * W_c * 0.6, -H_c * 0.40), (xx + 0.12, s_ * W_c * 1.1, -H_c * 0.42 - 0.75)]
                ob = malla(f"travesano{s_}{xx}", vs, [(0, 1, 2, 3)], suave=False, padre=raiz)
                asignar(ob, metal)
        info["altura_tren"] = H_c * 0.42 + 0.81
    else:
        for (xx, yy) in ((L * 0.12, 0), (L * 0.42, W_c * 0.95), (L * 0.42, -W_c * 0.95)):
            w = torno(f"rueda_h{xx}{yy}", [(-0.15, 0), (-0.15, 0.38), (0.15, 0.38), (0.15, 0)], padre=raiz, nseg=24)
            w.rotation_euler = (0, 0, math.pi / 2); w.location = (xx, yy, -H_c * 0.42 - 0.45)
            asignar(w, oscuro)
        info["altura_tren"] = H_c * 0.42 + 0.83
    info["H_c"] = H_c
    raiz["suelo"] = -info["altura_tren"]
    return raiz, info


def crear_caza(cfg=None, dir_tmp="/tmp/m3d"):
    cfg = cfg or {}
    L = 16.0
    raiz = bpy.data.objects.new("AVION", None)
    bpy.context.scene.collection.objects.link(raiz)
    col = cfg.get("fuselaje", "#8C959E")
    pint = material("caza", col, rough=0.55, metal=0.1)
    cab = material("cupula", "#22303D", rough=0.08, metal=0.3)
    tob = material("tobera", "#3A3D40", rough=0.4, metal=0.8)
    def perfil(x):
        s = x / L
        if s < 0.30:
            f = (s / 0.30) ** 0.62
            return 0.85 * f, -0.75 * f, 0.75 * f
        if s < 0.85:
            return 0.95 + 0.35 * min(1, (s - 0.3) / 0.2), -0.75, 0.80
        t = (s - 0.85) / 0.15
        return 1.3 - 0.35 * t, -0.75 + 0.1 * t, 0.8 - 0.15 * t
    fus = cuerpo("fuselaje", list(np.linspace(0, L, 90)), perfil, padre=raiz)
    asignar(fus, pint)
    cup = cuerpo("cupula", list(np.linspace(L * 0.17, L * 0.36, 24)),
                 lambda x: (0.48 * math.sin(math.pi * (x - L * 0.17) / (L * 0.19)) ** 0.6, 0.45,
                            0.45 + 0.62 * math.sin(math.pi * (x - L * 0.17) / (L * 0.19)) ** 0.7), padre=raiz)
    asignar(cup, cab)
    secs = [(L * 0.42, 0.8, -0.1, L * 0.45, 0.6), (L * 0.80, 5.2, -0.15, L * 0.10, 0.6)]
    for o in superficie("ala", secs, perfil="0006", padre=raiz):
        asignar(o, pint)
    for s_ in (1, -1):
        fs_ = [(L * 0.76, s_ * 0.9, 0.7, L * 0.17, 1), (L * 0.88, s_ * 1.7, 3.0, L * 0.07, 1)]
        for o in superficie(f"deriva{s_}", fs_, eje="y", perfil="0008", espejo=False, padre=raiz):
            asignar(o, pint)
    hs = [(L * 0.84, 1.0, -0.1, L * 0.14, 0.6), (L * 0.95, 3.4, -0.1, L * 0.05, 0.6)]
    for o in superficie("estab", hs, perfil="0006", padre=raiz):
        asignar(o, pint)
    tb = torno("tobera", [(L, 0.85), (L + 0.6, 0.75), (L + 0.6, 0.55), (L, 0.5)], padre=raiz, centro=(0, 0, 0.02))
    asignar(tb, tob)
    pc = torno("postcombustion", [(L + 0.55, 0.0), (L + 0.55, 0.55)], padre=raiz)
    asignar(pc, material("llama_pc", "#FF8A3D", emis="#FF8A3D", emis_f=6.0))
    raiz["suelo"] = -2.2
    return raiz, {"L": L, "altura_tren": 2.2, "motores": [(L, 0, 0, 0.8, 1)]}


def crear_aeronave(modelo, cfg=None, tren=True):
    if modelo.startswith("heli"):
        sub = modelo.split("_", 1)[1] if "_" in modelo else "medio"
        return crear_helicoptero(sub, cfg)
    if modelo == "caza":
        return crear_caza(cfg)
    return crear_avion(modelo, cfg, tren=tren)
