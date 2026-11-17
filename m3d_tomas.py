"""Tomas (planos) animadas. Cada toma monta la escena completa y anima avión y cámara.

construir(tarea) -> dict con 'anclas' (función frame -> {nombre: (x, y, visible)})
La tarea lleva: toma, aeronave, escenario, frames, fps, ancho, alto, semilla y parámetros."""
import math, os, random
import numpy as np
import bpy
from mathutils import Vector, Euler, Matrix
from bpy_extras.object_utils import world_to_camera_view
import m3d_escena as E
import m3d_avion as A
from m3d_avion import malla, material, hex2rgb, asignar, naca

DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = next((p for p in (os.path.join(DIR, "assets"), DIR, os.path.join(os.getcwd(), "assets")) if os.path.exists(os.path.join(p, "tierra.jpg"))), os.path.join(DIR, "assets"))

TOMAS = ["pista", "rodaje", "despegue", "aterrizaje", "vuelo", "viraje", "emergencia", "ala_flujo",
         "motor", "globo", "heli_estacionario", "heli_vuelo", "heli_rotor", "cenital"]


# ---------------------------------------------------------------- utilidades

def easing(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def clave(ob, f, loc=None, rot=None, esc=None):
    if loc is not None:
        ob.location = loc
        ob.keyframe_insert("location", frame=f)
    if rot is not None:
        ob.rotation_euler = rot
        ob.keyframe_insert("rotation_euler", frame=f)
    if esc is not None:
        ob.scale = esc
        ob.keyframe_insert("scale", frame=f)


def a_local(pos, rot, off):
    """Desplazamiento 'off' en el marco del avión (solo guiñada) -> mundo."""
    m = Euler((0, 0, rot[2])).to_matrix()
    return Vector(pos) + m @ Vector(off)


def girar_rotores(info, nf, fps, vel=1.0):
    for (piv, eje) in info.get("rotores", []):
        for f in range(1, nf + 1, 6):
            ang = (f - 1) * math.radians(31) * vel * (1 if eje == "z" else 2.3)
            clave(piv, f, rot=(0, ang, 0) if eje == "y" else (0, 0, ang))
        lineal(piv)
        # disco translúcido que simula el barrido de las palas
        if vel > 0.3:
            r = max(abs(v.co.x) for ch in piv.children for v in ch.data.vertices) if piv.children else 5
            n = 48
            vs = [(0, 0, 0)] + [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n), 0) for k in range(n)]
            if eje == "y":
                vs = [(x, z, y) for (x, y, z) in vs]
            fs = [(0, k + 1, (k + 1) % n + 1) for k in range(n)]
            d = malla(piv.name + "_disco", vs, fs, suave=False, padre=piv.parent)
            d.location = piv.location
            m = bpy.data.materials.new("disco"); m.use_nodes = True
            nt = m.node_tree; nt.nodes.clear()
            out = nt.nodes.new("ShaderNodeOutputMaterial")
            tr = nt.nodes.new("ShaderNodeBsdfTransparent")
            df = nt.nodes.new("ShaderNodeBsdfDiffuse"); df.inputs["Color"].default_value = (0.05, 0.05, 0.06, 1)
            mx = nt.nodes.new("ShaderNodeMixShader"); mx.inputs["Fac"].default_value = 0.22
            nt.links.new(tr.outputs[0], mx.inputs[1]); nt.links.new(df.outputs[0], mx.inputs[2])
            nt.links.new(mx.outputs[0], out.inputs["Surface"])
            asignar(d, m)
            d.visible_shadow = False


def girar_ventiladores(info, nf, vel=1.0):
    for ob in info.get("ventiladores", []):
        for f in range(1, nf + 1, 4):
            clave(ob, f, rot=((f - 1) * math.radians(23) * vel, 0, 0))
        lineal(ob)


def lineal(ob):
    ad = ob.animation_data
    if not ad or not ad.action:
        return
    for fc in _curvas(ad.action):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"


def _curvas(action):
    try:
        return list(action.fcurves)
    except AttributeError:
        out = []
        for layer in action.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    out.extend(cb.fcurves)
        return out


def material_flujo(nombre, color, fase_por_frame=0.05, nf=100):
    """Emisión con pulsos que avanzan a lo largo de la línea (UV.x)."""
    m = bpy.data.materials.new(nombre); m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    uv = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(uv.outputs["UV"], sep.inputs[0])
    ad = nt.nodes.new("ShaderNodeMath"); ad.operation = "SUBTRACT"
    nt.links.new(sep.outputs["X"], ad.inputs[0])
    ad.inputs[1].default_value = 0.0
    ad.inputs[1].keyframe_insert("default_value", frame=1)
    ad.inputs[1].default_value = fase_por_frame * nf
    ad.inputs[1].keyframe_insert("default_value", frame=nf + 1)
    for fc in _curvas(m.node_tree.animation_data.action):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    fr = nt.nodes.new("ShaderNodeMath"); fr.operation = "FRACT"
    sc = nt.nodes.new("ShaderNodeMath"); sc.operation = "MULTIPLY"; sc.inputs[1].default_value = 1.5
    nt.links.new(ad.outputs[0], sc.inputs[0]); nt.links.new(sc.outputs[0], fr.inputs[0])
    pw = nt.nodes.new("ShaderNodeMath"); pw.operation = "POWER"; pw.inputs[1].default_value = 3.0
    nt.links.new(fr.outputs[0], pw.inputs[0])
    mr = nt.nodes.new("ShaderNodeMath"); mr.operation = "MULTIPLY_ADD"
    mr.inputs[1].default_value = 6.0; mr.inputs[2].default_value = 2.0
    nt.links.new(pw.outputs[0], mr.inputs[0])
    em = nt.nodes.new("ShaderNodeEmission"); em.inputs["Color"].default_value = hex2rgb(color)
    nt.links.new(mr.outputs[0], em.inputs["Strength"])
    out_s = em.outputs[0]
    nt.links.new(out_s, out.inputs["Surface"])
    return m


