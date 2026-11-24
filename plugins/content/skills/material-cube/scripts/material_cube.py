#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пакет контента на артикул отделочного материала для 3D-куба.
Из фото плитки/ламината заказчика -> hero-кадр (материал парит в кубе)
+ комнаты-диорамы с этим материалом + лупы -> manifest.json для каталога куба.

Сеть, загрузка, задачи и кэш берутся из соседнего скилла cube-video
(scripts/cube_gen.py): механика одна на оба скилла, не дублируем.

Фазы (папка артикула = папка, где лежит sku.json):
    plan      собрать промпты из sku.json + rooms.json + style.json, смета -> plan.json
    stills    hero + комнаты, chatgpt-image-2 4K 9:16
    sheet     контактный лист для приёмки: фото заказчика | все кадры
    approve   записать приёмку по комнатам в sku.json
    loops     оживить hero и выбранные комнаты, пинг-понг, апскейл 2160x3840
    manifest  выгрузка для каталога куба (только принятые комнаты)
    status    фактические траты
    batch     пройти фазу по всем артикулам папки

Запуск:
    python material_cube.py plan    --sku артикулы/18456732
    python material_cube.py stills  --sku артикулы/18456732 --dry-run
    python material_cube.py stills  --sku артикулы/18456732
    python material_cube.py sheet   --sku артикулы/18456732
    python material_cube.py approve --sku артикулы/18456732 --ok hero,bath_wall --reject living_floor --note "плитка мельче, чем в жизни"
    python material_cube.py loops   --sku артикулы/18456732 --dry-run
    python material_cube.py manifest --sku артикулы/18456732 --by Boris
    python material_cube.py batch   --root артикулы --phase stills --dry-run

ВСЕГДА сначала --dry-run. Платная фаза - только после гейта (см. SKILL.md).
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CUBE_SCRIPTS = os.path.join(SKILL, "..", "cube-video", "scripts")
sys.path.insert(0, os.path.abspath(CUBE_SCRIPTS))
try:
    import cube_gen as cg  # api, upload, download, wait_all, load_cache, save_cache
except ImportError:
    sys.exit("не найден скилл cube-video рядом (" + os.path.abspath(CUBE_SCRIPTS)
             + "): material-cube работает только вместе с ним")

ROOMS = json.load(open(os.path.join(SKILL, "references", "rooms.json"),
                       encoding="utf-8"))

# Цены замерены (см. cube-video/references/models-prices.md и package.md)
STILL_MODEL, STILL_RES, STILL_COST = "chatgpt-image-2", "4K", 16
VIDEO_PRICES = {("grok-imagine-1.5", "1080p"): 48, ("grok-imagine-1.5", "720p"): 48,
                ("veo-3.1-lite", "720p"): 30, ("kling-3.0", "1080p"): 100,
                ("seedance-2.0", "720p"): 180, ("seedance-2.0", "1080p"): 450}
CUBE_SIZE = "2160x3840"

# ── Блоки промпта. Почему они такие - references/prompt-room.md ─────────────
ROOM_BASE = (
    "Vertical 9:16 photorealistic interior photograph, high-end architectural "
    "magazine quality, natural true-to-life colours. One-point frontal perspective: "
    "the camera stands at the open front of the room at eye level (1.5 m) and looks "
    "straight at the back wall, perfectly symmetrical; the floor, the ceiling and "
    "both side walls recede evenly towards the centre, so the room reads as a deep "
    "box. No people, no pets, no text, no logos, no watermark. Nothing resembling a "
    "glass case, showcase or box frame around the scene.")
ROOM_LIGHT = (
    "Bright soft daylight from a large window on the left side wall plus warm "
    "ceiling light. The whole image is light and airy: no dark corners, no black "
    "areas, no night scene. Lighting is fixed.")
ROOM_MATERIAL = (
    "THE HERO OF THE IMAGE: the {surface} {verb} finished with exactly the material "
    "from the reference images: {material}. Same colour, same pattern and veining, "
    "same texture, same surface finish ({finish}). Real piece size {w} x {h} mm, "
    "shown at true scale (a standard interior door is 2000 mm high, a bathtub is "
    "1700 mm long). Pieces are {layout}, joints {grout} mm wide with {grout_color} "
    "grout. The pattern varies naturally from piece to piece like a real "
    "installation, without obvious repetition, distortion or invented details.")
