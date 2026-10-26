#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Замыкание лупа и сборка финала для 3D-куба. Только stdlib + ffmpeg.

Ни одна видеомодель не отдаёт клип, у которого последний кадр совпадает с первым.
Луп делается склейкой: пинг-понг (клип + его реверс) даёт лупΔ 1-2 против 5-39
у сырой генерации и БЕСПЛАТНО удваивает длительность (6 сек -> 12 сек).

Оговорка: пинг-понг превращает движение в «туда-обратно». Для вращения это
читается как покачивание - либо принимаем, либо берём круговое движение,
которое само возвращается в исходную точку, и тогда хватит --mode cut.

Запуск:
    python cube_loop.py in.mp4 -o loop.mp4                    # пинг-понг
    python cube_loop.py in.mp4 -o loop.mp4 --repeat 2         # 6 -> 24 сек
    python cube_loop.py in.mp4 -o final.mp4 --upscale 2160x3840 \
           --music music.mp3 --volume 0.22                    # финал для куба
    python cube_loop.py in.mp4 -o loop.mp4 --mode xfade --xfade 0.5

Режимы:
    pingpong (по умолчанию) - клип + реверс. Лучший замер лупа.
    xfade                   - кросс-фейд хвоста на начало. Замер заметно хуже
                              (лупΔ ~19 против ~2), берётся только когда
                              покачивание пинг-понга недопустимо.
    cut                     - ничего не клеит, только перекодирует/апскейлит.
"""
import argparse
import os
import subprocess
import sys

WINGET = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    r"Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0-full_build\bin",
)


def tool(name):
    cand = os.environ.get("CUBE_" + name.upper())
    if cand and os.path.isfile(cand):
        return cand
    for suffix in ("", ".exe"):
        for d in os.environ.get("PATH", "").split(os.pathsep) + [WINGET]:
            p = os.path.join(d, name + suffix)
            if os.path.isfile(p):
                return p
    sys.exit(f"{name} не найден: положи в PATH или задай CUBE_{name.upper()}")


FF = tool("ffmpeg")
FP = tool("ffprobe")


def duration(path):
    out = subprocess.run(
        [FP, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        sys.exit(f"не читается длительность: {path}")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("ffmpeg упал:\n" + (r.stderr or "")[-2000:])


def build(args):
    src = args.input
    dur = duration(src)
    chain = []

    if args.mode == "pingpong":
        # trim=start_frame=1 убирает дубль кадра на стыке (иначе микро-заминка)
        chain.append(
            "[0:v]split=2[a][b];"
            "[b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];"
            "[a][r]concat=n=2:v=1[v]"
        )
        out_dur = dur * 2
    elif args.mode == "xfade":
        x = args.xfade
        chain.append(
            f"[0:v]split=2[a][b];"
            f"[a]trim=0:{dur - x},setpts=PTS-STARTPTS[head];"
            f"[b]trim={dur - x}:{dur},setpts=PTS-STARTPTS[tail];"
            f"[head][tail]xfade=transition=fade:duration={x}:"
            f"offset={dur - 2 * x}[v]"
        )
        out_dur = dur - x
    else:
        chain.append("[0:v]null[v]")
        out_dur = dur

    if args.repeat > 1:
        chain.append(f"[v]loop=loop={args.repeat - 1}:size=32767:start=0,"
                     f"setpts=N/FRAME_RATE/TB[v]")
        out_dur *= args.repeat
    if args.upscale:
        w, h = args.upscale.lower().split("x")
        chain.append(f"[v]scale={w}:{h}:flags=lanczos,"
                     f"setsar=1[v]")
    if args.fps:
        chain.append(f"[v]fps={args.fps}[v]")

    cmd = [FF, "-y", "-v", "error", "-i", src]
    if args.music:
        cmd += ["-stream_loop", "-1", "-i", args.music]
        # музыка подрезается ровно под ролик, по краям короткий фейд,
        # чтобы стык лупа не щёлкал
        fade = min(0.4, out_dur / 8)
        chain.append(
            f"[1:a]atrim=0:{out_dur:.3f},asetpts=PTS-STARTPTS,"
            f"volume={args.volume},"
            f"afade=t=in:st=0:d={fade:.3f},"
            f"afade=t=out:st={out_dur - fade:.3f}:d={fade:.3f}[aout]"
        )
    fc = ";".join(chain)

    cmd += ["-filter_complex", fc, "-map", "[v]"]
    if args.music:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", args.output]
    run(cmd)
    return out_dur


def main():
    ap = argparse.ArgumentParser(description="Луп и финальная сборка для 3D-куба")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--mode", choices=("pingpong", "xfade", "cut"),
                    default="pingpong")
    ap.add_argument("--repeat", type=int, default=1,
                    help="повторить готовый луп N раз (луп замкнут, стык не виден)")
    ap.add_argument("--upscale", help="например 2160x3840 - родной кадр куба")
    ap.add_argument("--fps", type=int, help="привести к частоте кадров плеера куба")
    ap.add_argument("--music", help="аудиофайл подложки")
    ap.add_argument("--volume", type=float, default=0.22)
    ap.add_argument("--xfade", type=float, default=0.5,
                    help="длина кросс-фейда в секундах для --mode xfade")
    ap.add_argument("--crf", type=int, default=16,
                    help="16 для сдачи на куб, 23 для черновика")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"нет файла: {args.input}")
    out_dur = build(args)
    size = os.path.getsize(args.output) / 1024 / 1024
    print(f"{args.output}: {out_dur:.1f} сек, {size:.1f} МБ, режим {args.mode}"
          + (f", повтор x{args.repeat}" if args.repeat > 1 else "")
          + (f", апскейл {args.upscale}" if args.upscale else "")
          + (", с музыкой" if args.music else ""))
    print("проверь замером: python cube_stats.py " + args.output)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    main()
