"""Renderiza tareas de tomas 3D. Uso:
  python render3d.py tareas.json salida_dir --worker K --total N
Reparte todos los fotogramas de todas las tareas entre N trabajadores en bloques contiguos.
Cada bloque se guarda como segmento mp4 (seg_<tarea>_<inicio>.mp4) + anclas_<tarea>_<inicio>.json."""
import argparse, json, os, subprocess, sys, time, glob, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def bloques(tareas, total):
    """Lista de (worker, tarea_id, f_ini, f_fin) repartiendo fotogramas por igual."""
    todos = sum(t["frames"] for t in tareas)
    cuota = todos / total
    res = []
    w, acumulado = 0, 0.0
    for t in tareas:
        f = 1
        while f <= t["frames"]:
            limite = (w + 1) * cuota - acumulado
            n = max(1, min(t["frames"] - f + 1, int(round(limite)) if w < total - 1 else t["frames"] - f + 1))
            res.append((w, t["id"], f, f + n - 1))
            acumulado += n
            f += n
            if w < total - 1 and acumulado >= (w + 1) * cuota - 0.5:
                w += 1
    return res


def renderizar(tarea, f_ini, f_fin, salida):
    import bpy
    import m3d_tomas as T
    anclas_fn = T.construir(dict(tarea))
    sc = bpy.context.scene
    tmp = os.path.join(salida, f"tmp_{tarea['id']}_{f_ini:05}")
    os.makedirs(tmp, exist_ok=True)
    sc.frame_start, sc.frame_end = f_ini, f_fin
    sc.render.filepath = os.path.join(tmp, "f")
    sc.render.use_overwrite = False   # si se relanza, salta los fotogramas ya hechos
    t0 = time.time()
    bpy.ops.render.render(animation=True)
    dt = time.time() - t0
    anclas = {f: anclas_fn(f) for f in range(f_ini, f_fin + 1)}
    with open(os.path.join(salida, f"anclas_{tarea['id']}_{f_ini:05}.json"), "w") as fh:
        json.dump(anclas, fh)
    fps = tarea.get("fps", 24)
    seg = os.path.join(salida, f"seg_{tarea['id']}_{f_ini:05}.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-start_number", str(f_ini),
                    "-i", os.path.join(tmp, "f%04d.jpg"), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                    "-pix_fmt", "yuv420p", seg], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"[render] {tarea['id']} {f_ini}-{f_fin}: {dt:.0f}s ({dt / (f_fin - f_ini + 1):.1f}s/fotograma)", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tareas"); ap.add_argument("salida")
    ap.add_argument("--worker", type=int, default=0); ap.add_argument("--total", type=int, default=1)
    a = ap.parse_args()
    tareas = json.load(open(a.tareas))
    os.makedirs(a.salida, exist_ok=True)
    por_id = {t["id"]: t for t in tareas}
    mios = [b for b in bloques(tareas, a.total) if b[0] == a.worker]
    print(f"[render] trabajador {a.worker}/{a.total}: {len(mios)} bloques, "
          f"{sum(b[3] - b[2] + 1 for b in mios)} fotogramas", flush=True)
    # cada bloque en un proceso aparte: Blender queda limpio entre escenas
    for (_, tid, fi, ff) in mios:
        r = subprocess.run([sys.executable, __file__, "--uno", a.tareas, a.salida, tid, str(fi), str(ff)])
        if r.returncode != 0:
            raise SystemExit(f"Falló el bloque {tid} {fi}-{ff}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--uno":
        _, _, ruta, sal, tid, fi, ff = sys.argv
        t = {x["id"]: x for x in json.load(open(ruta))}[tid]
        renderizar(t, int(fi), int(ff), sal)
    else:
        main()