def tubo(nombre, puntos, radio, mat, padre=None, nseg=6):
    """Tubo fino a lo largo de una polilínea, con UV.x de 0 a 1 a lo largo."""
    pts = [Vector(p) for p in puntos]
    n = len(pts)
    largo = [0.0]
    for i in range(1, n):
        largo.append(largo[-1] + (pts[i] - pts[i - 1]).length)
    tot = largo[-1] or 1
    vs, fs, uvs = [], [], []
    for i, p in enumerate(pts):
        t = (pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized()
        a = t.cross(Vector((0, 0, 1)))
        if a.length < 1e-3:
            a = t.cross(Vector((0, 1, 0)))
        a.normalize(); b = t.cross(a).normalized()
        afinado = min(1.0, largo[i] / (tot * 0.08), (tot - largo[i]) / (tot * 0.08)) ** 0.5
        for k in range(nseg):
            ang = 2 * math.pi * k / nseg
            vs.append(p + (a * math.cos(ang) + b * math.sin(ang)) * radio * max(afinado, 0.05))
            uvs.append((largo[i] / tot * (tot / 40), k / nseg))
    for i in range(n - 1):
        for k in range(nseg):
            a_ = i * nseg + k; b_ = i * nseg + (k + 1) % nseg
            fs.append((a_, b_, b_ + nseg, a_ + nseg))
    ob = malla(nombre, [tuple(v) for v in vs], fs, uvs, padre=padre)
    asignar(ob, mat)
    ob.visible_shadow = False
    return ob


def nubes_cercanas(centro_x0, centro_x1, z, ancho=900, n=40, gris=False, semilla=3):
    rng = random.Random(semilla)
    col = "#F4F6F8" if not gris else "#8E98A3"
    m = material("nube3d", col, rough=1.0, emis=col, emis_f=0.45 if not gris else 0.5)
    tex_nube = bpy.data.textures.new("nube_tex", "CLOUDS")
    tex_nube.noise_scale = 0.35
    tex_nube.noise_depth = 2
    for i in range(n):
        x = rng.uniform(min(centro_x0, centro_x1) - 300, max(centro_x0, centro_x1) + 300)
        y = rng.uniform(-ancho, ancho)
        if abs(y) < 60:
            y += 120 * (1 if y >= 0 else -1)
        zz = z + rng.uniform(-150, 120)
        for k in range(rng.randint(3, 6)):
            r = rng.uniform(25, 70)
            bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=r,
                                                  location=(x + rng.uniform(-80, 80), y + rng.uniform(-40, 40), zz + rng.uniform(-15, 25)))
            ob = bpy.context.object
            ob.scale = (1.4, 1.0, 0.65)
            for p in ob.data.polygons:
                p.use_smooth = True
            dm = ob.modifiers.new("esponja", "DISPLACE")
            dm.texture = tex_nube
            dm.strength = r * 0.35
            asignar(ob, m)
            ob.visible_shadow = False


def humo(origen_fn, t0, t1, nf, fps, color="#3A3A3A", tam=1.5, crec=1.2, cada=2, fuego=False, deriva=(0, 0, 0)):
    """Estela de humo: bolas que nacen en origen_fn(t) y crecen con la edad."""
    m = bpy.data.materials.new("humo"); m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    df = nt.nodes.new("ShaderNodeBsdfDiffuse"); df.inputs["Color"].default_value = hex2rgb(color)
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    mx = nt.nodes.new("ShaderNodeMixShader")
    lw = nt.nodes.new("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.5
    nzh = nt.nodes.new("ShaderNodeTexNoise"); nzh.inputs["Scale"].default_value = 2.5
    a1 = nt.nodes.new("ShaderNodeMath"); a1.operation = "MULTIPLY_ADD"
    a1.inputs[1].default_value = 1.3; a1.inputs[2].default_value = -0.15
    nt.links.new(lw.outputs["Facing"], a1.inputs[0])
    a2 = nt.nodes.new("ShaderNodeMath"); a2.operation = "MULTIPLY_ADD"
    a2.inputs[1].default_value = 0.45
    nt.links.new(nzh.outputs["Fac"], a2.inputs[0]); nt.links.new(a1.outputs[0], a2.inputs[2])
    cl = nt.nodes.new("ShaderNodeClamp")
    nt.links.new(a2.outputs[0], cl.inputs["Value"])
    nt.links.new(cl.outputs[0], mx.inputs["Fac"])
    nt.links.new(df.outputs[0], mx.inputs[1]); nt.links.new(tr.outputs[0], mx.inputs[2])
    nt.links.new(mx.outputs[0], out.inputs["Surface"])
    mf = material("fuego", "#FF7A1A", emis="#FF8A2A", emis_f=12.0) if fuego else None
    for f0 in range(1, nf + 1, cada):
        t = (f0 - 1) / fps
        if t < t0 or t > t1:
            continue
        p = origen_fn(t)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1, location=p)
        ob = bpy.context.object
        for pp in ob.data.polygons:
            pp.use_smooth = True
        asignar(ob, m)
        ob.visible_shadow = False
        clave(ob, 1, esc=(0, 0, 0))
        clave(ob, max(f0 - 1, 1), esc=(0.001, 0.001, 0.001))
        for f in list(range(f0, nf + 1, 6)) + [nf]:
            edad = (f - f0) / fps
            s = tam * (1 + crec * edad)
            clave(ob, f, esc=(s, s, s), loc=Vector(p) + Vector(deriva) * edad)
        if mf:
            bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.6, location=p)
            fo = bpy.context.object
            for pp in fo.data.polygons:
                pp.use_smooth = True; asignar(fo, mf); fo.visible_shadow = False
            clave(fo, 1, esc=(0, 0, 0)); clave(fo, max(f0 - 1, 1), esc=(0, 0, 0))
            clave(fo, f0, esc=(tam * 0.8,) * 3); clave(fo, min(f0 + 5, nf), esc=(0, 0, 0))


# ---------------------------------------------------------------- anclas para etiquetas

def anclas_locales(info, es_heli):
    L = info["L"]
    if es_heli:
        H = info.get("H_c", 2)
        return {"rotor": [(L * 0.36, 0, H * 1.2)], "rotor_cola": [(L * 0.965, -1, H * 0.3 + L * 0.06)],
                "cabina": [(L * 0.08, 0, H * 0.2)], "morro": [(0.2, 0, 0)], "cola": [(L * 0.93, 0, H * 0.3 + L * 0.1)],
                "motor": [(L * 0.36, 0, H * 0.75)], "fuselaje": [(L * 0.3, 0, 0)], "tren": [(L * 0.3, -1.5, -H * 0.5)]}
    R = info.get("R", 1.5)
    semi = info.get("semi", 10)
    an = {"morro": [(0.3, 0, 0)], "cabina": [(info.get("fus_nariz", 5) * 0.55, 0, R * 0.7)],
          "fuselaje": [(L * 0.5, 0, R * 0.2)], "cola": [(L * 0.93, 0, R + L * 0.12)],
          "estabilizador": [(L * 0.93, s * L * 0.1, R * 0.3) for s in (-1, 1)]}
    if "secs" in info:
        secs = info["secs"]
        def punto(y_rel, cfrac, s):
            y = semi * y_rel
            # interpolar entre secciones
            ys = [abs(q[1]) for q in secs]
            for i in range(len(secs) - 1):
                if ys[i] <= y <= ys[i + 1]:
                    t = (y - ys[i]) / (ys[i + 1] - ys[i])
                    x = secs[i][0] + t * (secs[i + 1][0] - secs[i][0])
                    c = secs[i][3] + t * (secs[i + 1][3] - secs[i][3])
                    z = secs[i][2] + t * (secs[i + 1][2] - secs[i][2])
                    return (x + c * cfrac, s * y, z + c * 0.05)
            return (secs[-1][0], s * y, secs[-1][2])
        an["ala"] = [punto(0.55, 0.35, s) for s in (-1, 1)]
        an["flaps"] = [punto(0.3, 0.92, s) for s in (-1, 1)]
        an["alerones"] = [punto(0.82, 0.9, s) for s in (-1, 1)]
        an["borde_ataque"] = [punto(0.5, 0.0, s) for s in (-1, 1)]
    if info.get("motores"):
        an["motor"] = [(m[0] + m[4] * 0.1, m[1], m[2]) for m in info["motores"]]
    an["tren"] = [(info.get("ala_x", L * 0.4) + info.get("c_raiz", 5) * 0.62, s * R * 1.35, -R - 1.2) for s in (-1, 1)]
    an["morro"] = [(0.2, 0, -R * 0.2)]
    return an


