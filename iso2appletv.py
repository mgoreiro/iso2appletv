#!/usr/bin/env python3
"""
iso2appletv - TUI para convertir los títulos de ISOs de DVD/Blu-ray a .m4v
compatible con Apple TV usando HandBrakeCLI.

  Video : H.264 High@4.1, máx. 1280x720, calidad constante (RF)
  Audio : AC3 (copia si ya es AC3), mezcla 5.1 (estéreo si el origen lo es)
  Extra : marcadores de capítulo, desentrelazado automático, faststart

Pantalla:
  ┌ lista de ficheros (2/3) ┐  estado, hora de fin, progreso
  └ log / resumen    (1/3) ┘  log de HandBrakeCLI o resumen si ya está convertido

Teclas: Tab cambia de panel, ↑↓ PgUp PgDn Home End desplazan, ? ayuda.
Solo usa la biblioteca estándar (Python >= 3.8).

Copyright (C) Miguel <mgoreiro@gmail.com>

Este programa es software libre: puedes redistribuirlo y/o modificarlo bajo
los términos de la Licencia Pública General de GNU publicada por la Free
Software Foundation, ya sea la versión 3 de la Licencia o (a tu elección)
cualquier versión posterior.

Se distribuye con la esperanza de que sea útil, pero SIN NINGUNA GARANTÍA;
ni siquiera la garantía implícita de COMERCIABILIDAD o IDONEIDAD PARA UN
PROPÓSITO PARTICULAR. Consulta la Licencia Pública General de GNU para más
detalles. Deberías haber recibido una copia junto a este programa (fichero
LICENSE); si no, consulta <https://www.gnu.org/licenses/>.
"""

import argparse
import curses
import datetime as dt
import json
import locale
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

### CONFIGURACIÓN ###########################################################
# La carpeta de salida se cambia desde la interfaz (tecla o) y se guarda en
# ~/.config/iso2appletv/config.json. La opción -o la sobrescribe solo esa vez.
DEFAULT_OUT_DIR = os.path.expanduser("~/HandBrake")
#############################################################################

HB = os.environ.get("HB", "HandBrakeCLI")
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))) / "iso2appletv" / "config.json"
STATE_FILE = ".iso2appletv.json"   # resúmenes de conversiones, dentro de la carpeta de salida
LOG_DIR = ".logs"                  # log completo de cada conversión
MAX_LOG_LINES = 20000

PROGRESS_RE = re.compile(
    r"Encoding: task (\d+) of (\d+), ([\d.]+) %"
    r"(?: \(([\d.]+) fps, avg ([\d.]+) fps, ETA (\d+)h(\d+)m(\d+)s\))?"
)


def hb_opts(quality: int) -> List[str]:
    return [
        "-f", "av_mp4", "--optimize", "--markers",
        "-e", "x264", "-q", str(quality),
        "--encoder-preset", "slow", "--encoder-profile", "high",
        "--encoder-level", "4.1",
        "--maxWidth", "1280", "--maxHeight", "720",
        "--cfr", "--comb-detect", "--decomb",
        "--all-audio", "-E", "copy:ac3", "--audio-fallback", "ac3",
        "--mixdown", "5point1",
    ]


def lowprio() -> List[str]:
    cmd = ["nice", "-n", "19"]
    if shutil.which("ionice"):
        cmd = ["ionice", "-c3"] + cmd
    return cmd


# --------------------------------------------------------------------------
# Configuración persistente
# --------------------------------------------------------------------------
def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_config(cfg: dict) -> Optional[str]:
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=1))
    except OSError as e:
        return str(e)
    return None


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def fmt_dur(s: Optional[float]) -> str:
    if s is None:
        return "--:--:--"
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_when(ts: Optional[float]) -> str:
    if not ts:
        return "--:--"
    d = dt.datetime.fromtimestamp(ts)
    if d.date() == dt.date.today():
        return d.strftime("%H:%M:%S")
    return d.strftime("%d/%m %H:%M")


def fmt_size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.2f} {unit}"
        v /= 1024
    return str(n)