ROOM_SCENE = "{scene} Furniture and decor are minimal and neutral (white, light wood, soft grey) and never compete with the material."
ROOM_LOCKS = (
    "Use this material ONLY on the {surface}. Do not change its colour or pattern. "
    "Do not put any other finish on the {surface}.")

HERO_BASE = (
    "Vertical 9:16 commercial product render, photorealistic, ultra detailed, 3D "
    "studio rendering. EXACTLY ONE object in the frame, centred. No duplicates, no "
    "second copy, no reflections of the object. No text, no logos, no watermark, no "
    "people, no hands. Nothing resembling a glass case, cabinet, showcase or box "
    "frame around the scene.")
HERO_LIGHT = (
    "LIGHTING IS FIXED AND MUST NOT CHANGE: one single soft key light placed IN FRONT "
    "of the subject, high above and slightly to the left, aimed backwards into the "
    "scene like a shop-window spotlight. Because the light comes from the viewer "
    "side, the shadow falls AWAY FROM THE CAMERA, backwards and downwards: a soft "
    "contact shadow on the floor behind the object and a faint diffuse shadow on the "
    "back wall. No shadow is cast towards the viewer. Light position, direction, "
    "colour and intensity are locked.")
HERO_SCENE = "Seamless neutral light-grey studio cyclorama, slightly darker floor than wall, soft diffused fill light, no harsh speculars."
HERO_OBJECT = (
    "The object: one single {piece} of {w} x {h} mm made of exactly the material "
    "from the reference images: {material}, surface {finish}. It stands upright on a "
    "low matte podium cube at the centre, tilted slightly towards the viewer so the "
    "texture and the light reflection read clearly. The product must match the "
    "reference images exactly: same colour, same pattern, same finish.")

MOTION_ROOM = (
    "Camera is locked, static. Only the daylight slowly changes: a soft patch of "
    "sunlight glides slowly across the {surface}, revealing its texture and gloss; "
    "the curtain at the window stirs gently. The material, the furniture and the "
    "room do not change, nothing appears or disappears, no people, no text.")
MOTION_HERO = (
    "Camera is locked, static. The piece slowly turns 20 degrees around its vertical "
    "axis and back, the reflection glides across its surface. "
    "CRITICAL: the lighting rig does NOT move; the shadow always falls backwards, "
    "away from the camera, and never swings. EXACTLY ONE object and ONE podium, "
    "nothing duplicates or morphs, no hands, no text, no haze, no light rays. "
    "Background stays exactly as in the first frame.")

SURFACE_EN = {"floor": ("floor", "is"), "walls": ("walls", "are"),
              "backsplash": ("kitchen backsplash between the worktop and the upper wall", "is"),
              "back wall": ("back wall", "is")}

PACKAGES = {"base": 0, "standard": 2, "premium": 99}


# ── Помощники ───────────────────────────────────────────────────────────────
def sku_dir(args):
    d = os.path.abspath(args.sku)
    if not os.path.isfile(os.path.join(d, "sku.json")):
        sys.exit("нет sku.json в " + d + " (шаблон - sku.template.json скилла)")
    return d


def load_sku(d):
    return json.load(open(os.path.join(d, "sku.json"), encoding="utf-8"))


def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def load_style(d):
    """style.json кладётся рядом с папками артикулов (одна на заказчика).
    Сюда переезжают правила промпт-книги, утверждённые на эталоне стиля."""
    for p in (os.path.join(d, "style.json"), os.path.join(os.path.dirname(d), "style.json")):
        if os.path.isfile(p):
            return json.load(open(p, encoding="utf-8")), p
    return {}, None


def room_list(sku):
    t = ROOMS["types"].get(sku.get("type"))
    if not t:
        sys.exit("неизвестный type '{}'. Варианты: {}".format(
            sku.get("type"), ", ".join(ROOMS["types"])))
    rooms = sku.get("rooms") or t["rooms"]
    bad = [r for r in rooms if r not in ROOMS["rooms"]]
    if bad:
        sys.exit("нет таких комнат в rooms.json: " + ", ".join(bad))
    return t, rooms


