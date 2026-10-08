#!/usr/bin/env bash
#
# iso2appletv.sh - Extrae los títulos de video de una ISO de DVD/Blu-ray
# a .m4v compatible con Apple TV usando HandBrakeCLI.
#
#   Video : H.264 High@4.1, máx. 1280x720 (720p), calidad constante (RF)
#   Audio : AC3 5.1 si el origen tiene 5.1 (o más, se reduce a 5.1);
#           si el origen es estéreo, AC3 estéreo. Si la pista ya es AC3
#           se copia sin recodificar.
#   Extra : marcadores de capítulo, desentrelazado automático (DVD),
#           "optimize" (faststart) para streaming.
#
# Dependencias (Ubuntu) - se pueden comprobar/instalar con:  ./iso2appletv.sh -D
#   sudo apt install handbrake-cli jq libdvd-pkg libbluray2 libaacs0
#   sudo dpkg-reconfigure libdvd-pkg        # para DVDs con CSS
#   (Blu-ray comercial cifrado: además necesita un KEYDB.cfg en ~/.config/aacs/)
#

set -u

### CONFIGURACIÓN ###########################################################
# Ruta de salida de los archivos generados (se crea si no existe).
# Si se deja vacía, se usa ./<nombre_iso>_appletv. La opción -o la sobrescribe.
OUT_DIR="/home/miguel/HandBrake"
#############################################################################

HB="${HB:-HandBrakeCLI}"
MIN_DURATION=300        # segundos; ignora menús/extras cortos
QUALITY=20              # RF x264 (18 = más calidad, 22 = más pequeño)
SPLIT_CHAPTERS=0
ONLY_TITLES=""
ASSUME_YES=0

usage() {
    cat <<EOF
Uso: $(basename "$0") [opciones] imagen.iso

Opciones:
  -o DIR   Directorio de salida (por defecto: el definido en OUT_DIR dentro del script)
  -d SEG   Duración mínima en segundos de un título para procesarlo (def: $MIN_DURATION)
  -q RF    Calidad x264, menor = mejor (def: $QUALITY)
  -t LISTA Solo estos títulos, separados por comas (ej: 1,3,5)
  -y       No preguntar confirmación tras listar títulos y capítulos
  -c       Un archivo por capítulo en lugar de uno por título
  -D       Comprobar dependencias (y ofrecer instalarlas) y salir
  -h       Esta ayuda

Variables de entorno: HB=ruta a HandBrakeCLI
EOF
}

# --- Comprobación e instalación de dependencias (apt) -----------------------
pkg_installed() {
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'
}

lib_present() {
    ldconfig -p 2>/dev/null | grep -q "$1"
}

# Devuelve el primer paquete de la lista que tenga candidato de instalación.
# (los nombres cambian según la versión de Ubuntu: libbluray2, libbluray2t64, ...)
first_candidate() {
    local p cand
    for p in "$@"; do
        cand="$(apt-cache policy "$p" 2>/dev/null | awk '/Candidate:/ {print $2}')"
        if [[ -n "$cand" && "$cand" != "(none)" ]]; then
            echo "$p"
            return 0
        fi
    done
    return 1
}

