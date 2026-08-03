#!/usr/bin/env python3
"""
product_video.py — пайплайн видеообзора товара через Genosai REST API.

ЗАПУСК:
    python3 product_video.py all --project project.json
    # Ключ: env GENOSAI_API_KEY, а если нет — скрипт САМ найдёт .secrets/genosai.env,
    # поднимаясь вверх от папки проекта и от папки скрипта (Windows-friendly, без source).
    # фазы по отдельности: balance | storyboard | videos | tts | music | assemble | status

СХЕМА project.json (Claude заполняет перед запуском):
{
  "product": "Название товара",
  "workdir": "output",                       // сюда падают все файлы и results.json
  "storyboard": {
    "prompt": "3x3 grid ... Frame 1 ... Frame 9 ... style ...",
    "reference_files": ["card.jpg"],         // карточка товара (локальные пути)
    "resolution": "4K"
  },
  "aspect_ratio": "16:9",                    // 16:9 (маркетплейсы) | 9:16 (Reels/Shorts)
  "video_model": "veo-3.1-fast",             // или veo-3.1-lite (дешевле)
  "video_fallback_models": ["kling-3.0", "seedance-2.0"],   // если основная лежит
  "video_resolution": "1080p",               // 720p | 1080p | 4k
  "scenes": [                                // ровно 9, по порядку
    {"n": 1, "video_prompt": "slow push-in ...", "vo_text": "..."},
    ...
  ],
  "tts": {"voice": "Kore", "style": "Promo/Hype", "pace": "Natural"},
  "music": {"prompt": "upbeat electronic, no vocals ...", "track_index": 0},
  "final": "product_video.mp4"
}

Усвоенные грабли (не менять без причины):
  - прод-хост api.genosai.io — live-ключи sdk_live_* работают ТОЛЬКО там;
  - свежая requests.Session + Connection: close + ретраи — лечит SSLEOFError LibreSSL;
  - createTask диспетчеризуются с шагом 1 сек, ожидание — параллельно;
  - вотчдог: не готово за 5 мин → НОВАЯ задача той же генерации, до 3 попыток;
  - results.json — кэш: успешные шаги при повторном запуске пропускаются.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

API_BASE = "https://api.genosai.io"


def _find_api_key() -> str | None:
    """env GENOSAI_API_KEY, иначе ищем .secrets/genosai.env вверх от cwd и от скрипта."""
    key = os.environ.get("GENOSAI_API_KEY")
    if key:
        return key
    import re
    starts = [Path.cwd(), Path(__file__).resolve().parent]
    seen = set()
    for start in starts:
        for d in [start, *start.parents]:
            if d in seen:
                continue
            seen.add(d)
            env = d / ".secrets" / "genosai.env"
            if env.is_file():
                m = re.search(r"GENOSAI_API_KEY\s*=\s*(\S+)", env.read_text(encoding="utf-8"))
                if m:
                    return m.group(1).strip("\"'")
    return None


API_KEY = _find_api_key()

POLL_INTERVAL_SEC = 5
WATCHDOG_SEC = 300          # 5 минут на одну попытку задачи
GEN_ATTEMPTS = 3            # пере-createTask при зависании/сбое
DISPATCH_GAP_SEC = 1.0      # шаг диспетчеризации createTask
MAX_WORKERS = 5
_HTTP_RETRIES = 5

SCENE_SEC = 4.0
TOTAL_SCENES = 9
VO_MAX_SEC = 3.7            # лимит ЧИСТОЙ РЕЧИ: 4 сек минус отступ 0.25 сек и запас

_results_lock = threading.Lock()


# --------------------------------------------------------------------------- HTTP

def _request(method: str, url: str, **kwargs):
    headers = {**kwargs.pop("headers", {}), "Connection": "close"}
    last = None
    for attempt in range(_HTTP_RETRIES):
        try:
            with requests.Session() as sess:
                return sess.request(method, url, headers=headers, **kwargs)
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def auth_headers() -> dict:
    if not API_KEY:
        sys.exit("Нет ключа: задай env GENOSAI_API_KEY или создай <корень фабрики>/.secrets/genosai.env "
                 "со строкой GENOSAI_API_KEY=sdk_live_...")
    return {"Authorization": f"Bearer {API_KEY}"}


def _raise_for_genosai_error(r: requests.Response) -> None:
    if r.ok:
        return
    try:
        body = r.json()
        msg = body.get("message") or body.get("error") or body
    except ValueError:
        msg = r.text
    hints = {
        401: "Ключ неверный ИЛИ не тот хост (live-ключ → api.genosai.io)",
        402: "Недостаточно кредитов",
        403: "У ключа нет нужного scope",
        400: "Неверные параметры — сверься с references/genosai-api.md",
    }
    raise RuntimeError(f"HTTP {r.status_code}: {msg}. {hints.get(r.status_code, 'Сбой Genosai — повтори')}")


def upload_file(path: str) -> str:
    with open(path, "rb") as f:
        r = _request("POST", f"{API_BASE}/v1/uploads", headers=auth_headers(),
                     files={"file": f}, timeout=120)
    _raise_for_genosai_error(r)
    return r.json()["url"]


def create_task(body: dict) -> str:
    r = _request("POST", f"{API_BASE}/v1/createTask",
                 headers={**auth_headers(), "Content-Type": "application/json"},
                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=30)
    _raise_for_genosai_error(r)
    return r.json()["data"]["taskId"]


def poll_task(task_id: str, timeout_sec: float) -> dict:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = _request("GET", f"{API_BASE}/v1/taskInfo", headers=auth_headers(),
                     params={"taskId": task_id}, timeout=30)
        _raise_for_genosai_error(r)
        d = r.json()["data"]
        st = d.get("status")
        if st == "succeeded":
            return d
        if st == "failed":
            raise RuntimeError(f"Задача {task_id} failed: {d.get('error') or d}")
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"Задача {task_id} не готова за {timeout_sec:.0f} сек")


def download(url: str, dest: Path) -> Path:
    r = _request("GET", url, timeout=180)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def _ext_from_url(url: str, default: str) -> str:
    tail = url.split("?")[0].rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[-1].lower() if "." in tail else default


# --------------------------------------------------------------------------- ядро задач

def run_generation(key: str, body: dict, results: dict, results_file: Path,
                   dest_stem: Path, default_ext: str, media_index: int = 0,
                   stagger_sec: float = 0.0) -> dict:
    """createTask + вотчдог (5 мин × 3 попытки) + скачивание. Кэшируется в results.json."""
    cached = results.get(key, {})
    if cached.get("status") == "succeeded" and Path(cached.get("local_path", "")).exists():
        print(f"{key}: уже готов, пропускаю", flush=True)
        return cached
    if stagger_sec:
        time.sleep(stagger_sec)
    last_err = None
    for attempt in range(1, GEN_ATTEMPTS + 1):
        try:
            tid = create_task(body)
            d = poll_task(tid, WATCHDOG_SEC)
            urls = d["result"]["media_urls"]
            url = urls[min(media_index, len(urls) - 1)]
            dest = dest_stem.with_suffix("." + _ext_from_url(url, default_ext))
            download(url, dest)
            entry = {"status": "succeeded", "media_urls": urls, "local_path": str(dest),
                     "cost": d.get("cost"), "taskId": tid}
            print(f"{key}: готово ({d.get('cost')} кред., попытка {attempt}) → {dest}", flush=True)
            break
        except TimeoutError as e:
            last_err = e
            print(f"{key}: вотчдог 5 мин (попытка {attempt}/{GEN_ATTEMPTS}) — новая задача", flush=True)
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"{key}: сбой (попытка {attempt}/{GEN_ATTEMPTS}) — {e}", flush=True)
            time.sleep(3 * attempt)
    else:
        entry = {"status": "failed", "error": str(last_err)}
        print(f"{key}: ОШИБКА после {GEN_ATTEMPTS} попыток — {last_err}", flush=True)
    with _results_lock:
        results[key] = entry
        results_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


# --------------------------------------------------------------------------- ffmpeg

def ff(*args: str) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(cmd, check=True)


def media_duration(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


# TTS отдаёт паузы по краям (~0.5-1 сек) — режем их, речи остаётся больше места в 4 сек
_TRIM_SILENCE = ("silenceremove=start_periods=1:start_threshold=-45dB,"
                 "areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse")


def speech_duration(path, tmp_dir: Path) -> float:
    """Длительность чистой речи (без тишины по краям)."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"_trim_{Path(path).stem}.wav"
    ff("-i", str(path), "-af", _TRIM_SILENCE, str(tmp))
    return media_duration(tmp)