# --------------------------------------------------------------------------
# Monitor de sistema (CPU, RAM, disco). Sin dependencias: /proc en Linux y
# ps/vm_stat como respaldo en macOS (solo para desarrollo).
# --------------------------------------------------------------------------
class Monitor:
    INTERVAL = 1.5

    def __init__(self, get_pid, get_path):
        self.get_pid, self.get_path = get_pid, get_path
        self.linux = os.path.exists("/proc/stat")
        self.ncpu = os.cpu_count() or 1
        self.stop = False
        self.cpu: Optional[float] = None
        self.iowait: Optional[float] = None
        self.cpu_hist: deque = deque(maxlen=200)
        self.load = (0.0, 0.0, 0.0)
        self.mem_total = self.mem_used = 0
        self.swap_total = self.swap_used = 0
        self.disk_total = self.disk_used = 0
        self.disk_path = ""
        self.io_r: Optional[float] = None    # bytes/s
        self.io_w: Optional[float] = None
        self.hb_cpu: Optional[float] = None  # 100 = un núcleo completo
        self.hb_rss: Optional[int] = None
        self._pc = None
        self._pio = None
        self._phb = None
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self.stop:
            for fn in (self._sample_cpu, self._sample_mem, self._sample_disk,
                       self._sample_io, self._sample_hb):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
            try:
                self.load = os.getloadavg()
            except OSError:
                pass
            time.sleep(self.INTERVAL)

    def _sample_cpu(self):
        if self.linux:
            v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
            total, idle, iow = sum(v[:8]), v[3] + v[4], v[4]
            if self._pc and total > self._pc[0]:
                dt = total - self._pc[0]
                self.cpu = max(0.0, min(100.0, 100.0 * (1 - (idle - self._pc[1]) / dt)))
                self.iowait = 100.0 * (iow - self._pc[2]) / dt
                self.cpu_hist.append(self.cpu)
            self._pc = (total, idle, iow)
        else:
            out = subprocess.run(["ps", "-A", "-o", "%cpu="], capture_output=True, text=True, timeout=3).stdout
            self.cpu = min(100.0, sum(float(x) for x in out.split()) / self.ncpu)
            self.cpu_hist.append(self.cpu)

    def _sample_mem(self):
        if self.linux:
            m = {}
            for ln in open("/proc/meminfo"):
                k, _, rest = ln.partition(":")
                m[k] = int(rest.split()[0]) * 1024
            self.mem_total = m["MemTotal"]
            self.mem_used = m["MemTotal"] - m.get("MemAvailable", m["MemFree"])
            self.swap_total = m.get("SwapTotal", 0)
            self.swap_used = self.swap_total - m.get("SwapFree", 0)
        else:
            page = os.sysconf("SC_PAGE_SIZE")
            self.mem_total = page * os.sysconf("SC_PHYS_PAGES")
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
            pages = {}
            for ln in out.splitlines()[1:]:
                k, _, v = ln.partition(":")
                v = v.strip().rstrip(".")
                if v.isdigit():
                    pages[k.strip()] = int(v)
            avail = sum(pages.get(k, 0) for k in ("Pages free", "Pages inactive",
                                                    "Pages speculative", "Pages purgeable"))
            self.mem_used = max(0, self.mem_total - avail * page)

    def _sample_disk(self):
        path = Path(self.get_path() or ".").resolve()
        while not path.exists() and path != path.parent:
            path = path.parent
        du = shutil.disk_usage(path)
        self.disk_total, self.disk_used, self.disk_path = du.total, du.used, str(path)

    def _sample_io(self):
        if not self.linux:
            return
        rd = wr = 0
        for ln in open("/proc/diskstats"):
            f = ln.split()
            name = f[2]
            if name.startswith(("loop", "ram", "zram")) or not os.path.isdir("/sys/block/" + name):
                continue   # solo discos completos (no particiones ni loops)
            rd += int(f[5]) * 512
            wr += int(f[9]) * 512
        now = time.time()
        if self._pio and now > self._pio[0]:
            dt = now - self._pio[0]
            self.io_r = max(0.0, (rd - self._pio[1]) / dt)
            self.io_w = max(0.0, (wr - self._pio[2]) / dt)
        self._pio = (now, rd, wr)

    def _sample_hb(self):
        pid = self.get_pid()
        if not pid:
            self.hb_cpu = self.hb_rss = None
            self._phb = None
            return
        if self.linux:
            data = open(f"/proc/{pid}/stat").read()
            rest = data[data.rindex(")") + 2:].split()
            ticks = int(rest[11]) + int(rest[12])
            now = time.time()
            if self._phb and self._phb[0] == pid and now > self._phb[1]:
                self.hb_cpu = 100.0 * (ticks - self._phb[2]) / os.sysconf("SC_CLK_TCK") / (now - self._phb[1])
            self._phb = (pid, now, ticks)
            self.hb_rss = int(open(f"/proc/{pid}/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        else:
            out = subprocess.run(["ps", "-o", "%cpu=,rss=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=3).stdout.split()
            if len(out) == 2:
                self.hb_cpu, self.hb_rss = float(out[0]), int(out[1]) * 1024


# --------------------------------------------------------------------------
# Modelo
# --------------------------------------------------------------------------
@dataclass
class Job:
    iso: str
    title: int
    chapter: Optional[int]
    chaps: int
    dur: int
    out: str
    audio: List[str] = field(default_factory=list)
    subs: int = 0
    status: str = "pending"        # pending running done failed canceled
    enabled: bool = True
    existing: bool = False         # ya estaba convertido antes de esta sesión
    started: Optional[float] = None
    finished: Optional[float] = None
    paused_total: float = 0.0
    pct: float = 0.0
    fps: float = 0.0
    avg_fps: float = 0.0
    eta: str = ""
    rc: Optional[int] = None
    size: int = 0
    progress: str = ""
    log: List[str] = field(default_factory=list)
    proc: Optional[subprocess.Popen] = None
    cancel: bool = False

    @property
    def name(self) -> str:
        return Path(self.out).name

    @property
    def elapsed(self) -> Optional[float]:
        if not self.started:
            return None
        end = self.finished or time.time()
        return max(0.0, end - self.started - self.paused_total)


class App:
    def __init__(self, args):
        self.args = args
        self.jobs: List[Job] = []
        self.sys_log: List[str] = []
        self.phase = "scanning"    # scanning ready running finished
        self.paused = False
        self.pause_since = 0.0
        self.quit = False
        self.worker: Optional[threading.Thread] = None
        self.run_started: Optional[float] = None
        self.cur = 0               # job bajo el cursor
        self.top = 0               # primera fila visible de la lista
        self.focus = 0             # 0 lista, 1 log/resumen
        self.scroll: Optional[int] = None   # None = seguir el final
        self.alt_view = False      # invierte log/resumen
        self.msg = ""
        self.msg_until = 0.0
        self.help = False
        self.cfg = load_config()
        self.out_base = os.path.expanduser(
            args.output if args.output is not None else self.cfg.get("output_dir") or DEFAULT_OUT_DIR)
        self.mon = Monitor(self.running_pid, self.stats_path)

    def running_pid(self) -> Optional[int]:
        j = next((x for x in self.jobs if x.status == "running"), None)
        return j.proc.pid if j and j.proc else None

    def stats_path(self) -> str:
        j = next((x for x in self.jobs if x.status == "running"), None) or \
            (self.jobs[min(self.cur, len(self.jobs) - 1)] if self.jobs else None)
        if j:
            return str(Path(j.out).parent)
        return self.out_base

    def set_output_dir(self, path: str):
        """Cambia la carpeta de salida y reevalúa los ficheros aún no convertidos."""
        self.out_base = path
        self.cfg["output_dir"] = path
        err = save_config(self.cfg)
        state_cache: dict = {}
        out_dir = Path(path)
        state_cache = self.load_state(out_dir)
        for j in self.jobs:
            if j.status == "running":
                continue
            if j.status == "pending" or j.existing or j.status == "canceled":
                keep = j.enabled if j.status == "pending" and not j.existing else True
                j.out = str(out_dir / j.name)
                j.status, j.enabled, j.existing = "pending", keep, False
                j.started = j.finished = None
                j.rc, j.size, j.pct, j.avg_fps, j.paused_total = None, 0, 0.0, 0.0, 0.0
                j.log = []
                self.fill_existing(j, state_cache, out_dir)
        self.scroll = None
        self.say(f"Carpeta de salida: {path}" + (f"  (no se pudo guardar la config: {err})" if err else ""), 6)

    @staticmethod
    def complete_dir(text: str) -> str:
        import glob
        base = os.path.expanduser(text)
        cands = [c for c in glob.glob(glob.escape(base) + "*") if os.path.isdir(c)]
        if not cands:
            return text
        if len(cands) == 1:
            return cands[0].rstrip("/") + "/"
        return os.path.commonprefix(cands)

    def prompt_output_dir(self, scr):
        text, pos, err = list(self.out_base), len(self.out_base), ""
        H, W = scr.getmaxyx()
        w = min(W - 4, max(64, W * 2 // 3))
        win = curses.newwin(7, w, max(0, H // 2 - 3), (W - w) // 2)
        win.keypad(True)
        win.timeout(250)
        iw = w - 4
        curses.curs_set(1)
        try:
            while not self.quit:
                self.draw(scr)
                win.erase()
                win.attrset(curses.color_pair(4) | curses.A_BOLD)
                win.box()
                win.attrset(0)
                self.put(win, 0, 2, " Carpeta de salida ", curses.A_REVERSE | curses.color_pair(4), w - 4)
                self.put(win, 1, 2, "Las conversiones pendientes se guardarán aquí:", curses.A_DIM, iw)
                off = max(0, pos - iw + 1)
                shown = "".join(text)[off:off + iw]
                self.put(win, 3, 2, shown.ljust(iw), curses.A_UNDERLINE, iw + 1)
                self.put(win, 4, 2, err, curses.color_pair(3) | curses.A_BOLD, iw)
                self.put(win, 5, 2, "Enter aceptar · Esc cancelar · Tab completar · Ctrl+U borrar", curses.A_DIM, iw)
                win.move(3, 2 + pos - off)
                win.refresh()
                try:
                    ch = win.get_wch()
                except curses.error:
                    continue
                if ch in ("\n", "\r", curses.KEY_ENTER):
                    path = os.path.abspath(os.path.expanduser("".join(text).strip() or DEFAULT_OUT_DIR))
                    try:
                        os.makedirs(path, exist_ok=True)
                        if not os.access(path, os.W_OK):
                            raise PermissionError(f"sin permiso de escritura en {path}")
                    except OSError as e:
                        err = str(e)[:iw]
                        continue
                    self.set_output_dir(path)
                    return
                elif ch == "\x1b":
                    return
                elif ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    if pos:
                        del text[pos - 1]
                        pos -= 1
                elif ch == curses.KEY_DC:
                    if pos < len(text):
                        del text[pos]
                elif ch == curses.KEY_LEFT:
                    pos = max(0, pos - 1)
                elif ch == curses.KEY_RIGHT:
                    pos = min(len(text), pos + 1)
                elif ch == curses.KEY_HOME:
                    pos = 0
                elif ch == curses.KEY_END:
                    pos = len(text)
                elif ch == "\x15":
                    text, pos = [], 0
                elif ch == "\t":
                    text = list(self.complete_dir("".join(text)))
                    pos = len(text)
                elif isinstance(ch, str) and ch.isprintable():
                    text.insert(pos, ch)
                    pos += 1
                err = ""
        finally:
            curses.curs_set(0)

    # ---- mensajes ----
    def say(self, text: str, secs: float = 4.0):
        self.msg, self.msg_until = text, time.time() + secs

    def slog(self, text: str):
        self.sys_log.append(text)

    # ---- rutas / estado persistente ----
    def out_dir_for(self, iso: str) -> Path:
        return Path(self.out_base)

    @staticmethod
    def load_state(out_dir: Path) -> dict:
        try:
            return json.loads((out_dir / STATE_FILE).read_text())
        except Exception:
            return {}

    def save_state(self, job: Job):
        d = Path(job.out).parent
        st = self.load_state(d)
        st[job.name] = {
            "started": job.started, "finished": job.finished,
            "elapsed": job.elapsed, "avg_fps": job.avg_fps,
            "size": job.size, "rc": job.rc, "status": job.status,
        }
        try:
            (d / STATE_FILE).write_text(json.dumps(st, indent=1))
            (d / LOG_DIR).mkdir(exist_ok=True)
            (d / LOG_DIR / (Path(job.out).stem + ".log")).write_text("\n".join(job.log) + "\n")
        except OSError:
            pass

    # ---- escaneo ----
    def scan_all(self):
        for iso in self.args.isos:
            if self.quit:
                return
            try:
                self.scan_iso(iso)
            except Exception as e:  # noqa: BLE001
                self.slog(f"ERROR escaneando {iso}: {e}")
        if not self.jobs:
            self.slog("No hay títulos que procesar. Prueba con -d 60. (q para salir)")
            self.phase = "finished"
            return
        self.phase = "ready"
        if self.args.yes:
            self.start()

    def scan_iso(self, iso: str):
        a = self.args
        if not os.path.isfile(iso):
            self.slog(f"ERROR: no existe '{iso}'")
            return
        self.slog(f">> Escaneando '{iso}' ...")
        with tempfile.TemporaryFile() as errf:
            r = subprocess.run(
                [HB, "-i", iso, "-t", "0", "--min-duration", str(a.min_duration), "--json"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errf,
            )
            errf.seek(0)
            err_lines = errf.read().decode(errors="replace").splitlines()
        lines = r.stdout.decode(errors="replace").splitlines()
        buf, on = [], False
        for ln in lines:
            if not on and ln.startswith("JSON Title Set: {"):
                on = True
                buf.append(ln[len("JSON Title Set: "):])
            elif on:
                buf.append(ln)
                if ln == "}":
                    break
        try:
            data = json.loads(re.sub(r":\s*-?(nan|inf)\b", ": null", "\n".join(buf)))
        except Exception:
            self.slog(f"ERROR: no se pudo escanear '{iso}' (¿cifrado sin libdvdcss/libaacs?)")
            self.sys_log.extend(err_lines[-15:])
            return

        only = {int(x) for x in a.titles.split(",")} if a.titles else None
        out_dir = self.out_dir_for(iso)
        state = self.load_state(out_dir)
        base = Path(iso).stem
        added = 0
        for t in data.get("TitleList", []):
            idx = t["Index"]
            d = t["Duration"]
            dur = d["Hours"] * 3600 + d["Minutes"] * 60 + d["Seconds"]
            if dur < a.min_duration or (only and idx not in only):
                continue
            chl = t.get("ChapterList", [])
            audio = [x.get("Description") or x.get("Language", "?") for x in t.get("AudioList", [])]
            subs = len(t.get("SubtitleList", []))
            if a.chapters:
                units = []
                for n, c in enumerate(chl, 1):
                    cd = c["Duration"]
                    units.append((n, cd["Hours"] * 3600 + cd["Minutes"] * 60 + cd["Seconds"]))
            else:
                units = [(None, dur)]
            for ch, udur in units:
                suffix = f"_T{idx:02d}" + (f"_C{ch:02d}" if ch else "")
                out = out_dir / f"{base}{suffix}.m4v"
                j = Job(iso, idx, ch, len(chl), udur, str(out), audio, subs)
                self.fill_existing(j, state, out_dir)
                self.jobs.append(j)
                added += 1
        self.slog(f"   {added} fichero(s) a generar desde '{iso}'")

    def fill_existing(self, j: Job, state: dict, out_dir: Path):
        p = Path(j.out)
        s = state.get(p.name, {})
        lf = out_dir / LOG_DIR / (p.stem + ".log")
        try:
            if p.is_file() and p.stat().st_size > 0:
                j.status, j.existing, j.enabled = "done", True, False
                j.size = p.stat().st_size
                j.finished = p.stat().st_mtime
                j.started = s.get("started")
                j.finished = s.get("finished") or j.finished
                j.avg_fps = s.get("avg_fps") or 0.0
                if j.started and s.get("elapsed"):
                    j.paused_total = max(0.0, (j.finished - j.started) - s["elapsed"])
            elif s.get("status") == "failed":
                # fallo de una sesión anterior: se muestra con su log; r para reintentar
                j.status, j.existing, j.enabled = "failed", True, False
                j.rc = s.get("rc")
                j.started, j.finished = s.get("started"), s.get("finished")
            else:
                return
            if lf.is_file():
                j.log = lf.read_text(errors="replace").splitlines()[-MAX_LOG_LINES:]
        except OSError:
            pass

    # ---- cola ----
    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if not any(j.status == "pending" and j.enabled for j in self.jobs):
            self.say("No hay ficheros pendientes seleccionados")
            return
        self.phase = "running"
        self.run_started = self.run_started or time.time()
        self.worker = threading.Thread(target=self.run_queue, daemon=True)
        self.worker.start()

    def run_queue(self):
        while not self.quit:
            job = next((j for j in self.jobs if j.status == "pending" and j.enabled), None)
            if not job:
                break
            self.run_job(job)
        if not self.quit:
            self.phase = "finished"
            try:
                curses.beep()
            except curses.error:
                pass

    def run_job(self, j: Job):
        Path(j.out).parent.mkdir(parents=True, exist_ok=True)
        part = j.out + ".part"   # se renombra a j.out solo si termina bien
        chap = ["-c", f"{j.chapter}-{j.chapter}"] if j.chapter else []
        cmd = lowprio() + [HB, "-i", j.iso, "-t", str(j.title)] + chap + ["-o", part] + hb_opts(self.args.quality)
        j.status, j.started, j.finished = "running", time.time(), None
        j.pct = j.fps = j.avg_fps = 0.0
        j.eta, j.progress, j.rc, j.cancel, j.existing = "", "", None, False, False
        j.paused_total, j.log = 0.0, []
        j.log.append("$ " + " ".join(cmd))
        try:
            j.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            j.log.append(f"ERROR lanzando HandBrakeCLI: {e}")
            j.status, j.finished, j.rc = "failed", time.time(), -1
            return
        fd = j.proc.stdout.fileno()
        buf = b""
        while True:
            try:
                chunk = os.read(fd, 8192)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for p in parts:
                if p:
                    self.handle_line(j, p.decode(errors="replace").rstrip())
        if buf:
            self.handle_line(j, buf.decode(errors="replace").rstrip())
        j.rc = j.proc.wait()
        j.finished = time.time()
        if j.progress:
            j.log.append(j.progress)
        j.progress = ""
        out, tmp = Path(j.out), Path(part)
        try:
            ok = (not j.cancel and j.rc == 0 and tmp.is_file() and tmp.stat().st_size > 0)
            if ok:
                os.replace(tmp, out)
        except OSError as e:
            ok = False
            j.log.append(f"ERROR renombrando {part}: {e}")
        if j.cancel:
            j.status = "canceled"
            j.log.append(">> Cancelado por el usuario")
        elif ok:
            j.status, j.pct = "done", 100.0
            j.size = out.stat().st_size
            j.log.append(f">> Terminado: {j.out} ({fmt_size(j.size)})")
        else:
            j.status = "failed"
            j.log.append(f">> ERROR codificando (código {j.rc})")
        if j.status != "done":
            try:
                tmp.unlink()   # el fichero final anterior, si existía, no se toca
            except OSError:
                pass
        j.proc = None
        self.save_state(j)

    @staticmethod
    def handle_line(j: Job, line: str):
        m = PROGRESS_RE.search(line)
        if m:
            j.pct = float(m.group(3))
            if m.group(4):
                j.fps, j.avg_fps = float(m.group(4)), float(m.group(5))
                j.eta = f"{int(m.group(6)):02d}:{int(m.group(7)):02d}:{int(m.group(8)):02d}"
            j.progress = line
            return
        j.log.append(line)
        if len(j.log) > MAX_LOG_LINES:
            del j.log[:MAX_LOG_LINES // 4]

    # ---- acciones ----
    def toggle_pause(self):
        j = next((x for x in self.jobs if x.status == "running"), None)
        if not j or not j.proc:
            return
        try:
            if not self.paused:
                os.killpg(j.proc.pid, signal.SIGSTOP)
                self.paused, self.pause_since = True, time.time()
            else:
                os.killpg(j.proc.pid, signal.SIGCONT)
                j.paused_total += time.time() - self.pause_since
                self.paused = False
        except OSError:
            pass

    def cancel_running(self):
        j = next((x for x in self.jobs if x.status == "running"), None)
        if not j or not j.proc:
            return
        j.cancel = True
        self.kill(j)

    def kill(self, j: Job):
        if j.proc:
            try:
                if self.paused:
                    os.killpg(j.proc.pid, signal.SIGCONT)
                    self.paused = False
                os.killpg(j.proc.pid, signal.SIGTERM)
            except OSError:
                pass

    def retry(self, j: Job):
        if j.status in ("failed", "canceled", "done"):
            j.status, j.enabled, j.existing = "pending", True, False
            j.pct, j.finished = 0.0, None
            if self.phase == "finished":
                self.phase = "ready"
            self.say("Reencolado; pulsa s para iniciar" if not self.worker or not self.worker.is_alive() else "Reencolado")

    def shutdown(self):
        self.quit = True
        self.mon.stop = True
        for j in self.jobs:
            if j.status == "running":
                j.cancel = True
                self.kill(j)
        if self.worker:
            self.worker.join(timeout=5)

    # ---- contenido del panel inferior ----
    def summary_lines(self, j: Job) -> List[str]:
        L: List[str] = []

        def kv(k, v):
            L.append(f"{k:<17}{v}")

        what = f"título {j.title}" + (f", capítulo {j.chapter}/{j.chaps}" if j.chapter else f" ({j.chaps} capítulos)")
        kv("Origen", f"{Path(j.iso).name}  {what}")
        kv("Duración vídeo", fmt_dur(j.dur))
        for i, a in enumerate(j.audio):
            kv("Audio origen" if i == 0 else "", a)
        kv("Subtítulos", f"{j.subs} pista(s)")
        kv("Salida", j.out)
        L.append("")
        if j.status == "done":
            kv("Estado", "Completado" + (" (de una sesión anterior)" if j.existing else ""))
            kv("Inicio", fmt_when(j.started))
            kv("Fin", fmt_when(j.finished))
            if j.elapsed:
                kv("Tiempo empleado", fmt_dur(j.elapsed))
                kv("Velocidad", f"{j.dur / j.elapsed:.2f}x tiempo real")
            if j.avg_fps:
                kv("FPS medios", f"{j.avg_fps:.1f}")
            kv("Tamaño", fmt_size(j.size))
            if j.dur:
                kv("Bitrate medio", f"{j.size * 8 / j.dur / 1000:.0f} kb/s (vídeo+audio)")
            if not j.log:
                L += ["", "(no hay log guardado de esta conversión)"]
            else:
                L += ["", "Pulsa l para ver el log completo."]
        elif j.status == "failed":
            kv("Estado", f"ERROR (código {j.rc})" + (" — de una sesión anterior" if j.existing else ""))
            kv("Inicio", fmt_when(j.started))
            kv("Fin", fmt_when(j.finished))
            L += ["", "Pulsa r para reintentar, l para alternar log/resumen."]
        elif j.status == "canceled":
            kv("Estado", "Cancelado")
            L += ["", "Pulsa r para reintentar."]
        elif j.status == "running":
            kv("Estado", "PAUSADO" if self.paused else "Convirtiendo")
            kv("Inicio", fmt_when(j.started))
            kv("Progreso", f"{j.pct:.1f} %   {j.fps:.1f} fps (medio {j.avg_fps:.1f})   ETA {j.eta or '--'}")
        else:
            kv("Estado", "Pendiente" if j.enabled else "Omitido (espacio para incluir)")
        return L

    def bottom_content(self):
        """-> (título, líneas, es_log)"""
        if not self.jobs:
            return "Mensajes", list(self.sys_log), True
        j = self.jobs[self.cur]
        default_log = j.status in ("running", "failed")
        show_log = default_log != self.alt_view
        if show_log:
            lines = list(j.log)
            if j.status == "running" and j.progress:
                lines.append(j.progress)
            if not lines:
                lines = ["(sin log en esta sesión)"]
            return f"Log HandBrakeCLI — {j.name}", lines, True
        return f"Resumen — {j.name}", self.summary_lines(j), False

    # ---- dibujo ----
    @staticmethod
    def put(win, y, x, s, attr=0, maxw=None):
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        lim = (w - x - 1) if maxw is None else min(maxw, w - x - 1)
        if lim <= 0:
            return
        try:
            win.addstr(y, x, s[:lim], attr)
        except curses.error:
            pass

    def panel(self, y, x, h, w, title, focused):
        win = curses.newwin(h, w, y, x)
        attr = curses.color_pair(4) | curses.A_BOLD if focused else curses.A_DIM
        win.attrset(attr)
        try:
            win.box()
        except curses.error:
            pass
        win.attrset(0)
        t = f" {title} "
        self.put(win, 0, 2, t, attr | (curses.A_REVERSE if focused else 0), w - 4)
        return win

    def row(self, j: Job, iw: int):
        icon = {"pending": "·" if j.enabled else "○", "running": "▶", "done": "✔",
                "failed": "✘", "canceled": "⊘"}[j.status]
        if j.status == "running":
            if self.paused:
                st = "‖ PAUSADO " + f"{j.pct:5.1f}%"
            else:
                n = int(j.pct / 10)
                st = "▕" + "█" * n + "░" * (10 - n) + f"▏{j.pct:5.1f}% {j.eta or '':>8}"
            attr = curses.color_pair(2) | curses.A_BOLD
        elif j.status == "done":
            st = ("convertido " if not j.existing else "ya estaba  ") + fmt_when(j.finished)
            attr = curses.color_pair(1)
        elif j.status == "failed":
            st = f"ERROR ({j.rc}) {fmt_when(j.finished)}"
            attr = curses.color_pair(3) | curses.A_BOLD
        elif j.status == "canceled":
            st, attr = "cancelado", curses.color_pair(3)
        else:
            st, attr = ("pendiente" if j.enabled else "omitido"), (0 if j.enabled else curses.A_DIM)
        sw = min(34, max(12, iw // 2))
        nw = max(8, iw - 3 - 1 - 8 - 1 - sw)
        text = f" {icon} {j.name[:nw].ljust(nw)} {fmt_dur(j.dur)} {st.rjust(sw)}"
        return text.ljust(iw)[:iw], attr

    def header(self, W):
        done = sum(1 for j in self.jobs if j.status == "done")
        fail = sum(1 for j in self.jobs if j.status == "failed")
        todo = sum(1 for j in self.jobs if j.status == "pending" and j.enabled)
        label = {"scanning": "ESCANEANDO", "ready": "LISTO — pulsa s para iniciar",
                 "running": "PAUSADO" if self.paused else "CONVIRTIENDO",
                 "finished": "FINALIZADO"}[self.phase]
        el = fmt_dur(time.time() - self.run_started) if self.run_started else "--:--:--"
        s = (f" iso2appletv │ {label} │ {done}/{len(self.jobs)} convertidos │ "
             f"{todo} pendientes │ {fail} errores │ sesión {el}")
        return s.ljust(W)[:W]

    def draw(self, scr):
        H, W = scr.getmaxyx()
        scr.erase()
        if H < 12 or W < 50:
            self.put(scr, 0, 0, "Terminal demasiado pequeña (mín. 50x12)")
            scr.refresh()
            return
        self.put(scr, 0, 0, self.header(W), curses.A_REVERSE | curses.A_BOLD)

        body = H - 2
        log_h = max(6, body // 3)
        list_h = body - log_h

        # --- lista ---
        total = len(self.jobs)
        vis = list_h - 2
        self.cur = min(max(self.cur, 0), max(0, total - 1))
        if self.cur < self.top:
            self.top = self.cur
        if self.cur >= self.top + vis:
            self.top = self.cur - vis + 1
        self.top = max(0, min(self.top, max(0, total - vis)))
        pos = f"{self.cur + 1}/{total}" if total else "0/0"
        win = self.panel(1, 0, list_h, W, f"Ficheros  {pos}  ·  salida: {self.out_base}", self.focus == 0)
        iw = W - 2
        if not self.jobs:
            self.put(win, 1, 1, "Escaneando... (ver mensajes abajo)" if self.phase == "scanning" else "Sin ficheros")
        for r in range(vis):
            i = self.top + r
            if i >= total:
                break
            text, attr = self.row(self.jobs[i], iw)
            if i == self.cur:
                attr |= curses.A_REVERSE if self.focus == 0 else curses.A_BOLD
            self.put(win, 1 + r, 1, text, attr, iw)
        if total > vis:   # barra de scroll
            bar_h = max(1, vis * vis // total)
            bar_y = (vis - bar_h) * self.top // max(1, total - vis)
            for r in range(bar_h):
                self.put(win, 1 + bar_y + r, W - 1, "┃", curses.color_pair(4))

        # --- panel inferior: log/resumen (2/3) + sistema (1/3) ---
        show_sys = W >= 80
        lw = W * 2 // 3 if show_sys else W
        liw = lw - 2
        title, lines, is_log = self.bottom_content()
        vh = log_h - 2
        maxstart = max(0, len(lines) - vh)
        if self.scroll is None:
            start = maxstart
        else:
            self.scroll = min(max(0, self.scroll), maxstart)
            start = self.scroll
        suffix = "" if self.scroll is None else "  [↑ scroll — End: seguir]"
        win2 = self.panel(1 + list_h, 0, log_h, lw, title + suffix, self.focus == 1)
        for r in range(vh):
            if start + r >= len(lines):
                break
            self.put(win2, 1 + r, 1, lines[start + r].expandtabs(4), 0, liw)
        if len(lines) > vh:
            bar_h = max(1, vh * vh // len(lines))
            bar_y = (vh - bar_h) * start // max(1, maxstart)
            for r in range(bar_h):
                self.put(win2, 1 + bar_y + r, lw - 1, "┃", curses.color_pair(4))
        win3 = None
        if show_sys:
            win3 = self.panel(1 + list_h, lw, log_h, W - lw, "Sistema", False)
            self.draw_stats(win3, vh, W - lw - 2)

        # --- pie ---
        if self.msg and time.time() < self.msg_until:
            foot, fattr = " " + self.msg, curses.color_pair(2) | curses.A_BOLD
        else:
            foot = (" Tab panel │ ↑↓ PgUp/PgDn mover │ espacio incluir │ s iniciar │ p pausa │ "
                    "x cancelar │ r reintentar │ l log/resumen │ o carpeta │ ? ayuda │ q salir")
            fattr = curses.A_DIM
        self.put(scr, H - 1, 0, foot.ljust(W), fattr)
        scr.noutrefresh()
        win.noutrefresh()
        win2.noutrefresh()
        if win3:
            win3.noutrefresh()
        curses.doupdate()

    def draw_stats(self, win, vh, iw):
        m = self.mon

        def col(p):
            return curses.color_pair(1 if p < 60 else 2 if p < 85 else 3)

        def gauge(label, pct, extra=""):
            tail = f" {pct:3.0f}%"
            gw = max(4, iw - len(label) - len(tail))
            n = int(round(gw * pct / 100))
            return label, "█" * n + "░" * (gw - n), tail + extra, col(pct)

        rows = []   # (texto_izq, barra, cola, atributo)
        if m.cpu is not None:
            rows.append(gauge("CPU ", m.cpu))
        else:
            rows.append(("CPU ", "", " midiendo...", 0))
        if m.mem_total:
            rows.append(gauge("RAM ", 100.0 * m.mem_used / m.mem_total))
        if m.disk_total:
            rows.append(gauge("DSK ", 100.0 * m.disk_used / m.disk_total))
        y = 1
        for label, bar, tail, attr in rows:
            if y > vh:
                break
            self.put(win, y, 1, label, curses.A_BOLD, iw)
            self.put(win, y, 1 + len(label), bar, attr, iw)
            self.put(win, y, 1 + len(label) + len(bar), tail, 0, iw)
            y += 1

        info = []
        if m.io_r is not None:
            info.append(f"E/S disco  ↓{fmt_size(int(m.io_r))}/s ↑{fmt_size(int(m.io_w))}/s")
        elif not m.linux:
            info.append("E/S disco  n/d (solo Linux)")
        if m.hb_cpu is not None:
            info.append(f"HandBrake  {m.hb_cpu:.0f}% CPU · {fmt_size(m.hb_rss or 0)}")
        else:
            info.append("HandBrake  inactivo")
        if m.cpu_hist:
            spark = "▁▂▃▄▅▆▇█"
            h = list(m.cpu_hist)[-iw:]
            info.append("".join(spark[min(7, int(v / 12.5))] for v in h))
        if m.iowait is not None:
            info.append(f"iowait {m.iowait:.1f}%   carga {m.load[0]:.2f} {m.load[1]:.2f} {m.load[2]:.2f}")
        else:
            info.append(f"carga {m.load[0]:.2f} {m.load[1]:.2f} {m.load[2]:.2f}")
        if m.mem_total:
            sw = f" · swap {fmt_size(m.swap_used)}" if m.swap_total and m.swap_used else ""
            info.append(f"RAM {fmt_size(m.mem_used)}/{fmt_size(m.mem_total)}{sw}")
        if m.disk_total:
            info.append(f"Disco {fmt_size(m.disk_used)}/{fmt_size(m.disk_total)}, libre {fmt_size(m.disk_total - m.disk_used)}")
            info.append(f"({m.disk_path})")
        for t in info:
            if y > vh:
                break
            self.put(win, y, 1, t, curses.A_DIM if t.startswith("(") else 0, iw)
            y += 1

    def draw_help(self, scr):
        H, W = scr.getmaxyx()
        scr.erase()
        lines = [
            "AYUDA — iso2appletv", "",
            "Tab / Shift+Tab   cambiar entre el panel de ficheros y el de log/resumen",
            "↑ ↓ PgUp PgDn     mover el cursor (lista) o desplazar el texto (log)",
            "Home / End        primero/último (lista) · inicio/seguir final (log)",
            "espacio           incluir/omitir un fichero pendiente",
            "a                 incluir/omitir todos los pendientes",
            "s                 iniciar la conversión de la cola",
            "p                 pausar/reanudar el HandBrakeCLI en curso",
            "x                 cancelar el fichero en curso (pasa al siguiente)",
            "r                 reencolar el fichero bajo el cursor (error/cancelado/hecho)",
            "l                 alternar log ↔ resumen del fichero bajo el cursor",
            "g                 ir al fichero que se está convirtiendo",
            "o                 cambiar la carpeta de salida (se guarda en la configuración)",
            "q                 salir (pide confirmación si hay una conversión en curso)", "",
            "Los ficheros ya existentes en la carpeta de salida aparecen como convertidos",
            "(hora = fecha de modificación). Los resúmenes y logs se guardan en la carpeta de",
            f"salida ({STATE_FILE} y {LOG_DIR}/). Configuración: {CONFIG_FILE}", "",
            "Pulsa cualquier tecla para volver.",
        ]
        for i, ln in enumerate(lines[:H - 1]):
            self.put(scr, i, 2, ln, curses.A_BOLD if i == 0 else 0)
        scr.refresh()

    # ---- entrada ----
    def page(self, scr):
        H, _ = scr.getmaxyx()
        body = H - 2
        log_h = max(6, body // 3)
        return (body - log_h - 2) if self.focus == 0 else (log_h - 2)

    def key(self, scr, k):
        if self.help:
            self.help = False
            return
        if k in (9, curses.KEY_BTAB):
            self.focus ^= 1
        elif k in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE,
                   curses.KEY_HOME, curses.KEY_END):
            self.navigate(scr, k)
        elif k == ord(" ") and self.jobs:
            j = self.jobs[self.cur]
            if j.status == "pending":
                j.enabled = not j.enabled
            if self.focus == 0 and self.cur < len(self.jobs) - 1:
                self.cur += 1
                self.scroll = None
        elif k == ord("a"):
            pend = [j for j in self.jobs if j.status == "pending"]
            val = not all(j.enabled for j in pend)
            for j in pend:
                j.enabled = val
        elif k == ord("s"):
            if self.phase in ("ready", "finished"):
                self.start()
        elif k == ord("p"):
            self.toggle_pause()
        elif k == ord("x"):
            self.cancel_running()
        elif k == ord("r") and self.jobs:
            self.retry(self.jobs[self.cur])
        elif k == ord("l"):
            self.alt_view = not self.alt_view
            self.scroll = None
        elif k == ord("o"):
            self.prompt_output_dir(scr)
        elif k == ord("g"):
            for i, j in enumerate(self.jobs):
                if j.status == "running":
                    self.cur = i
                    self.scroll = None
        elif k in (ord("?"), ord("h"), curses.KEY_F1):
            self.help = True
        elif k in (ord("q"), 27):
            if self.worker and self.worker.is_alive():
                self.say("¿Cancelar la conversión en curso y salir? (s/N)", 60)
                self.draw(scr)
                scr.timeout(-1)
                c = scr.getch()
                scr.timeout(250)
                self.msg = ""
                if c not in (ord("s"), ord("S"), ord("y"), ord("Y")):
                    return
            self.shutdown()

    def navigate(self, scr, k):
        pg = max(1, self.page(scr))
        if self.focus == 0 and self.jobs:
            old = self.cur
            if k == curses.KEY_UP:
                self.cur -= 1
            elif k == curses.KEY_DOWN:
                self.cur += 1
            elif k == curses.KEY_PPAGE:
                self.cur -= pg
            elif k == curses.KEY_NPAGE:
                self.cur += pg
            elif k == curses.KEY_HOME:
                self.cur = 0
            elif k == curses.KEY_END:
                self.cur = len(self.jobs) - 1
            self.cur = min(max(self.cur, 0), len(self.jobs) - 1)
            if self.cur != old:
                self.scroll, self.alt_view = None, False
        elif self.focus == 1:
            _, lines, _ = self.bottom_content()
            maxstart = max(0, len(lines) - pg)
            cur = maxstart if self.scroll is None else self.scroll
            if k == curses.KEY_UP:
                cur -= 1
            elif k == curses.KEY_DOWN:
                cur += 1
            elif k == curses.KEY_PPAGE:
                cur -= pg
            elif k == curses.KEY_NPAGE:
                cur += pg
            elif k == curses.KEY_HOME:
                cur = 0
            elif k == curses.KEY_END:
                cur = maxstart
            self.scroll = None if cur >= maxstart else max(0, cur)

    def run(self, scr):
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)
        scr.keypad(True)
        scr.timeout(250)
        threading.Thread(target=self.scan_all, daemon=True).start()
        while not self.quit:
            if self.help:
                self.draw_help(scr)
            else:
                self.draw(scr)
            k = scr.getch()
            if k != -1 and k != curses.KEY_RESIZE:
                self.key(scr, k)


def check_deps() -> int:
    ok = True
    for name, found in (
        (HB, shutil.which(HB) is not None),
        ("libdvdcss", "libdvdcss" in subprocess.run(["ldconfig", "-p"], capture_output=True, text=True).stdout
         if shutil.which("ldconfig") else None),
        ("libbluray", "libbluray" in subprocess.run(["ldconfig", "-p"], capture_output=True, text=True).stdout
         if shutil.which("ldconfig") else None),
        ("libaacs", "libaacs" in subprocess.run(["ldconfig", "-p"], capture_output=True, text=True).stdout
         if shutil.which("ldconfig") else None),
    ):
        print(f"   [{'OK' if found else '??' if found is None else 'FALTA'}]  {name}")
        ok &= found is not False
    print("\nUbuntu: sudo apt install handbrake-cli libdvd-pkg libbluray2 libaacs0 "
          "&& sudo dpkg-reconfigure libdvd-pkg")
    print("Blu-ray cifrado: necesita ~/.config/aacs/KEYDB.cfg")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description="TUI para convertir ISOs de DVD/Blu-ray a .m4v para Apple TV con HandBrakeCLI.")
    ap.add_argument("isos", nargs="*", metavar="imagen.iso")
    ap.add_argument("-o", "--output", default=None, help="directorio de salida solo para esta ejecución (la carpeta habitual se cambia con la tecla o)")
    ap.add_argument("-d", "--min-duration", type=int, default=300, metavar="SEG",
                    help="duración mínima de un título (def: 300)")
    ap.add_argument("-q", "--quality", type=int, default=20, metavar="RF",
                    help="calidad x264, menor = mejor (def: 20)")
    ap.add_argument("-t", "--titles", default="", metavar="LISTA", help="solo estos títulos (ej: 1,3,5)")
    ap.add_argument("-c", "--chapters", action="store_true", help="un archivo por capítulo")
    ap.add_argument("-y", "--yes", action="store_true", help="iniciar la conversión sin esperar a pulsar s")
    ap.add_argument("-D", "--check", action="store_true", help="comprobar dependencias y salir")
    args = ap.parse_args()

    if args.check:
        sys.exit(check_deps())
    if not args.isos:
        ap.error("indica al menos una imagen .iso")
    if not shutil.which(HB):
        sys.exit(f"Error: no se encuentra {HB} (ejecuta con -D para ver las dependencias)")
    if not sys.stdout.isatty():
        sys.exit("Error: se necesita un terminal interactivo")

    locale.setlocale(locale.LC_ALL, "")
    app = App(args)
    try:
        curses.wrapper(app.run)
    except KeyboardInterrupt:
        app.shutdown()
    app.shutdown()
    done = sum(1 for j in app.jobs if j.status == "done")
    fail = sum(1 for j in app.jobs if j.status == "failed")
    print(f"{done}/{len(app.jobs)} convertidos, {fail} con error.")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
