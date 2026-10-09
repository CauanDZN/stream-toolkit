#!/usr/bin/env python3
"""
watch_m3u8.py - abre ou baixa um stream HLS (.m3u8) a partir de uma URL.

Uso:
    python watch_m3u8.py "https://cdn.example.com/path/master.m3u8?token=XXXX"
    python watch_m3u8.py URL --referer "https://example.com/"
    python watch_m3u8.py URL --download video.mp4
    python watch_m3u8.py URL --probe          # só verifica, não abre player
    python watch_m3u8.py URL --player vlc

Download (gera MP4 com as faixas que você escolher):
    python watch_m3u8.py URL --download filme.mp4                # menu: qualidade, áudios e legendas
    python watch_m3u8.py URL --list                              # só mostra o que existe no stream
    python watch_m3u8.py URL --download filme.mp4 --audio pt-BR --subs pt-BR,en -y
    python watch_m3u8.py URL --download filme.mkv --audio all --subs none --quality 720
    --sub-mode embed|burn|srt|both: embed = faixa dentro do MP4 (ligar no player); burn = queimada na imagem
    (sempre visível, recodifica; usa a GPU se der: --encoder auto|nvenc|qsv|amf|x264; tamanho: --sub-size);
    srt = .srt ao lado. Precisa de ffmpeg (e ffprobe); a queima precisa do ffmpeg "full" (libass).

Player na aba do navegador (não precisa de mpv/vlc; é o padrão se nenhum estiver instalado):
    python watch_m3u8.py URL --referer "https://example.com/" --web
    Sobe um proxy local (envia Referer/User-Agent/token) e abre um player hls.js com:
    play/pausa, seek, volume até 200%, velocidade, qualidade, faixa de áudio, legenda,
    PiP, tela cheia, screenshot e atalhos de teclado (tecle ? na página).

Controles de reprodução:
    python watch_m3u8.py URL --quality 720 --volume 60 --speed 1.25 --fullscreen
    python watch_m3u8.py URL --start 10:30 --mute
    python watch_m3u8.py URL --audio-only

Com o mpv, um controlador interativo roda no terminal (tecle 'h' para a ajuda):
    espaço pausa | m mudo | ↑/↓ volume | ←/→ seek 10s | [ ] velocidade | r reseta velocidade
    a faixa de áudio | s legenda | 0-9 qualidade | f tela cheia | t screenshot | i info | q sair
Desative com --no-controls.

Só usa a biblioteca padrão. Precisa de mpv, vlc ou ffplay instalado para assistir,
e de ffmpeg para baixar. O controlador interativo exige mpv.
"""
import argparse
import html
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urljoin, urlparse

IS_WINDOWS = os.name == "nt"

DEFAULT_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)
PLAYERS = ("mpv", "vlc", "ffplay")


def build_headers(referer, user_agent, extra):
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
        # Muitos servidores também conferem o Origin
        proto, _, rest = referer.partition("://")
        if rest:
            headers["Origin"] = f"{proto}://{rest.split('/')[0]}"
    for h in extra or []:
        if ":" not in h:
            sys.exit(f"Header inválido (use 'Nome: valor'): {h}")
        k, v = h.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers


def fetch(url, headers, timeout=15):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def parse_attrs(line):
    """'#EXT-X-MEDIA:TYPE=AUDIO,NAME="a,b"' -> {'TYPE': 'AUDIO', 'NAME': 'a,b'}."""
    body = line.split(":", 1)[1] if ":" in line else ""
    return {k: v.strip('"') for k, v in ATTR_RE.findall(body)}