def fmt(sku):
    f = sku.get("format_mm") or []
    if len(f) != 2:
        sys.exit("format_mm должен быть [ширина, длина] в мм - без него масштаб врёт")
    return f


def build_plan(d):
    sku = load_sku(d)
    style, style_path = load_style(d)
    t, rooms = room_list(sku)
    w, h = fmt(sku)
    for k in ("material_en", "finish"):
        if not sku.get(k):
            sys.exit("в sku.json пусто поле " + k)
    photos = sku.get("photos") or []
    missing = [p for p in photos if not os.path.isfile(os.path.join(d, p))]
    if not photos or missing:
        sys.exit("нет фото заказчика: " + (", ".join(missing) or "список photos пуст"))
    layout = ROOMS["layouts"].get(sku.get("layout", "straight"))
    if not layout:
        sys.exit("неизвестный layout. Варианты: " + ", ".join(ROOMS["layouts"]))

    room_add = style.get("room_add", "")
    stills = []
    hero_prompt = " ".join(x for x in [
        HERO_BASE, HERO_LIGHT, HERO_SCENE,
        HERO_OBJECT.format(piece=t["piece"], w=w, h=h,
                           material=sku["material_en"], finish=sku["finish"]),
        style.get("hero_add", "")] if x)
    stills.append({"id": "hero", "title": "Материал", "prompt": hero_prompt})

    for rid in rooms:
        r = ROOMS["rooms"][rid]
        surface, verb = SURFACE_EN[r["surface"]]
        p = " ".join(x for x in [
            ROOM_BASE, ROOM_LIGHT,
            ROOM_MATERIAL.format(surface=surface, verb=verb, material=sku["material_en"],
                                 finish=sku["finish"], w=w, h=h, layout=layout,
                                 grout=sku.get("grout_mm", 2),
                                 grout_color=sku.get("grout_color", "matching")),
            ROOM_SCENE.format(scene=r["scene"]),
            ROOM_LOCKS.format(surface=surface),
            room_add, (sku.get("prompt_add") or {}).get(rid, "")] if x)
        stills.append({"id": rid, "title": r["ru"], "prompt": p, "surface": surface})

    n_loops = PACKAGES.get(sku.get("package", "base"))
    if n_loops is None:
        sys.exit("package: base | standard | premium")
    loop_ids = []
    if n_loops:
        chosen = sku.get("loop_rooms") or rooms
        loop_ids = ["hero"] + list(chosen)[:n_loops]
    vmodel = sku.get("video_model") or style.get("video_model")
    vres = sku.get("video_resolution") or style.get("video_resolution") or "1080p"
    loops = []
    for s in stills:
        if s["id"] not in loop_ids:
            continue
        motion = MOTION_HERO if s["id"] == "hero" else MOTION_ROOM.format(surface=s["surface"])
        motion = " ".join(x for x in [motion, style.get("motion_add", "")] if x)
        loops.append({"id": s["id"], "prompt": motion,
                      "start_frame": "stills/{}.png".format(s["id"])})

    plan = {"sku": sku.get("sku"), "built": datetime.date.today().isoformat(),
            "style": style_path, "photos": photos,
            "stills": {"model": STILL_MODEL, "resolution": STILL_RES, "jobs": stills},
            "loops": {"model": vmodel, "resolution": vres, "duration": "6", "jobs": loops}}
    save_json(os.path.join(d, "plan.json"), plan)
    return plan


def estimate(plan):
    s = len(plan["stills"]["jobs"]) * STILL_COST
    lp = plan["loops"]
    vcost = VIDEO_PRICES.get((lp["model"] or "grok-imagine-1.5", lp["resolution"]), 48)
    v = len(lp["jobs"]) * vcost
    return s, v