# --------------------------------------------------------------------------- фазы

def load_project(path: str) -> dict:
    p = json.loads(Path(path).read_text(encoding="utf-8"))
    scenes = p.get("scenes", [])
    if len(scenes) != TOTAL_SCENES:
        sys.exit(f"В project.json должно быть ровно {TOTAL_SCENES} сцен, сейчас {len(scenes)}")
    return p


_ctx_cache: dict = {}


def ctx(project: dict):
    """Общий results-словарь на процесс: параллельные фазы (prep) пишут в один объект
    под _results_lock, иначе последняя сохранившая фаза затёрла бы записи остальных."""
    workdir = Path(project["workdir"])
    workdir.mkdir(parents=True, exist_ok=True)
    results_file = workdir / "results.json"
    key = str(results_file)
    if key not in _ctx_cache:
        _ctx_cache[key] = (json.loads(results_file.read_text(encoding="utf-8"))
                           if results_file.exists() else {})
    return workdir, results_file, _ctx_cache[key]


def phase_balance(project: dict) -> None:
    r = _request("GET", f"{API_BASE}/v1/balance", headers=auth_headers(), timeout=15)
    _raise_for_genosai_error(r)
    d = r.json()
    print(f"Баланс: {d.get('total')} кредитов (main={d.get('main')}, bonus={d.get('bonus')})")


