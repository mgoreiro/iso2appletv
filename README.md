# iso2appletv

TUI para convertir ISOs de DVD/Blu-ray y ficheros de vídeo a `.m4v` compatible con Apple TV usando HandBrakeCLI.

- Orígenes: imágenes ISO/IMG y ficheros de vídeo (mkv, mp4, avi, mov, ts, m2ts, wmv, mpg, vob…), sueltos o por carpeta; mismas opciones para todos.
- Vídeo H.264 High@4.1 hasta 720p, audio AC3 (copia si ya es AC3), capítulos, faststart.
- Lista de ficheros con estado y hora de finalización, log de HandBrakeCLI o resumen de cada conversión, y panel de sistema (CPU, RAM, disco).
- HandBrakeCLI se ejecuta con `nice -n 19` (y `ionice -c3` si existe).
- Salida a `.m4v.part` y renombrado solo al terminar bien.
- Carpeta de salida configurable desde la interfaz (tecla `o`).
- Explorador de ISOs integrado (tecla `i`): el fondo se congela mientras está abierto, sin parpadeo del monitor.

## Instalación (Debian/Ubuntu)

Descarga el `.deb` de la sección *Releases* e instálalo:

```bash
sudo apt install ./iso2appletv_1.1.1_all.deb
```

## Uso

```bash
iso2appletv                          # sin argumentos: abre el explorador para elegir ISOs
iso2appletv peli.iso                 # abre la lista y espera a pulsar s
iso2appletv ~/isos/ -r               # todas las ISOs y vídeos de una carpeta (y subcarpetas)
iso2appletv pelicula.mkv serie/      # ficheros de vídeo sueltos o carpetas con vídeos
iso2appletv -y -c a.iso b.iso        # un fichero por capítulo, empieza solo
```

Teclas: `Tab` cambia de panel, flechas/RePág/AvPág desplazan, `espacio` incluye/omite, `s` inicia,
`p` pausa, `x` cancela el actual, `r` reintenta, `l` log/resumen, `i` añadir ISOs, `o` carpeta de salida, `?` ayuda, `q` salir.

`iso2appletv.sh` es el script original (sin interfaz).

## Construir el paquete

```bash
python3 packaging/build_deb.py --version 1.0.0   # genera dist/iso2appletv_1.1.1_all.deb
```

## Licencia

GPL-3.0 o posterior. Ver [LICENSE](LICENSE).