def parse_playlist(text, base_url):
    """Retorna (tipo, info). tipo: 'master' | 'media' | 'invalid'.

    Em playlists master, info é a lista de variantes; cada uma traz 'attrs' (do STREAM-INF)
    e 'media' (todas as EXT-X-MEDIA: áudios e legendas alternativos, com URI absoluta)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines or lines[0] != "#EXTM3U":
        return "invalid", None
    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
        media = []
        for l in lines:
            if l.startswith("#EXT-X-MEDIA:"):
                m = parse_attrs(l)
                if m.get("URI"):
                    m["URI"] = urljoin(base_url, m["URI"])
                media.append(m)
        variants = []
        for i, l in enumerate(lines):
            if l.startswith("#EXT-X-STREAM-INF"):
                attrs = parse_attrs(l)
                try:
                    bw = int(attrs.get("BANDWIDTH", ""))
                except ValueError:
                    bw = None
                if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                    variants.append({
                        "url": urljoin(base_url, lines[i + 1]), "res": attrs.get("RESOLUTION"),
                        "bw": bw, "attrs": attrs, "media": media,
                    })
        return "master", variants
    segs = [l for l in lines if not l.startswith("#")]
    info = {
        "segments": len(segs),
        "first": urljoin(base_url, segs[0]) if segs else None,
        "live": not any(l == "#EXT-X-ENDLIST" for l in lines),
        "encrypted": any(l.startswith("#EXT-X-KEY") and "METHOD=NONE" not in l for l in lines),
    }
    return "media", info


def probe(url, headers):
    """Verifica a playlist. Retorna (ok, variantes); variantes é [] se não for master."""
    try:
        status, ctype, body = fetch(url, headers)
    except urllib.error.HTTPError as e:
        print(f"[!] HTTP {e.code} ao abrir a playlist.")
        if e.code in (401, 403):
            print("    Token expirado ou o servidor exige Referer/headers. Tente --referer com o site de origem.")
        return False, []
    except urllib.error.URLError as e:
        print(f"[!] Falha de rede: {e.reason}")
        return False, []

    text = body.decode("utf-8", errors="replace")
    kind, info = parse_playlist(text, url)
    if kind == "invalid":
        print(f"[!] Resposta não parece um m3u8 (Content-Type: {ctype}). Início:")
        print("    " + text[:200].replace("\n", "\n    "))
        return False, []
    variants = []
    if kind == "master":
        variants = sorted(info, key=lambda v: v["bw"] or 0, reverse=True)
        print(f"[ok] Playlist master com {len(variants)} qualidade(s):")
        for i, v in enumerate(variants, 1):
            print(f"     {i}) {quality_label(v)}")
    else:
        tipo = "AO VIVO" if info["live"] else "VOD"
        print(f"[ok] Playlist de mídia ({tipo}), {info['segments']} segmentos.")
        if info["encrypted"]:
            print("     Aviso: usa #EXT-X-KEY (AES-128); o player/ffmpeg cuida disso se a chave for acessível.")
    return True, variants


def quality_label(v):
    return f"{v['res'] or '?':>10}  {(v['bw'] or 0) // 1000:>6} kbps"


def variant_height(v):
    try:
        return int((v["res"] or "").split("x")[1])
    except (IndexError, ValueError):
        return None


def pick_variant(variants, spec):
    """spec: 'best' | 'worst' | altura (ex.: '720'). Devolve a variante ou None."""
    if not variants or not spec or spec == "auto":
        return None
    if spec == "best":
        return variants[0]
    if spec == "worst":
        return variants[-1]
    try:
        want = int(spec.lower().rstrip("p"))
    except ValueError:
        sys.exit(f"--quality inválido: {spec} (use best, worst, auto ou uma altura como 720)")
    with_h = [v for v in variants if variant_height(v)]
    if not with_h:
        return variants[0]
    # Maior qualidade que não passa da altura pedida; senão a menor disponível.
    fit = [v for v in with_h if variant_height(v) <= want]
    return max(fit, key=variant_height) if fit else min(with_h, key=variant_height)


def parse_time(text):
    """'90', '1:30' ou '1:02:03' -> segundos (float)."""
    try:
        secs = 0.0
        for part in str(text).split(":"):
            secs = secs * 60 + float(part)
        return secs
    except ValueError:
        sys.exit(f"Tempo inválido: {text} (use segundos, MM:SS ou HH:MM:SS)")


def header_blob(headers):
    return "".join(f"{k}: {v}\r\n" for k, v in headers.items())


def player_cmd(player, url, headers, opts=None, ipc_path=None):
    opts = opts or {}
    ua = headers.get("User-Agent", DEFAULT_UA)
    other = {k: v for k, v in headers.items() if k != "User-Agent"}
    start = opts.get("start")
    if player == "mpv":
        cmd = ["mpv", f"--user-agent={ua}"]
        if other:
            fields = ",".join(f"{k}: {v}" for k, v in other.items())
            cmd.append(f"--http-header-fields={fields}")
        if ipc_path:
            cmd.append(f"--input-ipc-server={ipc_path}")
        if opts.get("volume") is not None:
            cmd.append(f"--volume={opts['volume']:g}")
        if opts.get("mute"):
            cmd.append("--mute=yes")
        if opts.get("speed"):
            cmd.append(f"--speed={opts['speed']:g}")
        if start:
            cmd.append(f"--start={start}")
        if opts.get("fullscreen"):
            cmd.append("--fs")
        if opts.get("audio_only"):
            cmd += ["--no-video", "--force-window=no"]
        return cmd + [url]
    if player == "vlc":
        cmd = ["vlc", f"--http-user-agent={ua}"]
        if "Referer" in other:
            cmd.append(f"--http-referrer={other['Referer']}")
        if start:
            cmd.append(f"--start-time={parse_time(start)}")
        if opts.get("speed"):
            cmd.append(f"--rate={opts['speed']}")
        if opts.get("fullscreen"):
            cmd.append("--fullscreen")
        if opts.get("audio_only"):
            cmd.append("--no-video")
        if opts.get("mute"):
            cmd.append("--volume=0")
        elif opts.get("volume") is not None:
            # VLC usa 0-256 (256 = 100%)
            cmd.append(f"--volume={round(opts['volume'] * 2.56)}")
        return cmd + [url]
    if player == "ffplay":
        cmd = ["ffplay", "-user_agent", ua]
        if other:
            cmd += ["-headers", header_blob(other)]
        if start:
            cmd += ["-ss", str(parse_time(start))]
        if opts.get("mute"):
            cmd += ["-volume", "0"]
        elif opts.get("volume") is not None:
            cmd += ["-volume", str(max(0, min(100, int(opts["volume"]))))]
        if opts.get("fullscreen"):
            cmd.append("-fs")
        if opts.get("audio_only"):
            cmd.append("-vn")
        if opts.get("speed"):
            cmd += ["-af", f"atempo={opts['speed']}"]
        return cmd + [url]
    raise ValueError(player)


class MpvIPC:
    """Cliente do IPC JSON do mpv (socket Unix ou named pipe no Windows).

    Síncrono e sem thread de leitura: cada comando lê até achar o request_id
    correspondente (eventos e respostas antigas são descartados). Assim uma mesma
    handle de pipe no Windows nunca fica com leitura e escrita concorrentes.
    """

    def __init__(self, path):
        self.path = path
        self._n = 0
        self._sock = None
        self._file = None

    def connect(self, timeout=10.0, alive=lambda: True):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline and alive():
            try:
                if IS_WINDOWS:
                    self._file = open(self.path, "r+b", buffering=0)
                else:
                    self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self._sock.settimeout(5)
                    self._sock.connect(self.path)
                    self._file = self._sock.makefile("rwb", buffering=0)
                try:
                    self.command("disable_event", "all")  # só queremos respostas
                except Exception:
                    pass
                return True
            except (OSError, FileNotFoundError) as e:
                last = e
                if self._sock:
                    self._sock.close()
                    self._sock = None
                time.sleep(0.15)
        return False

    def command(self, *args):
        """Envia um comando e devolve o campo 'data'. Levanta RuntimeError em erro do mpv."""
        self._n += 1
        rid = self._n
        self._file.write((json.dumps({"command": list(args), "request_id": rid}) + "\n").encode())
        while True:
            line = self._file.readline()
            if not line:
                raise ConnectionError("mpv fechou o IPC")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("request_id") == rid:
                if msg.get("error") != "success":
                    raise RuntimeError(msg.get("error"))
                return msg.get("data")

    def get(self, prop, default=None):
        try:
            return self.command("get_property", prop)
        except RuntimeError:
            return default

    def close(self):
        for o in (self._file, self._sock):
            try:
                if o:
                    o.close()
            except OSError:
                pass


class _KeyReader:
    """Leitura de tecla única sem Enter, multiplataforma. get(timeout) -> nome ou None."""

    def __enter__(self):
        if not IS_WINDOWS:
            import termios
            import tty
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *exc):
        if not IS_WINDOWS:
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)

    def get(self, timeout=0.1):
        if IS_WINDOWS:
            import msvcrt
            end = time.time() + timeout
            while time.time() < end:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch())
                    return ch
                time.sleep(0.02)
            return None
        import select
        if not select.select([sys.stdin], [], [], timeout)[0]:
            return None
        ch = os.read(self._fd, 1).decode(errors="ignore")
        if ch == "\x1b":
            if select.select([sys.stdin], [], [], 0.05)[0]:
                seq = os.read(self._fd, 2).decode(errors="ignore")
                return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(seq)
            return None
        return ch


HELP = """\
  Controles (com o terminal em foco):
    espaço  pausa/continua          m  mudo                 ↑/↓ ou +/-  volume ±5
    ←/→     voltar/avançar 10s      , .  voltar/avançar 60s  [ ]  velocidade -/+ 10%   r  velocidade 1x
    a       próxima faixa de áudio  s  próxima legenda       f  tela cheia
    0       qualidade automática    1-9  escolhe a qualidade da lista
    t       screenshot              i  informações           h  ajuda     q  sair"""


class Controller:
    def __init__(self, ipc, url, variants, proc):
        self.ipc = ipc
        self.url = url
        self.variants = variants
        self.proc = proc
        self.current = 0  # 0 = automática (playlist master)

    def say(self, msg):
        print(f"\r\033[K[ctl] {msg}")

    @staticmethod
    def fmt(t):
        if t is None:
            return "--:--"
        t = int(t)
        h, r = divmod(t, 3600)
        m, s = divmod(r, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def _track(self, kind):
        tracks = [t for t in (self.ipc.get("track-list") or []) if t.get("type") == kind]
        cur = next((t for t in tracks if t.get("selected")), None)
        if cur is None:
            return "desligada" if kind == "sub" else "nenhuma"
        name = cur.get("lang") or cur.get("title") or f"#{cur.get('id')}"
        return f"#{cur.get('id')} {name}"

    def info(self):
        g = self.ipc.get
        pos, dur = g("time-pos"), g("duration")
        self.say(
            f"{self.fmt(pos)} / {self.fmt(dur) if dur else 'ao vivo'} | "
            f"volume {g('volume', '?')}{' (mudo)' if g('mute') else ''} | "
            f"velocidade {g('speed', 1)}x | {'pausado' if g('pause') else 'tocando'} | "
            f"áudio {self._track('audio')} | legenda {self._track('sub')} | "
            f"qualidade {'auto' if not self.current else quality_label(self.variants[self.current - 1]).strip()}"
        )

    def set_quality(self, n):
        if not self.variants:
            return self.say("Esse stream não tem várias qualidades.")
        if n > len(self.variants):
            return self.say(f"Só existem {len(self.variants)} qualidades (0 = auto).")
        url = self.url if n == 0 else self.variants[n - 1]["url"]
        pos = self.ipc.get("time-pos")
        live = not self.ipc.get("duration")
        self.say("trocando qualidade...")
        self.ipc.command("loadfile", url, "replace")
        self.current = n
        if pos and not live:
            for _ in range(100):  # espera o novo arquivo abrir para voltar ao ponto
                if self.ipc.get("duration"):
                    self.ipc.command("seek", pos, "absolute")
                    break
                time.sleep(0.1)
        self.say("qualidade: " + ("auto" if n == 0 else quality_label(self.variants[n - 1]).strip()))

    def handle(self, key):
        c = self.ipc.command
        if key == " ":
            c("cycle", "pause")
            self.say("pausado" if self.ipc.get("pause") else "tocando")
        elif key == "m":
            c("cycle", "mute")
            self.say("mudo" if self.ipc.get("mute") else "som ligado")
        elif key in ("up", "+", "="):
            c("add", "volume", 5)
            self.say(f"volume {self.ipc.get('volume')}")
        elif key in ("down", "-"):
            c("add", "volume", -5)
            self.say(f"volume {self.ipc.get('volume')}")
        elif key in ("right", "left", ",", "."):
            step = {"right": 10, "left": -10, ".": 60, ",": -60}[key]
            try:
                c("seek", step, "relative")
                self.say(f"{self.fmt(self.ipc.get('time-pos'))} / {self.fmt(self.ipc.get('duration'))}")
            except RuntimeError:
                self.say("não dá para andar no tempo aqui (stream ao vivo?)")
        elif key in ("[", "]"):
            c("multiply", "speed", 0.9 if key == "[" else 1.1)
            self.say(f"velocidade {round(self.ipc.get('speed', 1), 2)}x")
        elif key == "r":
            c("set_property", "speed", 1.0)
            self.say("velocidade 1x")
        elif key == "a":
            c("cycle", "audio")
            self.say(f"áudio: {self._track('audio')}")
        elif key == "s":
            c("cycle", "sub")
            self.say(f"legenda: {self._track('sub')}")
        elif key == "f":
            c("cycle", "fullscreen")
        elif key == "t":
            c("screenshot")
            self.say("screenshot salvo (pasta atual do mpv)")
        elif key == "i":
            self.info()
        elif key in ("h", "?"):
            print(HELP)
        elif key and key.isdigit():
            self.set_quality(int(key))
        elif key == "q":
            c("quit")
            return False
        return True

    def run(self):
        print(HELP)
        try:
            with _KeyReader() as keys:
                while self.proc.poll() is None:
                    key = keys.get(0.1)
                    if key is None:
                        continue
                    try:
                        if not self.handle(key):
                            break
                    except (ConnectionError, BrokenPipeError, OSError):
                        break
                    except RuntimeError as e:
                        self.say(f"mpv recusou o comando: {e}")
        except KeyboardInterrupt:
            pass
        return self.proc


def run_with_controls(cmd, ipc_path, url, variants):
    proc = subprocess.Popen(cmd)
    ipc = MpvIPC(ipc_path)
    try:
        if not ipc.connect(alive=lambda: proc.poll() is None):
            print("[!] Não consegui conectar ao IPC do mpv; seguindo sem controlador (use as teclas da janela do mpv).")
            return proc.wait()
        Controller(ipc, url, variants, proc).run()
    except KeyboardInterrupt:
        pass
    finally:
        ipc.close()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.terminate()
    return proc.returncode or 0


PLAYER_HTML = r'''<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<link rel="icon" href="data:,">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --accent:#ff4d5a; --bg:#000; --fg:#f2f2f2; --muted:#9a9a9a; }
  * { box-sizing:border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--fg);
    font:14px/1.3 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; overflow:hidden; }
  #wrap { position:relative; width:100%; height:100%; background:#000; user-select:none; }
  #wrap.idle { cursor:none; }
  video { width:100%; height:100%; display:block; background:#000; object-fit:contain; }
  #center { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; pointer-events:none; }
  #bigplay { width:84px; height:84px; border-radius:50%; background:rgba(0,0,0,.55); border:2px solid rgba(255,255,255,.8);
    display:none; align-items:center; justify-content:center; pointer-events:auto; cursor:pointer; }
  #wrap.paused #bigplay { display:flex; }
  #bigplay svg { width:38px; height:38px; fill:#fff; margin-left:4px; }
  #spinner { position:absolute; width:52px; height:52px; border:4px solid rgba(255,255,255,.25); border-top-color:#fff;
    border-radius:50%; animation:spin .9s linear infinite; display:none; }
  #wrap.loading #spinner { display:block; }
  #wrap.loading #bigplay { display:none; }
  @keyframes spin { to { transform:rotate(360deg); } }
  #msg { position:absolute; left:50%; top:46%; transform:translate(-50%,-50%); max-width:80%; text-align:center;
    background:rgba(0,0,0,.75); border:1px solid #444; padding:14px 18px; border-radius:10px; display:none; }
  #msg.err { border-color:var(--accent); }
  #toast { position:absolute; top:16px; left:50%; transform:translateX(-50%); background:rgba(0,0,0,.72); padding:6px 14px;
    border-radius:999px; opacity:0; transition:opacity .2s; pointer-events:none; font-weight:600; }
  #toast.show { opacity:1; }
  #controls { position:absolute; left:0; right:0; bottom:0; padding:28px 14px 10px;
    background:linear-gradient(transparent, rgba(0,0,0,.85)); transition:opacity .2s, transform .2s; }
  #controls { opacity:0; pointer-events:none; transform:translateY(6px); }
  #wrap.hover:not(.idle) #controls, #controls:focus-within { opacity:1; pointer-events:auto; transform:none; }
  #seek { width:100%; height:16px; margin:0 0 6px; -webkit-appearance:none; appearance:none; cursor:pointer; border-radius:8px;
    background:
      linear-gradient(to right, var(--accent) var(--p,0%), transparent var(--p,0%)),
      linear-gradient(to right, #8a8a8a var(--b,0%), #3a3a3a var(--b,0%));
    background-size:100% 5px; background-repeat:no-repeat; background-position:center; }
  #seek::-webkit-slider-runnable-track { background:transparent; height:16px; }
  #seek::-moz-range-track { background:transparent; height:16px; }
  #seek::-webkit-slider-thumb { -webkit-appearance:none; width:13px; height:13px; border-radius:50%; background:var(--accent); margin-top:1.5px; }
  #seek::-moz-range-thumb { width:13px; height:13px; border:0; border-radius:50%; background:var(--accent); }
  .row { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .spacer { flex:1; }
  button, select { font:inherit; color:var(--fg); background:rgba(255,255,255,.1); border:1px solid transparent;
    border-radius:8px; padding:5px 9px; cursor:pointer; }
  button:hover, select:hover { background:rgba(255,255,255,.2); }
  button:focus-visible, select:focus-visible, #seek:focus-visible, #vol:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
  button svg { width:20px; height:20px; fill:currentColor; display:block; }
  button { display:inline-flex; align-items:center; gap:4px; }
  select { padding:5px 6px; max-width:150px; }
  select option { background:#1b1b1b; color:#fff; }
  #time { font-variant-numeric:tabular-nums; color:#ddd; padding:0 6px; white-space:nowrap; }
  #vol { width:90px; accent-color:var(--accent); }
  #live { display:none; color:#fff; background:#333; }
  #live.on { display:inline-flex; }
  #live.atedge { background:var(--accent); }
  #help { position:absolute; inset:auto 16px 110px auto; background:rgba(0,0,0,.85); border:1px solid #444; border-radius:10px;
    padding:12px 16px; display:none; font-size:13px; }
  #help.show { display:block; }
  #help td { padding:2px 10px 2px 0; } #help td:first-child { color:var(--accent); font-weight:600; white-space:nowrap; }
  @media (max-width:640px){ #vol{display:none;} select{max-width:96px;} }
</style>
</head>
<body>
<div id="wrap" class="paused loading">
  <video id="v" playsinline></video>
  <div id="center"><div id="spinner"></div>
    <div id="bigplay" title="Reproduzir"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></div></div>
  <div id="msg"></div>
  <div id="toast"></div>
  <div id="help"><table>
    <tr><td>Espaço / K</td><td>Pausar / continuar</td></tr>
    <tr><td>← →</td><td>-5s / +5s</td></tr>
    <tr><td>J L</td><td>-10s / +10s</td></tr>
    <tr><td>↑ ↓</td><td>Volume ±5% (até 200%)</td></tr>
    <tr><td>M</td><td>Mudo</td></tr>
    <tr><td>&lt; &gt;</td><td>Velocidade -/+</td></tr>
    <tr><td>0-9</td><td>Pular para 0%-90%</td></tr>
    <tr><td>Home / End</td><td>Início / ao vivo</td></tr>
    <tr><td>F</td><td>Tela cheia</td></tr>
    <tr><td>P</td><td>Picture-in-Picture</td></tr>
    <tr><td>S</td><td>Screenshot</td></tr>
    <tr><td>?</td><td>Esta ajuda</td></tr></table></div>
  <div id="controls">
    <input id="seek" type="range" min="0" max="1000" value="0" step="1" aria-label="Posição">
    <div class="row">
      <button id="play" title="Reproduzir/Pausar (Espaço)"></button>
      <button id="back" title="Voltar 10s (J)"><svg viewBox="0 0 24 24"><path d="M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/></svg>10</button>
      <button id="fwd" title="Avançar 10s (L)">10<svg viewBox="0 0 24 24"><path d="M12 5V1l5 5-5 5V7a6 6 0 1 0 6 6h2a8 8 0 1 1-8-8z"/></svg></button>
      <button id="mute" title="Mudo (M)"></button>
      <input id="vol" type="range" min="0" max="200" value="100" step="1" aria-label="Volume">
      <span id="time">0:00 / 0:00</span>
      <button id="live" title="Ir para o ao vivo (End)">AO VIVO</button>
      <span class="spacer"></span>
      <select id="speed" title="Velocidade"></select>
      <select id="quality" title="Qualidade" disabled><option>Qualidade</option></select>
      <select id="audio" title="Faixa de áudio" hidden></select>
      <select id="subs" title="Legenda" hidden></select>
      <button id="shot" title="Screenshot (S)"><svg viewBox="0 0 24 24"><path d="M9 3 7.2 5H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3.2L15 3H9zm3 15a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-2a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/></svg></button>
      <button id="pip" title="Picture-in-Picture (P)"><svg viewBox="0 0 24 24"><path d="M19 7h-8v6h8V7zm2-4H3a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h18a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 16H3V5h18v14z"/></svg></button>
      <button id="fs" title="Tela cheia (F)"><svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg></button>
    </div>
  </div>
</div>
<script src="__HLSJS__"></script>
<script>
(() => {
"use strict";
const cfg = __CONFIG__;
const $ = id => document.getElementById(id);
const v = $("v"), wrap = $("wrap");
const ICON = {
  play:'<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  pause:'<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>',
  vol:'<svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4z"/></svg>',
  muted:'<svg viewBox="0 0 24 24"><path d="M16.5 12A4.5 4.5 0 0 0 14 8v2.2l2.5 2.5V12zM19 12a7 7 0 0 1-.9 3.4l1.5 1.5A9 9 0 0 0 21 12a9 9 0 0 0-7-8.8v2.1A7 7 0 0 1 19 12zM4.3 3 3 4.3 7.7 9H3v6h4l5 5v-6.7l4.3 4.3c-.7.5-1.4.9-2.3 1.1v2.1a9 9 0 0 0 3.7-1.8l2 2 1.3-1.3L4.3 3zM12 4 9.9 6.1 12 8.2V4z"/></svg>'
};
const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 3];
const store = {
  get(k){ try { return localStorage.getItem("w8:"+k); } catch(e){ return null; } },
  set(k,x){ try { localStorage.setItem("w8:"+k, x); } catch(e){} }
};
let hls = null, live = false, seeking = false, idleTimer = 0, toastTimer = 0, netRetries = 0;
let audioCtx = null, gainNode = null, pendingBoost = null;

function fmt(t){
  if (!isFinite(t) || t < 0) t = 0;
  t = Math.floor(t);
  const h = Math.floor(t/3600), m = Math.floor(t%3600/60), s = t%60;
  return (h ? h+":"+String(m).padStart(2,"0") : m)+":"+String(s).padStart(2,"0");
}
function toast(text){
  const el = $("toast"); el.textContent = text; el.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("show"), 1200);
}
function showMsg(text, err){
  const el = $("msg"); el.textContent = text || ""; el.className = err ? "err" : "";
  el.style.display = text ? "block" : "none";
}

/* ---------- reprodução ---------- */
function togglePlay(){ if (v.paused) v.play().catch(()=>{}); else v.pause(); }
function seekBy(d){
  const r = range(); if (!r) return;
  v.currentTime = Math.min(Math.max(v.currentTime + d, r[0]), r[1]);
  toast((d > 0 ? "+" : "") + d + "s");
}
function range(){
  if (v.seekable && v.seekable.length) return [v.seekable.start(0), v.seekable.end(v.seekable.length-1)];
  if (isFinite(v.duration)) return [0, v.duration];
  return null;
}
function goLive(){
  const r = range(); if (!r) return;
  const edge = (hls && hls.liveSyncPosition) ? hls.liveSyncPosition : r[1];
  v.currentTime = edge; if (v.paused) v.play().catch(()=>{});
}
$("play").onclick = togglePlay;
$("bigplay").onclick = togglePlay;
v.addEventListener("click", togglePlay);
v.addEventListener("dblclick", toggleFs);
$("back").onclick = () => seekBy(-10);
$("fwd").onclick = () => seekBy(10);
$("live").onclick = goLive;
v.addEventListener("play", () => { wrap.classList.remove("paused"); $("play").innerHTML = ICON.pause; });
v.addEventListener("pause", () => { wrap.classList.add("paused"); $("play").innerHTML = ICON.play; });
v.addEventListener("waiting", () => wrap.classList.add("loading"));
["playing","canplay","loadeddata"].forEach(e => v.addEventListener(e, () => { wrap.classList.remove("loading"); showMsg(""); }));
$("play").innerHTML = ICON.play;

/* ---------- barra de progresso ---------- */
function paint(){
  const r = range();
  let p = 0, b = 0;
  if (r && r[1] > r[0]) {
    p = (v.currentTime - r[0]) / (r[1] - r[0]) * 100;
    for (let i = 0; i < v.buffered.length; i++)
      if (v.buffered.start(i) <= v.currentTime + .5 && v.buffered.end(i) >= v.currentTime) b = (v.buffered.end(i) - r[0]) / (r[1] - r[0]) * 100;
  }
  p = Math.min(100, Math.max(0, p)); b = Math.min(100, Math.max(b, p));
  const seek = $("seek");
  seek.style.setProperty("--p", p + "%"); seek.style.setProperty("--b", b + "%");
  if (!seeking) seek.value = Math.round(p * 10);
  const total = live ? (r ? fmt(r[1] - v.currentTime) : "") : fmt(v.duration);
  $("time").textContent = live ? "-" + total : fmt(v.currentTime) + " / " + total;
  const l = $("live"); l.classList.toggle("on", live);
  l.classList.toggle("atedge", live && r && (r[1] - v.currentTime) < 12);
}
["timeupdate","progress","durationchange","seeked","loadedmetadata"].forEach(e => v.addEventListener(e, paint));
$("seek").addEventListener("input", () => {
  seeking = true;
  const r = range(); if (!r) return;
  const t = r[0] + (r[1] - r[0]) * $("seek").value / 1000;
  $("time").textContent = fmt(t) + (live ? "" : " / " + fmt(v.duration));
});
$("seek").addEventListener("change", () => {
  const r = range(); seeking = false; if (!r) return;
  v.currentTime = r[0] + (r[1] - r[0]) * $("seek").value / 1000;
});

/* ---------- volume ---------- */
function applyVolume(pct){
  pct = Math.max(0, Math.min(200, pct));
  const wanted = pct;
  pendingBoost = null;
  if (pct > 100 && !ensureGain()) { pendingBoost = wanted; pct = 100; }   // sem gesto do usuário ainda: aplica no 1º clique
  if (gainNode) gainNode.gain.value = pct > 100 ? pct / 100 : 1;
  v.volume = pct > 100 ? 1 : pct / 100;
  $("vol").value = Math.round(wanted);
  store.set("vol", String(Math.round(wanted)));
  syncMute();
}
function ensureGain(){
  if (gainNode) return true;
  if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return false;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    audioCtx = new AC();
    audioCtx.resume().catch(()=>{});
    const src = audioCtx.createMediaElementSource(v);
    gainNode = audioCtx.createGain();
    src.connect(gainNode).connect(audioCtx.destination);
    return true;
  } catch (e) { audioCtx = null; gainNode = null; return false; }
}
function syncMute(){
  $("mute").innerHTML = (v.muted || v.volume === 0) ? ICON.muted : ICON.vol;
  store.set("mute", v.muted ? "1" : "0");
}
function toggleMute(){ v.muted = !v.muted; syncMute(); toast(v.muted ? "Mudo" : "Som ligado"); }
function volBy(d){
  const cur = Number($("vol").value); v.muted = false;
  applyVolume(cur + d); toast("Volume " + $("vol").value + "%");
}
$("mute").onclick = toggleMute;
$("vol").addEventListener("input", () => { v.muted = false; applyVolume(Number($("vol").value)); });
window.addEventListener("pointerdown", () => {           // libera o boost > 100% no primeiro gesto
  if (pendingBoost) { const p = pendingBoost; pendingBoost = null; applyVolume(p); }
}, { once: false });

/* ---------- velocidade ---------- */
const sp = $("speed");
SPEEDS.forEach(s => { const o = document.createElement("option"); o.value = s; o.textContent = s + "x"; sp.appendChild(o); });
function setSpeed(s){
  s = Math.min(16, Math.max(0.1, s));
  v.defaultPlaybackRate = s; v.playbackRate = s;   // default: senão o load do src zera a velocidade
  if (![...sp.options].some(o => Number(o.value) === s)) {
    const o = document.createElement("option"); o.value = s; o.textContent = s + "x"; sp.appendChild(o);
  }
  sp.value = s; store.set("speed", String(s));
}
sp.onchange = () => { setSpeed(Number(sp.value)); toast(sp.value + "x"); };
function speedStep(dir){
  const list = [...sp.options].map(o => Number(o.value)).sort((a,b)=>a-b);
  const cur = v.playbackRate;
  const next = dir > 0 ? list.find(x => x > cur + 1e-6) : [...list].reverse().find(x => x < cur - 1e-6);
  if (next) { setSpeed(next); toast(next + "x"); }
}

/* ---------- tela cheia / PiP / screenshot ---------- */
function toggleFs(){
  if (document.fullscreenElement) document.exitFullscreen();
  else (wrap.requestFullscreen || wrap.webkitRequestFullscreen).call(wrap).catch(()=>{});
}
$("fs").onclick = toggleFs;
function togglePip(){
  if (!document.pictureInPictureEnabled) return toast("PiP indisponível");
  if (document.pictureInPictureElement) document.exitPictureInPicture();
  else v.requestPictureInPicture().catch(()=>toast("PiP indisponível"));
}
$("pip").onclick = togglePip;
$("shot").onclick = shot;
function shot(){
  if (!v.videoWidth) return toast("Sem imagem ainda");
  const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext("2d").drawImage(v, 0, 0);
  c.toBlob(b => {
    if (!b) return toast("Falha no screenshot");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b); a.download = "frame_" + fmt(v.currentTime).replace(/:/g, "-") + ".png";
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); toast("Screenshot salvo");
  }, "image/png");
}

/* ---------- teclado ---------- */
window.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "select") return;
  const k = e.key;
  const map = {
    " ": togglePlay, k: togglePlay, K: togglePlay,
    ArrowLeft: () => seekBy(-5), ArrowRight: () => seekBy(5), j: () => seekBy(-10), J: () => seekBy(-10),
    l: () => seekBy(10), L: () => seekBy(10),
    ArrowUp: () => volBy(5), ArrowDown: () => volBy(-5),
    m: toggleMute, M: toggleMute, f: toggleFs, F: toggleFs, p: togglePip, P: togglePip, s: shot, S: shot,
    "<": () => speedStep(-1), ">": () => speedStep(1), ",": () => speedStep(-1), ".": () => speedStep(1),
    Home: () => { const r = range(); if (r) v.currentTime = r[0]; }, End: goLive,
    "?": () => $("help").classList.toggle("show"), Escape: () => $("help").classList.remove("show")
  };
  let fn = map[k];
  if (!fn && /^[0-9]$/.test(k) && !live) fn = () => { const r = range(); if (r) v.currentTime = r[0] + (r[1]-r[0]) * Number(k) / 10; };
  if (fn) { e.preventDefault(); if (tag === "input" && (k === " ")) e.target.blur(); fn(); }
});

/* ---------- controles somem quando parado o mouse ---------- */
function wake(){
  wrap.classList.add("hover"); wrap.classList.remove("idle"); clearTimeout(idleTimer);
  idleTimer = setTimeout(() => wrap.classList.add("idle"), 2500);
}
["mousemove","pointerdown","touchstart","keydown"].forEach(e => wrap.addEventListener(e, wake));
$("controls").addEventListener("mouseenter", () => { clearTimeout(idleTimer); wrap.classList.add("hover"); wrap.classList.remove("idle"); });
wrap.addEventListener("mouseleave", () => { clearTimeout(idleTimer); wrap.classList.remove("hover"); });

/* ---------- HLS ---------- */
function pickLevel(levels, spec){
  if (!spec || spec === "auto") return -1;
  if (spec === "best") return levels.length - 1;
  if (spec === "worst") return 0;
  const want = parseInt(String(spec), 10);
  let best = -1;
  levels.forEach((l, i) => { if (l.height && l.height <= want && (best < 0 || l.height >= levels[best].height)) best = i; });
  if (best >= 0) return best;
  let low = 0; levels.forEach((l, i) => { if (l.height && (!levels[low].height || l.height < levels[low].height)) low = i; });
  return low;
}
function fillSelect(sel, items, current){
  sel.innerHTML = "";
  items.forEach(([val, label]) => { const o = document.createElement("option"); o.value = val; o.textContent = label; sel.appendChild(o); });
  sel.value = String(current);
}
function setupHls(){
  hls = new Hls({ startPosition: cfg.start > 0 ? cfg.start : -1, enableWorker: true, lowLatencyMode: true });
  hls.loadSource(cfg.src); hls.attachMedia(v);
  hls.on(Hls.Events.MANIFEST_PARSED, (_, d) => {
    const levels = hls.levels;
    const q = $("quality");
    if (levels.length > 1) {
      const items = [["-1", "Auto"]];
      levels.forEach((l, i) => items.push([String(i), (l.height ? l.height + "p" : Math.round(l.bitrate/1000) + " kbps")]));
      items.sort((a, b) => (a[0] === "-1" ? -1 : b[0] === "-1" ? 1 : Number(b[0]) - Number(a[0])));   // maior primeiro
      const initial = pickLevel(levels, cfg.quality);
      fillSelect(q, items, initial);
      q.disabled = false; hls.currentLevel = initial;
    } else { q.innerHTML = "<option>Única qualidade</option>"; }
    if (cfg.autoplay) v.play().catch(() => {});
  });
  hls.on(Hls.Events.LEVEL_SWITCHED, (_, d) => {
    const l = hls.levels[d.level]; const q = $("quality");
    if (hls.autoLevelEnabled && l && q.options[0]) q.options[0].textContent = "Auto (" + (l.height ? l.height + "p" : Math.round(l.bitrate/1000)+"k") + ")";
  });
  hls.on(Hls.Events.LEVEL_LOADED, (_, d) => { live = !!d.details.live; paint(); });
  hls.on(Hls.Events.AUDIO_TRACKS_UPDATED, () => {
    const a = $("audio"), t = hls.audioTracks;
    a.hidden = t.length < 2;
    fillSelect(a, t.map((x, i) => [String(i), "Áudio: " + (x.name || x.lang || ("#" + (i+1)))]), hls.audioTrack);
  });
  hls.on(Hls.Events.SUBTITLE_TRACKS_UPDATED, () => {
    const s = $("subs"), t = hls.subtitleTracks;
    s.hidden = t.length < 1; hls.subtitleDisplay = true;
    fillSelect(s, [["-1", "Legenda: off"]].concat(t.map((x, i) => [String(i), "Legenda: " + (x.name || x.lang || ("#" + (i+1)))])), hls.subtitleTrack);
  });
  hls.on(Hls.Events.ERROR, (_, d) => {
    if (!d.fatal) return;
    if (d.type === Hls.ErrorTypes.NETWORK_ERROR && netRetries < 3) {
      netRetries++; showMsg("Falha de rede, tentando de novo (" + netRetries + "/3)..."); setTimeout(() => hls.startLoad(), 1000 * netRetries);
    } else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) {
      hls.recoverMediaError();
    } else {
      showMsg("Erro no stream: " + (d.details || d.type) + (d.response && d.response.code ? " (HTTP " + d.response.code + ")" : "") +
              ". Token expirado? Gere outra URL e reabra.", true);
      wrap.classList.remove("loading");
    }
  });
  v.addEventListener("playing", () => { netRetries = 0; });
  $("quality").onchange = () => { hls.currentLevel = Number($("quality").value); toast($("quality").selectedOptions[0].textContent); };
  $("audio").onchange = () => { hls.audioTrack = Number($("audio").value); };
  $("subs").onchange = () => { hls.subtitleTrack = Number($("subs").value); };
}

/* ---------- init ---------- */
const savedVol = store.get("vol"), savedSpeed = store.get("speed"), savedMute = store.get("mute");
applyVolume(cfg.volume != null ? cfg.volume : (savedVol != null ? Number(savedVol) : 100));
v.muted = cfg.mute || (cfg.volume == null && savedMute === "1"); syncMute();
setSpeed(cfg.speed || (savedSpeed ? Number(savedSpeed) : 1));
document.addEventListener("fullscreenchange", () => wake());

if (window.Hls && Hls.isSupported()) {
  setupHls();
} else if (v.canPlayType("application/vnd.apple.mpegurl")) {
  v.src = cfg.src;
  v.addEventListener("loadedmetadata", () => { if (cfg.start > 0) v.currentTime = cfg.start; if (cfg.autoplay) v.play().catch(()=>{}); }, { once: true });
} else {
  showMsg("Este navegador não suporta HLS (hls.js não carregou). Verifique a internet ou use --hlsjs.", true);
  wrap.classList.remove("loading");
}
window.__player = { v, get hls() { return hls; } };   // depuração
})();
</script>
</body>
</html>
'''

HLSJS_CDN = "https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"


def rewrite_playlist(text, base_url, proxy_prefix):
    """Reescreve as URLs de uma playlist m3u8 para passarem pelo proxy local."""
    def prox(u):
        return proxy_prefix + quote(urljoin(base_url, u), safe="")

    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append(line)
        elif stripped.startswith("#"):
            out.append(re.sub(
                r'URI="([^"]*)"',
                lambda m: m.group(0) if m.group(1).startswith("data:") else f'URI="{prox(m.group(1))}"',
                line,
            ))
        else:
            out.append(prox(stripped))
    return "\n".join(out) + "\n"


def make_web_handler(token, port, headers, page_html):
    prefix = f"/{token}/p?u="
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "watch_m3u8"

        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            try:
                self._route()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # o navegador cancelou (troca de qualidade, seek...)

        do_HEAD = do_GET

        def _route(self):
            if self.headers.get("Host", "") not in allowed_hosts:  # bloqueia DNS rebinding
                return self._send(403, b"forbidden")
            path, _, query = self.path.partition("?")
            if path == f"/{token}/":
                return self._send(200, page_html.encode("utf-8"), "text/html; charset=utf-8")
            if path == f"/{token}/p":
                target = parse_qs(query).get("u", [""])[0]
                if not target.startswith(("http://", "https://")):
                    return self._send(400, b"url invalida")
                return self._proxy(target)
            self._send(404, b"not found")

        def _proxy(self, target):
            h = dict(headers)
            if self.headers.get("Range"):
                h["Range"] = self.headers["Range"]
            try:
                resp = urllib.request.urlopen(urllib.request.Request(target, headers=h), timeout=30)
            except urllib.error.HTTPError as e:
                return self._send(e.code, e.read() or str(e).encode())
            except (urllib.error.URLError, OSError) as e:
                return self._send(502, f"falha ao buscar: {e}".encode())
            with resp:
                first = resp.read(7)
                if first == b"#EXTM3U":  # playlist: reescreve as URLs
                    text = (first + resp.read()).decode("utf-8", errors="replace")
                    body = rewrite_playlist(text, resp.geturl(), prefix).encode("utf-8")
                    return self._send(200, body, "application/vnd.apple.mpegurl")
                self.send_response(resp.status)
                for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                    if resp.headers.get(k):
                        self.send_header(k, resp.headers[k])
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command == "HEAD":
                    return
                self.wfile.write(first)
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

    return Handler, prefix


def run_web(args, headers):
    """Sobe um proxy local (manda Referer/UA/token) e abre um player hls.js numa aba do navegador."""
    token = secrets.token_urlsafe(9)
    # Porta 0 = o SO escolhe uma livre. O handler precisa saber a porta para validar o Host.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), BaseHTTPRequestHandler)
    port = server.server_address[1]
    title = (urlparse(args.url).path.rsplit("/", 1)[-1] or "stream") + " - m3u8"
    cfg = {
        "src": f"/{token}/p?u=" + quote(args.url, safe=""),
        "quality": args.quality or "auto",
        "volume": args.volume,
        "mute": bool(args.mute),
        "speed": args.speed,
        "start": parse_time(args.start) if args.start else 0,
        "autoplay": True,
    }
    page = (
        PLAYER_HTML.replace("__CONFIG__", json.dumps(cfg))
        .replace("__HLSJS__", args.hlsjs or HLSJS_CDN)
        .replace("__TITLE__", html.escape(title))
    )
    handler, _ = make_web_handler(token, port, headers, page)
    server.RequestHandlerClass = handler
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}/{token}/"
    print(f"[>] Player web em {url}")
    print("    Ctrl+C para encerrar (o proxy local para junto).")
    if args.fullscreen or args.audio_only:
        print("    Nota: --fullscreen/--audio-only não se aplicam ao player web (tecle F na página para tela cheia).")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# Download: escolhe qualidade / áudios / legendas e gera MP4 (ou MKV) com ffmpeg
# ---------------------------------------------------------------------------
LANG3 = {  # MP4 só aceita idioma ISO 639-2 (3 letras)
    "pt": "por", "en": "eng", "es": "spa", "fr": "fra", "de": "deu", "it": "ita", "ja": "jpn", "ko": "kor",
    "zh": "zho", "ru": "rus", "ar": "ara", "hi": "hin", "tr": "tur", "pl": "pol", "nl": "nld", "sv": "swe",
    "da": "dan", "fi": "fin", "no": "nor", "nb": "nor", "cs": "ces", "el": "ell", "he": "heb", "hu": "hun",
    "id": "ind", "th": "tha", "uk": "ukr", "vi": "vie", "ro": "ron",
}


def lang3(code):
    code = (code or "").lower()
    if len(code) == 3 and code.isalpha():
        return code
    return LANG3.get(code.split("-")[0].split("_")[0], "und")


def fmt_dur(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def ffprobe_streams(url, headers):
    """Lista as streams que o ffmpeg enxerga na playlist. Retorna (streams, duração) ou (None, None)."""
    if not shutil.which("ffprobe"):
        return None, None
    ua = headers.get("User-Agent", DEFAULT_UA)
    other = {k: v for k, v in headers.items() if k != "User-Agent"}
    cmd = ["ffprobe", "-v", "error", "-user_agent", ua]
    if other:
        cmd += ["-headers", header_blob(other)]
    cmd += ["-print_format", "json", "-show_streams", "-show_format", url]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=90)
        data = json.loads(out.stdout.decode("utf-8", errors="replace") or "{}")
    except (subprocess.SubprocessError, ValueError, OSError):
        return None, None
    if out.returncode != 0 or not data.get("streams"):
        return None, None
    try:
        dur = float(data.get("format", {}).get("duration") or 0) or None
    except ValueError:
        dur = None
    return data["streams"], dur


def stream_group(streams, bw):
    """Streams de uma variante (o ffmpeg marca cada uma com a tag variant_bitrate)."""
    tags = {s.get("tags", {}).get("variant_bitrate") for s in streams} - {None}
    if not tags:
        return streams
    if bw is not None and str(bw) in tags:
        want = str(bw)
    else:
        want = min(tags, key=lambda t: abs(int(t) - (bw or 0)) if t.isdigit() else 1 << 60)
    return [s for s in streams if s.get("tags", {}).get("variant_bitrate") == want]


def audio_items(streams):
    items = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        t = s.get("tags", {})
        lang = t.get("language") or "und"
        name = t.get("comment") or t.get("title") or t.get("handler_name") or lang
        ch = s.get("channels")
        items.append({
            "index": s["index"], "lang": lang, "name": name, "forced": False,
            "info": f"{s.get('codec_name', '?')}{f' {ch}ch' if ch else ''}",
            "default": bool(s.get("disposition", {}).get("default")),
        })
    return items


def sub_items(variant):
    """Legendas alternativas (EXT-X-MEDIA TYPE=SUBTITLES) do grupo usado pela variante."""
    group = (variant.get("attrs") or {}).get("SUBTITLES")
    items = []
    for m in variant.get("media", []):
        if m.get("TYPE") != "SUBTITLES" or not m.get("URI"):
            continue
        if group and m.get("GROUP-ID") != group:
            continue
        forced = m.get("FORCED", "NO").upper() == "YES"
        items.append({
            "url": m["URI"], "lang": m.get("LANGUAGE") or "und", "name": m.get("NAME") or m.get("LANGUAGE") or "legenda",
            "forced": forced, "info": "forçada" if forced else "", "default": m.get("DEFAULT", "NO").upper() == "YES",
        })
    return items


def item_label(it):
    extra = f" [{it['info']}]" if it.get("info") else ""
    return f"{it['lang']:<7} {it['name']}{extra}"


def select_items(items, spec, title, interactive, default="all"):
    """spec: None | 'all' | 'none' | '1,2' | 'pt-BR,en' (idioma casa com todas as não-forçadas; 'pt-BR:forced' pega a forçada)."""
    if not items:
        return []
    print(f"\n{title}:")
    for i, it in enumerate(items, 1):
        print(f"  {i}) {item_label(it)}")
    while True:
        answer = spec
        if answer is None:
            if not interactive:
                answer = default
            else:
                answer = input(f"  Quais? (ex.: 1,2 | all | none) [{default}]: ").strip() or default
        answer = answer.strip().lower()
        if answer in ("all", "todos", "todas"):
            return list(items)
        if answer in ("none", "nenhum", "nenhuma", "0", ""):
            return []
        chosen, bad = [], []
        for tok in [t.strip() for t in answer.split(",") if t.strip()]:
            found = []
            if tok.isdigit() and 1 <= int(tok) <= len(items):
                found = [items[int(tok) - 1]]
            else:
                lang, _, flag = tok.partition(":")
                found = [
                    it for it in items
                    if (it["lang"].lower() == lang or it["lang"].lower().split("-")[0] == lang)
                    and (it["forced"] == (flag == "forced"))
                ]
            if not found:
                bad.append(tok)
            chosen += [f for f in found if f not in chosen]
        if bad:
            msg = f"  Não entendi: {', '.join(bad)}"
            if not interactive or spec is not None:
                sys.exit(msg.strip() + " (use números da lista, all, none ou um idioma como pt-BR)")
            print(msg)
            continue
        return sorted(chosen, key=items.index)   # mantém a ordem da lista, não a digitada


def fetch_vtt(url, headers):
    """Baixa uma playlist de legenda WebVTT e junta os segmentos num único texto .vtt."""
    _, _, body = fetch(url, headers, timeout=30)
    text = body.decode("utf-8", errors="replace")
    if text.lstrip("﻿").startswith("WEBVTT"):   # já é o próprio .vtt
        return text
    segs = [urljoin(url, l.strip()) for l in text.splitlines() if l.strip() and not l.startswith("#")]
    if not segs:
        raise ValueError("playlist de legenda vazia")
    seen, cues, style = set(), [], []
    for i, seg in enumerate(segs):
        raw = fetch(seg, headers, timeout=30)[2].decode("utf-8", errors="replace").lstrip("﻿")
        for block in re.split(r"\r?\n\r?\n+", raw.replace("\r\n", "\n")):
            block = block.strip("\n")
            if not block or block.startswith("WEBVTT") or block.startswith("NOTE"):
                continue
            if block.startswith("STYLE"):
                if i == 0:
                    style.append(block)
                continue
            if "-->" in block and block not in seen:   # segmentos vizinhos repetem cues na fronteira
                seen.add(block)
                cues.append(block)
    return "WEBVTT\n\n" + "\n\n".join(style + cues) + "\n"


VTT_TIME = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})")


def shift_vtt(text, offset):
    """Soma 'offset' segundos a todos os tempos dos cues (para corrigir dessincronia)."""
    if not offset:
        return text

    def one(m):
        h, mi, se, ms = int(m.group(1) or 0), int(m.group(2)), int(m.group(3)), int(m.group(4))
        t = max(0.0, h * 3600 + mi * 60 + se + ms / 1000 + offset)
        return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}.{int(round((t % 1) * 1000)) % 1000:03d}"

    return "\n".join(VTT_TIME.sub(one, l) if "-->" in l else l for l in text.split("\n"))


ENCODERS = {  # nome -> (codec ffmpeg, opções de qualidade ~ equivalente a crf 20)
    "nvenc": ("h264_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]),
    "qsv": ("h264_qsv", ["-global_quality", "21"]),
    "amf": ("h264_amf", ["-quality", "balanced", "-rc", "cqp", "-qp_i", "21", "-qp_p", "21"]),
    "x264": ("libx264", ["-preset", "veryfast", "-crf", "20"]),
}


def pick_encoder(choice):
    """Devolve (nome, codec, opções). 'auto' testa a placa de vídeo com uma codificação mínima."""
    order = ["nvenc", "qsv", "amf", "x264"] if choice == "auto" else [choice]
    for name in order:
        codec, opts = ENCODERS[name]
        if name == "x264":
            return name, codec, opts
        try:
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2", "-c:v", codec, "-f", "null", "-"],
                capture_output=True, timeout=30)
            if r.returncode == 0:
                return name, codec, opts
        except (subprocess.SubprocessError, OSError):
            pass
        if choice != "auto":
            sys.exit(f"O encoder {name} ({codec}) não está disponível neste ffmpeg/placa. Use --encoder x264 ou auto.")
    return "x264", *ENCODERS["x264"]


def default_out_name(url):
    stem = os.path.splitext(urlparse(url).path.rsplit("/", 1)[-1])[0] or "video"
    return stem + ".mp4"


def build_ffmpeg_cmd(url, headers, out, video, audios, subs, sub_files, args, container, dur_known):
    ua = headers.get("User-Agent", DEFAULT_UA)
    other = {k: v for k, v in headers.items() if k != "User-Agent"}
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y", "-user_agent", ua]
    if other:
        cmd += ["-headers", header_blob(other)]
    cmd += ["-i", url]
    burn = args.sub_mode == "burn"
    if not burn:   # (na queima o arquivo de legenda é lido pelo filtro, não como entrada)
        for f in sub_files:
            cmd += ["-i", f]
    # --- saída principal (mp4/mkv)
    embed = args.sub_mode in ("embed", "both")
    if video is not None:
        cmd += ["-map", f"0:{video['index']}"]
    else:
        cmd += ["-map", "0:v:0"]
    for a in audios:
        cmd += ["-map", f"0:{a['index']}"]
    if not audios and video is None:
        cmd += ["-map", "0:a?"]
    if embed:
        for i in range(len(subs)):
            cmd += ["-map", f"{i + 1}:0"]
    if burn and subs:
        name, codec, qopts = args.encoder_choice
        style = f"FontName=Arial,FontSize={args.sub_size},Outline=2,Shadow=0,MarginV=22"
        # caminho relativo: o ffmpeg roda dentro da pasta temporária (evita o escape de 'C:\\' no filtro)
        cmd += ["-vf", f"subtitles={os.path.basename(sub_files[0])}:force_style='{style}'",
                "-c:v", codec] + qopts + ["-pix_fmt", "yuv420p", "-c:a", "copy"]
    else:
        cmd += ["-c:v", "copy", "-c:a", "copy"]
        if video is not None and video.get("codec_name") == "hevc" and container == "mp4":
            cmd += ["-tag:v", "hvc1"]
    if embed and subs:
        cmd += ["-c:s", "mov_text" if container == "mp4" else "srt"]
    for n, a in enumerate(audios):
        cmd += [f"-metadata:s:a:{n}", f"language={lang3(a['lang'])}", f"-metadata:s:a:{n}", f"title={a['name']}",
                f"-disposition:a:{n}", "default" if n == 0 else "0"]
    if embed and not burn:
        for n, sb in enumerate(subs):
            title = sb["name"] + (" (forced)" if sb["forced"] and "forc" not in sb["name"].lower() else "")
            cmd += [f"-metadata:s:s:{n}", f"language={lang3(sb['lang'])}", f"-metadata:s:s:{n}", f"title={title}",
                    f"-disposition:s:{n}", "forced" if sb["forced"] else "0"]
    cmd += ["-map_metadata", "-1", os.path.abspath(out)]
    # --- legendas externas (.srt ao lado do vídeo)
    if args.sub_mode in ("srt", "both"):
        base = os.path.splitext(out)[0]
        used = set()
        for i, sb in enumerate(subs):
            name = f"{base}.{sb['lang']}{'.forced' if sb['forced'] else ''}"
            k = 1
            while name in used:
                k += 1
                name = f"{base}.{sb['lang']}{'.forced' if sb['forced'] else ''}.{k}"
            used.add(name)
            cmd += ["-map", f"{i + 1}:0", "-c:s", "srt", name + ".srt"]
    return cmd


def run_ffmpeg(cmd, total_dur, cwd=None):
    """Executa o ffmpeg mostrando progresso. Ctrl+C encerra com 'q' e mantém o arquivo parcial."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", cwd=cwd)
    errors, prog = [], {}
    started = time.time()

    def show():
        try:
            t = int(prog.get("out_time_us") or prog.get("out_time_ms") or 0) / 1e6
        except ValueError:
            t = 0
        size = int(prog["total_size"]) if prog.get("total_size", "").isdigit() else 0
        speed = prog.get("speed", "").strip()
        if total_dur:
            pct = min(100.0, t / total_dur * 100)
            try:
                sp = float(speed.rstrip("x"))
                eta = fmt_dur((total_dur - t) / sp) if sp > 0 else "--:--:--"
            except ValueError:
                eta = "--:--:--"
            line = f"  {pct:5.1f}% | {fmt_dur(t)}/{fmt_dur(total_dur)} | {size / 1048576:,.0f} MB | {speed or '?'} | ETA {eta}"
        else:
            line = f"  {fmt_dur(t)} | {size / 1048576:,.0f} MB | {speed or '?'}"
        sys.stdout.write("\r\033[K" + line)
        sys.stdout.flush()

    try:
        for raw in proc.stdout:
            line = raw.strip()
            m = re.match(r"^([a-z_0-9]+)=(.*)$", line)
            if m and m.group(1) in ("frame", "fps", "bitrate", "total_size", "out_time_us", "out_time_ms", "out_time",
                                   "dup_frames", "drop_frames", "speed", "progress", "stream_0_0_q") or (m and m.group(1).startswith("stream_")):
                prog[m.group(1)] = m.group(2)
                if m.group(1) == "progress":
                    show()
            elif line:
                errors.append(line)
        rc = proc.wait()
    except KeyboardInterrupt:
        print("\n[!] Interrompido; finalizando o arquivo com o que já foi baixado...")
        try:
            proc.stdin.write("q\n")
            proc.stdin.flush()
            proc.wait(timeout=15)
        except Exception:
            proc.terminate()
        return 130, errors
    print()
    return rc, errors