def phase_storyboard(project: dict) -> None:
    workdir, results_file, results = ctx(project)
    sb = project["storyboard"]
    refs = [upload_file(p) for p in sb.get("reference_files", [])]
    body = {"model": "chatgpt-image-2",
            "input": {"prompt": sb["prompt"],
                      "aspect_ratio": project.get("aspect_ratio", "16:9"),
                      "resolution": sb.get("resolution", "4K")}}
    if refs:
        body["input"]["image_urls"] = refs
    entry = run_generation("storyboard", body, results, results_file,
                           workdir / "storyboard", "png")
    if entry["status"] != "succeeded":
        sys.exit("Раскадровка не сгенерировалась")
    # нарезка 3×3 по точным третям (поэтому в промпте запрещены рамки и подписи)
    src = entry["local_path"]
    for i in range(TOTAL_SCENES):
        row, col = divmod(i, 3)
        dest = workdir / f"scene_{i + 1:02d}.png"
        ff("-i", src, "-vf", f"crop=iw/3:ih/3:{col}*iw/3:{row}*ih/3", str(dest))
    print(f"Нарезано {TOTAL_SCENES} стартовых кадров → {workdir}/scene_01..09.png")


# длительности, которые поддерживают запасные модели (kling не умеет "4")
_MODEL_DURATION = {"kling-3.0": "5", "seedance-2.0": "4"}


def phase_videos(project: dict) -> None:
    workdir, results_file, results = ctx(project)
    model = project.get("video_model", "veo-3.1-fast")
    fallbacks = project.get("video_fallback_models", ["kling-3.0", "seedance-2.0"])
    resolution = project.get("video_resolution", "1080p")
    aspect = project.get("aspect_ratio", "16:9")

    def worker(idx: int, scene: dict) -> dict:
        n = scene["n"]
        tile = workdir / f"scene_{n:02d}.png"
        if not tile.exists():
            return {"status": "failed", "error": f"нет {tile} — сначала фаза storyboard"}
        key = f"video_{n:02d}"
        if results.get(key, {}).get("status") == "succeeded" and \
                Path(results[key].get("local_path", "")).exists():
            print(f"{key}: уже готов, пропускаю", flush=True)
            return results[key]
        start_frame = upload_file(str(tile))
        entry = None
        for m in [model, *fallbacks]:
            dur = _MODEL_DURATION.get(m, str(int(SCENE_SEC)))
            body = {"model": m,
                    "input": {"prompt": scene["video_prompt"], "aspect_ratio": aspect,
                              "resolution": resolution, "duration": dur,
                              "image_urls": [start_frame]}}
            entry = run_generation(key, body, results, results_file,
                                   workdir / f"clip_{n:02d}", "mp4",
                                   stagger_sec=idx * DISPATCH_GAP_SEC if m == model else 0.0)
            if entry.get("status") == "succeeded":
                if m != model:
                    print(f"{key}: снят запасной моделью {m} (основная {model} легла)", flush=True)
                break
            print(f"{key}: модель {m} не справилась — пробую следующую", flush=True)
        return entry

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(worker, i, s) for i, s in enumerate(project["scenes"])]
        for fut in as_completed(futures):
            fut.result()
    fails = [k for k, v in results.items() if k.startswith("video_") and v.get("status") != "succeeded"]
    if fails:
        sys.exit(f"Не готовы сцены: {fails}. Повторный запуск фазы videos догенерирует только их.")


