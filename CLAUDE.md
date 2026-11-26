# Cabina Abierta: canal automático de aviación (contexto para Claude Code)

## Quién y qué
- Dueño: Daniel (España). No es programador: explícale todo en español sencillo, paso a paso,
  y no le pidas tareas técnicas que puedas hacer tú. Él solo contesta los comentarios en redes.
- Canal **"Cabina Abierta"**: aviones y helicópteros, para España y toda Latinoamérica.
  Instagram `@cabinaabierta.aviacion`, YouTube `@CabinaAbiertaAviacion`.
- Objetivo: **2 vídeos al día, 100 % automáticos** (salen hacia las 03:00 y las 15:00, hora de España),
  vídeo **vertical de 50 s a 3 min**, el **mismo** para Instagram (Reel) y YouTube (Short).
- Repo público en GitHub: `telaestancolando/canal-bot`. Todo corre gratis en **GitHub Actions**.

## Lo que Daniel quiere ahora (prioridad)
Los vídeos 3D actuales se ven **pobres y repetitivos**: todos tienen la misma estructura (avión, líneas
de aire en el ala...). Quiere que **cada vídeo se adapte a su historia** y sea mucho más realista:
- Los Rodeos 1977 → pista con niebla espesa, DOS Boeing 747 (KLM despegando, Pan Am rodando), corte antes del choque.
- Hudson (US Airways 1549) → A320 despegando de Nueva York, bandada de pájaros, giro sobre la ciudad, amerizaje en el río.
- Andes 571 → turbohélice entre montañas nevadas. Etc.
Pasos propuestos (en este orden):
1. **Modelos 3D reales** (licencia libre CC0/CC-BY, p. ej. Sketchfab/Poly Pizza, o de pago barato) en vez de los
   aviones geométricos hechos por código (`m3d_avion.py`). Libreas por color/texto, sin logotipos de marcas.
2. **Cielos HDRI fotográficos** (Poly Haven, CC0) para la luz y el fondo; nubes con volumen si el render lo aguanta.
3. **Nuevas capacidades del motor**: varios aviones en la misma toma, niebla, amerizaje en río/mar, aeropuerto
   con terminal y torre, ciudad de día y de noche, montaña nevada, incendio/humo en tierra (sin víctimas, nunca
   se muestra el impacto), pájaros, hielo/nieve, interior de cabina sencillo.
4. **Guion visual por historia**: en `temas.json` añadir a cada accidente un campo `storyboard` (qué tiene que verse,
   plano a plano) y que el prompt lo use. La IA no debe repetir tomas ni usar planos que no pintan nada
   (p. ej. líneas de aire en un accidente salvo que la causa sea aerodinámica).
5. **Render con GPU**: en GitHub (sin GPU) Cycles va a ~5 s/fotograma a 720x1280 con 8 muestras, por eso la calidad es baja.
   Opciones: renderizar en el Mac de Daniel (Apple Silicon, Metal) si está encendido, o una GPU barata en la nube
   (presupuesto total de Daniel ~15 €/mes). Consúltale antes de gastar.
6. Música de fondo libre de derechos (biblioteca de audio de YouTube), según el tono del vídeo.
Valida SIEMPRE visualmente: renderiza fotogramas sueltos y míralos antes de dar algo por bueno.

## Cómo funciona (flujo)
`.github/workflows/diario.yml` (cron `7 1 * * *` y `7 13 * * *` UTC, ~1 h antes de publicar):
1. **guion** (`python aviacion.py --fase guion`): elige tema según calendario y rendimiento, baja fuentes de
   Wikipedia (es + en), escribe el guion con Gemini, **verifica cada cifra contra las fuentes**, genera la voz
   (edge-tts, `es-MX-JorgeNeural`, con tiempos por palabra) y escribe `salida/tareas.json` (tomas 3D) y `salida/plan.json`.
2. **render** (matriz de 16 máquinas): `python render3d.py salida/tareas.json fotogramas --worker K --total 16`.
   Blender como módulo de Python (`pip install bpy==5.3.0`, Python 3.13). Cada bloque -> `seg_<tarea>_<inicio>.mp4` + `anclas_*.json`.
3. **publicar** (`python aviacion.py --fase publicar`): `montaje3d.py` une segmentos y dibuja encima (marca, tarjetas
   tipo "ALA / 02 · Sustentación" con cifras que cuentan, subtítulos karaoke palabra a palabra, etiquetas que siguen
   a piezas 3D, lluvia/relámpagos, tarjetas de comparación de perfiles, rumor de cabina de fondo), crea la release de
   GitHub (la URL del vídeo la usa Instagram), publica Reel + Story, comenta en la parte 1 cuando sale la parte 2,
   y guarda `estado_aviacion.json` (commit).
Modo prueba: lanzar el workflow a mano con "prueba" marcado (no publica; el vídeo queda en el artefacto `video-del-dia`).

## Archivos
- `aviacion.py`: lógica del canal (estado, elección de tema, guion, verificación, voces, tareas, montaje, publicación).
- `bot.py`: utilidades comunes (Gemini con varios modelos gratis + respaldo Pollinations + reparación de JSON, Wikipedia,
  verificación de cifras, voz, Instagram Graph API, release de GitHub, YouTube). Contiene restos del canal político
  antiguo (BOE, prensa) que ya no se usan.
