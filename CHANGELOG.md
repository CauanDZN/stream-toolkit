# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [1.0.0] - 2026-10-09

Primeira versão pública.

### Adicionado
- `watch_m3u8.py`: verificação de playlists HLS, reprodução em mpv/VLC/ffplay com controlador interativo no terminal, player web com proxy local (hls.js) e download para MP4/MKV com escolha de qualidade, áudios e legendas (embutidas, queimadas ou `.srt`).
- `boost_audio.py`: aumento de volume, normalização (`loudnorm`) e realce de falas sem recodificar a imagem, com análise prévia (`--analyze`).
- `m3u8-controls.user.js`: userscript com barra de controles ao passar o mouse sobre vídeos (volume até 200 %, velocidade, qualidade/áudio/legenda via hls.js, screenshot, PiP, tela cheia e atalhos).
- Testes unitários (`tests/`) e CI no GitHub Actions (Linux e Windows).
