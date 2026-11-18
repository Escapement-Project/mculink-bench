//! How fast are this probe's debug wires, end to end through probe-rs?
//!
//! The question behind it is whether a firmware change slowed the probe down, and "slower" has two
//! separate shapes. A change to the bit loop costs per byte; a change to command dispatch or the USB
//! path costs per transfer, whatever its size. One throughput figure cannot tell them apart, so every
//! arm runs at several transfer sizes and the time is fit to `a + b * bytes`: `a` is the fixed cost of
//! a transfer and `1/b` is the ceiling a large one approaches.
//!
//! Sweeping the SWD clock on top of that says which of the two is actually binding. Throughput that
//! stops rising with the clock is bound by something off the wire, and a slower bit loop would then
//! cost nothing at all.
//!
//! A clock is worth its figure only if the data comes back right, which probe-rs does not check.
//! So each clock also writes a pattern to RAM and reads it back, and reads the target's flash
//! against a reference from the slowest clock.

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::time::{Duration, Instant};

use anyhow::Context as _;
use indicatif::ProgressStyle;
use probe_rs::probe::{
    DebugProbeSelector, ProbeTransport, WireProtocol, cmsisdap::ScanFormat, list::Lister,
};
use probe_rs::{MemoryInterface, Permissions, Session, config::TargetSelector};
use tracing::{Span, info, info_span, warn};
use tracing_indicatif::IndicatifLayer;
use tracing_indicatif::span_ext::IndicatifSpanExt as _;
use tracing_subscriber::prelude::*;

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug)]
enum Arm {
    Read32,
    Write32,
    Read8,
    Write8,
}

impl Arm {
    const ALL: [Arm; 4] = [Arm::Read32, Arm::Write32, Arm::Read8, Arm::Write8];

    fn name(self) -> &'static str {
        match self {
            Arm::Read32 => "read32",
            Arm::Write32 => "write32",
            Arm::Read8 => "read8",
            Arm::Write8 => "write8",
        }
    }

    /// Clocks one word costs on an idealised SWD wire: an 8-bit request, the turnarounds and
    /// acknowledge, 32 data bits and parity. The probe is free to add idle cycles, so a measured
    /// figure at or near this ceiling means the wire is the limit and nothing else can be.
    const CLOCKS_PER_WORD: f64 = 46.0;
}

#[derive(Clone, Copy, PartialEq, Eq, Debug, Default, clap::ValueEnum)]
enum Protocol {
    #[default]
    Swd,
    Jtag,
    Cjtag,
}

impl Protocol {
    fn wire_protocol(self) -> WireProtocol {
        match self {
            Self::Swd => WireProtocol::Swd,
            Self::Jtag => WireProtocol::Jtag,
            Self::Cjtag => WireProtocol::CJtag,
        }
    }
}

/// A scan format to hold cJTAG to.
#[derive(Clone, Copy, PartialEq, Eq, Debug, clap::ValueEnum)]
enum CjtagFormat {
    Oscan1,
    Oscan2,
    Oscan5,
    Oscan6,
}

impl CjtagFormat {
    fn scan_format(self) -> ScanFormat {
        match self {
            Self::Oscan1 => ScanFormat::OScan1,
            Self::Oscan2 => ScanFormat::OScan2,
            Self::Oscan5 => ScanFormat::OScan5,
            Self::Oscan6 => ScanFormat::OScan6,
        }
    }
}

#[derive(clap::Parser)]
#[command(
    about = "Probe throughput over SWD, JTAG or cJTAG, decomposed into fixed and per-byte cost"
)]
struct Cli {
    /// Chip to attach to. The vendor's own debug sequence runs, as it would in normal use.
    #[arg(long, default_value = "MSPM0L1306")]
    chip: String,

    /// Probe selector, `VID:PID` or `VID:PID:SERIAL`. The only probe found, if left out.
    #[arg(long)]
    probe: Option<String>,

    /// Debug wire protocol to select. cJTAG needs an MCU-Link running Verge.
    #[arg(long, value_enum, default_value_t = Protocol::Swd)]
    protocol: Protocol,

