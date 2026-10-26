"""Финальная сборка и автопроверка ролика.

    python qa.py <папка ролика> --video out/video_noaudio.mp4 [--audio audio/mix.wav] --out out/<имя>.mp4

1. Сводит видео и звук в MP4: H.264 high, yuv420p, AAC 192k, faststart (без --audio – немой ролик).
2. Проверяет декодированный результат: кодек, пиксельный формат, размер, fps, число кадров = dur × fps,
   длительность, наличие AAC, громкость (ffmpeg ebur128: integrated LUFS, true peak), скачки яркости.
3. Строит листы кадров: контактный лист (1 кадр/с) и плотные листы вокруг стыков timeline["cuts"].
4. Читает QR с кадра timeline["qr_check_t"] (нужен opencv-python-headless), если задан qr_url.
5. Пишет qa/report.md со статусами PASS / FAIL / НЕ ПРОВЕРЕНО.

Скрипт проверяет техническое. Восприятие – смысл, темп, читаемость, стыки в движении, звук на слух –
проверяет агент глазами по листам кадров и человек по ролику. Скрипт честно помечает это как НЕ ПРОВЕРЕНО."""
import argparse, json, os, pathlib, re, shutil, subprocess, sys

def ff(): return os.environ.get("MOTION_FFMPEG") or shutil.which("ffmpeg") or sys.exit("нет ffmpeg")
def fp(): return os.environ.get("MOTION_FFPROBE") or shutil.which("ffprobe") or sys.exit("нет ffprobe")
def run(*a, **k): return subprocess.run([str(x) for x in a], capture_output=True, text=True, **k)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root"); ap.add_argument("--video", required=True); ap.add_argument("--audio", default="")
    ap.add_argument("--out", required=True); ap.add_argument("--crf", default="17")
    a = ap.parse_args()
    root = pathlib.Path(a.root).resolve(); tl = json.loads((root / "timeline.json").read_text(encoding="utf-8"))
    P = lambda s: (pathlib.Path(s) if pathlib.Path(s).is_absolute() else root / s)
    video, out = P(a.video), P(a.out); audio = P(a.audio) if a.audio else None
    qa = root / "qa"; qa.mkdir(exist_ok=True)
    W, H, FPS, DUR = tl.get("w", 1080), tl.get("h", 1920), tl.get("fps", 60), float(tl["dur"])
    rows = []
    def row(name, ok, fact, need=""):
        rows.append((name, "PASS" if ok is True else "FAIL" if ok is False else "НЕ ПРОВЕРЕНО", fact, need))

    # 1. сведение
    cmd = [ff(), "-y", "-loglevel", "error", "-i", video]
    if audio: cmd += ["-i", audio, "-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", a.crf, "-pix_fmt", "yuv420p", "-r", str(FPS), "-profile:v", "high", "-movflags", "+faststart"]
    cmd += (["-c:a", "aac", "-b:a", "192k", "-ar", "48000"] if audio else ["-an"]) + ["-t", str(DUR), out]
    r = run(*cmd)
    if r.returncode: sys.exit("сведение упало: " + r.stderr[-800:])

    # 2. технические параметры декодированного файла
    pr = json.loads(run(fp(), "-v", "error", "-count_frames", "-show_entries",
                        "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,nb_read_frames,duration", "-of", "json", out).stdout)
    v = next(s for s in pr["streams"] if s["codec_type"] == "video"); au = [s for s in pr["streams"] if s["codec_type"] == "audio"]
    n, d = v["r_frame_rate"].split("/"); fps = int(n) / int(d); nf = int(v["nb_read_frames"]); exp = round(DUR * FPS)
    row("Кодек и формат", v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p", f'{v["codec_name"]}, {v["pix_fmt"]}')
    row("Размер", (int(v["width"]), int(v["height"])) == (W, H), f'{v["width"]}×{v["height"]}', f"{W}×{H}")
    row("Частота кадров", abs(fps - FPS) < 1e-6, f"{fps:g} fps", f"{FPS} fps")
    row("Число кадров", nf == exp, f"{nf}", f"{exp} (= {DUR:g} с × {FPS})")
    row("Длительность", abs(float(v.get("duration", 0)) - DUR) < 1 / FPS + 1e-3, f'{float(v.get("duration", 0)):.3f} с', f"{DUR:g} с")
    if audio:
        row("Аудиопоток AAC", bool(au) and au[0]["codec_name"] == "aac", au[0]["codec_name"] if au else "нет")
        e = run(ff(), "-hide_banner", "-nostats", "-i", out, "-map", "0:a", "-af", "ebur128=peak=true", "-f", "null", "-").stderr
        I = re.findall(r"I:\s+(-?[\d.]+) LUFS", e); TP = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", e)
        li, tp = (float(I[-1]) if I else None), (float(TP[-1]) if TP else None)
        row("Громкость (телефон)", li is not None and -17 <= li <= -13, f"{li} LUFS", "-15 ± 2 LUFS")
        row("Пиковый уровень", tp is not None and tp <= -0.5, f"{tp} dBTP", "≤ -0.5 dBTP")
    else:
        row("Аудиопоток", not au, "нет (немой ролик по брифу)" if not au else "есть, хотя бриф немой")

    # скачки яркости (вспышки) по средней яркости кадров, 20 кадров/с
    raw = subprocess.run([ff(), "-loglevel", "error", "-i", str(out), "-vf", "fps=20,scale=36:64,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    if raw:
        import numpy as np
        L = np.frombuffer(raw, np.uint8).reshape(-1, 64 * 36).mean(1)
        jumps = np.abs(np.diff(L)); big = int((jumps > 40).sum())
        row("Резкие скачки яркости", big <= 3, f"макс. {jumps.max():.0f}/255 за 1/20 с, скачков >40: {big}", "не больше 3 за ролик")

    # 3. листы кадров
    cols = 10; step = max(1, round(DUR / 50)); sw = 144 if H >= W else 256; sh = round(sw * H / W)
    nsheet = int(DUR // step) + 1; rows_n = -(-nsheet // cols)
    run(ff(), "-y", "-loglevel", "error", "-i", out, "-vf", f"fps=1/{step},scale={sw}:{sh},tile={cols}x{rows_n}", "-frames:v", "1", qa / "contact_sheet.png")
    cuts = tl.get("cuts", [])
    for c in cuts:
        s = max(0.0, c - 0.25)
        run(ff(), "-y", "-loglevel", "error", "-ss", f"{s:.3f}", "-t", "0.5", "-i", out, "-vf", f"fps=24,scale={sw}:{sh},tile=12x1", "-frames:v", "1", qa / f"cut_{c:06.2f}.png")
    row("Листы кадров", (qa / "contact_sheet.png").exists(), f"qa/contact_sheet.png (шаг {step} с) + {len(cuts)} листов стыков")

    # 4. QR
    if tl.get("qr_url"):
        t = float(tl.get("qr_check_t", DUR - 1.0)); png = qa / "qr_frame.png"
        run(ff(), "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", out, "-frames:v", "1", png)
        try:
            import cv2
            txt, pts, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(str(png)))
            row("QR читается", txt == tl["qr_url"], f"прочитано: «{txt or 'ничего'}» на {t:.1f} с", tl["qr_url"])
        except ImportError:
            row("QR читается", None, "нет opencv", "pip install opencv-python-headless, или проверить телефоном")

    # восприятие – не автоматизируется
    row("Просмотр в движении: смысл, темп, стыки, читаемость", None, "листы кадров готовы", "агент смотрит листы, человек – ролик")
    row("Звук на слух: музыка, SFX, голос, баланс", None, "агент не слышит звук", "прослушать человеку") if audio else None

    rep = [f"# QA – {tl.get('title', out.stem)}", "", f"Файл: `{out.name}`, {out.stat().st_size / 1e6:.1f} МБ", "",
           "| Проверка | Статус | Факт | Норма |", "|---|---|---|---|"]
    rep += [f"| {a_} | {b_} | {c_} | {d_} |" for a_, b_, c_, d_ in rows]
    (qa / "report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    fails = [r_ for r_ in rows if r_[1] == "FAIL"]
    print("\n".join(f"  {r_[1]:<13} {r_[0]}: {r_[2]}" for r_ in rows))
    print(f"отчёт: {qa / 'report.md'}"); sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
