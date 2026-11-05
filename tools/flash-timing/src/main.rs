//! How long does flashing an image take through a probe, end to end through probe-rs?
//!
//! A programming station flashes one board after another, so every run here does what it does per
//! board: open the probe, attach, erase the sectors the image covers, program, read back to verify,
//! and reset so the image runs. Each phase is timed on its own: a probe that attaches slowly and one
//! that programs slowly need different fixes.
//!
//! Never a chip erase: on the CC13xx an erased CCFG turns the debug port off from the next reset.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::Context as _;
use indicatif::ProgressStyle;
use probe_rs::flashing::{
    DownloadOptions, FlashProgress, HexLoader, ProgressEvent, ProgressOperation,
    download_file_with_options,
};
use probe_rs::probe::{DebugProbeSelector, WireProtocol, list::Lister};
use probe_rs::{Permissions, config::TargetSelector};
use serde::Serialize;
use tracing::{Span, debug, info, info_span, warn};
use tracing_indicatif::IndicatifLayer;
use tracing_indicatif::span_ext::IndicatifSpanExt as _;
use tracing_subscriber::prelude::*;

#[derive(Clone, Copy, PartialEq, Eq, Debug, clap::ValueEnum, Serialize)]
#[serde(rename_all = "lowercase")]
enum Protocol {
    Jtag,
    Cjtag,
    Swd,
}

impl Protocol {
    fn wire_protocol(self) -> WireProtocol {
        match self {
            Self::Jtag => WireProtocol::Jtag,
            Self::Cjtag => WireProtocol::CJtag,
            Self::Swd => WireProtocol::Swd,
        }
    }
}

#[derive(clap::Parser)]
#[command(about = "Time erase, program and verify of an image through probe-rs, one fresh attach per run")]
struct Cli {
    /// probe-rs's name for the target.
    #[arg(long, default_value = "CC1312R")]
    chip: String,

    /// Probe selector, `VID:PID:SERIAL`.
    #[arg(long)]
    probe: String,

    /// Debug wire protocol. cJTAG on a Verge probe goes through verge-probe's vendor commands; set
    /// its scan formats with `verge cjtag --formats` before the run.
    #[arg(long, value_enum)]
    protocol: Protocol,

    /// Wire clocks to sweep, in kHz.
    #[arg(long, value_delimiter = ',', default_values_t = [1000u32, 4000, 10000, 20000, 30000, 50000])]
    speeds: Vec<u32>,

    /// HEX images to flash, each at every clock.
    #[arg(long = "image", required = true)]
    images: Vec<PathBuf>,

    /// Runs per image and clock.
    #[arg(long, default_value_t = 5)]
    repeats: usize,

    /// Names this probe, host and interface in the results.
    #[arg(long)]
    label: String,

    /// Write every run here as JSON.
    #[arg(long)]
    json: PathBuf,
}

/// One run: a fresh open and attach, then the download and a reset. Times are seconds.
#[derive(Serialize, Default, Clone)]
struct Run {
    image: String,
    speed_khz: u32,
    actual_khz: Option<u32>,
    repeat: usize,
    open_s: Option<f64>,
    attach_s: Option<f64>,
    erase_s: Option<f64>,
    program_s: Option<f64>,
    verify_s: Option<f64>,
    /// The whole download call: the phases above plus algorithm loading and anything between them.
    download_s: Option<f64>,
    programmed_bytes: Option<u64>,
    reset_s: Option<f64>,
    /// Open to reset done.
    total_s: Option<f64>,
    error: Option<String>,
}

#[derive(Serialize)]
struct Report<'a> {
    label: &'a str,
    chip: &'a str,
    probe: &'a str,
    protocol: Protocol,
    host: &'static str,
    runs: &'a [Run],
}

/// Time spent in each flash operation, from probe-rs's progress events.
#[derive(Default)]
struct Phases {
    started: [Option<Instant>; 3],
    spent: [Option<Duration>; 3],
    totals: [Option<u64>; 3],
    programmed: u64,
}

impl Phases {
    fn slot(operation: ProgressOperation) -> Option<usize> {
        match operation {
            ProgressOperation::Erase => Some(0),
            ProgressOperation::Program => Some(1),
            ProgressOperation::Verify => Some(2),
            _ => None,
        }
    }

