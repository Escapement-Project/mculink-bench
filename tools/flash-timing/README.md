# flash-timing

Times flash programming of a CC1312R1: erase the sectors an image covers, program, verify. It
compares a probe running Verge with SEGGER's Flasher ARM. Results are in `results/flash-timing/`.

- `src/main.rs`: through probe-rs, as a library. Each run opens the probe, attaches, downloads with
  verify (read back over the wire), and resets the core. Each phase is timed from probe-rs's
  progress events. cJTAG on a Verge probe goes through verge-probe's vendor commands. Limit the
  scan formats first with `verge cjtag --formats`.
- `ours_sweep.sh`: runs the tool on a Verge probe over JTAG, then over each cJTAG format alone.
  OScan1 is always offered. On exit it offers every format again.
- `make_images.py`: builds the two test images from `itm_counter.hex`, which comes from
  mculink-verge `tools/swo_target`. The second adds seeded random data from 0x2000 to 0x56000.
- `flasher/jflash_sweep.py`: J-Flash production programming on the PC, one J-Flash process a run.
  Reports J-Flash's own "Completed after" time.
- `flasher/standalone_sweep.py`: the Flasher's stand-alone mode. J-Flash downloads the project and
  image into the Flasher. Each run is the Flasher's `#AUTO`, sent over its virtual COM port, and
  reports the Flasher's own `#OK (Total, Erase, Prog, Verify)`.
- `flasher/jflash/`: the J-Flash projects, saved from the J-Flash GUI. The CRC and readback
  variants differ only in `VerifyType`. Stand-alone mode needs the detailed JTAG chain they carry.

No host may erase the whole chip. The last sector, 0x56000, holds the CCFG, and an erased CCFG
turns the debug port off from the next reset. The images stop below it. The projects use erase
type "Sectors". The sweeps ran on copies that select sectors 0-42 only, made per clock:

    sed -e "s/^  Speed0 = [0-9]*/  Speed0 = $khz/" -e "s/^  Speed1 = [0-9]*/  Speed1 = $khz/" \
        -e 's/^  aRangeSel\[1\] = 0-43/  aRangeSel[1] = 0-42/' cc1312-$ifc.jflash > runs/$ifc-$khz.jflash

The project files use CRLF line endings, so match the sector range without `$`. Check every copy
selects 0-42 before running.

The Python sweeps and `ours_sweep.sh` ran from a working directory holding the images,
`jflash/runs/` (or `jflash/runs-readback/`) and `runs/`. They resolve those paths beside
themselves.