def preparar_anclas(raiz, info, es_heli, extra=None):
    loc = anclas_locales(info, es_heli)
    if extra:
        loc.update(extra)
    sc = bpy.context.scene
    cam = sc.camera

    def fn(frame):
        sc.frame_set(frame)
        mw = raiz.matrix_world
        res = {}
        for nombre, pts in loc.items():
            mejor = None
            for p in pts:
                w = mw @ Vector(p)
                d = (w - cam.matrix_world.translation).length
                v = world_to_camera_view(sc, cam, w)
                vis = v.z > 0 and 0.03 < v.x < 0.97 and 0.03 < v.y < 0.97
                if mejor is None or d < mejor[0]:
                    mejor = (d, (round(v.x, 4), round(1 - v.y, 4), bool(vis)))
            res[nombre] = mejor[1]
        return res
    return fn


# ---------------------------------------------------------------- escenario común

def escenario(esc, aire=False, pista=True, seed=1):
    hora = esc.get("hora", "noche" if esc.get("noche") else "dia")
    clima = "tormenta" if esc.get("tormenta") else ("noche" if hora == "noche" else ("atardecer" if hora == "atardecer" else "normal"))
    E.cielo(tormenta=1.0 if esc.get("tormenta") else 0.0, noche=(hora == "noche"), atardecer=(hora == "atardecer"),
            nubes=0.5 if not esc.get("tormenta") else 0.9, sol_rot=esc.get("sol", 150))
    terreno = esc.get("terreno", "hierba")
    if hora in ("atardecer", "noche"):
        rl = bpy.data.lights.new("relleno", "SUN"); rl.energy = 1.3 if hora == "atardecer" else 0.9
        rl.color = (0.62, 0.72, 1.0); rl.angle = math.radians(20)
        ro = bpy.data.objects.new("relleno", rl); bpy.context.scene.collection.objects.link(ro)
        ro.rotation_euler = (math.radians(55), 0, math.radians(-40))
    if aire:
        E.suelo(pista=False, terreno=terreno, tam=40000, bruma=9000, clima=clima)
    else:
        E.suelo(pista=pista, terreno=terreno if terreno not in ("mar",) else "hierba", tam=12000, bruma=7000, clima=clima)
        if terreno != "ciudad":
            E.arboles(450, semilla=seed, tipo=terreno)
        if terreno not in ("montana", "nieve", "ciudad"):
            E.colinas(tipo=terreno)


def aeronave(aer, tren=True):
    modelo = aer.get("modelo", "a320")
    cfg = dict(aer.get("colores") or {})
    if aer.get("texto") is not None:
        cfg["texto"] = aer["texto"]
    if modelo.startswith("heli"):
        raiz, info = A.crear_helicoptero(modelo.split("_", 1)[1] if "_" in modelo else "medio", cfg)
        return raiz, info, True
    if modelo == "caza":
        raiz, info = A.crear_caza(cfg)
        return raiz, info, False
    raiz, info = A.crear_avion(modelo, cfg, tren=tren)
    return raiz, info, False


# ---------------------------------------------------------------- tomas

def t_pista(tk, nf, fps, rng):
    escenario(tk["escenario"], pista=True, seed=tk["semilla"])
    raiz, info, heli = aeronave(tk["aeronave"])
    alt = info["altura_tren"]
    raiz.location = (0, 0, alt)
    L = info["L"]
    cam, obj = E.camara(lente=32 if not heli else 30, dof=None)
    a0 = rng.choice([-2.5, -2.0, -1.2, -0.6, 0.6])
    da = rng.choice([0.35, -0.35, 0.5])
    r = max(L * 0.95, 14)
    h = rng.choice([2.0, 3.5, 6.0]) if not heli else 3
    for f in range(1, nf + 1):
        t = (f - 1) / max(nf - 1, 1)
        a = a0 + da * easing(t)
        rr = r * (1 - 0.12 * easing(t))
        clave(cam, f, loc=(L * 0.45 + rr * math.cos(a), rr * math.sin(a), h))
    clave(obj, 1, loc=(L * 0.45, 0, alt * 0.8))
    if heli:
        girar_rotores(info, nf, fps, vel=0.25)
    return raiz, info, heli


def t_rodaje(tk, nf, fps, rng):
    escenario(tk["escenario"], pista=True, seed=tk["semilla"])
    raiz, info, heli = aeronave(tk["aeronave"])
    alt = info["altura_tren"]; L = info["L"]
    cam, obj = E.camara(lente=30)
    v = 12.0
    lado = rng.choice([-1, 1])
    for f in range(1, nf + 1):
        t = (f - 1) / fps
        x = 400 - v * t
        clave(raiz, f, loc=(x, 0, alt), rot=(0, 0, 0))
        k = easing((f - 1) / max(nf - 1, 1))
        clave(cam, f, loc=(x - L * 0.35 - 6 * k, lado * (L * 0.55 + 3 * k), 2.5))
        clave(obj, f, loc=(x + L * 0.35, 0, alt))
    lineal(raiz); lineal(cam); lineal(obj)
    girar_ventiladores(info, nf, 0.6)
    return raiz, info, heli


