# stream-toolkit

[![CI](https://github.com/CauanDZN/stream-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/CauanDZN/stream-toolkit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![Dependências: nenhuma](https://img.shields.io/badge/depend%C3%AAncias-nenhuma-brightgreen.svg)

Pequena caixa de ferramentas para quem lida com vídeo e streaming HLS (`.m3u8`) pela linha de comando e pelo navegador.
Tudo em Python puro (só biblioteca padrão) mais um userscript, sem `pip install`, sem `node_modules`.

| Ferramenta | O que faz |
|---|---|
| [`watch_m3u8.py`](watch_m3u8.py) | Abre um stream HLS no **mpv / VLC / ffplay** ou num **player web** local, e **baixa** para MP4/MKV escolhendo qualidade, áudios e legendas. |
| [`boost_audio.py`](boost_audio.py) | **Aumenta o volume** de um vídeo (ou normaliza / realça falas) sem recodificar a imagem. |
| [`m3u8-controls.user.js`](m3u8-controls.user.js) | **Userscript** que injeta uma barra de controles (volume até 200 %, velocidade, qualidade, screenshot, PiP…) em qualquer player de vídeo de um site. |

## Sumário

- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [watch_m3u8.py](#watch_m3u8py)
- [boost_audio.py](#boost_audiopy)
- [m3u8-controls.user.js](#m3u8-controlsuserjs)
- [Como funciona](#como-funciona)
- [Desenvolvimento e testes](#desenvolvimento-e-testes)
- [Aviso legal](#aviso-legal)
- [Licença](#licença)

## Requisitos

| Item | Para quê | Obrigatório? |
|---|---|---|
| Python 3.9+ | rodar os scripts | sim |
| [ffmpeg](https://ffmpeg.org/) (+ `ffprobe`) | baixar streams e ajustar áudio | só para `--download` / `boost_audio.py` |
| [mpv](https://mpv.io/), VLC ou `ffplay` | assistir no desktop | não: sem eles o player web abre no navegador |
| Tampermonkey / Violentmonkey | rodar o userscript | só para o userscript |

No Windows, o jeito mais simples de instalar as dependências opcionais:

```powershell
winget install Gyan.FFmpeg
winget install mpv
```

> Para **queimar legendas** na imagem (`--sub-mode burn`) é preciso um ffmpeg "full" com `libass` (o build do Gyan.FFmpeg tem).

## Instalação

```bash
git clone https://github.com/CauanDZN/stream-toolkit.git
cd stream-toolkit
python watch_m3u8.py --help
```

Não há nada para instalar: são scripts independentes. Se quiser, copie os `.py` para uma pasta que esteja no seu `PATH`.

---

## watch_m3u8.py

Recebe a URL de uma playlist `.m3u8` (**sempre entre aspas**, por causa do `?token=...`) e faz uma de quatro coisas: verificar, assistir, assistir no navegador ou baixar.

### Verificar a playlist

```bash
python watch_m3u8.py "https://cdn.example.com/path/master.m3u8?token=XXXX" --probe
```

Mostra se a playlist abre, se é *master* (várias qualidades) ou *media* (VOD/ao vivo) e se usa criptografia AES-128.

### Assistir no desktop (mpv, VLC ou ffplay)

```bash
python watch_m3u8.py URL --referer "https://example.com/"
python watch_m3u8.py URL --quality 720 --volume 60 --speed 1.25 --start 10:30 --fullscreen
python watch_m3u8.py URL --player vlc
python watch_m3u8.py URL --audio-only
```

Usa o primeiro player instalado (mpv → VLC → ffplay). Com **mpv**, um controlador interativo roda no próprio terminal (tecle `h` para a ajuda; desligue com `--no-controls`):

| Tecla | Ação | Tecla | Ação |
|---|---|---|---|
| `espaço` | pausa | `a` | troca faixa de áudio |
| `m` | mudo | `s` | troca legenda |
| `↑` / `↓` | volume | `0`–`9` | qualidade (`0` = auto) |
| `←` / `→` | seek de 10 s | `f` | tela cheia |
| `[` / `]` | velocidade | `t` | screenshot |
| `r` | reseta velocidade | `i` | informações |
| `q` | sair | | |

### Assistir no navegador (player web)

```bash
python watch_m3u8.py URL --referer "https://example.com/" --web
```

Sobe um proxy HTTP **local** que envia o `Referer`, o `User-Agent` e o token que o servidor exige, e abre uma aba com um player [hls.js](https://github.com/video-dev/hls.js): play/pausa, seek, volume até 200 %, velocidade, qualidade, faixa de áudio, legenda, PiP, tela cheia, screenshot e atalhos de teclado (tecle `?` na página). É o padrão quando nenhum player de desktop está instalado. Opções úteis: `--port`, `--no-open`, `--hlsjs URL` (hls.js alternativo/offline).

### Baixar para MP4 / MKV

```bash
python watch_m3u8.py URL --list                                    # só mostra o que existe no stream
python watch_m3u8.py URL --download filme.mp4                      # menu interativo: qualidade, áudios e legendas
python watch_m3u8.py URL --download filme.mp4 --audio pt-BR --subs pt-BR,en -y
python watch_m3u8.py URL --download filme.mkv --audio all --subs none --quality 720
```

- `--audio` / `--subs` aceitam `all`, `none`, números da lista (`1,3`) ou idiomas (`pt-BR,en`; `pt-BR:forced` seleciona a legenda forçada).
- `--sub-mode` define o que fazer com as legendas:

  | Modo | Resultado |
  |---|---|
  | `embed` | faixa dentro do arquivo (ligue no player) |
  | `burn` | queimada na imagem (sempre visível; recodifica o vídeo) |
  | `srt` | arquivo `.srt` ao lado do vídeo |
  | `both` | `embed` + `srt` |

- `--sub-offset SEG` atrasa/adianta legendas; `--sub-size N` muda o tamanho da legenda queimada.
- `--encoder auto|nvenc|qsv|amf|x264` escolhe o codificador da queima (`auto` usa a GPU se houver).
- `-y` não pergunta nada: usa o que você passou e o padrão (tudo) no resto.

### Opções gerais

| Opção | Descrição |
|---|---|
| `--referer URL` | `Referer` (e `Origin`) exigido pelo servidor |
| `--user-agent UA` | User-Agent (padrão: Chrome mobile) |
| `-H "Nome: valor"` | header extra, repetível (ex.: `-H "Cookie: a=b"`) |
| `--quality Q` | `best`, `worst`, `auto` ou altura (`720`) |
| `--volume 0-150`, `--mute`, `--speed X`, `--start TEMPO` | estado inicial do player |
| `--print-cmd` | mostra o comando do player sem executar |

> **Erro 401/403?** O token expirou ou o servidor confere o `Referer`. Abra o site de origem, pegue uma URL nova e passe `--referer` com o endereço do site.

---

## boost_audio.py

Sobe o volume de um vídeo copiando imagem e legendas (rápido), em duas etapas (áudio recodificado à parte e remux final, com verificação de que o áudio chegou até o fim) e recodificando **só o áudio** (AAC: 192 kbps em estéreo, 384 kbps em 5.1). Um limitador evita distorção nos picos.

```bash
python boost_audio.py filme.mp4 --analyze            # mede o volume atual e sugere quantos dB subir
python boost_audio.py filme.mp4                      # +6 dB (≈ 2x a amplitude)
python boost_audio.py filme.mp4 --db 9               # mais alto
python boost_audio.py filme.mp4 --normalize          # nivela para volume de streaming (-16 LUFS)
python boost_audio.py filme.mp4 --dialogue --db 4    # comprime explosões e realça as falas
python boost_audio.py filme.mp4 --track 1            # só a 1ª faixa de áudio (as outras passam sem mudar)
python boost_audio.py filme.mp4 -o saida.mp4
```

A saída vai ao lado do original (`<nome>_vol+6dB.mp4`, ou `<nome>_norm.mp4` com `--normalize`); o arquivo original nunca é alterado. Outras opções: `--bitrate 256k` (força o bitrate), `-y` (sobrescrever sem perguntar).

**Qual modo usar?** Rode `--analyze` primeiro. Se o pico já está em ~0 dB, subir o volume "puro" só achata as cenas altas: prefira `--normalize` ou `--dialogue`.

---

## m3u8-controls.user.js

Ao passar o mouse sobre um `<video>`, mostra uma barra com play/pausa, seek com buffer, ±10 s, volume **até 200 %**, velocidade, qualidade/áudio/legenda (quando o site usa hls.js), screenshot, Picture-in-Picture e tela cheia. Roda em todos os frames, então funciona com players dentro de `<iframe>`.

### Instalar

1. Instale o [Tampermonkey](https://www.tampermonkey.net/) ou o [Violentmonkey](https://violentmonkey.github.io/).
2. Abra o [arquivo bruto](https://raw.githubusercontent.com/CauanDZN/stream-toolkit/main/m3u8-controls.user.js) e confirme a instalação.
3. **Edite os `@match`** no cabeçalho para os sites onde você quer os controles. Por padrão eles apontam para `example.com`, ou seja, o script não faz nada até você configurar:

   ```js
   // @match        *://meusite.com/*
   // @match        *://*.meusite.com/*
   ```

### Atalhos (valem com o mouse sobre o vídeo)

| Tecla | Ação | Tecla | Ação |
|---|---|---|---|
| `espaço` / `K` | play/pausa | `M` | mudo |
| `←` / `→` | ∓5 s | `J` / `L` | ∓10 s |
| `↑` / `↓` | volume ±5 % | `F` | tela cheia |
| `<` / `>` | velocidade | `P` | Picture-in-Picture |
| `0`–`9` | pula para 0–90 % | `S` | screenshot (PNG) |
| `Home` / `End` | início / ao vivo | | |

Volume acima de 100 % usa a Web Audio API; ele fica indisponível quando o vídeo é *cross-origin* ou o site já controla o áudio. A última configuração de volume é lembrada via `localStorage`.

---

## Como funciona

```
 URL .m3u8 ──► watch_m3u8.py ──┬─ --probe/--list ─► lê e interpreta a playlist (master/media, áudios, legendas)
                               ├─ mpv/vlc/ffplay ─► passa Referer/UA/Origin como headers do player
                               ├─ --web ──────────► proxy local 127.0.0.1 ─► reescreve a playlist ─► hls.js no navegador
                               └─ --download ─────► ffprobe/ffmpeg ─► MP4/MKV (+ legendas WebVTT convertidas)
```

- **Proxy web:** escuta só em `127.0.0.1`, numa porta livre e sob um caminho com token aleatório; rejeita requisições cujo `Host` não seja o do próprio servidor (proteção contra *DNS rebinding*). Todas as URLs da playlist (segmentos, chaves, áudios, legendas) são reescritas para passarem por ele, que adiciona os headers exigidos.
- **hls.js** é carregado do jsDelivr por padrão; use `--hlsjs` para apontar para uma cópia local.
- Nenhuma credencial é salva em disco; tokens existem apenas na linha de comando e na memória do processo.

## Desenvolvimento e testes

Os testes cobrem as funções puras (parser de playlist, escolha de qualidade, reescrita para o proxy, seleção de faixas, deslocamento de legendas, montagem de filtros) e **não precisam de rede nem de ffmpeg**:

```bash
python -m unittest discover -s tests -v
```

O CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) roda os testes em Linux e Windows (Python 3.9 e 3.13) e checa a sintaxe do userscript. Veja [CONTRIBUTING.md](CONTRIBUTING.md) para contribuir e o [CHANGELOG.md](CHANGELOG.md) para o histórico.

## Aviso legal

Estas ferramentas são de uso pessoal e **não** quebram DRM nem contornam proteções: só funcionam com conteúdo que o seu navegador/player já consegue acessar. Use apenas com conteúdo que você tem o direito de assistir, baixar ou modificar, e respeite os termos de uso dos serviços e a legislação de direitos autorais do seu país. O software é fornecido "como está", sem garantias (veja a licença).

## Licença

[MIT](LICENSE) © 2026 Cauan Victor