    fn name(operation: ProgressOperation) -> &'static str {
        match operation {
            ProgressOperation::Fill => "fill",
            ProgressOperation::Erase => "erase",
            ProgressOperation::Program => "program",
            ProgressOperation::Verify => "verify",
            ProgressOperation::Ram => "ram",
        }
    }

    fn on(&mut self, event: ProgressEvent, bar: &Span) {
        match event {
            ProgressEvent::AddProgressBar { operation, total } => {
                if let Some(slot) = Self::slot(operation) {
                    self.totals[slot] = total;
                }
            }
            ProgressEvent::Started(operation) => {
                bar.pb_set_message(Self::name(operation));
                bar.pb_set_position(0);
                if let Some(slot) = Self::slot(operation) {
                    bar.pb_set_length(self.totals[slot].unwrap_or(0));
                    self.started[slot] = Some(Instant::now());
                }
            }
            ProgressEvent::Progress { operation, size, .. } => {
                bar.pb_inc(size);
                if matches!(operation, ProgressOperation::Program) {
                    self.programmed += size;
                }
            }
            ProgressEvent::Finished(operation) | ProgressEvent::Failed(operation) => {
                if let Some(slot) = Self::slot(operation)
                    && let Some(started) = self.started[slot].take()
                {
                    // An operation can run more than once, once per flash region.
                    *self.spent[slot].get_or_insert_default() += started.elapsed();
                }
            }
            _ => {}
        }
    }
}

fn main() -> anyhow::Result<()> {
    let cli = <Cli as clap::Parser>::parse();
    anyhow::ensure!(cli.repeats > 0, "at least one run");
    let selector: DebugProbeSelector = cli.probe.parse().context("--probe")?;
    for image in &cli.images {
        anyhow::ensure!(image.is_file(), "{} is not a file", image.display());
    }

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
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn,flash_timing=info")),
        )
        .with(indicatif)
        .init();

    let sweep = info_span!("sweep");
    sweep.pb_set_style(&ProgressStyle::with_template(
        "{spinner} {msg} {wide_bar} {pos}/{len} {elapsed}",
    )?);
    sweep.pb_set_length((cli.images.len() * cli.speeds.len() * cli.repeats) as u64);
    let sweep = sweep.entered();

    let mut runs = Vec::new();
    for image in &cli.images {
        for &speed_khz in &cli.speeds {
            let name = image.file_name().map_or_else(String::new, |n| n.to_string_lossy().into_owned());
            Span::current().pb_set_message(&format!("{name} {speed_khz} kHz"));
            for repeat in 0..cli.repeats {
                let mut run = Run {
                    image: name.clone(),
                    speed_khz,
                    repeat,
                    ..Run::default()
                };
                let bar = info_span!(parent: &Span::current(), "run");
                bar.pb_set_style(&ProgressStyle::with_template("  {msg} {wide_bar} {bytes}/{total_bytes}")?);
                bar.pb_start();
                let outcome = run_once(&cli, &selector, image, speed_khz, &mut run, &bar);
                drop(bar);
                Span::current().pb_inc(1);
                let failed = outcome.is_err();
                if let Err(error) = outcome {
                    warn!("{name} {speed_khz} kHz run {repeat}: {error:#}");
                    run.error = Some(format!("{error:#}"));
                } else {
                    debug!("{name} {speed_khz} kHz run {repeat}: {:.3} s", run.total_s.unwrap_or(0.0));
                }
                runs.push(run);
                // A clock that fails once is a result about that clock. The rest of its runs would
                // repeat the failure, so the sweep moves on.
                if failed {
                    Span::current().pb_inc((cli.repeats - repeat - 1) as u64);
                    break;
                }
            }
        }
    }
    drop(sweep);

    let report = Report {
        label: &cli.label,
        chip: &cli.chip,
        probe: &cli.probe,
        protocol: cli.protocol,
        host: "probe-rs 17beb8e80",
        runs: &runs,
    };
    std::fs::write(&cli.json, serde_json::to_string_pretty(&report)? + "\n")
        .with_context(|| format!("writing {}", cli.json.display()))?;
    summarise(&cli, &runs);
    Ok(())
}