def phase_tts(project: dict) -> None:
    workdir, results_file, results = ctx(project)
    tts = project.get("tts", {})

    def worker(idx: int, scene: dict) -> dict:
        n = scene["n"]
        body = {"model": "gemini-3.1-flash-tts",
                "input": {"text": scene["vo_text"],
                          "voice": tts.get("voice", "Kore"),
                          "style": tts.get("style", "Promo/Hype"),
                          "pace": tts.get("pace", "Natural")}}
        return run_generation(f"vo_{n:02d}", body, results, results_file,
                              workdir / f"vo_{n:02d}", "wav",
                              stagger_sec=idx * DISPATCH_GAP_SEC)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for fut in as_completed([ex.submit(worker, i, s) for i, s in enumerate(project["scenes"])]):
            fut.result()

    long_ones = []
    for scene in project["scenes"]:
        entry = results.get(f"vo_{scene['n']:02d}", {})
        if entry.get("status") == "succeeded":
            dur = speech_duration(entry["local_path"], workdir / "build")
            mark = " !ДЛИННО" if dur > VO_MAX_SEC else ""
            print(f"vo_{scene['n']:02d}: речь {dur:.2f} сек{mark}")
            if dur > VO_MAX_SEC:
                long_ones.append(scene["n"])
    if long_ones:
        print(f"\nСцены {long_ones}: речь длиннее {VO_MAX_SEC} сек — сократи vo_text "
              f"(Promo/Hype ≈ 2 слова/сек → максимум 6-7 русских слов), удали их записи "
              f"vo_XX из results.json и перезапусти фазу tts.")


def phase_music(project: dict) -> None:
    workdir, results_file, results = ctx(project)
    music = project.get("music", {})
    body = {"model": "suno-v5.5",
            "input": {"prompt": music["prompt"], "instrumental": True}}  # строго без слов
    entry = run_generation("music", body, results, results_file, workdir / "music_1", "mp3")
    if entry["status"] == "succeeded" and len(entry.get("media_urls", [])) > 1:
        second = workdir / "music_2"
        url = entry["media_urls"][1]
        download(url, second.with_suffix("." + _ext_from_url(url, "mp3")))
        print("Второй трек Suno сохранён рядом (music_2.*) — переключение полем music.track_index")


