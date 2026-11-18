# flash-timing

Times flash programming of a CC1312R1: erase the sectors an image covers, program, verify. It
compares a probe running Verge with SEGGER's Flasher ARM. Results are in `results/flash-timing/`.

It builds against verge-probe, cloned beside this repository as `../verge-probe`. The first build
fetches the probe-rs fork pinned in `Cargo.toml`. In this directory:

    cargo build --release
    arm-none-eabi-objcopy -O ihex \
        <mculink-verge>/tools/swo_target/build/itm_counter.elf itm_counter.hex
    uv run make_images.py itm_counter.hex itm_counter_fill344k.hex
    ./ours_sweep.sh <serial of an MCU-Link Pro running Verge> jtag oscan1

Or one image at one clock, with the tool alone:

    target/release/flash-timing --probe 1fc9:0143:<serial> --protocol cjtag \
        --cjtag-format oscan2 --image itm_counter.hex --speeds 10000 --label test \
        --json runs/test.json

Run one sweep at a time, with nothing else using the bench. Each writes its result file at the
start and again as it goes, so a sweep that stops keeps what it measured.

- `src/main.rs`: through probe-rs, as a library. Each run opens the probe, attaches, downloads with
  verify (read back over the wire), and resets the core. Each phase is timed from probe-rs's
  progress events. cJTAG needs a probe running Verge. `--cjtag-format` picks the scan format to
  connect in, OScan1 when left out.
- `ours_sweep.sh`: runs the tool on an MCU-Link Pro running Verge, over JTAG, then over each cJTAG
  format alone. It takes the Pro's serial, then the formats to run. `VERGE` and `TOOL` name the
  binaries when `verge` is not on the PATH or flash-timing is not built here.
- `make_images.py`: builds the second test image from the first, `itm_counter.hex`, which is
  mculink-verge's `tools/swo_target` build as Intel HEX. The second adds seeded random data from
  0x2000 to 0x56000.
- `flasher/jflash_sweep.py`: J-Flash production programming on the PC, one J-Flash process a run.
  Reports J-Flash's own "Completed after" time. `--flasher-serial` selects the Flasher.
- `flasher/standalone_sweep.py`: the Flasher's stand-alone mode. J-Flash downloads the project and
  image into the Flasher. Each run is the Flasher's `#AUTO`, sent over its virtual COM port, and
  reports the Flasher's own `#OK (Total, Erase, Prog, Verify)`. `--flasher-serial` selects the
  Flasher and finds its port.
- `flasher/jflash/`: the J-Flash projects, saved from the J-Flash GUI. The CRC and readback
  variants differ only in `VerifyType`. Stand-alone mode needs the detailed JTAG chain they carry.

No host may erase the whole chip. The last sector, 0x56000, holds the CCFG, and an erased CCFG
turns the debug port off from the next reset. The images stop below it. The projects use erase
type "Sectors". The sweeps ran on copies that select sectors 0-42 only, made per clock:

    sed -e "s/^  Speed0 = [0-9]*/  Speed0 = $khz/" -e "s/^  Speed1 = [0-9]*/  Speed1 = $khz/" \
        -e 's/^  aRangeSel\[1\] = 0-43/  aRangeSel[1] = 0-42/' cc1312-$ifc.jflash > runs/$ifc-$khz.jflash

The project files use CRLF line endings, so match the sector range without `$`. Check every copy
selects 0-42 before running.

`ours_sweep.sh` reads the images and writes `runs/` beside itself, in this directory. The Flasher
sweeps read the images and `jflash/runs*/` beside themselves, in `flasher/`: copy the images there
too, or give `--images` paths to them. Their `--out` is relative to the directory they run from.
