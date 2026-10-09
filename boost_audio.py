#!/usr/bin/env python3
"""
boost_audio.py - aumenta o volume de um vídeo (mp4/mkv) sem recodificar a imagem.

Uso:
    python boost_audio.py filme.mp4                 # +6 dB (o dobro da amplitude, com limitador contra estouro)
    python boost_audio.py filme.mp4 --db 9          # mais alto
    python boost_audio.py filme.mp4 --normalize     # nivela para volume de streaming (recomendado se o filme é baixo)
    python boost_audio.py filme.mp4 --dialogue      # realça falas: comprime picos (explosões) e sobe o resto
    python boost_audio.py filme.mp4 --analyze       # só mede o volume atual e sugere quantos dB subir
    python boost_audio.py filme.mp4 --track 1       # só a 1ª faixa de áudio (as outras são copiadas sem mudar)
    python boost_audio.py filme.mp4 -o saida.mp4

A imagem e as legendas são copiadas (rápido); só o áudio é recodificado (AAC 192 kbps).
Precisa de ffmpeg e ffprobe no PATH. Só usa a biblioteca padrão.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time


def need(tool):
    if not shutil.which(tool):
        sys.exit(f"{tool} não encontrado no PATH (Windows: winget install Gyan.FFmpeg).")


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
        capture_output=True, text=True, errors="replace",
    )
    if out.returncode != 0:
        sys.exit("ffprobe não conseguiu ler o arquivo:\n" + out.stderr.strip())
    data = json.loads(out.stdout)
    audio = [s for s in data["streams"] if s.get("codec_type") == "audio"]
    try:
        dur = float(data.get("format", {}).get("duration") or 0)
    except ValueError:
        dur = 0.0
    return audio, dur


def label(i, s):
    t = s.get("tags", {})
    name = t.get("title") or t.get("comment") or t.get("handler_name") or ""
    return f"#{i + 1}  {t.get('language', 'und'):<5} {s.get('codec_name', '?')} {s.get('channels', '?')}ch  {name}".rstrip()


def fmt(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def analyze(path, track, dur):
    """Mede volume médio e de pico com o filtro volumedetect (lê o áudio inteiro, sem gravar nada)."""
    print("Medindo o volume (lê o áudio todo, sem gerar arquivo)...")
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", f"0:a:{track}", "-af", "volumedetect", "-vn", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
    if not (mean and peak):
        sys.exit("Não consegui medir o volume:\n" + r.stderr[-500:])
    return float(mean.group(1)), float(peak.group(1))


def build_filter(args):
    parts = []
    if args.dialogue:
        # comprime os picos (efeitos altos) e depois sobe: as falas ficam mais audíveis sem estourar
        parts.append("acompressor=threshold=-24dB:ratio=3:attack=20:release=250:makeup=2")
    if args.normalize:
        parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if args.db:
            parts.append(f"volume={args.db}dB")
    else:
        parts.append(f"volume={args.db}dB")
    parts.append("alimiter=limit=0.97:level=disabled")   # segura picos acima de 0 dBFS (evita distorção)
    return ",".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Aumenta o volume do áudio de um vídeo sem recodificar a imagem.")
    ap.add_argument("input", help="vídeo de entrada (mp4, mkv...)")
    ap.add_argument("-o", "--output", help="arquivo de saída (padrão: <nome>_vol+6dB.mp4 ao lado do original)")
    ap.add_argument("--db", type=float, default=None, help="quanto subir, em dB (padrão 6; +6 dB = ~2x; +10 dB = ~3x)")
    ap.add_argument("--normalize", action="store_true", help="nivela o volume (loudnorm -16 LUFS); com --db, soma depois")
    ap.add_argument("--dialogue", action="store_true", help="comprime picos para realçar falas")
    ap.add_argument("--track", type=int, metavar="N", help="só a faixa de áudio N (1, 2...); as demais passam sem mudar")
    ap.add_argument("--bitrate", default="192k", help="bitrate do áudio recodificado (padrão 192k)")
    ap.add_argument("--analyze", action="store_true", help="só mede o volume atual e sugere ganho")
    ap.add_argument("-y", "--yes", action="store_true", help="sobrescreve a saída sem perguntar")
    args = ap.parse_args()

    need("ffmpeg")
    need("ffprobe")
    if not os.path.isfile(args.input):
        sys.exit(f"Arquivo não encontrado: {args.input}")
    audio, dur = probe(args.input)
    if not audio:
        sys.exit("Esse arquivo não tem faixa de áudio.")
    print("Faixas de áudio:")
    for i, s in enumerate(audio):
        print("  " + label(i, s))
    if args.track is not None and not 1 <= args.track <= len(audio):
        sys.exit(f"--track deve estar entre 1 e {len(audio)}.")
    tracks = [args.track - 1] if args.track else list(range(len(audio)))

    if args.analyze:
        mean, peak = analyze(args.input, tracks[0], dur)
        print(f"\nVolume médio: {mean:.1f} dB | pico: {peak:.1f} dB")
        # Filme normal fica perto de -20 a -16 dB de média; o pico é o limite do que dá para subir sem cortar.
        want = max(0.0, -18.0 - mean)
        if peak > -1.0:
            print("O pico já está em ~0 dB: subir o volume "'"puro"'" só achata/distorce as cenas altas.")
            print("Melhor: --dialogue --db 4   (segura os picos e sobe as falas)")
            print("   ou:  --normalize          (nivela o filme todo para o padrão de streaming)")
        else:
            print(f"Sugestão: --db {min(round(want), int(-peak), 12)}   ou  --normalize para nivelar automaticamente.")
        return 0

    if args.db is None:
        args.db = 0.0 if args.normalize else 6.0
    if not args.normalize and args.db <= 0:
        sys.exit("Use --db com um valor positivo (ex.: --db 6) ou --normalize.")

    ext = os.path.splitext(args.input)[1] or ".mp4"
    tag = "norm" if args.normalize else f"vol+{args.db:g}dB"
    out = args.output or os.path.splitext(args.input)[0] + f"_{tag}{ext}"
    if os.path.abspath(out) == os.path.abspath(args.input):
        sys.exit("A saída não pode ser o mesmo arquivo da entrada.")
    if os.path.exists(out) and not args.yes:
        if not sys.stdin.isatty() or input(f"'{out}' já existe. Sobrescrever? [s/N]: ").strip().lower() not in ("s", "sim", "y", "yes"):
            sys.exit("Cancelado (use -y para sobrescrever).")

    flt = build_filter(args)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y", "-i", args.input,
           "-map", "0", "-c", "copy", "-map_metadata", "0"]
    for n in tracks:
        cmd += [f"-filter:a:{n}", flt, f"-c:a:{n}", "aac", f"-b:a:{n}", args.bitrate]
    cmd.append(out)
    print(f"\nFiltro: {flt}")
    print(f"Gerando {out} ...")

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    prog, errors = {}, []
    try:
        for raw in proc.stdout:
            line = raw.strip()
            m = re.match(r"^([a-z_0-9]+)=(.*)$", line)
            if m and (m.group(1) in ("out_time_us", "out_time_ms", "total_size", "speed", "progress") or m.group(1).startswith(("stream_", "frame", "fps", "bitrate", "dup_", "drop_", "out_time"))):
                prog[m.group(1)] = m.group(2)
                if m.group(1) == "progress":
                    try:
                        t = int(prog.get("out_time_us") or prog.get("out_time_ms") or 0) / 1e6
                    except ValueError:
                        t = 0
                    size = int(prog["total_size"]) / 1048576 if prog.get("total_size", "").isdigit() else 0
                    pct = f"{min(100.0, t / dur * 100):5.1f}% | " if dur else ""
                    sys.stdout.write(f"\r\033[K  {pct}{fmt(t)}/{fmt(dur)} | {size:,.0f} MB | {prog.get('speed', '?').strip()}")
                    sys.stdout.flush()
            elif line:
                errors.append(line)
        rc = proc.wait()
    except KeyboardInterrupt:
        print("\nInterrompido; finalizando o arquivo parcial...")
        try:
            proc.stdin.write("q\n")
            proc.stdin.flush()
            proc.wait(timeout=15)
        except Exception:
            proc.terminate()
        return 130
    print()
    if rc != 0:
        print(f"ffmpeg terminou com erro ({rc}):")
        for l in errors[-8:]:
            print("  " + l)
        return rc
    print(f"Pronto: {os.path.abspath(out)} ({os.path.getsize(out) / 1048576:,.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