def t_despegue(tk, nf, fps, rng):
    escenario(tk["escenario"], pista=True, seed=tk["semilla"])
    raiz, info, heli = aeronave(tk["aeronave"])
    alt = info["altura_tren"]; L = info["L"]
    T = (nf - 1) / fps
    tr = T * 0.45          # rotación
    vr = 75.0
    a = vr / max(tr, 0.5)
    x0 = 500.0
    def estado(t):
        if t <= tr:
            x = x0 - 0.5 * a * t * t
            return x, alt, 0.0
        dt = t - tr
        x = x0 - 0.5 * a * tr * tr - vr * dt - 0.5 * 1.5 * dt * dt
        cab = math.radians(9) * easing(dt / 2.5)
        z = alt + (0 if dt < 1.2 else 0.5 * 4.0 * (dt - 1.2) ** 2 if dt < 3.2 else 8 + 8 * (dt - 3.2))
        return x, z, cab
    xl, _, _ = estado(tr + 1.2)
    lado = rng.choice([-1, 1])
    camp = (xl - 40, lado * 75, 3.0)
    cam, obj = E.camara(lente=rng.choice([60, 75]))
    clave(cam, 1, loc=camp)
    for f in range(1, nf + 1):
        t = (f - 1) / fps
        x, z, cab = estado(t)
        clave(raiz, f, loc=(x, 0, z), rot=(0, cab, 0))
        clave(obj, f, loc=(x + L * 0.4, 0, z))
    lineal(raiz); lineal(obj)
    girar_ventiladores(info, nf, 1.0)
    return raiz, info, heli


def t_aterrizaje(tk, nf, fps, rng):
    escenario(tk["escenario"], pista=True, seed=tk["semilla"])
    raiz, info, heli = aeronave(tk["aeronave"])
    alt = info["altura_tren"]; L = info["L"]
    T = (nf - 1) / fps
    ttd = T * 0.6
    v = 68.0
    xtd = -300.0
    def estado(t):
        if t < ttd:
            dx = v * (ttd - t)
            x = xtd + dx
            z = alt + dx * math.tan(math.radians(3)) * easing(min(1, (ttd - t) / 3.0)) ** 0.6 if dx < 170 else alt + dx * math.tan(math.radians(3))
            return x, z, math.radians(4)
        dt = t - ttd
        x = xtd - v * dt + 0.5 * 2.5 * dt * dt
        return x, alt, math.radians(4) * max(0, 1 - dt / 2.0)
    lado = rng.choice([-1, 1])
    cam, obj = E.camara(lente=65)
    clave(cam, 1, loc=(xtd - 40, lado * 80, 2.5))
    for f in range(1, nf + 1):
        t = (f - 1) / fps
        x, z, cab = estado(t)
        clave(raiz, f, loc=(x, 0, z), rot=(0, cab, 0))
        clave(obj, f, loc=(x + L * 0.4, 0, z))
    lineal(raiz); lineal(obj)
    # humo de neumáticos al tocar
    ftd = int(ttd * fps) + 1
    for s_ in (-1, 1):
        humo(lambda t, s_=s_: (xtd - v * (t - ttd) + L * 0.45, s_ * info["R"] * 1.35, 0.6), ttd, ttd + 0.5,
             nf, fps, color="#E8E8E8", tam=0.9, crec=2.2, cada=1)
    girar_ventiladores(info, nf, 0.8)
    return raiz, info, heli


def _vuelo_generico(tk, nf, fps, rng, modo="vuelo"):
    esc = tk["escenario"]
    escenario(esc, aire=True, seed=tk["semilla"])
    raiz, info, heli = aeronave(tk["aeronave"], tren=False)
    L = info["L"]
    p = tk.get("params", {})
    H = float(p.get("altitud_m", 900 if not heli else 250))
    if esc.get("terreno") in ("montana", "nieve"):
        H = max(H, 2600)
    v = 85.0 if not heli else 55.0
    T = (nf - 1) / fps
    camaras = {
        "lateral": [(-L * 0.25, -L * 1.25, -L * 0.02), (L * 0.05, -L * 1.05, L * 0.04)],
        "persecucion": [(L * 1.5, -L * 0.55, L * 0.12), (L * 1.2, -L * 0.3, L * 0.08)],
        "frontal": [(-L * 1.6, -L * 0.45, L * 0.05), (-L * 1.3, -L * 0.7, L * 0.15)],
        "abajo": [(-L * 0.3, -L * 0.8, -L * 0.55), (L * 0.2, -L * 0.6, -L * 0.45)],
        "arriba": [(L * 0.2, -L * 0.7, L * 0.9), (-L * 0.1, -L * 0.5, L * 0.8)],
    }
    estilo = p.get("camara") or rng.choice(["lateral", "persecucion", "frontal", "lateral", "abajo"])
    c0, c1 = camaras.get(estilo, camaras["lateral"])
    cam, obj = E.camara(lente=35)
    radio_v = 1800.0
    for f in range(1, nf + 1):
        t = (f - 1) / fps
        k = easing((f - 1) / max(nf - 1, 1))
        if modo == "viraje":
            ang = -v * t / radio_v
            pos = (radio_v * math.sin(ang) * -1, radio_v - radio_v * math.cos(ang), H)
            rot = (math.radians(-28), 0, -ang * -1 * -1)
            pos = Vector((-radio_v * math.sin(v * t / radio_v), -radio_v * (1 - math.cos(v * t / radio_v)), H))
            rot = (math.radians(28), 0, v * t / radio_v)
        elif modo == "emergencia":
            cab = -math.radians(p.get("picado_grados", 12)) * easing(t / 3)
            pos = Vector((-v * t, 0, H - v * t * math.sin(-cab if cab < 0 else 0) * 0.9))
            alab = math.radians(14) * math.sin(t * 0.9) + math.radians(6) * math.sin(t * 2.3)
            rot = (alab, cab, 0)
        else:
            pos = Vector((-v * t, 0, H + 3 * math.sin(t * 0.7)))
            alab = math.radians(3) * math.sin(t * 0.5)
            cab = math.radians(2) if not heli else -math.radians(6)
            rot = (alab, cab, 0)
        clave(raiz, f, loc=pos, rot=rot)
        off = Vector(c0).lerp(Vector(c1), k)
        temblor = Vector((0, 0, 0))
        if modo == "emergencia" or esc.get("tormenta"):
            temblor = Vector((math.sin(f * 1.7) * 0.15, math.sin(f * 2.3) * 0.15, math.sin(f * 3.1) * 0.2)) * (L / 30)
        clave(cam, f, loc=a_local(pos, rot, off) + temblor)
        clave(obj, f, loc=a_local(pos, rot, (L * 0.45, 0, 0)))
    lineal(raiz); lineal(cam); lineal(obj)
    x_fin = -v * T
    if esc.get("hora", "dia") == "dia" or esc.get("tormenta"):
        nubes_cercanas(200, x_fin - 400, H + rng.uniform(-60, 120), gris=bool(esc.get("tormenta")),
                       n=int(10 + T * 3), semilla=tk["semilla"])
    if heli:
        girar_rotores(info, nf, fps, 1.0)
    else:
        girar_ventiladores(info, nf, 1.0)
    if modo == "emergencia" and info.get("motores") and p.get("fuego", True):
        m0 = min(info["motores"], key=lambda m: m[1])
        def origen(t, m0=m0):
            sc_ = bpy.context.scene
            sc_.frame_set(int(t * fps) + 1)
            return raiz.matrix_world @ Vector((m0[0] + m0[4] * 1.05, m0[1], m0[2]))
        humo(origen, 0.0, T, nf, fps, color="#7A7E83", tam=max(1.5, L / 14), crec=2.2, cada=1, fuego=True, deriva=(0, 0, 3))
    return raiz, info, heli


