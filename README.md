# transcodex

[Español](README.es.md)

A terminal UI that converts DVD/Blu-ray ISOs and video files into `.m4v` files
that play on Apple TV, using [HandBrakeCLI](https://handbrake.fr/).

> Formerly `iso2appletv`; it still reads the old config and history.
> Not affiliated with the HandBrake project.

## Features

- **Sources:** ISO/IMG images and video files (mkv, mp4, avi, mov, ts, m2ts, wmv, mpg, vob…), single files or whole folders, all with the same options.
- **Apple TV friendly output:** H.264 High@4.1 up to 720p, AC3 audio (copied when already AC3), chapters, faststart.
- **Live queue:** per-file status and finish time, HandBrakeCLI log or per-conversion summary, and a system panel (CPU, RAM, disk).
- **Gentle on your machine:** runs HandBrakeCLI with `nice -n 19` (and `ionice -c3` when available).
- **Safe output:** writes to `.m4v.part` and renames only when the conversion succeeds.
- **Built-in ISO browser** (`i`) and a configurable output folder (`o`).

## Install

### Debian / Ubuntu

Download the `.deb` from the [Releases](../../releases) page and install it:

```bash
sudo apt install ./transcodex_<version>_all.deb
```

It pulls in `handbrake-cli`. For encrypted discs you will also want `libdvd-pkg` and `libbluray`.

### Other systems

`transcodex` is a single Python 3 file with no third-party modules. Install HandBrakeCLI, then run:

```bash
python3 transcodex.py
```

## Usage

```bash
transcodex                          # no arguments: open the browser to pick ISOs
transcodex movie.iso                # open the list and wait for `s` to start
transcodex ~/isos/ -r               # every ISO and video in a folder (and subfolders)
transcodex film.mkv series/         # loose video files or folders with videos
transcodex -y -c a.iso b.iso        # one file per chapter, start right away
```

Keys: `Tab` switches panel, arrows/PgUp/PgDn scroll, `space` include/skip, `s` start,
`p` pause, `x` cancel current, `r` retry, `l` log/summary, `i` add ISOs, `o` output folder, `?` help, `q` quit.

Run `transcodex --help` for all options (`-q` quality, `-t` titles, `-d` minimum title length, `-o` output folder).
`transcodex.sh` is the original script without an interface.

## Build the package

```bash
python3 packaging/build_deb.py --version 1.2.0   # creates dist/transcodex_1.2.0_all.deb
```

## A note on discs

Use transcodex only to make copies of discs and files you own, and check what
your local law allows when a disc is copy-protected.

## License

GPL-3.0 or later. See [LICENSE](LICENSE).