check_deps() {
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "Este chequeo solo funciona en Ubuntu/Debian (apt). Instala a mano:" >&2
        echo "  HandBrakeCLI, jq, libdvdcss, libbluray y libaacs" >&2
        return 1
    fi

    local SUDO=""
    if [[ $EUID -ne 0 ]]; then
        command -v sudo >/dev/null 2>&1 || { echo "Error: se necesita sudo o ejecutar como root." >&2; return 1; }
        SUDO="sudo"
    fi

    local ok_hb=0 ok_jq=0 ok_dvd=0 ok_bd=0 ok_aacs=0 need_reconf=0
    command -v "$HB" >/dev/null 2>&1 && ok_hb=1      # vale también PPA/Flatpak
    command -v jq    >/dev/null 2>&1 && ok_jq=1
    pkg_installed libdvd-pkg           && ok_dvd=1
    lib_present 'libbluray\.so'        && ok_bd=1
    lib_present 'libaacs\.so'          && ok_aacs=1
    # libdvd-pkg solo descarga/compila libdvdcss2 al reconfigurarlo
    if [[ $ok_dvd -eq 1 ]] && ! lib_present libdvdcss; then need_reconf=1; fi

    status() { if [[ $1 -eq 1 ]]; then printf "   [OK]     %s\n" "$2"; else printf "   [FALTA]  %s\n" "$2"; fi; }
    echo ">> Comprobando dependencias..."
    status $ok_hb   "HandBrakeCLI (codificador)"
    status $ok_jq   "jq (lectura del escaneo JSON)"
    status $ok_dvd  "libdvd-pkg (descifrado CSS de DVD)"
    [[ $ok_dvd -eq 1 && $need_reconf -eq 1 ]] && echo "   [FALTA]  libdvdcss2 (libdvd-pkg instalado pero sin compilar)"
    status $ok_bd   "libbluray (lectura de Blu-ray)"
    status $ok_aacs "libaacs (descifrado AACS de Blu-ray)"

    if [[ ! -f "${XDG_CONFIG_HOME:-$HOME/.config}/aacs/KEYDB.cfg" ]]; then
        echo "   [AVISO]  No hay KEYDB.cfg en ~/.config/aacs/: los Blu-ray comerciales"
        echo "            cifrados no se podrán leer (los DVD y Blu-ray sin cifrar sí)."
    fi

    if [[ $((ok_hb + ok_jq + ok_dvd + ok_bd + ok_aacs)) -eq 5 && $need_reconf -eq 0 ]]; then
        echo ">> Todas las dependencias están instaladas."
        return 0
    fi

    echo
    local ans
    read -r -p "¿Instalar lo que falta ahora? [s/N] " ans
    if [[ ! "$ans" =~ ^[sSyY]$ ]]; then
        echo "No se instalará nada. El script puede fallar sin estas dependencias."
        return 1
    fi

    # Primero actualizar índices: los candidatos se resuelven después
    $SUDO apt-get update || return 1

    local pkgs=() p
    [[ $ok_hb  -eq 0 ]] && pkgs+=(handbrake-cli)
    [[ $ok_jq  -eq 0 ]] && pkgs+=(jq)
    if [[ $ok_dvd -eq 0 ]]; then
        if p="$(first_candidate libdvd-pkg)"; then
            pkgs+=("$p"); need_reconf=1
        else
            echo "   [AVISO] libdvd-pkg no está disponible: habilita los repositorios universe/multiverse." >&2
        fi
    fi
    if [[ $ok_bd -eq 0 ]]; then
        if p="$(first_candidate libbluray2t64 libbluray2 libbluray3t64 libbluray3)"; then
            pkgs+=("$p")
        else
            echo "   [AVISO] No se encontró paquete libbluray en tus repositorios (se omite)." >&2
        fi
    fi
    if [[ $ok_aacs -eq 0 ]]; then
        if p="$(first_candidate libaacs0t64 libaacs0)"; then
            pkgs+=("$p")
        else
            echo "   [AVISO] No se encontró paquete libaacs en tus repositorios (se omite)." >&2
        fi
    fi

    if [[ ${#pkgs[@]} -gt 0 ]]; then
        echo ">> Instalando: ${pkgs[*]}"
        $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y "${pkgs[@]}" || return 1
    fi
    if [[ $need_reconf -eq 1 ]]; then
        echo ">> Compilando libdvdcss2 (dpkg-reconfigure libdvd-pkg)..."
        $SUDO dpkg-reconfigure -f noninteractive libdvd-pkg || return 1
    fi

    echo ">> Listo."
    return 0
}

while getopts ":o:d:q:t:cyDh" opt; do
    case "$opt" in
        o) OUT_DIR="$OPTARG" ;;
        d) MIN_DURATION="$OPTARG" ;;
        q) QUALITY="$OPTARG" ;;
        t) ONLY_TITLES="$OPTARG" ;;
        c) SPLIT_CHAPTERS=1 ;;
        y) ASSUME_YES=1 ;;
        D) check_deps; exit $? ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

