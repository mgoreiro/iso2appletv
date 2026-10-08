#!/usr/bin/env python3
"""
Construye el paquete Debian de iso2appletv sin necesitar dpkg-deb
(sirve desde macOS o cualquier Linux).

    python3 packaging/build_deb.py [--version 1.0.0] [--out dist]

Genera dist/iso2appletv_<version>_all.deb
"""

import argparse
import gzip
import hashlib
import io
import re
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "iso2appletv"
MAINTAINER = "Miguel <mgoreiro@gmail.com>"
DESCRIPTION_SHORT = "TUI para convertir ISOs de DVD/Blu-ray a .m4v para Apple TV"
DESCRIPTION_LONG = """\
 Interfaz de terminal que extrae los títulos de video de imágenes ISO de
 DVD/Blu-ray y los convierte con HandBrakeCLI a .m4v compatible con Apple TV
 (H.264 High@4.1 hasta 720p, audio AC3, marcadores de capítulo).
 .
 Muestra la lista de ficheros con su estado y hora de finalización, el log de
 HandBrakeCLI o el resumen de cada conversión, y el uso de CPU, RAM y disco.
 Se ejecuta con prioridad mínima (nice/ionice) para no saturar la máquina."""

CONTROL = f"""\
Package: {PKG}
Version: {{version}}
Section: video
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), handbrake-cli
Recommends: libdvd-pkg, libbluray2 | libbluray2t64 | libbluray3 | libbluray3t64, libaacs0 | libaacs0t64
Installed-Size: {{size}}
Maintainer: {MAINTAINER}
Description: {DESCRIPTION_SHORT}
{DESCRIPTION_LONG}
"""

POSTINST = """\
#!/bin/sh
set -e
if [ "$1" = "configure" ] && command -v dpkg-reconfigure >/dev/null 2>&1; then
    if dpkg -s libdvd-pkg >/dev/null 2>&1 && ! ldconfig -p 2>/dev/null | grep -q libdvdcss; then
        echo "iso2appletv: libdvd-pkg instalado pero sin compilar libdvdcss2."
        echo "             Ejecuta: sudo dpkg-reconfigure libdvd-pkg"
    fi
fi
exit 0
"""

MANPAGE = r""".TH ISO2APPLETV 1 "" "iso2appletv" "Comandos de usuario"
.SH NOMBRE
iso2appletv \- convierte ISOs de DVD/Blu-ray a .m4v para Apple TV
.SH SINOPSIS
.B iso2appletv
[\fIopciones\fR] \fIimagen.iso\fR...
.SH DESCRIPCIÓN
Interfaz de terminal sobre HandBrakeCLI. Escanea las imágenes, lista los
títulos (o capítulos) y los convierte uno a uno a H.264 High@4.1 (máx. 720p)
con audio AC3. HandBrakeCLI se ejecuta con \fBnice -n 19\fR (y \fBionice -c3\fR
si está disponible).
.PP
Los ficheros se escriben primero como \fI.m4v.part\fR y solo se renombran a
\fI.m4v\fR si la conversión termina bien.
.SH OPCIONES
.TP
.BR \-o ", " \-\-output " " \fIDIR\fR
Directorio de salida solo para esta ejecución. La carpeta habitual se cambia
desde la interfaz con la tecla \fBo\fR y se guarda en
\fI~/.config/iso2appletv/config.json\fR (por defecto \fB~/HandBrake\fR).
.TP
.BR \-d ", " \-\-min\-duration " " \fISEG\fR
Duración mínima de un título para procesarlo (def. 300).
.TP
.BR \-q ", " \-\-quality " " \fIRF\fR
Calidad x264, menor = mejor (def. 20).
.TP
.BR \-t ", " \-\-titles " " \fILISTA\fR
Solo estos títulos, separados por comas.
.TP
.BR \-c ", " \-\-chapters
Un archivo por capítulo.
.TP
.BR \-y ", " \-\-yes
Empezar sin esperar a pulsar \fBs\fR.
.TP
.BR \-D ", " \-\-check
Comprobar dependencias y salir.
.SH TECLAS
.TP
.B Tab
Cambiar entre el panel de ficheros y el de log/resumen.
.TP
.B Flechas, RePág, AvPág, Inicio, Fin
Desplazarse.
.TP
.B espacio / a
Incluir u omitir un fichero / todos.
.TP
.B s
Iniciar la cola.
.TP
.B p
Pausar o reanudar.
.TP
.B x
Cancelar el fichero en curso.
.TP
.B r
Reencolar el fichero seleccionado.
.TP
.B l
Alternar log y resumen.
.TP
.B o
Cambiar la carpeta de salida.
.TP
.B g
Ir al fichero en curso.
.TP
.B q
Salir.
.SH LICENCIA
GNU GPL versión 3 o posterior.
.SH FICHEROS
\fI~/.config/iso2appletv/config.json\fR contiene la carpeta de salida.
En el directorio de salida: \fI.iso2appletv.json\fR (resúmenes) y \fI.logs/\fR
(logs de cada conversión).
.SH ENTORNO
.TP
.B HB
Ruta de HandBrakeCLI.
"""