# ── Фазы ────────────────────────────────────────────────────────────────────
def phase_plan(args):
    d = sku_dir(args)
    plan = build_plan(d)
    s, v = estimate(plan)
    lp = plan["loops"]
    print("Артикул {}: {} кадров, {} лупов".format(
        plan["sku"], len(plan["stills"]["jobs"]), len(lp["jobs"])))
    for j in plan["stills"]["jobs"]:
        print("  кадр  {:20} {}".format(j["id"], j["title"]))
    for j in lp["jobs"]:
        print("  луп   {}".format(j["id"]))
    print("\nСмета: кадры ~{} + лупы ~{} = ~{} кредитов".format(s, v, s + v))
    if lp["jobs"] and not lp["model"]:
        print("  ! видеомодель не выбрана (video_model в style.json или sku.json):"
              " лупы посчитаны по grok-imagine-1.5, выбор за заказчиком")
    print("  стиль: " + (plan["style"] or "style.json нет - эталон стиля ещё не утверждён"))
    print("  план записан: plan.json")
    if args.verbose:
        for j in plan["stills"]["jobs"] + lp["jobs"]:
            print("\n[{}]\n{}".format(j["id"], j["prompt"]))


def _run_jobs(d, jobs, model, inp_common, sub, ext, refs_key, args):
    cache_p = os.path.join(d, "results.json")
    cache = cg.load_cache(cache_p)
    tasks = []
    for j in jobs:
        key = sub + "/" + j["id"]
        prev = cache.get(key)
        if prev and prev.get("status") == "succeeded" and not args.force:
            print("  {}: уже сделано, пропуск (--force чтобы заново)".format(key))
            continue
        inp = dict(inp_common, prompt=j["prompt"])
        inp["image_urls"] = refs_key(j)
        r = cg.api("/v1/createTask", {"model": model, "input": inp})
        tid = (r.get("data") or {}).get("taskId")
        if not tid:
            print("  {}: задача не создалась: {}".format(
                key, json.dumps(r, ensure_ascii=False)[:300]))
            continue
        tasks.append((key, tid))
        print("  создано " + key)
        time.sleep(1)
    if not tasks:
        return 0
    done = cg.wait_all(tasks, poll=10, limit=180)
    spent = 0
    for key, (status, cost, urls) in done.items():
        spent += cost
        rec = {"status": status, "cost": cost, "model": model, "files": []}
        for k, u in enumerate(urls[:1]):
            dest = os.path.join(d, sub, key.split("/", 1)[1] + ext)
            cg.download(u, dest)
            rec["files"].append(os.path.relpath(dest, d).replace("\\", "/"))
        cache[key] = rec
    cg.save_cache(cache_p, cache)
    print("Потрачено: {} кредитов".format(spent))
    return spent


def only(jobs, args):
    if not args.only:
        return jobs
    keep = set(args.only.split(","))
    return [j for j in jobs if j["id"] in keep]


def phase_stills(args):
    d = sku_dir(args)
    plan = build_plan(d)
    jobs = only(plan["stills"]["jobs"], args)
    print("Кадры {}: {} шт x {} = ~{} кредитов".format(
        plan["sku"], len(jobs), STILL_COST, len(jobs) * STILL_COST))
    if args.dry_run:
        for j in jobs:
            print("\n[{}] {} {} 9:16\n{}".format(j["id"], STILL_MODEL, STILL_RES, j["prompt"]))
        print("\n--- dry-run, ничего не отправлено ---")
        return
    print("Загружаю фото заказчика...")
    urls = [cg.upload(os.path.join(d, p)) for p in plan["photos"][:4]]
    _run_jobs(d, jobs, STILL_MODEL, {"aspect_ratio": "9:16", "resolution": STILL_RES},
              "stills", ".png", lambda j: urls, args)


def ffmpeg():
    sys.path.insert(0, os.path.abspath(CUBE_SCRIPTS))
    import cube_loop
    return cube_loop.tool("ffmpeg")


