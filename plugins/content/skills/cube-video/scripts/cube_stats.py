#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Метрики приёмки контента для 3D-куба. Считает то, что на глаз не видно:
светлоту фона, долю чёрного, объём (полутона-тени), муть (дым/дымку),
дрейф тени по кадрам и замкнутость лупа.

Только stdlib + ffmpeg (внешняя утилита).

Запуск:
    python cube_stats.py <файл|маска> [...]        # png/jpg/mp4/mov
    python cube_stats.py *.mp4 --json out.json     # ещё и машинный вывод

Колонки:
    фон      средняя яркость рамки кадра (лево/право/верх, 6% ширины).
             Эталоны кубов: 109-225. Ниже 90 - тёмный фон, для куба брак.
    чёрное%  доля пикселей < 8. Эталоны: 0-3.7%. Выше 5% - провал в чёрное.
    ярко%    доля пикселей > 200 - светимость объекта на просвет.
    тень%    доля полутонов 60-150 - объём. Резкое падение против стартового
             кадра = тень потерялась, объект стал плоской наклейкой.
    муть%    доля 20-90 - дым, дымка, тёмные разводы. Рост против стартового
             кадра = модель напустила тумана.
    дрейфX/Y (видео) разброс центра масс полутонов по кадрам. Замер: grok 1.25,
             seedance 0.21. Больше 2.0 - свет гуляет, тень качается.
    мигание  (видео) разброс площади полутонов по кадрам.
    лупΔ     (видео) средняя разница первого и последнего кадра. Сырая
             генерация: 20-39. Пинг-понг: 1.2-2.1. Норма для сдачи <= 3.
"""
import glob
import json
import os
import statistics as st
import subprocess
import sys

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

VW, VH = 72, 128      # сетка сэмплирования для видео (быстро и хватает)
IW, IH = 120, 212     # для картинки можно подробнее
FPS = 6               # кадров в секунду при разборе видео
EDGE = 0.06           # ширина рамки «фона» в долях кадра

WINGET_FFMPEG = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    r"Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0-full_build\bin\ffmpeg.exe",
)


def find_ffmpeg():
    """env CUBE_FFMPEG -> PATH -> типовая установка WinGet."""
    cand = os.environ.get("CUBE_FFMPEG")
    if cand and os.path.isfile(cand):
        return cand
    for name in ("ffmpeg", "ffmpeg.exe"):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    if os.path.isfile(WINGET_FFMPEG):
        return WINGET_FFMPEG
    sys.exit("ffmpeg не найден: положи в PATH или задай переменную CUBE_FFMPEG")


FF = find_ffmpeg()


def gray_frames(path, w, h, fps=None):
    """Кадры в 8-битном сером как список bytes длиной w*h."""
    vf = f"scale={w}:{h}"
    if fps:
        vf = f"fps={fps}," + vf
    raw = subprocess.run(
        [FF, "-v", "error", "-i", path, "-vf", vf,
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True).stdout
    n = len(raw) // (w * h)
    return [raw[i * w * h:(i + 1) * w * h] for i in range(n)]


def frame_stats(fr, w, h):
    """Доли пикселей по диапазонам + яркость рамки, в процентах."""
    ex = max(1, int(w * EDGE))
    ey = max(1, int(h * EDGE))
    edge = [fr[y * w + x] for y in range(h) for x in range(w)
            if x < ex or x >= w - ex or y < ey]
    tot = w * h
    dark = bright = mid = haze = 0
    for v in fr:
        if v < 8:
            dark += 1
        if v > 200:
            bright += 1
        if 60 <= v <= 150:
            mid += 1
        if 20 <= v <= 90:
            haze += 1
    return {
        "bg": sum(edge) / len(edge),
        "black": dark / tot * 100,
        "bright": bright / tot * 100,
        "shadow": mid / tot * 100,
        "haze": haze / tot * 100,
    }


def centroid(fr, w, h):
    """Центр масс полутонов (тени) и её площадь в процентах."""
    sx = sy = s = 0
    for y in range(h):
        row = y * w
        for x in range(w):
            if 60 <= fr[row + x] <= 150:
                sx += x
                sy += y
                s += 1
    if not s:
        return None
    return sx / s, sy / s, s / (w * h) * 100


def analyse_image(path):
    fr = gray_frames(path, IW, IH)
    if not fr:
        return None
    r = frame_stats(fr[0], IW, IH)
    r["kind"] = "кадр"
    return r


def analyse_video(path):
    frames = gray_frames(path, VW, VH, FPS)
    if len(frames) < 4:
        return None
    acc = [frame_stats(f, VW, VH) for f in frames]
    r = {k: st.mean(a[k] for a in acc) for k in acc[0]}
    cs = [c for c in (centroid(f, VW, VH) for f in frames) if c]
    r["drift_x"] = st.pstdev([c[0] for c in cs]) if len(cs) > 1 else 0.0
    r["drift_y"] = st.pstdev([c[1] for c in cs]) if len(cs) > 1 else 0.0
    r["flicker"] = st.pstdev([c[2] for c in cs]) if len(cs) > 1 else 0.0
    r["loop"] = sum(abs(a - b) for a, b in
                    zip(frames[0], frames[-1])) / (VW * VH)
    r["kind"] = "видео"
    r["frames"] = len(frames)
    return r


def verdict(r):
    """Короткий приговор по порогам, выведенным из замеров эталонов."""
    bad = []
    if r["bg"] < 90:
        bad.append("тёмный фон")
    if r["black"] > 5:
        bad.append("провал в чёрное")
    if r["kind"] == "видео":
        if max(r["drift_x"], r["drift_y"]) > 2.0:
            bad.append("тень гуляет")
        if r["loop"] > 3:
            bad.append("луп не замкнут")
    return "БРАК: " + ", ".join(bad) if bad else "ок"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    json_out = None
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        json_out = sys.argv[i + 1] if i + 1 < len(sys.argv) else "stats.json"
    files = []
    for a in args:
        if a == json_out:
            continue
        files.extend(sorted(glob.glob(a)) or [a])
    files = [f for f in files if os.path.splitext(f)[1].lower()
             in VIDEO_EXT | IMAGE_EXT]
    if not files:
        sys.exit("нечего мерить: передай png/jpg/mp4/mov или маску")

    head = (f"{'файл':22}{'фон':>7}{'чёрное%':>9}{'ярко%':>8}{'тень%':>8}"
            f"{'муть%':>8}{'дрейфX':>8}{'дрейфY':>8}{'мигание':>9}"
            f"{'лупΔ':>7}  приговор")
    print(head)
    print("-" * len(head))
    out = {}
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        r = analyse_video(f) if ext in VIDEO_EXT else analyse_image(f)
        name = os.path.basename(f)
        if not r:
            print(f"{name[:22]:22}  не читается")
            continue
        out[name] = r
        cells = ""
        for k, w in (("drift_x", 8), ("drift_y", 8), ("flicker", 9)):
            v = r.get(k)
            cells += f"{v:{w}.2f}" if isinstance(v, float) else " " * w
        lp = r.get("loop")
        cells += f"{lp:7.1f}" if isinstance(lp, float) else " " * 7
        print(f"{name[:22]:22}{r['bg']:7.1f}{r['black']:8.1f}%"
              f"{r['bright']:7.1f}%{r['shadow']:7.1f}%{r['haze']:7.1f}%"
              + cells + f"  {verdict(r)}")

    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        print(f"\nмашинный вывод: {json_out}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    main()