    /// With cJTAG, the scan format to connect in. OScan1 when left out. Nothing checks that the
    /// target has the format.
    #[arg(long, value_enum)]
    cjtag_format: Option<CjtagFormat>,

    /// Wire clock speeds to sweep, in kHz.
    #[arg(
        long,
        value_delimiter = ',',
        default_value = "1000,2000,5000,10000,20000,30000,50000"
    )]
    speeds: Vec<u32>,

    /// Transfer sizes to measure at each speed, in bytes, each a whole number of 32-bit words. Two
    /// or more are needed for the fit.
    #[arg(long, value_delimiter = ',', default_value = "64,256,1024,4096")]
    sizes: Vec<usize>,

    /// Timed runs per point. The median is reported.
    #[arg(long, default_value_t = 7)]
    repeats: usize,

    /// RAM to transfer against. Written to and restored before exit.
    #[arg(long, value_parser = parse_hex, default_value = "0x20000000")]
    base: u64,

    /// Single-word reads to time per speed, for the round-trip figure.
    #[arg(long, default_value_t = 200)]
    round_trips: usize,

    /// Write the per-point rows here as CSV.
    #[arg(long)]
    csv: Option<String>,

    /// Leave the core running. It is halted by default, so the target's own bus traffic does not
    /// land in the measurement.
    #[arg(long)]
    no_halt: bool,

    /// Rounds of each check at every clock that measured: a pattern written to the RAM under test
    /// and read back `--ram-reads` times, and the start of the target's flash compared with a
    /// reference read twice at the slowest clock. The flash check runs only on flash with varied
    /// content. 0 leaves the checks out.
    #[arg(long, default_value_t = 10)]
    checks: usize,

    /// Reads of each pattern a RAM check round makes.
    #[arg(long, default_value_t = 16)]
    ram_reads: usize,

    /// Flash each read check covers, in bytes, up to the size of the first flash region.
    #[arg(long, default_value_t = 65536)]
    check_bytes: usize,

    /// Write the checks' results here as CSV.
    #[arg(long)]
    checks_csv: Option<String>,

    /// Print every probe this probe-rs finds, one a line, tab-separated: VID:PID:serial,
    /// identifier, HID or bulk; then exit.
    #[arg(long)]
    list: bool,
}

fn parse_hex(text: &str) -> Result<u64, std::num::ParseIntError> {
    match text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
        Some(hex) => u64::from_str_radix(hex, 16),
        None => text.parse(),
    }
}

/// How many rounds of one check at one clock came back right, and the error that ended the
/// rounds early, if one did.
#[derive(Default)]
struct Checked {
    right: usize,
    error: Option<String>,
}

impl Checked {
    fn failed(error: &anyhow::Error) -> Self {
        Self {
            right: 0,
            error: Some(format!("{error:#}")),
        }
    }
}

/// The flash the flash check compares against.
struct Reference {
    address: u64,
    words: Vec<u32>,
}

/// One timed point: an arm at one size and one clock.
struct Point {
    speed_khz: u32,
    arm: Arm,
    bytes: usize,
    median: Duration,
}

impl Point {
    fn bytes_per_second(&self) -> f64 {
        self.bytes as f64 / self.median.as_secs_f64()
    }
}