def t_vuelo(tk, nf, fps, rng):
    return _vuelo_generico(tk, nf, fps, rng, "vuelo")


def t_viraje(tk, nf, fps, rng):
    return _vuelo_generico(tk, nf, fps, rng, "viraje")


def t_emergencia(tk, nf, fps, rng):
    tk["escenario"].setdefault("tormenta", True)
    return _vuelo_generico(tk, nf, fps, rng, "emergencia")


def t_heli_vuelo(tk, nf, fps, rng):
    return _vuelo_generico(tk, nf, fps, rng, "vuelo")


def t_ala_flujo(tk, nf, fps, rng):
    esc = tk["escenario"]
    escenario(esc, aire=True, seed=tk["semilla"])
    p = tk.get("params", {})
    aer = dict(tk["aeronave"])
    if aer.get("modelo", "").startswith("heli") or aer.get("modelo") == "caza":
        aer["modelo"] = "a320"
    raiz, info, _ = aeronave(aer, tren=False)
    L = info["L"]; secs = info["secs"]
    H = 1500.0
    aoa = math.radians(float(p.get("angulo", 6)))
    perdida = bool(p.get("perdida", False))
    raiz.location = (0, 0, H)
    raiz.rotation_euler = (0, aoa, 0)
    bpy.context.view_layer.update()
    mw = raiz.matrix_world
    af = naca("2414", 80)
    sup = sorted([q for q in af[:40]], key=lambda q: q[0])
    inf = sorted([q for q in af[40:]], key=lambda q: q[0])
    def z_sup(xc):
        return float(np.interp(xc, [q[0] for q in sup], [q[1] for q in sup]))
    def z_inf(xc):
        return float(np.interp(xc, [q[0] for q in inf], [q[1] for q in inf]))
    colores_arriba = ["#3FD0FF", "#5BE8C8", "#9CF06A", "#3FA9FF", "#6AF0E0"]
    colores_abajo = ["#FFD84A", "#FFB23F", "#FFE27A"]
    mats = {}
    def mat(c):
        if c not in mats:
            mats[c] = material_flujo("flujo" + c, c, 0.03, nf)
        return mats[c]
    estaciones = [0.22, 0.42, 0.62, 0.80]
    lado = -1
    for yi, yr in enumerate(estaciones):
        y = info["semi"] * yr
        # sección local en esa envergadura
        ys = [abs(q[1]) for q in secs]
        for i in range(len(secs) - 1):
            if ys[i] <= y <= ys[i + 1]:
                t_ = (y - ys[i]) / (ys[i + 1] - ys[i])
                xle = secs[i][0] + t_ * (secs[i + 1][0] - secs[i][0])
                c = secs[i][3] + t_ * (secs[i + 1][3] - secs[i][3])
                zl = secs[i][2] + t_ * (secs[i + 1][2] - secs[i][2])
                break
        else:
            continue
        for j, (h, arriba) in enumerate([(0.05, True), (0.14, True), (0.28, True), (0.08, False)]):
            pts = []
            for xs in np.linspace(-1.6, 2.6, 70):
                if arriba:
                    base = z_sup(min(max(xs, 0), 1)) if 0 <= xs <= 1 else (z_sup(0) if xs < 0 else z_sup(1))
                    gap = h * (0.55 + 0.45 * min(1, abs(xs - 0.3) / 1.2))
                    zc = base + gap
                    if xs < 0:
                        zc = h * (1 - math.exp(-(xs * 2) ** 2)) + base * math.exp(-(xs * 2) ** 2) + gap * math.exp(-(xs * 2) ** 2) * 0
                        zc = max(zc, h)
                    if xs > 1:
                        zc = z_sup(1) + gap - (xs - 1) * 0.10
                    if perdida and xs > 0.35:
                        k = (xs - 0.35)
                        zc += k * 0.18 + 0.05 * math.sin(xs * 14 + j * 1.7 + yi) * min(1, k * 2)
                else:
                    base = z_inf(min(max(xs, 0), 1))
                    zc = base - h * (0.6 + 0.4 * min(1, abs(xs - 0.4) / 1.2))
                    if xs > 1:
                        zc -= (xs - 1) * 0.06
                # local avión -> mundo
                loc = Vector((xle + xs * c, lado * y, zl + zc * c))
                pts.append(mw @ loc)
            col = colores_arriba[(j + yi) % len(colores_arriba)] if arriba else colores_abajo[(j + yi) % len(colores_abajo)]
            tubo(f"flujo{yi}_{j}", pts, 0.022 * c / 4 + 0.012, mat(col))
    # cámara: junto al fuselaje, por encima del ala, mirando hacia la punta
    cam, obj = E.camara(lente=26)
    xr, zr, cr_ = secs[1][0], secs[1][2], secs[1][3]
    R = info["R"]
    estilo = p.get("camara") or rng.choice(["raiz", "trasera"])
    if estilo == "raiz":
        c0 = mw @ Vector((xr + cr_ * 1.6, R * 0.6, zr + R * 3.6))
        c1 = mw @ Vector((xr + cr_ * 1.3, R * 0.3, zr + R * 3.2))
        tgt = mw @ Vector((xr + cr_ * 0.15 + info["semi"] * 0.42 * math.tan(info["flecha"]), -info["semi"] * 0.42, zr))
    else:
        c0 = mw @ Vector((xr + cr_ * 2.6, -info["semi"] * 0.25, zr + R * 1.6))
        c1 = mw @ Vector((xr + cr_ * 2.2, -info["semi"] * 0.32, zr + R * 1.3))
        tgt = mw @ Vector((xr + info["semi"] * 0.4 * math.tan(info["flecha"]), -info["semi"] * 0.42, zr))
    for f in range(1, nf + 1):
        k = easing((f - 1) / max(nf - 1, 1))
        clave(cam, f, loc=c0.lerp(c1, k))
    clave(obj, 1, loc=tgt)
    return raiz, info, False