def phase_assemble(project: dict) -> None:
    workdir, results_file, results = ctx(project)
    tmp = workdir / "build"
    tmp.mkdir(exist_ok=True)

    # 1) нормализация сцен: 30fps, ровно 4 сек, без родного звука; холст по аспекту
    W, H = (1080, 1920) if project.get("aspect_ratio", "16:9") == "9:16" else (1920, 1080)
    norm_paths = []
    for scene in project["scenes"]:
        n = scene["n"]
        clip = results.get(f"video_{n:02d}", {}).get("local_path")
        if not clip or not Path(clip).exists():
            sys.exit(f"Нет клипа сцены {n} — сначала фаза videos")
        dest = tmp / f"norm_{n:02d}.mp4"
        ff("-i", clip,
           "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                  f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1",
           "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-t", str(SCENE_SEC), str(dest))
        norm_paths.append(dest)
    concat_list = tmp / "list.txt"
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in norm_paths), encoding="utf-8")
    video_all = tmp / "video_all.mp4"
    ff("-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(video_all))

    # 2) озвучка: каждый фрагмент добивается тишиной ровно до 4 сек → одна дорожка
    vo_paths = []
    for scene in project["scenes"]:
        n = scene["n"]
        vo = results.get(f"vo_{n:02d}", {}).get("local_path")
        if not vo or not Path(vo).exists():
            sys.exit(f"Нет озвучки сцены {n} — сначала фаза tts")
        dur = speech_duration(vo, tmp)
        if dur > VO_MAX_SEC:
            sys.exit(f"Озвучка сцены {n}: речь длиннее {VO_MAX_SEC} сек ({dur:.2f}) — сократи текст и перегенери")
        # тишина по краям срезается, речь центрируется небольшим отступом, добивка до ровно 4 сек
        dest = tmp / f"vo4_{n:02d}.wav"
        ff("-i", vo, "-af", f"{_TRIM_SILENCE},adelay=250|250,apad",
           "-t", str(SCENE_SEC), "-ar", "48000", "-ac", "2", str(dest))
        vo_paths.append(dest)
    vo_list = tmp / "vo_list.txt"
    vo_list.write_text("".join(f"file '{p.resolve()}'\n" for p in vo_paths), encoding="utf-8")
    vo_all = tmp / "vo_all.wav"
    ff("-f", "concat", "-safe", "0", "-i", str(vo_list), "-c", "copy", str(vo_all))

    # 3) музыка: нужный трек, 36 сек, тихая подложка с fade-out
    total = SCENE_SEC * TOTAL_SCENES
    idx = int(project.get("music", {}).get("track_index", 0))
    music_src = None
    if idx == 0:
        music_src = results.get("music", {}).get("local_path")
    else:
        for cand in workdir.glob("music_2.*"):
            music_src = str(cand)
    if not music_src or not Path(music_src).exists():
        sys.exit("Нет музыки — сначала фаза music")
    music36 = tmp / "music36.wav"
    ff("-i", music_src, "-t", str(total),
       "-af", f"volume=0.22,afade=t=out:st={total - 3}:d=3",
       "-ar", "48000", "-ac", "2", str(music36))

    # 4) сведение и финал
    final = workdir / project.get("final", "product_video.mp4")
    ff("-i", str(video_all), "-i", str(vo_all), "-i", str(music36),
       "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=first:normalize=0[a]",
       "-map", "0:v", "-map", "[a]",
       "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(final))
    print(f"ГОТОВО: {final.resolve()} ({media_duration(final):.1f} сек)")


def phase_status(project: dict) -> None:
    _, _, results = ctx(project)
    total_cost = 0
    for key in sorted(results):
        v = results[key]
        cost = v.get("cost") or 0
        total_cost += cost if isinstance(cost, (int, float)) else 0
        print(f"{key:14s} {v.get('status', '?'):10s} {cost or '':>6} {v.get('local_path', v.get('error', ''))}")
    print(f"\nИтого потрачено: {total_cost} кредитов")


def phase_prep(project: dict) -> None:
    """Раскадровка + озвучка + музыка ПАРАЛЛЕЛЬНО (правило: не ждать друг друга).
    Ветки независимы: озвучке и музыке раскадровка не нужна."""
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = [ex.submit(phase_storyboard, project),
                   ex.submit(phase_tts, project),
                   ex.submit(phase_music, project)]
        for fut in as_completed(futures):
            fut.result()


PHASES = {
    "balance": phase_balance,
    "prep": phase_prep,
    "storyboard": phase_storyboard,
    "videos": phase_videos,
    "tts": phase_tts,
    "music": phase_music,
    "assemble": phase_assemble,
    "status": phase_status,
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Видеообзор товара через Genosai API")
    ap.add_argument("phase", choices=[*PHASES, "all"])
    ap.add_argument("--project", default="project.json")
    args = ap.parse_args()
    project = load_project(args.project)
    if args.phase == "all":
        phase_balance(project)
        phase_prep(project)      # раскадровка + озвучка + музыка параллельно
        phase_videos(project)
        phase_assemble(project)
        phase_status(project)
    else:
        PHASES[args.phase](project)


if __name__ == "__main__":
    main()

# v0.2: auto-key from .secrets, aspect_ratio 16:9|9:16, video fallback models