def run_download(args, headers, variants, master_ok):
    if not shutil.which("ffmpeg") and not args.print_cmd:
        sys.exit("ffmpeg não encontrado. Instale (Windows: winget install Gyan.FFmpeg | Linux: sudo apt install ffmpeg).")
    interactive = sys.stdin.isatty() and not args.yes and not args.print_cmd
    # 1) qualidade
    variant = None
    if variants:
        spec = args.quality
        if spec in (None, "auto") and len(variants) > 1 and interactive:
            print("\nQualidade do vídeo:")
            for i, v in enumerate(variants, 1):
                print(f"  {i}) {quality_label(v).strip()}")
            ans = input("  Qual? [1 = melhor]: ").strip() or "1"
            variant = variants[int(ans) - 1] if ans.isdigit() and 1 <= int(ans) <= len(variants) else variants[0]
        else:
            variant = pick_variant(variants, spec if spec not in (None, "auto") else "best")
        print(f"[>] Vídeo: {quality_label(variant).strip()}")
    # 2) faixas de áudio (via ffprobe) e legendas (via playlist master)
    streams, total_dur = ffprobe_streams(args.url, headers)
    video, audios_all = None, []
    if streams:
        group = stream_group(streams, variant["bw"] if variant else None)
        vids = [s for s in group if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
        video = vids[0] if vids else None
        audios_all = audio_items(group)
    else:
        print("[!] ffprobe indisponível ou não conseguiu listar as faixas: baixando vídeo + todos os áudios, sem escolha de faixa.")
    subs_all = sub_items(variant) if variant else []
    if args.list:
        for title, items in (("Áudios", audios_all), ("Legendas", subs_all)):
            print(f"\n{title}:")
            for i, it in enumerate(items, 1):
                print(f"  {i}) {item_label(it)}")
        return 0
    audios = select_items(audios_all, args.audio, "Faixas de áudio", interactive, "all")
    subs = select_items(subs_all, args.subs, "Legendas", interactive, "all")
    if not subs_all and args.subs not in (None, "none"):
        print("[!] Este stream não tem legendas separadas para baixar.")

    # 2b) como aplicar a legenda
    mode = args.sub_mode
    if subs and mode is None:
        if interactive:
            print("\nComo aplicar a legenda?")
            print("  1) Embutida no MP4 (faixa que você liga/desliga no player) - rápido, sem recodificar")
            print("  2) Queimada na imagem (fica sempre visível, em qualquer player) - recodifica o vídeo, demora")
            print("  3) Arquivo .srt separado ao lado do vídeo")
            ans = input("  Qual? [1]: ").strip() or "1"
            mode = {"1": "embed", "2": "burn", "3": "srt"}.get(ans, "embed")
        else:
            mode = "embed"
    mode = mode or "embed"
    if mode == "burn" and subs:
        if len(subs) > 1:   # só dá para queimar uma legenda
            if not interactive:
                sys.exit("--sub-mode burn queima apenas UMA legenda: escolha uma em --subs (ex.: --subs 2 ou --subs pt-BR).")
            print("\nSó uma legenda pode ser queimada na imagem. Qual?")
            for i, it in enumerate(subs, 1):
                print(f"  {i}) {item_label(it)}")
            ans = input("  Número [1]: ").strip() or "1"
            subs = [subs[int(ans) - 1] if ans.isdigit() and 1 <= int(ans) <= len(subs) else subs[0]]
        out_enc = pick_encoder(args.encoder)
        args.encoder_choice = out_enc[0], out_enc[1], out_enc[2]
        print(f"[>] Encoder: {out_enc[0]} ({out_enc[1]})"
              + ("  -> a queima recodifica o vídeo; com a GPU é bem mais rápido." if out_enc[0] == "x264" else ""))
    args.sub_mode = mode

    # 3) arquivo de saída
    out = args.download if args.download else ""
    if not out:
        default = default_out_name(args.url)
        out = input(f"\nNome do arquivo [{default}]: ").strip() if interactive else ""
        out = out or default
    if not os.path.splitext(out)[1]:
        out += ".mp4"
    ext = os.path.splitext(out)[1].lower()
    if ext not in (".mp4", ".m4v", ".mov", ".mkv"):
        sys.exit(f"Extensão {ext} não suportada para o download (use .mp4 ou .mkv).")
    container = "mkv" if ext == ".mkv" else "mp4"
    if os.path.exists(out) and interactive and input(f"'{out}' já existe. Sobrescrever? [s/N]: ").strip().lower() not in ("s", "sim", "y", "yes"):
        print("Cancelado.")
        return 1

    # 4) baixa as legendas (WebVTT) para arquivos temporários
    out = os.path.abspath(out)   # o ffmpeg roda dentro da pasta temporária; o destino tem de ser absoluto
    tmpdir = tempfile.mkdtemp(prefix="watch_m3u8_")
    sub_files, ok_subs = [], []
    try:
        for sb in subs:
            path = os.path.join(tmpdir, f"sub{len(sub_files)}.vtt")
            if args.print_cmd:
                sub_files.append(path)
                ok_subs.append(sb)
                continue
            try:
                print(f"[>] Baixando legenda {sb['lang']} ({sb['name']})...")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(shift_vtt(fetch_vtt(sb["url"], headers), args.sub_offset))
                sub_files.append(path)
                ok_subs.append(sb)
            except Exception as e:
                print(f"[!] Falha na legenda {sb['lang']} ({sb['name']}): {e}. Continuando sem ela.")
        cmd = build_ffmpeg_cmd(args.url, headers, out, video, audios, ok_subs, sub_files, args, container, bool(total_dur))
        if args.print_cmd:
            print(" ".join(f'"{c}"' if (" " in c or "\r" in c) else c for c in cmd))
            return 0
        how = {"srt": ", .srt separadas", "burn": ", queimada na imagem", "both": ", embutidas + .srt"}.get(args.sub_mode, "")
        print(f"\n[>] Gerando {out} ({len(audios)} áudio(s), {len(ok_subs)} legenda(s){how})...")
        rc, errors = run_ffmpeg(cmd, total_dur, cwd=tmpdir)
        if rc == 130:
            size = os.path.getsize(out) / 1048576 if os.path.exists(out) else 0
            print(f"[!] Download interrompido. Arquivo parcial (só o trecho baixado): {os.path.abspath(out)} ({size:,.1f} MB)")
            return 130
        if rc != 0:
            print(f"[!] ffmpeg terminou com erro ({rc}).")
            for l in errors[-8:]:
                print("    " + l)
            if rc != 130 and container == "mp4":
                print("    Dica: se o erro for de codec/legenda incompatível com MP4, tente salvar como .mkv.")
            return rc
        size = os.path.getsize(out) / 1048576 if os.path.exists(out) else 0
        print(f"[ok] Pronto: {os.path.abspath(out)} ({size:,.0f} MB)")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Assiste ou baixa um stream HLS (.m3u8).")
    ap.add_argument("url", help="URL do .m3u8 (entre aspas por causa do ?token=)")
    ap.add_argument("--referer", help="Referer/Origin exigido pelo servidor, ex.: https://example.com/")
    ap.add_argument("--user-agent", default=DEFAULT_UA)
    ap.add_argument("-H", "--header", action="append", help="Header extra 'Nome: valor' (repetível)")
    ap.add_argument("--player", choices=PLAYERS + ("web",), help="Força um player (padrão: primeiro instalado)")
    ap.add_argument("--download", metavar="ARQUIVO", nargs="?", const="",
                    help="Baixa e gera um .mp4 (ou .mkv) com ffmpeg em vez de abrir o player. Sem nome, pergunta")
    ap.add_argument("--probe", action="store_true", help="Só verifica a playlist")
    ap.add_argument("--print-cmd", action="store_true", help="Mostra o comando sem executar")
    d = ap.add_argument_group("download (--download): escolha de faixas")
    d.add_argument("--audio", metavar="SEL", help="Áudios: all, none, números da lista (1,2) ou idiomas (pt-BR,en). Padrão: pergunta / all")
    d.add_argument("--subs", metavar="SEL", help="Legendas: all, none, números (1,3) ou idiomas (pt-BR, en, pt-BR:forced)")
    d.add_argument("--sub-mode", choices=("embed", "burn", "srt", "both"),
                   help="embed = faixa dentro do MP4 (liga/desliga no player); burn = queimada na imagem, permanente "
                        "(recodifica o vídeo, demora); srt = .srt ao lado; both = embed + .srt. Padrão: pergunta / embed")
    d.add_argument("--sub-offset", type=float, metavar="SEG", help="Atrasa (+) ou adianta (-) as legendas, em segundos")
    d.add_argument("--sub-size", type=int, default=18, metavar="N", help="Tamanho da legenda queimada (padrão 18; maior = maior)")
    d.add_argument("--encoder", choices=("auto", "x264", "nvenc", "qsv", "amf"), default="auto",
                   help="Encoder do vídeo ao queimar a legenda (auto = placa de vídeo se der, senão x264)")
    d.add_argument("--list", action="store_true", help="Só lista as qualidades, áudios e legendas disponíveis")
    d.add_argument("-y", "--yes", action="store_true", help="Não pergunta nada: usa as opções dadas e o padrão (tudo) no resto")
    w = ap.add_argument_group("player web (aba do navegador)")
    w.add_argument("--web", action="store_true", help="Abre um player com controles numa aba do navegador (sem mpv/vlc)")
    w.add_argument("--port", type=int, default=0, help="Porta do servidor local (padrão: livre aleatória)")
    w.add_argument("--no-open", action="store_true", help="Não abre o navegador; só mostra a URL")
    w.add_argument("--hlsjs", metavar="URL", help="URL alternativa do hls.js (padrão: jsDelivr)")
    g = ap.add_argument_group("controles de reprodução")
    g.add_argument("--quality", metavar="Q", help="best, worst, auto ou altura (ex.: 720). Só em playlists master")
    g.add_argument("--volume", type=float, metavar="0-150", help="Volume inicial em %%")
    g.add_argument("--mute", action="store_true", help="Começa sem som")
    g.add_argument("--speed", type=float, metavar="X", help="Velocidade inicial (ex.: 1.25)")
    g.add_argument("--start", metavar="TEMPO", help="Começa em SEGUNDOS, MM:SS ou HH:MM:SS")
    g.add_argument("--fullscreen", action="store_true", help="Abre em tela cheia")
    g.add_argument("--audio-only", action="store_true", help="Só áudio (sem vídeo)")
    g.add_argument("--no-controls", action="store_true", help="Não abre o controlador interativo do terminal (mpv)")
    args = ap.parse_args()

    if args.volume is not None and not 0 <= args.volume <= 150:
        ap.error("--volume deve estar entre 0 e 150")
    if args.speed is not None and not 0.1 <= args.speed <= 10:
        ap.error("--speed deve estar entre 0.1 e 10")
    if args.start:
        parse_time(args.start)  # valida cedo

    headers = build_headers(args.referer, args.user_agent, args.header)

    ok, variants = probe(args.url, headers)
    if args.probe:
        return 0 if ok else 1
    if not ok:
        print("    Seguindo mesmo assim (o player pode ter mais sorte)...")

    if args.download is not None or args.list:
        return run_download(args, headers, variants, ok)

    if args.web or args.player == "web":
        if args.print_cmd:
            print("(player web: sobe um servidor local e abre o navegador)")
            return 0
        return run_web(args, headers)

    play_url = args.url
    chosen = pick_variant(variants, args.quality)
    if chosen:
        play_url = chosen["url"]
        print(f"[>] Qualidade escolhida: {quality_label(chosen).strip()}")
    elif args.quality and not variants and args.quality != "auto":
        print("[!] --quality ignorado: a playlist não é master (só há uma qualidade).")

    player = args.player or next((p for p in PLAYERS if shutil.which(p)), None)
    if not player:
        if not args.print_cmd:
            print("[!] Nenhum player instalado (mpv/vlc/ffplay); abrindo o player web no navegador.")
            return run_web(args, headers)
        player = "mpv"
    opts = {
        "volume": args.volume, "mute": args.mute, "speed": args.speed,
        "start": args.start, "fullscreen": args.fullscreen, "audio_only": args.audio_only,
    }
    interactive = player == "mpv" and not args.no_controls and not args.print_cmd and sys.stdin.isatty()
    ipc_path = None
    if interactive:
        ipc_path = (
            rf"\\.\pipe\watch_m3u8_{os.getpid()}" if IS_WINDOWS
            else os.path.join(tempfile.gettempdir(), f"watch_m3u8_{os.getpid()}.sock")
        )
    cmd = player_cmd(player, play_url, headers, opts, ipc_path)

    if args.print_cmd:
        print(" ".join(f'"{c}"' if " " in c or "\r" in c else c for c in cmd))
        return 0
    print(f"[>] Executando {cmd[0]}...")
    if interactive:
        # Se o usuário escolheu uma qualidade fixa, o '0' (auto) do controlador volta à master.
        try:
            return run_with_controls(cmd, ipc_path, args.url, variants)
        finally:
            if not IS_WINDOWS and ipc_path and os.path.exists(ipc_path):
                os.remove(ipc_path)
    if not interactive and player != "mpv" and not args.no_controls:
        print("    (controlador interativo só está disponível com mpv; use os atalhos da janela do player)")
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