def t_motor(tk, nf, fps, rng):
    esc = tk["escenario"]
    p = tk.get("params", {})
    en_tierra = p.get("en_tierra", False)
    escenario(esc, aire=not en_tierra, pista=True, seed=tk["semilla"])
    aer = dict(tk["aeronave"])
    if aer.get("modelo", "").startswith("heli"):
        aer["modelo"] = "a320"
    raiz, info, _ = aeronave(aer, tren=en_tierra)
    L = info["L"]
    H = info["altura_tren"] if en_tierra else 1500
    raiz.location = (0, 0, H)
    bpy.context.view_layer.update()
    mot = [m for m in info["motores"] if m[1] <= 0] or info["motores"]
    mx, my, mz, rm, lm = mot[0]
    mw = raiz.matrix_world
    cen = mw @ Vector((mx, my, mz))
    cam, obj = E.camara(lente=35)
    estilo = p.get("camara") or rng.choice(["frente", "lado"])
    if estilo == "frente":
        c0 = cen + Vector((-rm * 7.5, -rm * 3.2, rm * 0.9))
        c1 = cen + Vector((-rm * 5.0, -rm * 2.2, rm * 0.5))
    else:
        c0 = cen + Vector((lm * 0.3, -rm * 5.5, rm * 1.4))
        c1 = cen + Vector((-lm * 0.4, -rm * 4.8, rm * 0.8))
    for f in range(1, nf + 1):
        k = easing((f - 1) / max(nf - 1, 1))
        clave(cam, f, loc=c0.lerp(c1, k))
    clave(obj, 1, loc=cen + Vector((lm * 0.3, 0, 0)))
    estado = p.get("estado", "normal")
    girar_ventiladores(info, nf, 0.15 if estado in ("apagado", "fuego") else 1.2)
    if estado == "fuego":
        T = (nf - 1) / fps
        salida = mw @ Vector((mx + lm * 1.02, my, mz))
        humo(lambda t: salida, 0, T, nf, fps, color="#55585C", tam=rm * 0.35, crec=2.5, cada=2, fuego=True,
             deriva=(45, 0, 3))
    return raiz, info, False


def t_globo(tk, nf, fps, rng):
    p = tk.get("params", {})
    sc = bpy.context.scene
    w = bpy.data.worlds.new("espacio"); sc.world = w; w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = hex2rgb("#0B1A2E"); bg.inputs["Strength"].default_value = 1.0
    sol = bpy.data.lights.new("sol", "SUN"); sol.energy = 4.0
    so = bpy.data.objects.new("sol", sol); sc.collection.objects.link(so)
    so.rotation_euler = (math.radians(50), 0, math.radians(-30))
    R = 10.0
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=R)
    tierra = bpy.context.object
    for pp in tierra.data.polygons:
        pp.use_smooth = True
    ruta_tex = os.path.join(ASSETS, "tierra.jpg")
    mt = A.material_imagen("tierra", ruta_tex, rough=0.6, coat=0)
    asignar(tierra, mt)
    # atmósfera
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=R * 1.025)
    atm = bpy.context.object
    for pp in atm.data.polygons:
        pp.use_smooth = True
    m = bpy.data.materials.new("atm"); m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    lw = nt.nodes.new("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.35
    em = nt.nodes.new("ShaderNodeEmission"); em.inputs["Color"].default_value = hex2rgb("#7CC4FF"); em.inputs["Strength"].default_value = 2.5
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    mx = nt.nodes.new("ShaderNodeMixShader")
    pw = nt.nodes.new("ShaderNodeMath"); pw.operation = "POWER"; pw.inputs[1].default_value = 2.5
    nt.links.new(lw.outputs["Facing"], pw.inputs[0])
    nt.links.new(pw.outputs[0], mx.inputs["Fac"])
    nt.links.new(tr.outputs[0], mx.inputs[1]); nt.links.new(em.outputs[0], mx.inputs[2])
    nt.links.new(mx.outputs[0], out.inputs["Surface"])
    asignar(atm, m); atm.visible_shadow = False

    def xyz(lat, lon, r=R):
        la, lo = math.radians(lat), math.radians(lon)
        # la textura equirectangular de Blender: lon=0 en -Y? se ajusta con u=0.5 en +X
        return Vector((r * math.cos(la) * math.cos(lo), r * math.cos(la) * math.sin(lo), r * math.sin(la)))
    puntos = p.get("puntos") or []
    o = puntos[0] if puntos else {"lat": 40.4, "lon": -3.7, "nombre": "Madrid"}
    d = puntos[-1] if len(puntos) > 1 else None
    # arco
    extra = {}
    marcador = material("marcador", "#E8772E", emis="#E8772E", emis_f=3.0)
    for i, q in enumerate(puntos[:6]):
        pos = xyz(q["lat"], q["lon"], R * 1.003)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=R * 0.012, location=pos)
        asignar(bpy.context.object, marcador)
        extra[f"p{i}"] = [tuple(pos)]
    va = xyz(o["lat"], o["lon"]).normalized()
    if d:
        vb = xyz(d["lat"], d["lon"]).normalized()
        ang = va.angle(vb)
        pts = []
        for k in range(120):
            t = k / 119
            vv = (va * math.sin((1 - t) * ang) + vb * math.sin(t * ang)) / max(math.sin(ang), 1e-6) if ang > 1e-3 else va
            h = 1.0 + 0.10 * ang * math.sin(math.pi * t)
            pts.append(vv.normalized() * R * h)
        T = (nf - 1) / fps
        curva = bpy.data.curves.new("ruta", "CURVE"); curva.dimensions = "3D"
        sp = curva.splines.new("POLY"); sp.points.add(len(pts) - 1)
        for k, q in enumerate(pts):
            sp.points[k].co = (q.x, q.y, q.z, 1)
        curva.bevel_depth = R * 0.004
        ob = bpy.data.objects.new("ruta", curva); sc.collection.objects.link(ob)
        asignar(ob, material("ruta", "#FFB45C", emis="#FFB45C", emis_f=4.0))
        f_ini = int(nf * 0.25); f_fin = int(nf * 0.8)
        curva.bevel_factor_end = 0.0; curva.keyframe_insert("bevel_factor_end", frame=f_ini)
        curva.bevel_factor_end = 1.0; curva.keyframe_insert("bevel_factor_end", frame=f_fin)
        # punta de flecha
        bpy.ops.mesh.primitive_cone_add(vertices=16, radius1=R * 0.02, depth=R * 0.05)
        punta = bpy.context.object
        asignar(punta, material("punta", "#FFFFFF", emis="#FFFFFF", emis_f=2.0))
        for f in range(1, nf + 1, 2):
            t = min(1, max(0, (f - f_ini) / max(f_fin - f_ini, 1)))
            i = min(int(t * 119), 118)
            dirv = (pts[i + 1] - pts[i]).normalized()
            punta.location = pts[i]
            punta.rotation_euler = dirv.to_track_quat("Z", "Y").to_euler()
            punta.keyframe_insert("location", frame=f); punta.keyframe_insert("rotation_euler", frame=f)
            esc = 0.0 if f < f_ini else 1.0
            punta.scale = (esc, esc, esc); punta.keyframe_insert("scale", frame=f)
        centro = (va + vb).normalized()
        dist_f = R * (1.9 + 0.9 * ang)
    else:
        centro = va
        dist_f = R * 2.0
    cam, obj = E.camara(lente=40)
    lado = centro.cross(Vector((0, 0, 1))).normalized()
    if lado.length < 0.5:
        lado = Vector((1, 0, 0))
    for f in range(1, nf + 1):
        k = easing((f - 1) / max(nf - 1, 1))
        dist = R * 4.2 * (1 - k) + dist_f * k
        dirc = (centro + lado * 0.25 * (1 - k) + Vector((0, 0, -0.15 * k))).normalized()
        clave(cam, f, loc=dirc * dist)
        clave(obj, f, loc=centro * R * k * 0.9)
    tierra.rotation_euler = (0, 0, 0)
    raiz = bpy.data.objects.new("AVION", None); sc.collection.objects.link(raiz)
    return raiz, {"L": 1, "extra": extra}, False