fn main() -> anyhow::Result<()> {
    let cli = <Cli as clap::Parser>::parse();
    anyhow::ensure!(
        cli.cjtag_format.is_none() || cli.protocol == Protocol::Cjtag,
        "--cjtag-format needs --protocol cjtag"
    );
    if cli.list {
        for probe in Lister::new().list_all() {
            let ProbeTransport::Usb(usb) = &probe.transport;
            println!(
                "{:04x}:{:04x}:{}\t{}\t{}",
                usb.vendor_id,
                usb.product_id,
                usb.serial_number.as_deref().unwrap_or(""),
                probe.identifier,
                if usb.is_hid_interface { "hid" } else { "bulk" },
            );
        }
        return Ok(());
    }
    anyhow::ensure!(cli.sizes.len() >= 2, "two sizes or more, for the fit");
    anyhow::ensure!(
        cli.sizes.iter().all(|&bytes| bytes > 0 && bytes % 4 == 0),
        "--sizes are whole 32-bit words: give multiples of 4 bytes, from 4"
    );
    anyhow::ensure!(cli.repeats > 0, "at least one timed run");
    anyhow::ensure!(
        cli.round_trips > 0,
        "--round-trips needs at least one read to time"
    );
    anyhow::ensure!(
        cli.checks == 0 || cli.ram_reads > 0,
        "--ram-reads 0 would pass every RAM check without a read: give 1 or more, or --checks 0"
    );

    let indicatif = IndicatifLayer::new();
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::fmt::layer()
                .without_time()
                .with_target(false)
                .with_writer(indicatif.get_stderr_writer()),
        )
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn,swd_throughput=info")),
        )
        .with(indicatif)
        .init();

    let largest = *cli.sizes.iter().max().expect("checked non-empty");
    let mut points = Vec::new();
    let mut round_trips = BTreeMap::new();
    let mut achieved = BTreeMap::new();
    let mut failed = BTreeMap::new();
    let mut checks = BTreeMap::new();

    anyhow::ensure!(
        cli.check_bytes >= 4,
        "--check-bytes covers at least one word"
    );
    let flash = if cli.checks == 0 {
        Err("--checks 0".to_owned())
    } else {
        reference(&cli).map_err(|error| format!("{error:#}"))
    };
    if let Err(reason) = &flash
        && cli.checks > 0
    {
        warn!("no flash check: {reason}");
    }

    let per_speed = (cli.sizes.len() * Arm::ALL.len() + 1) as u64;
    let sweep = info_span!("sweep");
    sweep.pb_set_style(&bar_style(
        "{spinner} {msg} {wide_bar} {pos}/{len} {elapsed}",
    )?);
    sweep.pb_set_length(cli.speeds.len() as u64 * per_speed);
    let sweep = sweep.entered();

    for &speed_khz in &cli.speeds {
        Span::current().pb_set_message(&format!("{speed_khz} kHz"));
        // One session per speed. Setting the clock before attaching means the whole connection,
        // including the vendor's sequence, runs at the speed being reported.
        // A speed the wire cannot carry fails at attach. That is a result about this clock, not a
        // reason to discard the speeds already measured, so the sweep records it and carries on.
        let (mut session, actual) = match attach(&cli, speed_khz) {
            Ok(session) => session,
            Err(error) => {
                warn!("{speed_khz} kHz: no attach ({error:#})");
                failed.insert(speed_khz, format!("no attach: {error:#}"));
                Span::current().pb_inc(per_speed);
                continue;
            }
        };
        achieved.insert(speed_khz, actual);
        if actual != speed_khz {
            warn!("asked for {speed_khz} kHz, probe reports {actual} kHz");
        }

        // A transfer that fails at this clock is a result about the clock as well, and the speeds
        // measured already stand.
        match measure_speed(&cli, &mut session, speed_khz, largest) {
            Ok((measured, per_access)) => {
                points.extend(measured);
                round_trips.insert(speed_khz, per_access);
                if cli.checks > 0 {
                    let ram = check_ram(&cli, &mut session, largest);
                    let read = flash
                        .as_ref()
                        .ok()
                        .map(|reference| check_flash(&cli, &mut session, reference));
                    info!(
                        "{speed_khz:>6} kHz checks: ram {}/{n}{}",
                        ram.right,
                        read.as_ref()
                            .map(|c| format!(", flash {}/{}", c.right, cli.checks))
                            .unwrap_or_default(),
                        n = cli.checks,
                    );
                    checks.insert(speed_khz, (ram, read));
                }
            }
            Err(error) => {
                warn!("{speed_khz} kHz: failed ({error:#})");
                failed.insert(speed_khz, format!("{error:#}"));
            }
        }
        Span::current().pb_inc(per_speed);
    }

    drop(sweep);

    print!("{}", report(&cli, &points, &round_trips, &achieved));
    for (speed_khz, error) in &failed {
        println!("{speed_khz} kHz failed: {error}");
    }
    if cli.checks > 0 {
        let flash_bytes = flash.as_ref().map_or(0, |r| r.words.len() * 4);
        println!(
            "\nchecks, rounds right of {}: {largest} B written to RAM and read back {} times; {}",
            cli.checks,
            cli.ram_reads,
            match &flash {
                Ok(_) => format!("{} KiB of flash read", flash_bytes / 1024),
                Err(reason) => format!("no flash check ({reason})"),
            }
        );
        for (speed_khz, (ram, read)) in &checks {
            println!(
                "{speed_khz:>8} kHz ram {:>3}{}{}",
                ram.right,
                read.as_ref()
                    .map(|c| format!(" flash {:>3}", c.right))
                    .unwrap_or_default(),
                std::iter::once(ram)
                    .chain(read.as_ref())
                    .filter_map(|c| c.error.as_deref())
                    .map(|e| format!("; stopped: {e}"))
                    .collect::<String>()
            );
        }
        if let Some(path) = &cli.checks_csv {
            // bytes is what a round reads. A check that never ran has a row at clock 0 saying why.
            let field = |text: &str| format!("\"{}\"", text.replace('"', "\"\""));
            let mut csv = String::from("speed_khz,probe_khz,check,bytes,rounds,right,error\n");
            if let Err(reason) = &flash {
                writeln!(csv, "0,0,flash,0,0,0,{}", field(reason))?;
            }
            for (speed_khz, (ram, read)) in &checks {
                let rows = std::iter::once(("ram", largest * cli.ram_reads, ram))
                    .chain(read.as_ref().map(|c| ("flash", flash_bytes, c)));
                for (check, bytes, checked) in rows {
                    writeln!(
                        csv,
                        "{speed_khz},{},{check},{bytes},{},{},{}",
                        achieved[speed_khz],
                        cli.checks,
                        checked.right,
                        field(checked.error.as_deref().unwrap_or("")),
                    )?;
                }
            }
            std::fs::write(path, csv)?;
            eprintln!("wrote {path}");
        }
    }

    if let Some(path) = &cli.csv {
        // probe_khz is the clock the probe took, which a divider rounds down from the one asked.
        // The rt rows are single-word reads timed one at a time.
        let mut csv = String::from("speed_khz,probe_khz,arm,bytes,median_us,bytes_per_second\n");
        for point in &points {
            writeln!(
                csv,
                "{},{},{},{},{:.3},{:.1}",
                point.speed_khz,
                achieved[&point.speed_khz],
                point.arm.name(),
                point.bytes,
                point.median.as_secs_f64() * 1e6,
                point.bytes_per_second(),
            )?;
        }
        for (speed_khz, per_access) in &round_trips {
            writeln!(
                csv,
                "{},{},rt,4,{:.3},{:.1}",
                speed_khz,
                achieved[speed_khz],
                per_access.as_secs_f64() * 1e6,
                4.0 / per_access.as_secs_f64(),
            )?;
        }
        // A clock that never attached has no probe clock: 0.
        for speed_khz in failed.keys() {
            let probe_khz = achieved.get(speed_khz).copied().unwrap_or(0);
            writeln!(csv, "{speed_khz},{probe_khz},failed,0,0,0")?;
        }
        std::fs::write(path, csv)?;
        eprintln!("wrote {path}");
    }

    Ok(())
}

