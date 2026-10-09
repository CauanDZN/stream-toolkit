# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [1.2.0] - 2026-10-09

### Adicionado
- `boost_audio.py --web`: MP4 compatível com players de navegador (Google Drive, Rave): uma faixa de áudio em estéreo (5.1 mixado com as falas em destaque), sem legendas embutidas (exportadas como `.srt`) e com `faststart`.

## [1.1.0] - 2026-10-09

### Adicionado
- `boost_audio.py --tv`: preset de "som de TV" (compressão de dinâmica + `loudnorm` com LRA baixo) para deixar falas altas e cenas de ação controladas.
- `boost_audio.py --mp4`: gera MP4 na mesma passada; legendas de texto viram `mov_text`.

## [1.0.2] - 2026-10-09

### Corrigido
- `boost_audio.py`: em MKVs com legendas (ex.: SRT), o ffmpeg cortava em silêncio o áudio recodificado após ~3 s (a faixa ficava muda). A geração agora é em duas etapas (áudio recodificado à parte, depois remux copiando o resto).
- `boost_audio.py`: passou a verificar se o áudio recodificado chega até o fim e avisa com erro caso contrário, em vez de reportar sucesso.

## [1.0.1] - 2026-10-09

### Corrigido
- `boost_audio.py --normalize`: o `loudnorm` deixava o áudio em 96/192 kHz; agora volta para 48 kHz.
- `boost_audio.py`: faixas com mais de 2 canais (5.1) usam AAC 384 kbps por padrão (antes 192 kbps).
- `boost_audio.py --help` não quebra mais em consoles cp1252 do Windows.

## [1.0.0] - 2026-10-09

Primeira versão pública.

### Adicionado
- `watch_m3u8.py`: verificação de playlists HLS, reprodução em mpv/VLC/ffplay com controlador interativo no terminal, player web com proxy local (hls.js) e download para MP4/MKV com escolha de qualidade, áudios e legendas (embutidas, queimadas ou `.srt`).
- `boost_audio.py`: aumento de volume, normalização (`loudnorm`) e realce de falas sem recodificar a imagem, com análise prévia (`--analyze`).
- `m3u8-controls.user.js`: userscript com barra de controles ao passar o mouse sobre vídeos (volume até 200 %, velocidade, qualidade/áudio/legenda via hls.js, screenshot, PiP, tela cheia e atalhos).
- Testes unitários (`tests/`) e CI no GitHub Actions (Linux e Windows).
