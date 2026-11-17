"""Escenarios, cámaras y render para el motor 3D."""
import math, os, random
import numpy as np
import bpy
from mathutils import Vector
from m3d_avion import malla, material, hex2rgb, asignar, torno
from PIL import Image, ImageDraw, ImageFont
import m3d_avion


def limpiar():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def sin_muestreo_emision():
    """Las emisiones (ventanas, farolas, líneas de aire) no se tratan como luces: mucho más rápido."""
    for m in bpy.data.materials:
        try:
            m.cycles.emission_sampling = "NONE"
        except Exception:
            pass


def render_cfg(ancho, alto, muestras=12, fps=24):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    c = sc.cycles
    c.device = "CPU"
    c.samples = muestras
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.05
    c.use_denoising = True
    c.max_bounces = 2
    c.diffuse_bounces = 1
    c.glossy_bounces = 1
    c.use_fast_gi = True
    c.fast_gi_method = "REPLACE"
    c.ao_bounces_render = 1
    c.light_sampling_threshold = 0.05
    c.denoiser = "OPENIMAGEDENOISE"
    try:
        c.denoising_prefilter = "FAST"
        c.denoising_quality = "FAST"
    except Exception:
        pass
    c.transmission_bounces = 2
    c.volume_bounces = 0
    c.transparent_max_bounces = 4
    c.caustics_reflective = False
    c.caustics_refractive = False
    c.blur_glossy = 1.0
    sc.render.resolution_x = ancho
    sc.render.resolution_y = alto
    sc.render.fps = fps
    sc.render.use_persistent_data = True
    sc.render.image_settings.file_format = "JPEG"
    sc.render.image_settings.quality = 92
    try:
        sc.view_settings.view_transform = "AgX"
        sc.view_settings.look = "AgX - Medium High Contrast"
    except Exception:
        pass
    return sc


# ---------------------------------------------------------------- cielo