def t_heli_estacionario(tk, nf, fps, rng):
    escenario(tk["escenario"], pista=False, seed=tk["semilla"])
    aer = dict(tk["aeronave"])
    if not aer.get("modelo", "").startswith("heli"):
        aer["modelo"] = "heli_medio"
    raiz, info, _ = aeronave(aer)
    L = info["L"]
    alt0 = info["altura_tren"]
    h = 7.0
    # helipuerto
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=11, depth=0.2, location=(L * 0.36, 0, 0.1))
    asignar(bpy.context.object, material("helipad", "#5A5E64", rough=0.9))
    bpy.ops.mesh.primitive_torus_add(major_radius=9.5, minor_radius=0.25, location=(L * 0.36, 0, 0.21))
    asignar(bpy.context.object, material("hring", "#F2F2EC", rough=0.8))
    hm = material("hletra", "#F2F2EC", rough=0.8)
    for (dx, dy, sx, sy) in ((0, -2.2, 4.6, 0.6), (0, 2.2, 4.6, 0.6), (0, 0, 0.6, 4.4)):
        bpy.ops.mesh.primitive_cube_add(size=1, location=(L * 0.36 + dx, dy, 0.22))
        o = bpy.context.object; o.scale = (sx, sy, 0.02); asignar(o, hm)
    for f in range(1, nf + 1):
        t = (f - 1) / fps
        k = easing(t / 3.0)
        clave(raiz, f, loc=(0, 0, alt0 + h * k + 0.3 * math.sin(t * 1.3)), rot=(math.radians(1.5) * math.sin(t), -math.radians(2), 0))
    lineal(raiz)
    girar_rotores(info, nf, fps, 1.0)
    if tk.get("params", {}).get("flujo", True):
        mf = material_flujo("downwash", "#5BD8FF", 0.04, nf)
        rr = L * 0.58
        cx = L * 0.36
        for k in range(8):
            a = 2 * math.pi * k / 8 + 0.2
            for rf in (0.7,):
                x0 = cx + rr * rf * math.cos(a); y0 = rr * rf * math.sin(a)
                pts = []
                for s in np.linspace(0, 1, 40):
                    z = alt0 + h + info["H_c"] * 1.1 - s * (h + info["H_c"] * 1.1)
                    spread = 1 + 0.35 * s ** 2
                    pts.append((cx + (x0 - cx) * spread, y0 * spread, max(z, 0.3)))
                tubo(f"dw{k}{rf}", pts, 0.045, mf)
    cam, obj = E.camara(lente=30)
    a0 = rng.choice([-2.2, -1.4, -0.8])
    for f in range(1, nf + 1):
        k = easing((f - 1) / max(nf - 1, 1))
        a = a0 + 0.4 * k
        r = L * 2.0
        clave(cam, f, loc=(L * 0.36 + r * math.cos(a), r * math.sin(a), 3.0 + 2 * k))
    clave(obj, 1, loc=(L * 0.36, 0, h * 0.75))
    return raiz, info, True


def t_heli_rotor(tk, nf, fps, rng):
    escenario(tk["escenario"], aire=True, seed=tk["semilla"])
    aer = dict(tk["aeronave"])
    if not aer.get("modelo", "").startswith("heli"):
        aer["modelo"] = "heli_medio"
    raiz, info, _ = aeronave(aer)
    L = info["L"]; Hc = info["H_c"]
    H = 600.0
    raiz.location = (0, 0, H)
    bpy.context.view_layer.update()
    girar_rotores(info, nf, fps, 0.45)
    cx = L * 0.36
    mf = material_flujo("rotorflujo", "#5BD8FF", 0.035, nf)
    rr = L * 0.58
    zr = H + Hc * 1.16
    for k in range(9):
        a = 2 * math.pi * k / 9 + 0.3
        rf = 0.55 + 0.3 * (k % 2)
        x0 = cx + rr * rf * math.cos(a); y0 = rr * rf * math.sin(a)
        pts = [(x0 * 1.0 + (x0 - cx) * 0.25 * s_ ** 2, y0 + y0 * 0.25 * s_ ** 2, zr + 2.5 - s_ * 7.5) for s_ in np.linspace(0, 1, 30)]
        tubo(f"rf{k}", pts, 0.035, mf)
    cam, obj = E.camara(lente=28)
    for f in range(1, nf + 1):
        k = easing((f - 1) / max(nf - 1, 1))
        clave(cam, f, loc=(cx - L * 0.75 + L * 0.15 * k, -L * 0.85 + L * 0.1 * k, zr + L * 0.55 - L * 0.1 * k))
    clave(obj, 1, loc=(cx, 0, zr - Hc * 0.6))
    return raiz, info, True


def t_cenital(tk, nf, fps, rng):
    """Vista desde arriba del avión en la pista o en vuelo sobre el terreno."""
    esc = tk["escenario"]
    escenario(esc, pista=True, seed=tk["semilla"])
    raiz, info, heli = aeronave(tk["aeronave"])
    L = info["L"]
    raiz.location = (0, 0, info["altura_tren"])
    cam, obj = E.camara(lente=30)
    for f in range(1, nf + 1):
        k = easing((f - 1) / max(nf - 1, 1))
        clave(cam, f, loc=(L * 0.5 + 0.1, -2 + 4 * k, L * (1.7 - 0.4 * k)))
        clave(obj, f, loc=(L * 0.5, 0, 0))
    if heli:
        girar_rotores(info, nf, fps, 0.3)
    return raiz, info, heli