if [[ $# -ne 1 ]]; then
    usage
    exit 1
fi

ISO="$1"

# --- Comprobaciones ---------------------------------------------------------
[[ -f "$ISO" ]] || { echo "Error: no existe el archivo '$ISO'" >&2; exit 1; }
command -v "$HB" >/dev/null 2>&1 || { echo "Error: no se encuentra $HB (ejecuta: $(basename "$0") -D)" >&2; exit 1; }
command -v jq    >/dev/null 2>&1 || { echo "Error: falta jq (ejecuta: $(basename "$0") -D)" >&2; exit 1; }

BASENAME="$(basename "${ISO%.*}")"
[[ -n "$OUT_DIR" ]] || OUT_DIR="./${BASENAME}_appletv"
mkdir -p "$OUT_DIR" || exit 1

# --- Escaneo de títulos -----------------------------------------------------
echo ">> Escaneando '$ISO' ..."
SCAN_LOG="$(mktemp)"
trap 'rm -f "$SCAN_LOG"' EXIT

# El JSON sale por stdout; los mensajes de log (libdvdread, etc.) por stderr.
# Se separan para que los logs no se cuelen dentro del JSON.
SCAN_JSON="$("$HB" -i "$ISO" -t 0 --min-duration "$MIN_DURATION" --json </dev/null 2>"$SCAN_LOG" \
    | sed -n '/^JSON Title Set: {/,/^}/p' \
    | sed -e '1s/^JSON Title Set: //' -E -e 's/: -?nan/: null/g; s/: -?inf/: null/g')"

if [[ -z "$SCAN_JSON" ]] || ! jq -e . >/dev/null 2>&1 <<<"$SCAN_JSON"; then
    echo "Error: no se pudo escanear la imagen (¿cifrado sin libdvdcss/libaacs?)." >&2
    echo "--- Últimas líneas del log de HandBrake ---" >&2
    tail -n 15 "$SCAN_LOG" >&2
    exit 1
fi

# índice <TAB> duración(s) <TAB> nº capítulos
mapfile -t TITLES < <(jq -r --argjson min "$MIN_DURATION" '
    .TitleList[]
    | { i: .Index,
        d: (.Duration.Hours*3600 + .Duration.Minutes*60 + .Duration.Seconds),
        c: (.ChapterList | length) }
    | select(.d >= $min)
    | [.i, .d, .c] | @tsv' <<<"$SCAN_JSON")

if [[ ${#TITLES[@]} -eq 0 ]]; then
    echo "No hay títulos de al menos ${MIN_DURATION}s. Prueba con -d 60." >&2
    exit 1
fi

# --- Lista de títulos/capítulos y confirmación ------------------------------
fmt_dur() { printf "%02d:%02d:%02d" $(($1/3600)) $(($1%3600/60)) $(($1%60)); }

SELECTED=0
echo ">> Contenido de '$ISO':"
for line in "${TITLES[@]}"; do
    IFS=$'\t' read -r idx dur chaps <<<"$line"
    if [[ -n "$ONLY_TITLES" && ",$ONLY_TITLES," != *",$idx,"* ]]; then
        continue
    fi
    ((SELECTED++))
    printf "   Título %s  %s  (%s capítulos)\n" "$idx" "$(fmt_dur "$dur")" "$chaps"
    while IFS=$'\t' read -r cn cd; do
        printf "      Capítulo %2s  %s\n" "$cn" "$(fmt_dur "$cd")"
    done < <(jq -r --argjson t "$idx" '
        .TitleList[] | select(.Index == $t)
        | .ChapterList | to_entries[]
        | [(.key + 1),
           (.value.Duration.Hours*3600 + .value.Duration.Minutes*60 + .value.Duration.Seconds)]
        | @tsv' <<<"$SCAN_JSON")
done

if [[ $SELECTED -eq 0 ]]; then
    echo "Ningún título coincide con -t '$ONLY_TITLES'." >&2
    exit 1
fi

if [[ $ASSUME_YES -ne 1 ]]; then
    read -r -p "¿Continuar con la conversión de $SELECTED título(s)? [S/n] " ans
    if [[ "$ans" =~ ^[nN]$ ]]; then
        echo "Cancelado."
        exit 0
    fi
fi
echo

# --- Opciones comunes de HandBrake -----------------------------------------
HB_OPTS=(
    -f av_mp4 --optimize --markers
    -e x264 -q "$QUALITY"
    --encoder-preset slow --encoder-profile high --encoder-level 4.1
    --maxWidth 1280 --maxHeight 720
    --cfr
    --comb-detect --decomb
    # Audio: todas las pistas; AC3 (copia si ya es AC3) con mezcla a 5.1.
    # HandBrake reduce la mezcla automáticamente si el origen es estéreo.
    --all-audio
    -E copy:ac3 --audio-fallback ac3
    --mixdown 5point1
)

# Prioridad mínima de CPU (nice 19) y de disco (ionice idle) para no saturar el VPS.
LOWPRIO=(nice -n 19)
command -v ionice >/dev/null 2>&1 && LOWPRIO=(ionice -c3 "${LOWPRIO[@]}")

encode() {
    local title="$1" chap_args=("${@:3}") out="$2"

    if [[ -s "$out" ]]; then
        echo "   (ya existe, se omite) $out"
        return 0
    fi

    "${LOWPRIO[@]}" "$HB" -i "$ISO" -t "$title" "${chap_args[@]}" -o "$out" "${HB_OPTS[@]}" </dev/null
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "   ERROR codificando $out (código $rc)" >&2
        rm -f "$out"
        return $rc
    fi
}

# --- Bucle principal --------------------------------------------------------
ERRORS=0
for line in "${TITLES[@]}"; do
    IFS=$'\t' read -r idx dur chaps <<<"$line"

    if [[ -n "$ONLY_TITLES" && ",$ONLY_TITLES," != *",$idx,"* ]]; then
        continue
    fi

    printf -v tt "%02d" "$idx"
    printf ">> Título %s  (%02d:%02d:%02d, %s capítulos)\n" \
        "$idx" $((dur/3600)) $((dur%3600/60)) $((dur%60)) "$chaps"

    if [[ $SPLIT_CHAPTERS -eq 1 ]]; then
        for ((c = 1; c <= chaps; c++)); do
            printf -v cc "%02d" "$c"
            echo "   Capítulo $c/$chaps"
            encode "$idx" "$OUT_DIR/${BASENAME}_T${tt}_C${cc}.m4v" -c "${c}-${c}" || ((ERRORS++))
        done
    else
        encode "$idx" "$OUT_DIR/${BASENAME}_T${tt}.m4v" || ((ERRORS++))
    fi
done

echo
if [[ $ERRORS -eq 0 ]]; then
    echo ">> Terminado. Archivos en: $OUT_DIR"
else
    echo ">> Terminado con $ERRORS error(es). Archivos en: $OUT_DIR" >&2
    exit 1
fi