def cielo(tormenta=0.0, nubes=0.55, sol_elev=38, sol_rot=150, noche=False, atardecer=False):
    sc = bpy.context.scene
    w = bpy.data.worlds.new("cielo")
    sc.world = w
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    # degradado azul estilizado (más "ilustración" que el sky físico)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(coord.outputs["Generated"] if False else coord.outputs["Window"] if False else coord.outputs["Generated"], sep.inputs[0])
    # dirección normalizada: usamos Object? En world, 'Generated' es la dirección
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    cr = ramp.color_ramp
    if atardecer:
        cols = ["#2B3352", "#121A30"]
        cr.elements.new(0.2); cr.elements.new(0.3)
    if noche:
        cols = ["#2A3346", "#141B2B"]
    elif tormenta > 0.5:
        cols = ["#8E99A6", "#4E5B6A"]
    else:
        cols = ["#DCEAF5", "#5E9BD6"]
    if atardecer:
        objetivo = [(0.0, "#E07E4E"), (0.05, "#9A6A72"), (0.16, "#343B5C"), (0.5, "#121A30")]
        for el, (pos, c) in zip(cr.elements, objetivo):
            el.position = pos; el.color = hex2rgb(c)
    else:
        cr.elements[0].position = 0.0; cr.elements[0].color = hex2rgb(cols[0])
        cr.elements[1].position = 0.45; cr.elements[1].color = hex2rgb(cols[1])
    # nubes: ruido sobre la dirección
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 2.2
    noise.inputs["Detail"].default_value = 6
    noise.inputs["Roughness"].default_value = 0.55
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1, 1, 3.2)
    nt.links.new(coord.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], noise.inputs["Vector"])
    cm = nt.nodes.new("ShaderNodeValToRGB")
    cm.color_ramp.interpolation = "EASE"
    thr = 0.62 - 0.22 * nubes
    cm.color_ramp.elements[0].position = thr
    cm.color_ramp.elements[0].color = (0, 0, 0, 1)
    cm.color_ramp.elements[1].position = thr + 0.07
    cm.color_ramp.elements[1].color = (1, 1, 1, 1)
    nt.links.new(noise.outputs["Fac"], cm.inputs["Fac"])
    # máscara: nubes solo por encima del horizonte
    hz = nt.nodes.new("ShaderNodeMapRange")
    hz.inputs["From Min"].default_value = 0.0
    hz.inputs["From Max"].default_value = 0.12
    nt.links.new(sep.outputs["Z"], hz.inputs["Value"])
    mul = nt.nodes.new("ShaderNodeMath"); mul.operation = "MULTIPLY"
    nt.links.new(cm.outputs["Color"], mul.inputs[0]); nt.links.new(hz.outputs["Result"], mul.inputs[1])
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"
    nt.links.new(mul.outputs[0], mix.inputs["Factor"])
    nt.links.new(ramp.outputs["Color"], mix.inputs["A"])
    nubec = "#F7F9FB" if tormenta < 0.5 else "#6C7682"
    if noche:
        nubec = "#3A4357"
    if atardecer:
        nubec = "#4A4560"
    mix.inputs["B"].default_value = hex2rgb(nubec)
    lp = nt.nodes.new("ShaderNodeLightPath")
    mix2 = nt.nodes.new("ShaderNodeMix"); mix2.data_type = "RGBA"
    nt.links.new(lp.outputs["Is Camera Ray"], mix2.inputs["Factor"])
    amb = "#C8D6E4" if tormenta < 0.5 and not noche else ("#6E7884" if not noche else "#202838")
    if atardecer:
        amb = "#3A4260"
    mix2.inputs["A"].default_value = hex2rgb(amb)
    nt.links.new(mix.outputs["Result"], mix2.inputs["B"])
    nt.links.new(mix2.outputs["Result"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.0 if not noche else 0.5
    if atardecer:
        bg.inputs["Strength"].default_value = 0.8
    nt.links.new(bg.outputs[0], out.inputs[0])
    # sol
    sd = bpy.data.lights.new("sol", "SUN")
    sd.energy = 5.0 if tormenta < 0.5 else 1.4
    if noche:
        sd.energy = 0.25
    if atardecer:
        sd.energy = 2.2
        sd.color = (1.0, 0.62, 0.38)
        sol_elev = 6
    sd.angle = math.radians(2.5 if tormenta < 0.5 else 25)
    sd.color = (1.0, 0.96, 0.9)
    so = bpy.data.objects.new("sol", sd)
    sc.collection.objects.link(so)
    so.rotation_euler = (math.radians(90 - sol_elev), 0, math.radians(sol_rot))
    return w


# ---------------------------------------------------------------- suelo, pista, árboles

def textura_pista(ruta, largo=3000, ancho=45):
    W, H = 512, 8192
    img = Image.new("RGB", (W, H), "#4A4E54")
    d = ImageDraw.Draw(img)
    rng = random.Random(3)
    for _ in range(9000):
        x, y = rng.randrange(W), rng.randrange(H)
        c = rng.choice(["#53575D", "#43474C", "#4E5157"])
        d.rectangle([x, y, x + 2, y + 6], fill=c)
    ppm = H / largo
    # bordes
    d.rectangle([12, 0, 22, H], fill="#E9E9E2"); d.rectangle([W - 22, 0, W - 12, H], fill="#E9E9E2")
    # línea central discontinua
    y = 0
    while y < H:
        d.rectangle([W // 2 - 4, y, W // 2 + 4, y + int(30 * ppm)], fill="#EDEDE6")
        y += int(50 * ppm)
    # piano keys en ambos extremos
    for base, sgn in ((int(10 * ppm), 1), (H - int(10 * ppm), -1)):
        for k in range(8):
            for lado in (0, 1):
                x0 = 40 + k * 26 if lado == 0 else W - 40 - k * 26 - 16
                y0, y1 = base, base + sgn * int(45 * ppm)
                d.rectangle([x0, min(y0, y1), x0 + 16, max(y0, y1)], fill="#EDEDE6")
        # zona de toma de contacto
        for k in range(3):
            yy = base + sgn * int((150 + k * 150) * ppm)
            for xx in (120, W - 150):
                d.rectangle([xx, min(yy, yy + sgn * int(22 * ppm)), xx + 30, max(yy, yy + sgn * int(22 * ppm))], fill="#EDEDE6")
    img.save(ruta)


HAZE = {"normal": "#C9DCEB", "tormenta": "#7D8894", "noche": "#1C2433", "atardecer": "#3B3F5C"}


def con_bruma(mat, color_bruma, distancia):
    """Mezcla el color del material con el de la bruma según la distancia a la cámara."""
    nt = mat.node_tree
    p = nt.nodes["Principled BSDF"]
    out = nt.nodes["Material Output"]
    cd = nt.nodes.new("ShaderNodeCameraData")
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = distancia * 0.08
    mr.inputs["From Max"].default_value = distancia
    nt.links.new(cd.outputs["View Distance"], mr.inputs["Value"])
    pw = nt.nodes.new("ShaderNodeMath"); pw.operation = "POWER"; pw.inputs[1].default_value = 0.8
    nt.links.new(mr.outputs["Result"], pw.inputs[0])
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = hex2rgb(color_bruma)
    em.inputs["Strength"].default_value = 1.0
    mx = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(pw.outputs[0], mx.inputs["Fac"])
    nt.links.new(p.outputs[0], mx.inputs[1])
    nt.links.new(em.outputs[0], mx.inputs[2])
    nt.links.new(mx.outputs[0], out.inputs["Surface"])


COLORES_TERRENO = {
    "hierba": ("#4E9A2E", "#78B83F"), "campo": ("#6E9A3A", "#B8A25A"), "selva": ("#245A20", "#3C7A30"),
    "mar": ("#164E7A", "#1F6596"), "nieve": ("#DDE4EA", "#F6F8FA"), "montana": ("#5A7A3A", "#8A8070"),
    "desierto": ("#C9A66B", "#DEC08A"), "ciudad": ("#7A7F86", "#9A9EA4"),
}


def suelo(dir_tmp="/tmp/m3d", pista=True, largo_pista=3000, terreno="hierba", tam=12000, bruma=4000, clima="normal"):
    os.makedirs(dir_tmp, exist_ok=True)
    c0, c1 = COLORES_TERRENO.get(terreno, COLORES_TERRENO["hierba"])
    hz = HAZE.get(clima, HAZE["normal"])
    if terreno == "ciudad":
        return ciudad(tam, bruma, hz, oscura=(clima in ("noche", "atardecer")))
    if terreno in ("montana", "nieve"):
        g = montanas(tam, nieve=(terreno == "nieve"))
    else:
        g = malla("suelo", [(-tam, -tam, 0), (tam, -tam, 0), (tam, tam, 0), (-tam, tam, 0)], [(0, 1, 2, 3)], suave=False)
    m = bpy.data.materials.new("terreno"); m.use_nodes = True
    nt = m.node_tree; p = nt.nodes["Principled BSDF"]
    co = nt.nodes.new("ShaderNodeTexCoord")
    nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 0.002 if terreno != "campo" else 0.0006
    nz.inputs["Detail"].default_value = 6
    nt.links.new(co.outputs["Object"], nz.inputs["Vector"])
    rp = nt.nodes.new("ShaderNodeValToRGB")
    rp.color_ramp.elements[0].color = hex2rgb(c0); rp.color_ramp.elements[1].color = hex2rgb(c1)
    if terreno == "campo":  # parcelas de cultivo
        vo = nt.nodes.new("ShaderNodeTexVoronoi"); vo.inputs["Scale"].default_value = 0.004
        nt.links.new(co.outputs["Object"], vo.inputs["Vector"])
        nt.links.new(vo.outputs["Color"], rp.inputs["Fac"])
        rp.color_ramp.elements[0].position = 0.3; rp.color_ramp.elements[1].position = 0.7
    elif terreno in ("montana", "nieve"):
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        nt.links.new(co.outputs["Object"], sep.inputs[0])
        mr = nt.nodes.new("ShaderNodeMapRange")
        mr.inputs["From Min"].default_value = 300 if terreno == "montana" else -100
        mr.inputs["From Max"].default_value = 1500 if terreno == "montana" else 600
        nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
        ad = nt.nodes.new("ShaderNodeMath"); ad.operation = "ADD"
        nt.links.new(mr.outputs["Result"], ad.inputs[0])
        mul = nt.nodes.new("ShaderNodeMath"); mul.operation = "MULTIPLY"; mul.inputs[1].default_value = 0.25
        nt.links.new(nz.outputs["Fac"], mul.inputs[0]); nt.links.new(mul.outputs[0], ad.inputs[1])
        nt.links.new(ad.outputs[0], rp.inputs["Fac"])
        el = rp.color_ramp.elements
        el[0].color = hex2rgb("#4F6E35" if terreno == "montana" else "#7C7A76"); el[0].position = 0.15
        el[1].color = hex2rgb("#F3F6F9"); el[1].position = 0.75
        e2 = el.new(0.45); e2.color = hex2rgb("#8C8174" if terreno == "montana" else "#BFC4C9")
    else:
        nt.links.new(nz.outputs["Fac"], rp.inputs["Fac"])
    nt.links.new(rp.outputs["Color"], p.inputs["Base Color"])
    p.inputs["Roughness"].default_value = 0.9 if terreno != "mar" else 0.05
    if terreno == "mar":
        bump = nt.nodes.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.15
        wn = nt.nodes.new("ShaderNodeTexNoise"); wn.inputs["Scale"].default_value = 0.08
        nt.links.new(co.outputs["Object"], wn.inputs["Vector"])
        nt.links.new(wn.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], p.inputs["Normal"])
    con_bruma(m, hz, bruma)
    asignar(g, m)
    if pista and terreno not in ("mar",):
        tex = os.path.join(dir_tmp, "pista.png")
        if not os.path.exists(tex):
            textura_pista(tex, largo_pista)
        a = 45 / 2
        pv = [(-largo_pista / 2, -a, 0.03), (largo_pista / 2, -a, 0.03), (largo_pista / 2, a, 0.03), (-largo_pista / 2, a, 0.03)]
        pob = malla("pista", pv, [(0, 1, 2, 3)], uvs=[(0, 0), (0, 1), (1, 1), (1, 0)], suave=False)
        mp = m3d_avion.material_imagen("pista", tex, rough=0.85, coat=0)
        con_bruma(mp, hz, bruma)
        asignar(pob, mp)
        tw = malla("rodadura", [(-largo_pista / 2, 120, 0.02), (largo_pista / 2, 120, 0.02), (largo_pista / 2, 143, 0.02), (-largo_pista / 2, 143, 0.02)],
                   [(0, 1, 2, 3)], suave=False)
        ma = material("asfalto2", "#555A60", rough=0.9)
        con_bruma(ma, hz, bruma)
        asignar(tw, ma)
    return g


def montanas(tam, nieve=False, n=180, semilla=5):
    rng = np.random.default_rng(semilla)
    xs = np.linspace(-tam, tam, n)
    X, Y = np.meshgrid(xs, xs)
    Z = np.zeros_like(X)
    for k in range(7):
        f = (2 ** k) / tam * 1.6
        a = 900 / (1.9 ** k)
        ph = rng.uniform(0, 6.28, 2)
        ang = rng.uniform(0, 3.14)
        u = X * math.cos(ang) + Y * math.sin(ang)
        v = -X * math.sin(ang) + Y * math.cos(ang)
        Z += a * (np.sin(u * f + ph[0]) * np.cos(v * f * 1.3 + ph[1]))
    Z = np.abs(Z) * 1.4 + 150
    # valle llano en el centro para que la escena no choque
    d = np.sqrt(X ** 2 + Y ** 2)
    Z *= np.clip((d - 1500) / 4000, 0.05, 1.0)
    if nieve:
        Z += 900
    vs = [(float(X[i, j]), float(Y[i, j]), float(Z[i, j])) for i in range(n) for j in range(n)]
    fs = [(i * n + j, i * n + j + 1, (i + 1) * n + j + 1, (i + 1) * n + j) for i in range(n - 1) for j in range(n - 1)]
    return malla("suelo", vs, fs)


def arboles(n=900, radio_min=180, radio_max=2500, semilla=7, evitar_pista=True, tipo="hierba"):
    if tipo in ("mar", "nieve"):
        return
    rng = random.Random(semilla)
    # dos modelos: copa redonda y abeto
    copa = material("copa", "#3E7A35" if tipo != "selva" else "#2F6528", rough=0.9)
    copa2 = material("copa2", "#4F8B3C", rough=0.9)
    tronco = material("tronco", "#6B4E37", rough=0.9)
    col = bpy.data.collections.new("arbol_modelos")
    bpy.context.scene.collection.children.link(col)
    col.hide_render = False
    modelos = []
    # redondo
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=4, location=(0, 0, 9))
    a1 = bpy.context.object; asignar(a1, copa)
    for v in a1.data.vertices:
        v.co *= 1 + rng.uniform(-0.12, 0.12)
    for p_ in a1.data.polygons: p_.use_smooth = True
    bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.5, depth=6, location=(0, 0, 3))
    t1 = bpy.context.object; asignar(t1, tronco)
    # abeto
    bpy.ops.mesh.primitive_cone_add(vertices=10, radius1=3.6, depth=12, location=(0, 0, 8))
    a2 = bpy.context.object; asignar(a2, copa2)
    for ob in (a1, t1, a2):
        for c in ob.users_collection:
            c.objects.unlink(ob)
        col.objects.link(ob)
    m1 = bpy.data.collections.new("arbol1"); m1.objects.link(a1); m1.objects.link(t1)
    m2 = bpy.data.collections.new("arbol2"); m2.objects.link(a2)
    # el modelo no se renderiza en su sitio
    for ob in (a1, t1, a2):
        ob.location.x += 99999
    bpy.context.view_layer.update()
    for i in range(n):
        while True:
            ang = rng.uniform(0, 2 * math.pi)
            r = radio_min + (radio_max - radio_min) * rng.random() ** 0.7
            x, y = r * math.cos(ang) * 1.6, r * math.sin(ang)
            if evitar_pista and abs(y) < 170 and abs(x) < 1700:
                continue
            break
        inst = bpy.data.objects.new(f"arbol{i}", None)
        inst.instance_type = "COLLECTION"
        inst.instance_collection = m1 if rng.random() < 0.6 else m2
        s = rng.uniform(0.7, 1.5)
        inst.scale = (s, s, s * rng.uniform(0.9, 1.3))
        inst.location = (x - 99999 * s, y, 0)
        bpy.context.scene.collection.objects.link(inst)


def colinas(radio=5000, tipo="hierba"):
    """Anillo de montañas suaves en el horizonte."""
    rng = random.Random(11)
    n, m = 180, 6
    vs, fs = [], []
    alturas = [0] * n
    for k in range(n):
        a = 2 * math.pi * k / n
        alturas[k] = 250 + 380 * (0.5 + 0.5 * math.sin(3 * a + 1)) * (0.6 + 0.4 * math.sin(7 * a)) + rng.uniform(0, 120)
    for j in range(m):
        t = j / (m - 1)
        for k in range(n):
            a = 2 * math.pi * k / n
            r = radio * (1 - 0.25 * t)
            h = alturas[k] * math.sin(t * math.pi * 0.5) ** 0.7 if j < m - 1 else alturas[k]
            r2 = radio * (1.0 - 0.18 * t)
            vs.append((r2 * math.cos(a) * 1.4, r2 * math.sin(a), h * (t if j < m - 1 else 1)))
    for j in range(m - 1):
        for k in range(n):
            a = j * n + k; b = j * n + (k + 1) % n
            fs.append((a, b, b + n, a + n))
    ob = malla("colinas", vs, fs)
    col = "#9FBFC4" if tipo != "nieve" else "#C9D6E2"
    m = material("colinas", col, rough=1.0, emis=col, emis_f=0.35)
    asignar(ob, m)
    return ob


# ---------------------------------------------------------------- cámara

def camara(lente=35, dof=None):
    sc = bpy.context.scene
    cd = bpy.data.cameras.new("cam")
    cd.lens = lente
    cd.clip_start = 0.1; cd.clip_end = 40000
    cam = bpy.data.objects.new("cam", cd)
    sc.collection.objects.link(cam)
    sc.camera = cam
    obj = bpy.data.objects.new("cam_obj", None)
    sc.collection.objects.link(obj)
    tc = cam.constraints.new("TRACK_TO")
    tc.target = obj; tc.track_axis = "TRACK_NEGATIVE_Z"; tc.up_axis = "UP_Y"
    if dof:
        cd.dof.use_dof = True
        cd.dof.focus_object = obj
        cd.dof.aperture_fstop = dof
    return cam, obj


def suavizar(ob, data_path="location"):
    ad = ob.animation_data
    if not ad or not ad.action:
        return
    try:
        curvas = ad.action.fcurves
    except AttributeError:
        curvas = []
        for layer in ad.action.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    curvas.extend(cb.fcurves)
    for fc in curvas:
        for kp in fc.keyframe_points:
            kp.interpolation = "BEZIER"
            kp.easing = "AUTO"


def ciudad(tam, bruma, hz, oscura=True, bloque=140.0, calle=22.0, radio=5000, semilla=9):
    """Cuadrícula de manzanas con edificios y calles iluminadas (luces en el sombreado del suelo)."""
    g = malla("suelo", [(-tam, -tam, 0), (tam, -tam, 0), (tam, tam, 0), (-tam, tam, 0)], [(0, 1, 2, 3)], suave=False)
    m = bpy.data.materials.new("ciudad_suelo"); m.use_nodes = True
    nt = m.node_tree; p = nt.nodes["Principled BSDF"]
    co = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ"); nt.links.new(co.outputs["Object"], sep.inputs[0])
    def fr(canal, per):
        d = nt.nodes.new("ShaderNodeMath"); d.operation = "DIVIDE"; d.inputs[1].default_value = per
        nt.links.new(sep.outputs[canal], d.inputs[0])
        f = nt.nodes.new("ShaderNodeMath"); f.operation = "FRACT"; nt.links.new(d.outputs[0], f.inputs[0])
        return f
    def menor(nodo, v):
        c = nt.nodes.new("ShaderNodeMath"); c.operation = "LESS_THAN"; c.inputs[1].default_value = v
        nt.links.new(nodo.outputs[0], c.inputs[0]); return c
    def op(a, b, o):
        c = nt.nodes.new("ShaderNodeMath"); c.operation = o
        nt.links.new(a.outputs[0], c.inputs[0]); nt.links.new(b.outputs[0], c.inputs[1]); return c
    q = calle / bloque
    calle_x = menor(fr("X", bloque), q); calle_y = menor(fr("Y", bloque), q)
    es_calle = op(calle_x, calle_y, "MAXIMUM")
    # farolas: puntitos en los bordes de las calles
    lx = menor(fr("X", 18.0), 0.07); ly = menor(fr("Y", 18.0), 0.07)
    borde_y = menor(fr("Y", bloque), q * 0.10)
    borde_x = menor(fr("X", bloque), q * 0.10)
    farola = op(op(borde_y, lx, "MULTIPLY"), op(borde_x, ly, "MULTIPLY"), "MAXIMUM")
    col_s = nt.nodes.new("ShaderNodeMix"); col_s.data_type = "RGBA"
    nt.links.new(es_calle.outputs[0], col_s.inputs["Factor"])
    col_s.inputs["A"].default_value = hex2rgb("#1F252E" if oscura else "#7E848C")
    col_s.inputs["B"].default_value = hex2rgb("#2B3038" if oscura else "#55595F")
    nt.links.new(col_s.outputs["Result"], p.inputs["Base Color"])
    p.inputs["Roughness"].default_value = 0.85
    if oscura:
        p.inputs["Emission Color"].default_value = hex2rgb("#FFC36B")
        st = nt.nodes.new("ShaderNodeMath"); st.operation = "MULTIPLY"; st.inputs[1].default_value = 12.0
        nt.links.new(farola.outputs[0], st.inputs[0])
        nt.links.new(st.outputs[0], p.inputs["Emission Strength"])
    con_bruma(m, hz, bruma)
    asignar(g, m)
    # edificios: cubo unidad instanciado
    rng = random.Random(semilla)
    mb = bpy.data.materials.new("edificio"); mb.use_nodes = True
    nb = mb.node_tree; pb = nb.nodes["Principled BSDF"]
    pb.inputs["Base Color"].default_value = hex2rgb("#1C222B" if oscura else "#B9BEC5")
    pb.inputs["Roughness"].default_value = 0.7
    if oscura:
        # ventanas encendidas al azar (coordenadas de mundo)
        geo = nb.nodes.new("ShaderNodeNewGeometry")
        br = nb.nodes.new("ShaderNodeTexBrick")
        br.inputs["Scale"].default_value = 1.0
        br.inputs["Mortar Size"].default_value = 1.3
        br.inputs["Brick Width"].default_value = 3.2
        br.inputs["Row Height"].default_value = 3.6
        br.inputs["Color1"].default_value = (1, 1, 1, 1); br.inputs["Color2"].default_value = (1, 1, 1, 1)
        br.inputs["Mortar"].default_value = (0, 0, 0, 1)
        nb.links.new(geo.outputs["Position"], br.inputs["Vector"])
        vo = nb.nodes.new("ShaderNodeTexVoronoi"); vo.inputs["Scale"].default_value = 0.3
        nb.links.new(geo.outputs["Position"], vo.inputs["Vector"])
        sepc = nb.nodes.new("ShaderNodeSeparateColor")
        nb.links.new(vo.outputs["Color"], sepc.inputs["Color"])
        gt = nb.nodes.new("ShaderNodeMath"); gt.operation = "GREATER_THAN"; gt.inputs[1].default_value = 0.72
        nb.links.new(sepc.outputs[0], gt.inputs[0])
        mu = nb.nodes.new("ShaderNodeMath"); mu.operation = "MULTIPLY"
        nb.links.new(br.outputs["Fac"], mu.inputs[0]); nb.links.new(gt.outputs[0], mu.inputs[1])
        inv = nb.nodes.new("ShaderNodeMath"); inv.operation = "SUBTRACT"; inv.inputs[0].default_value = 1.0
        nb.links.new(br.outputs["Fac"], inv.inputs[1])
        mu2 = nb.nodes.new("ShaderNodeMath"); mu2.operation = "MULTIPLY"
        nb.links.new(inv.outputs[0], mu2.inputs[0]); nb.links.new(gt.outputs[0], mu2.inputs[1])
        pb.inputs["Emission Color"].default_value = hex2rgb("#FFD08A")
        st = nb.nodes.new("ShaderNodeMath"); st.operation = "MULTIPLY"; st.inputs[1].default_value = 1.4
        nb.links.new(mu2.outputs[0], st.inputs[0])
        nb.links.new(st.outputs[0], pb.inputs["Emission Strength"])
    con_bruma(mb, hz, bruma)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
    cubo = bpy.context.object
    cubo.data.transform(__import__("mathutils").Matrix.Translation((0, 0, 0)))
    asignar(cubo, mb)
    colm = bpy.data.collections.new("edif_modelo"); colm.objects.link(cubo)
    for c in list(cubo.users_collection):
        if c != colm:
            c.objects.unlink(cubo)
    n = int(radio / bloque)
    for i in range(-n, n):
        for j in range(-n, n):
            cx, cy = (i + 0.5) * bloque + calle / 2, (j + 0.5) * bloque + calle / 2
            d = math.hypot(cx, cy)
            if d > radio:
                continue
            util = bloque - calle
            k = rng.choice([1, 2, 2, 3, 4])
            for a in range(k):
                for b in range(k):
                    if rng.random() < 0.15:
                        continue
                    w = util / k * rng.uniform(0.6, 0.9)
                    x = cx - util / 2 + (a + 0.5) * util / k
                    y = cy - util / 2 + (b + 0.5) * util / k
                    if (abs(y) < 160 and abs(x) < 1700) or math.hypot(x, y) < 140:
                        continue  # deja libre la pista y la zona de la cámara
                    centro = max(0.15, 1 - d / radio)
                    h = rng.uniform(6, 22) + (rng.random() ** 6) * 110 * centro
                    inst = bpy.data.objects.new("ed", None)
                    inst.instance_type = "COLLECTION"; inst.instance_collection = colm
                    inst.location = (x, y, 0)
                    inst.scale = (w, w * rng.uniform(0.7, 1.0), h)
                    bpy.context.scene.collection.objects.link(inst)
    return g