COPYRIGHT = f"""\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: {PKG}

Files: *
Copyright: {time.strftime("%Y")} {MAINTAINER}
License: GPL-3+
 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.
 .
 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.
 .
 On Debian systems, the full text of the GNU General Public License
 version 3 can be found in the file /usr/share/common-licenses/GPL-3.
"""


def changelog(version: str) -> bytes:
    date = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())
    txt = (f"{PKG} ({version}) unstable; urgency=low\n\n"
           f"  * Versión inicial: TUI de conversión con panel de sistema.\n"
           f"  * Carpeta de salida configurable desde la interfaz. Licencia GPL-3+.\n\n"
           f" -- {MAINTAINER}  {date}\n")
    return gzip.compress(txt.encode(), mtime=0)


def tar_gz(entries):
    """entries: lista de (ruta, bytes|None(dir), modo). Propietario root:root."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tf:
            for path, data, mode in entries:
                ti = tarfile.TarInfo(path)
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = "root"
                ti.mtime = int(time.time())
                ti.mode = mode
                if data is None:
                    ti.type = tarfile.DIRTYPE
                    tf.addfile(ti)
                else:
                    ti.size = len(data)
                    tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def ar_member(name: str, data: bytes) -> bytes:
    hdr = f"{name:<16}{int(time.time()):<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n"
    assert len(hdr) == 60
    return hdr.encode() + data + (b"\n" if len(data) % 2 else b"")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args()
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+~-]*", a.version):
        ap.error("versión inválida")

    script = (ROOT / "iso2appletv.py").read_bytes()
    man = gzip.compress(MANPAGE.encode(), mtime=0)
    docdir = f"usr/share/doc/{PKG}"

    files = {   # ruta -> (datos, modo)
        "usr/bin/iso2appletv": (script, 0o755),
        f"usr/share/man/man1/iso2appletv.1.gz": (man, 0o644),
        f"{docdir}/copyright": (COPYRIGHT.encode(), 0o644),
        f"{docdir}/changelog.gz": (changelog(a.version), 0o644),
        f"{docdir}/LICENSE": ((ROOT / "LICENSE").read_bytes(), 0o644),
    }

    dirs = set()
    for p in files:
        parts = p.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))

    data_entries = [("./", None, 0o755)]
    data_entries += [(f"./{d}/", None, 0o755) for d in sorted(dirs)]
    data_entries += [(f"./{p}", d, m) for p, (d, m) in sorted(files.items())]

    size_kb = (sum(len(d) for d, _ in files.values()) + 1023) // 1024
    md5 = "".join(f"{hashlib.md5(d).hexdigest()}  {p}\n" for p, (d, _) in sorted(files.items()))
    control_entries = [
        ("./", None, 0o755),
        ("./control", CONTROL.format(version=a.version, size=size_kb).encode(), 0o644),
        ("./md5sums", md5.encode(), 0o644),
        ("./postinst", POSTINST.encode(), 0o755),
    ]

    deb = b"!<arch>\n"
    deb += ar_member("debian-binary", b"2.0\n")
    deb += ar_member("control.tar.gz", tar_gz(control_entries))
    deb += ar_member("data.tar.gz", tar_gz(data_entries))

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{PKG}_{a.version}_all.deb"
    target.write_bytes(deb)
    print(f"Generado {target} ({len(deb)} bytes)")


if __name__ == "__main__":
    main()
