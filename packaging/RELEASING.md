# Publicar una versión

Cada paso público lo hace el dueño del proyecto; aquí queda el orden y los comandos.

## 0. Una sola vez

- Renombrar el repo de GitHub a `transcodex`.
- Crear el repo vacío `mgoreiro/homebrew-transcodex` (el tap de Homebrew).
- Cuenta en [PyPI](https://pypi.org) con token de API.

## 1. Subir la versión

1. Cambia `__version__` en `transcodex.py` y la URL en `packaging/homebrew/transcodex.rb`.
2. Merge a `main`, y después:

```bash
git tag v1.3.1 && git push origin v1.3.1
```

3. En GitHub, crea la release de ese tag y adjunta el `.deb`:

```bash
python3 packaging/build_deb.py --version 1.3.1
gh release create v1.3.1 dist/transcodex_1.3.1_all.deb --title "transcodex 1.3.1" --generate-notes
```

## 2. PyPI

```bash
uv build
uv publish          # pide el token de PyPI
```

Prueba antes en TestPyPI con `uv publish --index testpypi` si quieres.

## 3. Homebrew

```bash
curl -sL https://github.com/mgoreiro/transcodex/archive/refs/tags/v1.3.1.tar.gz | shasum -a 256
```

Pon el hash en `packaging/homebrew/transcodex.rb`, cópialo a `Formula/transcodex.rb` del repo `homebrew-transcodex` y súbelo. Comprueba con:

```bash
brew install --build-from-source mgoreiro/transcodex/transcodex && brew test transcodex && brew audit --strict --online transcodex
```