def phase_sheet(args):
    d = sku_dir(args)
    plan = build_plan(d)
    items = [os.path.join(d, plan["photos"][0])]
    labels = ["ФОТО"]
    for j in plan["stills"]["jobs"]:
        p = os.path.join(d, "stills", j["id"] + ".png")
        if os.path.isfile(p):
            items.append(p)
            labels.append(j["id"])
    if len(items) < 2:
        sys.exit("кадров ещё нет - сначала stills")
    cmd = [ffmpeg(), "-y", "-loglevel", "error"]
    for p in items:
        cmd += ["-i", p]
    parts = []
    for i in range(len(items)):
        parts.append("[{i}:v]scale=540:960:force_original_aspect_ratio=decrease,"
                     "pad=540:960:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1[v{i}]".format(i=i))
    parts.append("".join("[v{}]".format(i) for i in range(len(items)))
                 + "hstack=inputs={}[out]".format(len(items)))
    out = os.path.join(d, "sheet.jpg")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-q:v", "3", out]
    subprocess.run(cmd, check=True)
    print("Контактный лист: {}\nпорядок слева направо: {}".format(out, " | ".join(labels)))


def phase_approve(args):
    d = sku_dir(args)
    sku = load_sku(d)
    qc = sku.get("qc") or {}
    rooms = qc.get("rooms") or {}
    today = datetime.date.today().isoformat()
    for rid in filter(None, (args.ok or "").split(",")):
        rooms[rid] = {"ok": True, "date": today}
    for rid in filter(None, (args.reject or "").split(",")):
        rooms[rid] = {"ok": False, "date": today, "note": args.note or ""}
    qc["rooms"] = rooms
    if args.by:
        qc["by"] = args.by
    sku["qc"] = qc
    save_json(os.path.join(d, "sku.json"), sku)
    ok = [k for k, v in rooms.items() if v["ok"]]
    bad = [k for k, v in rooms.items() if not v["ok"]]
    print("Принято: {}\nВ брак: {}".format(", ".join(ok) or "-", ", ".join(bad) or "-"))
    if bad:
        print("Замечание -> правило в ПРОМПТ-КНИГА.md, затем stills --only {} --force"
              .format(",".join(bad)))


def phase_loops(args):
    d = sku_dir(args)
    plan = build_plan(d)
    lp = plan["loops"]
    jobs = only(lp["jobs"], args)
    if not jobs:
        print("в пакете {} лупов нет".format(load_sku(d).get("package")))
        return
    model = args.model or lp["model"]
    if not model:
        sys.exit("видеомодель не выбрана: выбор за заказчиком (гейт 2 cube-video). "
                 "Укажи video_model в style.json или --model")
    cost = VIDEO_PRICES.get((model, lp["resolution"]), 48)
    print("Лупы {}: {} x {} {} = ~{} кредитов".format(
        plan["sku"], len(jobs), model, lp["resolution"], len(jobs) * cost))
    missing = [j["start_frame"] for j in jobs
               if not os.path.isfile(os.path.join(d, j["start_frame"]))]
    if missing:
        sys.exit("нет стартовых кадров: " + ", ".join(missing))
    if args.dry_run:
        for j in jobs:
            print("\n[{}] start={}\n{}".format(j["id"], j["start_frame"], j["prompt"]))
        print("\n--- dry-run, ничего не отправлено ---")
        return
    starts = {j["id"]: cg.upload(os.path.join(d, j["start_frame"])) for j in jobs}
    _run_jobs(d, jobs, model, {"aspect_ratio": "9:16", "resolution": lp["resolution"],
                               "duration": lp["duration"]},
              "clips", ".mp4", lambda j: [starts[j["id"]]], args)
    loop_py = os.path.join(os.path.abspath(CUBE_SCRIPTS), "cube_loop.py")
    os.makedirs(os.path.join(d, "loops"), exist_ok=True)
    for j in jobs:
        clip = os.path.join(d, "clips", j["id"] + ".mp4")
        if os.path.isfile(clip):
            subprocess.run([sys.executable, loop_py, clip, "-o",
                            os.path.join(d, "loops", j["id"] + ".mp4"),
                            "--upscale", CUBE_SIZE, "--crf", "16"], check=True)
    print("Лупы готовы в loops/. Замер: cube_stats.py loops/*.mp4")