- `m3d_avion.py`: aviones paramétricos (a320, b737, b757, b777, a330, a340, b747, a380, dc10, md80, rj85, atr72, c130),
  helicópteros (ligero, medio, militar, tandem) y caza genérico. Morro en x=0, avanza hacia -X, metros.
- `m3d_escena.py`: cielo (día/atardecer/noche/tormenta), suelos (hierba, campo, selva, mar, montaña, nieve, desierto,
  ciudad con luces), pista, árboles, colinas, bruma, cámara, ajustes de Cycles.
- `m3d_tomas.py`: las tomas: pista, rodaje, despegue, aterrizaje, vuelo, viraje, emergencia, ala_flujo, motor, globo,
  rayos_x, heli_estacionario, heli_vuelo, heli_rotor, cenital. `construir(tarea)` monta la escena y devuelve las anclas.
- `render3d.py`: reparte fotogramas entre máquinas y renderiza (cada bloque en un proceso aparte).
- `montaje3d.py`: composición 2D fotograma a fotograma con PIL + ffmpeg y audio.
- `config.json`: canal, voz, colores (`acento #E8772E`, `texto #14365A`), constantes de conversión,
  `calendario_aviacion` (lunes a domingo), `youtube_auditado: false`, `modelo_gemini`.
- `temas.json`: temas curados por categoría (accidente 40, aerodinamica, general, legendarios, supervivencia),
  cada uno con `fuentes` (`es:Título` / `en:Title` de Wikipedia). Si quedan ≤2 libres, el bot amplía con Gemini.
- `estado_aviacion.json` (lo escribe el bot): `usados`, `pendiente` (parte 2), `publicados` (id de Instagram + score), `temas_extra`.
- `Inter-*.otf` y `tierra.jpg`: el workflow los mueve a `assets/` (Daniel sube por la web de GitHub y las carpetas se aplanan).

## Calendario (config.json, lunes=0)
**Todos los días accidentes** en 2 partes (parte 1 a las 03:00, parte 2 a las 15:00): es el formato que funciona
(el AF447, de noche y contado como un thriller en segunda persona, hizo ~17.000 visitas y disparó los seguidores).
60 accidentes en temas.json; si se acaban, `ampliar_temas` propone más (usando lo que pide el público).
## Interacción con el público (automática)
- Parte 1 termina con una pregunta ABIERTA ("¿qué crees que falló?"), nunca opciones tipo concurso.
- La parte 2 lee los comentarios de la parte 1 (`leer_comentarios`) y menciona lo que pensaba la gente.
- La parte 2 invita a pedir el próximo accidente; `registrar_peticiones` lee los comentarios de los últimos 12 posts,
  cuenta peticiones (`estado.pedidos`) y el más pedido sale primero; lo que no está en la lista va a `peticiones_libres`.

## Secretos de GitHub (ya configurados)
`GEMINI_API_KEY` (clave que empieza por "AQ."), `IG_TOKEN` (token de usuario del sistema de Meta, empieza por "EAA",
no caduca), `IG_USER_ID`. Opcionales: `POLLINATIONS_TOKEN`, `GH_PAT`, `YT_CLIENT_ID/SECRET/REFRESH_TOKEN`
(YouTube aún sin auditar: Daniel sube a mano desde la release `corto.mp4` + `textos_youtube.txt`).
Nunca pidas a Daniel que pegue claves en el chat.

## Reglas de contenido (no negociables)
- Solo datos de las fuentes; todas las cifras verificadas (`bot.verificar`); si una no se puede respaldar se quita la frase.
- Narrador único masculino, tono documental cercano, ni friki ni "guay"; español neutro para España y Latinoamérica
  (nada de "vale", "guay", "vosotros"); km/m y altitud también en pies.
- **Sin preguntas tipo concurso** ni pedir al público que elija opciones.
- Accidentes: respeto total, sin víctimas, sin morbo, nunca mostrar el impacto; causas solo las oficiales.
- Sin política. No anunciar vídeos futuros salvo "la parte 2".
- No automatizar comentarios falsos ni spam en otras cuentas.
- Caption de Instagram con "🎬 Recreación en animación 3D".

## Historial de problemas ya resueltos (no repetirlos)
- Cupo diario de Gemini agotado → rotación de 6 modelos gratis + respaldo + `json_repair`.
- Guion de 1 frase (vídeo de 10 s) → `asegurar_longitud` (130-470 palabras, ≥4 escenas).
- "700 millones" rechazado → el verificador entiende millones/million/billion en la fuente.
- Ciudad: edificios encima de la pista/cámara → zona libre alrededor del origen y de la pista.
- Emisiones (ventanas, farolas) lentísimas → `emission_sampling = NONE`.
- El cron de GitHub se retrasa hasta ~1-2 h en horas punta: por eso arranca a los :07.

## Presupuesto
~15 €/mes. Recomendado: Gemini de pago (~2-3 €/mes, evita fallos) + ~10 €/mes promocionando el mejor Reel.
Voz mejor (ElevenLabs Creator ~22 $/mes) solo si sube el presupuesto.
