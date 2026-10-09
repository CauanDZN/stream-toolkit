# Contribuindo

Obrigado pelo interesse! Sugestões, relatos de bugs e PRs são bem-vindos.

## Bugs e ideias

Abra uma *issue* informando: sistema operacional, versão do Python (`python --version`), versão do ffmpeg/mpv (se relevante), o comando executado e a saída completa. **Remova tokens e URLs privadas** antes de colar.

## Ambiente

Não há dependências externas: basta ter Python 3.9+.

```bash
git clone https://github.com/CauanDZN/stream-toolkit.git
cd stream-toolkit
python -m unittest discover -s tests -v
```

## Diretrizes

- **Só biblioteca padrão** nos scripts Python: a ideia é rodar copiando um arquivo.
- Mantenha compatibilidade com Python 3.9+ e com Windows, Linux e macOS.
- Lógica nova que não depende de rede/ffmpeg deve vir com teste em `tests/`.
- Mensagens e comentários em português, como no restante do projeto.
- Para o userscript, rode `node --check m3u8-controls.user.js` antes de enviar.
- Não inclua URLs reais de streams, tokens ou sites específicos em exemplos: use `example.com`.

## Pull requests

1. Faça um fork e crie uma branch (`git checkout -b minha-melhoria`).
2. Garanta que os testes passam.
3. Atualize o `README.md` e o `CHANGELOG.md` se mudar o comportamento.
4. Abra o PR descrevendo o problema e a solução.
