"""Вклеивает перерендеренный отрезок в готовое видео без полного перерендера.

    python splice.py <base.mp4> <segment.mp4> <начало отрезка, с> <out.mp4>

Отрезок (segment.mp4) должен быть отрендерен render.py с тем же fps и размером, начиная с указанной
секунды. Куски базы режутся ОТДЕЛЬНЫМИ проходами ffmpeg и склеиваются concat-демуксером: один проход
с filter_complex (trim+concat) на 1080×1920 съедает больше 7 ГБ памяти и падает."""
import json, os, pathlib, shutil, subprocess, sys

def ff(): return os.environ.get("MOTION_FFMPEG") or shutil.which("ffmpeg")
def fp(): return os.environ.get("MOTION_FFPROBE") or shutil.which("ffprobe")

def frames(p):
    o = subprocess.run([fp(), "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames,r_frame_rate", "-of", "json", str(p)], capture_output=True, text=True).stdout
    s = json.loads(o)["streams"][0]; n, d = s["r_frame_rate"].split("/"); return int(s["nb_read_frames"]), int(n) / int(d)

def main():
    if len(sys.argv) < 5: sys.exit(__doc__)
    base, segm, start, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), float(sys.argv[3]), pathlib.Path(sys.argv[4])
    nb, fps = frames(base); ns, _ = frames(segm); s0 = round(start * fps); s1 = s0 + ns
    if s1 > nb: sys.exit(f"отрезок выходит за конец базы: {s1} > {nb} кадров")
    tmp = out.parent; parts = []
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "8", "-pix_fmt", "yuv420p", "-r", str(fps)]
    if s0 > 0:
        p1 = tmp / "_splice_a.mp4"; subprocess.run([ff(), "-y", "-loglevel", "error", "-i", str(base), "-an", "-vf", f"trim=start_frame=0:end_frame={s0},setpts=PTS-STARTPTS", *enc, str(p1)], check=True); parts.append(p1)
    parts.append(segm.resolve())
    if s1 < nb:
        p3 = tmp / "_splice_b.mp4"; subprocess.run([ff(), "-y", "-loglevel", "error", "-i", str(base), "-an", "-vf", f"trim=start_frame={s1}:end_frame={nb},setpts=PTS-STARTPTS", *enc, str(p3)], check=True); parts.append(p3)
    lst = tmp / "_splice.txt"; lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    subprocess.run([ff(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], check=True)
    for p in parts:
        if p.name.startswith("_splice"): p.unlink()
    lst.unlink()
    n2, _ = frames(out); print(f"готово: {out} ({n2} кадров, было {nb})")
    if n2 != nb: sys.exit("ВНИМАНИЕ: число кадров изменилось")

if __name__ == "__main__":
    main()