def t_rayos_x(tk, nf, fps, rng):
    """Vista de rayos X: el aparato en líneas de alambre y la pieza clave resaltada en rojo."""
    sc = bpy.context.scene
    w = bpy.data.worlds.new("oscuro"); sc.world = w; w.use_nodes = True
    w.node_tree.nodes["Background"].inputs["Color"].default_value = hex2rgb("#0E1520")
    p = tk.get("params", {})
    raiz, info, heli = aeronave(tk["aeronave"], tren=True)
    L = info["L"]
    raiz.location = (0, 0, 0)
    pieza = (p.get("pieza") or "motor").lower()
    claves = {"motor": ("motor", "fan", "alabes", "cono", "carenado", "pala"), "rotor": ("ROTOR",),
              "rotor_cola": ("ROTOR_COLA",), "tren": ("rueda", "pata", "patin"), "ala": ("ala", "sharklet"),
              "cola": ("deriva", "estab"), "cabina": ("cupula",), "fuselaje": ("fuselaje",)}.get(pieza, (pieza,))
    alambre = material("alambre", "#CFE3F5", emis="#CFE3F5", emis_f=1.6)
    rojo = material("resalte", "#FF3B30", emis="#FF3B30", emis_f=4.0)
    vidrio = bpy.data.materials.new("vidrio"); vidrio.use_nodes = True
    nt = vidrio.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    em = nt.nodes.new("ShaderNodeEmission"); em.inputs["Color"].default_value = hex2rgb("#3C5B7A"); em.inputs["Strength"].default_value = 0.6
    lw = nt.nodes.new("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.3
    mx = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(lw.outputs["Facing"], mx.inputs["Fac"])
    nt.links.new(tr.outputs[0], mx.inputs[1]); nt.links.new(em.outputs[0], mx.inputs[2])
    nt.links.new(mx.outputs[0], out.inputs["Surface"])
    def todos(o):
        for c in o.children:
            yield c
            yield from todos(c)
    for ob in list(todos(raiz)):
        if ob.type != "MESH":
            continue
        if heli and pieza == "motor" and "pala" in ob.name:
            resaltar = False
        else:
            resaltar = any(ob.name.startswith(k) or ("_" + k) in ob.name or bool(ob.parent and ob.parent.name.startswith(k)) for k in claves)
        ob.data.materials.clear()
        ob.data.materials.append(rojo if resaltar else vidrio)
        ob.visible_shadow = False
        if not resaltar:
            wm = ob.modifiers.new("alambre", "WIREFRAME")
            wm.thickness = max(0.008, L * 0.0005)
            wm.use_even_offset = False
            wm.use_replace = False
            wm.material_offset = 1
            ob.data.materials.append(alambre)
    cam, obj = E.camara(lente=32)
    a0 = rng.choice([-2.4, -1.9, -1.0])
    for f in range(1, nf + 1):
        k = easing((f - 1) / max(nf - 1, 1))
        a = a0 + 0.6 * k
        r = L * (1.15 - 0.15 * k)
        clave(cam, f, loc=(L * 0.45 + r * math.cos(a), r * math.sin(a), L * 0.28))
    clave(obj, 1, loc=(L * 0.45, 0, 0))
    if heli:
        girar_rotores(info, nf, fps, 0.2)
    sc.cycles.use_fast_gi = False
    return raiz, info, heli


FUNCIONES = {
    "rayos_x": t_rayos_x,
    "pista": t_pista, "rodaje": t_rodaje, "despegue": t_despegue, "aterrizaje": t_aterrizaje,
    "vuelo": t_vuelo, "viraje": t_viraje, "emergencia": t_emergencia, "ala_flujo": t_ala_flujo,
    "motor": t_motor, "globo": t_globo, "heli_estacionario": t_heli_estacionario,
    "heli_vuelo": t_heli_vuelo, "heli_rotor": t_heli_rotor, "cenital": t_cenital,
}


def construir(tk):
    """Monta la escena de la tarea. Devuelve la función de anclas."""
    E.limpiar()
    rng = random.Random(tk["semilla"])
    random.seed(tk["semilla"])
    E.render_cfg(tk["ancho"], tk["alto"], tk.get("muestras", 8), tk.get("fps", 24))
    nf = tk["frames"]
    fps = tk.get("fps", 24)
    tk.setdefault("escenario", {})
    tk.setdefault("aeronave", {"modelo": "a320"})
    toma = tk.get("toma", "vuelo")
    es_heli_aer = tk["aeronave"].get("modelo", "").startswith("heli")
    if es_heli_aer and toma in ("vuelo", "viraje", "emergencia"):
        toma = "heli_vuelo" if toma != "emergencia" else "emergencia"
    if es_heli_aer and toma in ("despegue", "aterrizaje", "rodaje"):
        toma = "heli_estacionario"
    if not es_heli_aer and toma in ("heli_estacionario", "heli_rotor"):
        toma = "vuelo"
    fn = FUNCIONES.get(toma, t_vuelo)
    raiz, info, heli = fn(tk, nf, fps, rng)
    sc = bpy.context.scene
    sc.frame_start = 1
    sc.frame_end = nf
    E.sin_muestreo_emision()
    # encuadre vertical: lente más abierta
    if tk["alto"] > tk["ancho"]:
        sc.camera.data.lens *= 0.78 if toma not in ("rayos_x", "pista", "heli_estacionario", "cenital") else 0.55
    if heli and toma not in ("globo",):
        sc.render.use_motion_blur = True
        sc.render.motion_blur_shutter = 0.35
    if toma == "globo":
        return preparar_anclas(raiz, info, False, extra=info.get("extra")) if False else _anclas_globo(info)
    return preparar_anclas(raiz, info, heli)


def _anclas_globo(info):
    sc = bpy.context.scene
    cam = sc.camera
    pts = info.get("extra", {})
    def fn(frame):
        sc.frame_set(frame)
        res = {}
        for k, v in pts.items():
            w = Vector(v[0])
            pv = world_to_camera_view(sc, cam, w)
            # visible si mira hacia la cámara
            vis = w.normalized().dot((cam.matrix_world.translation - w).normalized()) > 0.1
            res[k] = (round(pv.x, 4), round(1 - pv.y, 4), bool(vis and 0.03 < pv.x < 0.97 and 0.03 < pv.y < 0.97))
        return res
    return fn