/// Every arm, then the round trips, at the clock `session` was attached at.
fn measure_speed(
    cli: &Cli,
    session: &mut Session,
    speed_khz: u32,
    largest: usize,
) -> anyhow::Result<(Vec<Point>, Duration)> {
    check_fits_in_ram(session, cli.base, largest)?;
    with_ram(cli, session, largest, |core| run_arms(cli, core, speed_khz))
}

/// Runs `work` on core 0, halted unless `--no-halt`, with `bytes` of the RAM under test saved
/// first. The RAM is put back and the core resumed even when `work` fails, so a failed clock leaves
/// the target as the sweep found it. The save is read twice and must agree, so that a clock that
/// misreads does not write its misread back.
fn with_ram<T>(
    cli: &Cli,
    session: &mut Session,
    bytes: usize,
    work: impl FnOnce(&mut probe_rs::Core<'_>) -> anyhow::Result<T>,
) -> anyhow::Result<T> {
    let mut core = session.core(0)?;
    if !cli.no_halt {
        core.halt(Duration::from_millis(500))?;
    }
    let saved = (|| {
        let mut original = vec![0u8; bytes];
        let mut again = vec![0u8; bytes];
        if bytes > 0 {
            core.read(cli.base, &mut original)?;
            core.read(cli.base, &mut again)?;
        }
        // A running core may change its RAM between the reads; a halted one may not.
        anyhow::ensure!(cli.no_halt || original == again, "two reads differ");
        anyhow::Ok(original)
    })()
    .with_context(|| format!("saving {bytes} bytes at {:#x}", cli.base));
    let original = match saved {
        Ok(original) => original,
        Err(error) => {
            if !cli.no_halt {
                let _ = core.run();
            }
            return Err(error);
        }
    };

    let done = work(&mut core);
    // After a failure these can fail as well, on the same wire; the first error is the one to report.
    let restored = if bytes > 0 {
        core.write(cli.base, &original)
    } else {
        Ok(())
    };
    let resumed = if cli.no_halt { Ok(()) } else { core.run() };
    let done = done?;
    restored?;
    resumed?;
    Ok(done)
}

/// The start of the target's first flash region, read twice at the slowest clock of the sweep with
/// the core halted. Erased flash reads the same whichever word a misread returns, so flash with
/// too few distinct words is refused.
fn reference(cli: &Cli) -> anyhow::Result<Reference> {
    let slowest = *cli.speeds.iter().min().context("no speeds")?;
    let (mut session, _) = attach(cli, slowest)?;
    let flash = session
        .target()
        .memory_map
        .iter()
        .filter_map(|region| match region {
            probe_rs::config::MemoryRegion::Nvm(nvm) => Some(nvm.range.clone()),
            _ => None,
        })
        .min_by_key(|range| range.start)
        .context("the target has no flash region")?;
    let bytes = (cli.check_bytes as u64).min(flash.end - flash.start) as usize;
    let (first, second) = with_ram(cli, &mut session, 0, |core| {
        let mut first = vec![0u32; bytes / 4];
        let mut second = vec![0u32; bytes / 4];
        core.read_32(flash.start, &mut first)?;
        core.read_32(flash.start, &mut second)?;
        Ok((first, second))
    })?;
    anyhow::ensure!(
        first == second,
        "two reads of {bytes} bytes at {slowest} kHz differ"
    );
    let distinct = first
        .iter()
        .collect::<std::collections::BTreeSet<_>>()
        .len();
    anyhow::ensure!(
        distinct >= 64,
        "the first {bytes} bytes of flash hold {distinct} distinct words, too few to show a misread"
    );
    Ok(Reference {
        address: flash.start,
        words: first,
    })
}

/// A progress bar over one clock's check rounds.
fn check_bar(name: &str, rounds: usize) -> Span {
    let run = info_span!("check");
    if let Ok(style) = bar_style("  {msg} {wide_bar} {pos}/{len}") {
        run.pb_set_style(&style);
    }
    run.pb_set_message(name);
    run.pb_set_length(rounds as u64);
    run
}

/// Rounds of a pattern written to the RAM under test and read back `--ram-reads` times, at the
/// clock `session` was attached at. A round is right if every read matches.
fn check_ram(cli: &Cli, session: &mut Session, bytes: usize) -> Checked {
    let _bar = check_bar("ram", cli.checks).entered();
    with_ram(cli, session, bytes, |core| {
        let mut checked = Checked::default();
        let mut back = vec![0u32; bytes / 4];
        for round in 0..cli.checks {
            let written = pattern(bytes / 4, round);
            let result = (|| {
                core.write_32(cli.base, &written)?;
                let mut right = true;
                for _ in 0..cli.ram_reads {
                    // A read that returned without filling the buffer must not pass on old words.
                    for (word, expected) in back.iter_mut().zip(&written) {
                        *word = !expected;
                    }
                    core.read_32(cli.base, &mut back)?;
                    right &= back == written;
                }
                anyhow::Ok(right)
            })();
            match result {
                Ok(right) => checked.right += usize::from(right),
                Err(error) => {
                    checked.error = Some(format!("{error:#}"));
                    break;
                }
            }
            Span::current().pb_inc(1);
        }
        Ok(checked)
    })
    .unwrap_or_else(|error| Checked::failed(&error))
}

/// Rounds of the reference's flash read again with the core halted, at the clock `session` was
/// attached at.
fn check_flash(cli: &Cli, session: &mut Session, reference: &Reference) -> Checked {
    let _bar = check_bar("flash", cli.checks).entered();
    with_ram(cli, session, 0, |core| {
        let mut checked = Checked::default();
        let mut words = vec![0u32; reference.words.len()];
        for _ in 0..cli.checks {
            for (word, expected) in words.iter_mut().zip(&reference.words) {
                *word = !expected;
            }
            if let Err(error) = core.read_32(reference.address, &mut words) {
                checked.error = Some(format!("{error:#}"));
                break;
            }
            checked.right += usize::from(words == reference.words);
            Span::current().pb_inc(1);
        }
        Ok(checked)
    })
    .unwrap_or_else(|error| Checked::failed(&error))
}

/// `words` of xorshift32, a different run for each round.
fn pattern(words: usize, round: usize) -> Vec<u32> {
    let mut state = 0x9E37_79B9u32 ^ (round as u32).wrapping_mul(0x85EB_CA6B);
    (0..words)
        .map(|_| {
            state ^= state << 13;
            state ^= state >> 17;
            state ^= state << 5;
            state
        })
        .collect()
}

fn run_arms(
    cli: &Cli,
    core: &mut probe_rs::Core<'_>,
    speed_khz: u32,
) -> anyhow::Result<(Vec<Point>, Duration)> {
    let mut points = Vec::new();
    for arm in Arm::ALL {
        for &bytes in &cli.sizes {
            let median = time_arm(core, arm, cli.base, bytes, cli.repeats)?;
            let point = Point {
                speed_khz,
                arm,
                bytes,
                median,
            };
            info!(
                "{:>6} kHz {:>7} {:>6} B {:>9.3} ms {:>10.1} KiB/s",
                speed_khz,
                arm.name(),
                bytes,
                median.as_secs_f64() * 1e3,
                point.bytes_per_second() / 1024.0,
            );
            points.push(point);
        }
    }
    let per_access = time_round_trips(core, cli.base, cli.round_trips)?;
    info!(
        "{:>6} kHz {:>7} {:>19.1} us {:>8.0} /s",
        speed_khz,
        "rt",
        per_access.as_secs_f64() * 1e6,
        1.0 / per_access.as_secs_f64(),
    );
    Ok((points, per_access))
}

fn bar_style(template: &str) -> anyhow::Result<ProgressStyle> {
    Ok(ProgressStyle::with_template(template)?)
}

/// Refuses a transfer that would run past the end of the RAM region it starts in.
///
/// Reads past the end of RAM do not fail loudly — the AP answers, the words come back, and the
/// timing is of whatever the bus does with an unmapped address rather than of the probe.
fn check_fits_in_ram(session: &Session, base: u64, bytes: usize) -> anyhow::Result<()> {
    let end = base + bytes as u64;
    let ram = session
        .target()
        .memory_map
        .iter()
        .filter_map(|region| match region {
            probe_rs::config::MemoryRegion::Ram(ram) => Some(&ram.range),
            _ => None,
        })
        .find(|range| range.contains(&base));

    match ram {
        Some(range) if end <= range.end => Ok(()),
        Some(range) => anyhow::bail!(
            "{bytes} bytes at {base:#x} runs past RAM {:#x}..{:#x}; largest size here is {}",
            range.start,
            range.end,
            range.end - base
        ),
        None => anyhow::bail!("{base:#x} is not in any RAM region of this target"),
    }
}

/// Attaches at `speed_khz`, and reports the speed the probe actually took.
fn attach(cli: &Cli, speed_khz: u32) -> anyhow::Result<(Session, u32)> {
    let lister = Lister::new();
    let selector: DebugProbeSelector = match &cli.probe {
        Some(text) => text.parse()?,
        None => {
            let found = lister.list_all();
            let only = match found.as_slice() {
                [one] => one,
                [] => anyhow::bail!("no probe found"),
                many => anyhow::bail!(
                    "{} probes found; pass --probe with one of: {}",
                    many.len(),
                    many.iter()
                        .map(|p| {
                            let ProbeTransport::Usb(usb) = &p.transport;
                            match &usb.serial_number {
                                Some(serial) => {
                                    format!("{:04x}:{:04x}:{serial}", usb.vendor_id, usb.product_id)
                                }
                                None => format!("{:04x}:{:04x}", usb.vendor_id, usb.product_id),
                            }
                        })
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
            };
            only.into()
        }
    };

    let DebugProbeSelector::Usb(usb) = &selector;
    let usb_id = (usb.vendor_id, usb.product_id);
    let mut probe = lister.open(selector)?;
    escapement_verge_probe::prepare_probe(
        &mut probe,
        usb_id,
        cli.protocol.wire_protocol(),
        cli.cjtag_format.map(CjtagFormat::scan_format),
    )?;
    probe.select_protocol(cli.protocol.wire_protocol())?;
    let actual = probe.set_speed(speed_khz)?;
    let session = probe.attach(
        TargetSelector::Unspecified(cli.chip.clone()),
        Permissions::default(),
    )?;
    Ok((session, actual))
}

fn time_arm(
    core: &mut probe_rs::Core<'_>,
    arm: Arm,
    base: u64,
    bytes: usize,
    repeats: usize,
) -> anyhow::Result<Duration> {
    let mut words = vec![0u32; bytes / 4];
    let mut raw = vec![0u8; bytes];

    let run = info_span!("point");
    run.pb_set_style(&bar_style("  {msg} {wide_bar} {pos}/{len}")?);
    run.pb_set_message(&format!("{} {bytes} B", arm.name()));
    run.pb_set_length(repeats as u64 + 1);
    let run = run.entered();

    let mut medians = Vec::with_capacity(repeats);
    // One untimed run first: the first transfer at a new size pays for whatever the AP's address
    // and control registers still hold from the previous one.
    for attempt in 0..=repeats {
        let started = Instant::now();
        match arm {
            Arm::Read32 => core.read_32(base, &mut words)?,
            Arm::Write32 => core.write_32(base, &words)?,
            Arm::Read8 => core.read_8(base, &mut raw)?,
            Arm::Write8 => core.write_8(base, &raw)?,
        }
        let elapsed = started.elapsed();
        if attempt > 0 {
            medians.push(elapsed);
        }
        Span::current().pb_inc(1);
    }
    drop(run);

    medians.sort_unstable();
    Ok(medians[medians.len() / 2])
}

/// Microseconds one single-word read costs, start to finish.
///
/// Nothing can be batched here: each read is a separate call and so a separate round trip. This is
/// the figure a change to command dispatch moves, and a change to the bit loop barely touches.
fn time_round_trips(
    core: &mut probe_rs::Core<'_>,
    base: u64,
    count: usize,
) -> anyhow::Result<Duration> {
    core.read_word_32(base)?;

    let started = Instant::now();
    for _ in 0..count {
        core.read_word_32(base)?;
    }
    Ok(started.elapsed() / count as u32)
}

/// Least squares over the size points: seconds = fixed + per_byte * bytes.
fn fit(points: &[&Point]) -> (f64, f64) {
    let n = points.len() as f64;
    let mean_x = points.iter().map(|p| p.bytes as f64).sum::<f64>() / n;
    let mean_y = points.iter().map(|p| p.median.as_secs_f64()).sum::<f64>() / n;
    let covariance: f64 = points
        .iter()
        .map(|p| (p.bytes as f64 - mean_x) * (p.median.as_secs_f64() - mean_y))
        .sum();
    let variance: f64 = points
        .iter()
        .map(|p| (p.bytes as f64 - mean_x).powi(2))
        .sum();
    let per_byte = covariance / variance;
    (mean_y - per_byte * mean_x, per_byte)
}

fn report(
    cli: &Cli,
    points: &[Point],
    round_trips: &BTreeMap<u32, Duration>,
    achieved: &BTreeMap<u32, u32>,
) -> String {
    let mut out = String::new();

    let _ = writeln!(out, "\nmedian throughput, KiB/s\n");
    let _ = write!(out, "{:>8}", "kHz");
    for arm in Arm::ALL {
        for bytes in &cli.sizes {
            let _ = write!(out, "{:>12}", format!("{} {}", arm.name(), bytes));
        }
    }
    let _ = writeln!(out, "{:>10}", "rt us");

    for speed in &cli.speeds {
        let _ = write!(out, "{speed:>8}");
        for arm in Arm::ALL {
            for bytes in &cli.sizes {
                match points
                    .iter()
                    .find(|p| p.speed_khz == *speed && p.arm == arm && p.bytes == *bytes)
                {
                    Some(point) => {
                        let _ = write!(out, "{:>12.1}", point.bytes_per_second() / 1024.0);
                    }
                    None => {
                        let _ = write!(out, "{:>12}", "-");
                    }
                }
            }
        }
        match round_trips.get(speed) {
            Some(rt) => {
                let _ = writeln!(out, "{:>10.1}", rt.as_secs_f64() * 1e6);
            }
            None => {
                let _ = writeln!(out, "{:>10}", "-");
            }
        }
    }

    let _ = writeln!(
        out,
        "\nfixed cost per transfer and the ceiling a large one approaches\n"
    );
    if cli.protocol == Protocol::Swd {
        let _ = writeln!(
            out,
            "{:>8} {:>8} {:>10} {:>12} {:>12} {:>8}",
            "kHz", "arm", "fixed us", "ceiling KiB/s", "wire KiB/s", "of wire"
        );
    } else {
        let _ = writeln!(
            out,
            "{:>8} {:>8} {:>10} {:>12}",
            "kHz", "arm", "fixed us", "ceiling KiB/s"
        );
    }
    for speed in &cli.speeds {
        // The clock the probe actually took, not the one asked for: a probe that rounds down to a
        // divisor of its own clock would otherwise be charged for the difference.
        let actual = *achieved.get(speed).unwrap_or(speed) as f64;
        let wire = actual * 1e3 / Arm::CLOCKS_PER_WORD * 4.0;
        for arm in Arm::ALL {
            let selected: Vec<&Point> = points
                .iter()
                .filter(|p| p.speed_khz == *speed && p.arm == arm)
                .collect();
            if selected.len() < 2 {
                continue;
            }
            let (fixed, per_byte) = fit(&selected);
            let ceiling = 1.0 / per_byte;
            if cli.protocol == Protocol::Swd {
                let _ = writeln!(
                    out,
                    "{:>8} {:>8} {:>10.1} {:>12.1} {:>12.1} {:>7.0}%",
                    speed,
                    arm.name(),
                    fixed * 1e6,
                    ceiling / 1024.0,
                    wire / 1024.0,
                    ceiling / wire * 100.0,
                );
            } else {
                let _ = writeln!(
                    out,
                    "{:>8} {:>8} {:>10.1} {:>12.1}",
                    speed,
                    arm.name(),
                    fixed * 1e6,
                    ceiling / 1024.0,
                );
            }
        }
    }

    out
}
