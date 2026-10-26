"""Покадровый рендер ролика в MP4 без звука.

    python render.py <папка ролика> <начало, с> <конец, с> <out.mp4> [--workers N]
    python render.py <папка ролика> 0 0 qa/x.mp4 --stills 1.5,7.2,16.0     # отдельные кадры в PNG

Каждый кадр i рендерится по времени i/fps в headless Chromium (window.renderFrame), PNG идёт в ffmpeg.
Отрезок делится между воркерами, куски склеиваются без перекодирования. Промежуточный файл – почти без
потерь (crf 8): финальное сжатие делает qa.py. Ошибка JS на странице останавливает рендер.
Зависимости: playwright (pip install playwright, затем один раз: playwright install chromium), ffmpeg.
ffmpeg ищется в PATH или в переменной MOTION_FFMPEG."""
import argparse, base64, json, os, pathlib, shutil, subprocess, sys, time
from multiprocessing import Process

def ffmpeg():
    return os.environ.get("MOTION_FFMPEG") or shutil.which("ffmpeg") or sys.exit("нет ffmpeg: поставь или задай MOTION_FFMPEG")

def open_page(p, root, tl):
    b = p.chromium.launch(args=["--disable-gpu", "--force-color-profile=srgb"])
    pg = b.new_page(viewport={"width": tl.get("w", 1080), "height": tl.get("h", 1920)})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto((root / "index.html").as_uri(), wait_until="domcontentloaded", timeout=60000)
    try:
        pg.evaluate("window.ready")
    except Exception as e:
        b.close(); sys.exit(f"страница не поднялась: {e}; ошибки JS: {errs}")
    if errs: b.close(); sys.exit(f"ошибки JS на странице: {errs}")
    return b, pg, errs

def grab(pg, t):
    url = pg.evaluate("t=>{renderFrame(t);return document.getElementById('c').toDataURL('image/png')}", t)
    return base64.b64decode(url.split(",", 1)[1])

def worker(root, tl, f0, f1, out):
    from playwright.sync_api import sync_playwright
    fps = tl.get("fps", 60)
    with sync_playwright() as p:
        b, pg, errs = open_page(p, root, tl)
        ff = subprocess.Popen([ffmpeg(), "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-i", "-",
                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "8", "-pix_fmt", "yuv420p", "-r", str(fps), str(out)],
                              stdin=subprocess.PIPE)
        t0 = time.time()
        for i in range(f0, f1):
            ff.stdin.write(grab(pg, i / fps))
            if errs: ff.stdin.close(); ff.wait(); b.close(); sys.exit(f"ошибка JS на кадре {i}: {errs}")
            n = i - f0 + 1
            if n % 120 == 0 or n == f1 - f0:
                el = time.time() - t0; print(f"[{out.name}] {n}/{f1 - f0} кадров, {el:.0f} с, осталось ~{el / n * (f1 - f0 - n):.0f} с", flush=True)
        ff.stdin.close(); ff.wait(); b.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root"); ap.add_argument("start", type=float); ap.add_argument("end", type=float); ap.add_argument("out")
    ap.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2)))); ap.add_argument("--stills", default="")
    a = ap.parse_args()
    root = pathlib.Path(a.root).resolve(); tl = json.loads((root / "timeline.json").read_text(encoding="utf-8"))
    out = pathlib.Path(a.out); out = out if out.is_absolute() else root / out; out.parent.mkdir(parents=True, exist_ok=True)
    if a.stills:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b, pg, errs = open_page(p, root, tl)
            for s in a.stills.split(","):
                f = out.parent / f"still_{float(s):06.2f}.png"; f.write_bytes(grab(pg, float(s))); print(" ", f.name)
            b.close()
        return
    fps = tl.get("fps", 60); f0, f1 = round(a.start * fps), round(a.end * fps)
    n = max(1, min(a.workers, f1 - f0)); step = -(-(f1 - f0) // n)
    parts = [(f0 + k * step, min(f1, f0 + (k + 1) * step), out.parent / f"{out.stem}_part{k}.mp4") for k in range(n)]
    parts = [pr for pr in parts if pr[0] < pr[1]]
    procs = [Process(target=worker, args=(root, tl) + pr) for pr in parts]
    for pr in procs: pr.start()
    for pr in procs: pr.join()
    if any(pr.exitcode for pr in procs): sys.exit("рендер упал, см. сообщения выше")
    lst = out.parent / f"{out.stem}_list.txt"
    lst.write_text("".join(f"file '{pr[2].as_posix()}'\n" for pr in parts), encoding="utf-8")
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], check=True)
    for pr in parts: pr[2].unlink(missing_ok=True)
    lst.unlink()
    print("готово:", out, f"({f1 - f0} кадров)")

if __name__ == "__main__":
    main()