fn run_once(
    cli: &Cli,
    selector: &DebugProbeSelector,
    image: &Path,
    speed_khz: u32,
    run: &mut Run,
    bar: &Span,
) -> anyhow::Result<()> {
    let start = Instant::now();
    let mut probe = Lister::new().open(selector.clone()).context("open")?;
    run.open_s = Some(start.elapsed().as_secs_f64());
    if cli.protocol == Protocol::Cjtag {
        // Verge frames cJTAG through its own vendor commands. Any other probe keeps whatever cJTAG
        // support probe-rs has for it, and select_protocol says if there is none.
        if let Err(error) = verge_probe::install_cjtag_support(&mut probe) {
            debug!("no Verge cJTAG support on this probe: {error}");
        }
    }
    probe
        .select_protocol(cli.protocol.wire_protocol())
        .context("select protocol")?;
    run.actual_khz = Some(probe.set_speed(speed_khz).context("set speed")?);

    let attach = Instant::now();
    let mut session = probe
        .attach(TargetSelector::Unspecified(cli.chip.clone()), Permissions::default())
        .context("attach")?;
    run.attach_s = Some(attach.elapsed().as_secs_f64());

    let mut phases = Phases::default();
    let mut options = DownloadOptions::default();
    options.verify = true;
    options.progress = FlashProgress::new(|event| phases.on(event, bar));
    let download = Instant::now();
    download_file_with_options(&mut session, image, HexLoader, options).context("download")?;
    run.download_s = Some(download.elapsed().as_secs_f64());
    [run.erase_s, run.program_s, run.verify_s] = phases.spent.map(|d| d.map(|d| d.as_secs_f64()));
    run.programmed_bytes = Some(phases.programmed);

    let reset = Instant::now();
    session.core(0).context("core")?.reset().context("reset")?;
    run.reset_s = Some(reset.elapsed().as_secs_f64());
    drop(session);
    run.total_s = Some(start.elapsed().as_secs_f64());
    Ok(())
}

fn median(values: &mut [f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    values.sort_by(f64::total_cmp);
    let mid = values.len() / 2;
    Some(if values.len() % 2 == 1 {
        values[mid]
    } else {
        (values[mid - 1] + values[mid]) / 2.0
    })
}

fn summarise(cli: &Cli, runs: &[Run]) {
    info!(
        "{}: median of good runs; attach, erase, program, verify, total in seconds",
        cli.label
    );
    for image in &cli.images {
        let name = image.file_name().map_or_else(String::new, |n| n.to_string_lossy().into_owned());
        for &speed_khz in &cli.speeds {
            let here: Vec<&Run> = runs
                .iter()
                .filter(|r| r.image == name && r.speed_khz == speed_khz)
                .collect();
            let good: Vec<&Run> = here.iter().copied().filter(|r| r.error.is_none()).collect();
            let column = |pick: fn(&Run) -> Option<f64>| {
                let mut values: Vec<f64> = good.iter().filter_map(|r| pick(r)).collect();
                median(&mut values).map_or_else(|| "-".to_owned(), |m| format!("{m:.3}"))
            };
            let mut totals: Vec<f64> = good.iter().filter_map(|r| r.total_s).collect();
            let (low, high) = (
                totals.iter().copied().fold(f64::INFINITY, f64::min),
                totals.iter().copied().fold(f64::NEG_INFINITY, f64::max),
            );
            let range = if totals.is_empty() {
                String::new()
            } else {
                format!(" ({low:.3}-{high:.3})")
            };
            let error = here.iter().find_map(|r| r.error.as_deref()).unwrap_or("");
            info!(
                "{name} {speed_khz:>5} kHz: {}/{} ok  {} {} {} {} total {}{range} {error}",
                good.len(),
                here.len(),
                column(|r| r.attach_s),
                column(|r| r.erase_s),
                column(|r| r.program_s),
                column(|r| r.verify_s),
                median(&mut totals).map_or_else(|| "-".to_owned(), |m| format!("{m:.3}")),
            );
        }
    }
}