def phase_manifest(args):
    d = sku_dir(args)
    sku = load_sku(d)
    plan = build_plan(d)
    qc = (sku.get("qc") or {}).get("rooms") or {}
    if not qc:
        sys.exit("приёмки нет: сначала sheet и approve. Без приёмки в каталог не выгружаем")
    cache = cg.load_cache(os.path.join(d, "results.json"))
    credits = sum(v.get("cost", 0) for v in cache.values())

    def entry(j):
        e = {"image": "stills/{}.png".format(j["id"])}
        if os.path.isfile(os.path.join(d, "loops", j["id"] + ".mp4")):
            e["loop"] = "loops/{}.mp4".format(j["id"])
        return e

    jobs = {j["id"]: j for j in plan["stills"]["jobs"]}
    if not (qc.get("hero") or {}).get("ok"):
        sys.exit("hero не принят - без обложки артикул в каталог не идёт")
    rooms = [dict(id=rid, title=jobs[rid]["title"], **entry(jobs[rid]))
             for rid in jobs if rid != "hero" and (qc.get(rid) or {}).get("ok")]
    if not rooms:
        sys.exit("ни одной принятой комнаты")
    man = {"schema": 1, "sku": sku.get("sku"), "barcode": sku.get("barcode"),
           "name": sku.get("name"), "type": sku.get("type"),
           "format_mm": sku.get("format_mm"), "finish": sku.get("finish"),
           "hero": entry(jobs["hero"]), "rooms": rooms,
           "qc": {"by": args.by or (sku.get("qc") or {}).get("by"),
                  "date": datetime.date.today().isoformat()},
           "credits": credits, "generated": datetime.date.today().isoformat()}
    save_json(os.path.join(d, "manifest.json"), man)
    print("manifest.json: hero + {} комнат, потрачено {} кредитов".format(len(rooms), credits))


def phase_status(args):
    d = sku_dir(args)
    cache = cg.load_cache(os.path.join(d, "results.json"))
    if not cache:
        print("платных фаз ещё не было")
        return
    total = 0
    for k, v in cache.items():
        total += v.get("cost", 0)
        print("{:28}{:18}{:10}{:>5}".format(k, v.get("model", "?"),
                                           v.get("status", "?"), v.get("cost", 0)))
    print("Всего: {} кредитов".format(total))


def phase_batch(args):
    root = os.path.abspath(args.root)
    dirs = sorted(os.path.join(root, x) for x in os.listdir(root)
                  if os.path.isfile(os.path.join(root, x, "sku.json")))
    if not dirs:
        sys.exit("в {} нет папок с sku.json".format(root))
    if args.phase in ("stills", "loops") and not args.dry_run:
        _, sp = load_style(dirs[0])
        if not sp:
            sys.exit("нет style.json: пакетный режим только после утверждённого эталона стиля")
    fn = PHASES[args.phase]
    for dd in dirs:
        print("\n=== " + os.path.basename(dd))
        a = argparse.Namespace(**vars(args))
        a.sku = dd
        try:
            fn(a)
        except SystemExit as e:
            print("  пропуск: {}".format(e))


PHASES = {"plan": phase_plan, "stills": phase_stills, "sheet": phase_sheet,
          "approve": phase_approve, "loops": phase_loops,
          "manifest": phase_manifest, "status": phase_status}


def main():
    ap = argparse.ArgumentParser(description="Пакет артикула отделки для 3D-куба")
    ap.add_argument("phase", choices=sorted(list(PHASES) + ["batch"]))
    ap.add_argument("--sku", help="папка артикула с sku.json")
    ap.add_argument("--root", help="batch: папка с папками артикулов")
    ap.add_argument("--phase", dest="bphase", help="batch: какую фазу гнать")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", help="id через запятую: hero,bath_wall")
    ap.add_argument("--model", help="loops: видеомодель, если не задана в style.json")
    ap.add_argument("--ok"), ap.add_argument("--reject"), ap.add_argument("--note")
    ap.add_argument("--by", help="кто принял")
    ap.add_argument("-v", "--verbose", action="store_true", help="plan: показать промпты")
    args = ap.parse_args()
    if args.phase == "batch":
        if not args.root or args.bphase not in PHASES:
            sys.exit("batch: --root <папка> --phase plan|stills|sheet|loops|manifest|status")
        args.phase = args.bphase
        phase_batch(args)
        return
    if not args.sku:
        sys.exit("--sku <папка артикула>")
    PHASES[args.phase](args)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    main()
