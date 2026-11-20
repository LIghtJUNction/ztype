//! Control the installed input services without opening a trial or a panel.
use std::process::{Command, Output};

#[derive(Clone, Copy)]
struct Unit {
    name: &'static str,
    display: &'static str,
}

fn compiled_units() -> &'static [Unit] {
    &[
        #[cfg(feature = "wayland")]
        Unit {
            name: "ztype-wayland.service",
            display: "WAYLAND_DISPLAY",
        },
        #[cfg(feature = "x11")]
        Unit {
            name: "ztype-x11.service",
            display: "DISPLAY",
        },
    ]
}

struct Session {
    present: Vec<&'static str>,
}

impl Session {
    fn current() -> Self {
        Self {
            present: ["DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"]
                .into_iter()
                .filter(|name| std::env::var_os(name).is_some_and(|value| !value.is_empty()))
                .collect(),
        }
    }

    fn has(&self, name: &str) -> bool {
        self.present.contains(&name)
    }
}

trait Runner {
    fn systemctl(&mut self, args: &[&str]) -> std::io::Result<Output>;
}

struct Systemctl;
impl Runner for Systemctl {
    fn systemctl(&mut self, args: &[&str]) -> std::io::Result<Output> {
        Command::new("systemctl").args(args).output()
    }
}

pub fn run(action: &str) -> Result<(), String> {
    let output = control(
        action,
        compiled_units(),
        &Session::current(),
        &mut Systemctl,
    )?;
    print!("{output}");
    Ok(())
}

fn checked(output: Output, purpose: &str) -> Result<Output, String> {
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        let detail = if !stderr.trim().is_empty() {
            stderr.trim().to_owned()
        } else if !stdout.trim().is_empty() {
            stdout.trim().to_owned()
        } else {
            output.status.to_string()
        };
        return Err(format!("cannot {purpose}: {detail}"));
    }
    Ok(output)
}

fn invoke(runner: &mut impl Runner, args: &[&str], purpose: &str) -> Result<Output, String> {
    let output = runner
        .systemctl(args)
        .map_err(|error| format!("cannot {purpose}: {error}"))?;
    checked(output, purpose)
}

struct UnitState {
    unit: Unit,
    load: String,
    active: String,
}

impl UnitState {
    fn installed(&self) -> bool {
        self.load != "not-found"
    }
}

fn query(runner: &mut impl Runner, unit: Unit) -> Result<UnitState, String> {
    let output = runner
        .systemctl(&[
            "--user",
            "show",
            unit.name,
            "--property=LoadState",
            "--property=ActiveState",
        ])
        .map_err(|error| format!("cannot read {} status: {error}", unit.name))?;
    let text = String::from_utf8_lossy(&output.stdout);
    let mut load = None;
    let mut active = None;
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("LoadState=") {
            load = Some(value.to_owned());
        } else if let Some(value) = line.strip_prefix("ActiveState=") {
            active = Some(value.to_owned());
        }
    }
    drop(text);
    // systemctl may use a nonzero status when a unit is not installed. Preserve
    // that explicit state so stop/status remain useful from a terminal session.
    if load.as_deref() != Some("not-found") {
        checked(output, &format!("read {} status", unit.name))?;
    }
    match (load, active) {
        (Some(load), Some(active)) if !load.is_empty() && !active.is_empty() => {
            Ok(UnitState { unit, load, active })
        }
        _ => Err(format!(
            "systemctl returned incomplete status for {}",
            unit.name
        )),
    }
}

fn describe(states: &[UnitState]) -> String {
    let mut output = String::new();
    for state in states {
        let status = if state.installed() {
            state.active.as_str()
        } else {
            "not installed"
        };
        output.push_str(&format!("{}: {status}\n", state.unit.name));
    }
    output
}

fn control(
    action: &str,
    units: &[Unit],
    session: &Session,
    runner: &mut impl Runner,
) -> Result<String, String> {
    if !matches!(action, "start" | "stop" | "status") {
        return Err(format!("unknown service action {action:?}"));
    }
    if units.is_empty() {
        return Err("this build has no native Linux input service; use 'ztype help' for command-line conversion".into());
    }
    let selected: Vec<Unit> = units
        .iter()
        .copied()
        .filter(|unit| action != "start" || session.has(unit.display))
        .collect();
    if selected.is_empty() {
        return Err(
            "no supported graphical session is available; start Ztype from the desktop session"
                .into(),
        );
    }
    if action == "start" {
        let mut import = vec!["--user", "import-environment"];
        import.extend(session.present.iter().copied());
        invoke(runner, &import, "refresh the graphical session connection")?;
        let mut start = vec!["--user", "start"];
        start.extend(selected.iter().map(|unit| unit.name));
        invoke(runner, &start, "start the installed input services")?;
    }
    let mut states: Vec<UnitState> = selected
        .iter()
        .map(|unit| query(runner, *unit))
        .collect::<Result<_, _>>()?;
    if action == "stop" {
        let installed: Vec<&str> = states
            .iter()
            .filter(|state| state.installed())
            .map(|state| state.unit.name)
            .collect();
        if installed.is_empty() {
            return Err("no Ztype input service is installed for this build".into());
        }
        let mut stop = vec!["--user", "stop"];
        stop.extend(installed);
        invoke(runner, &stop, "stop the input services")?;
        states = selected
            .iter()
            .map(|unit| query(runner, *unit))
            .collect::<Result<_, _>>()?;
        if states.iter().any(|state| {
            state.installed() && !matches!(state.active.as_str(), "inactive" | "failed")
        }) {
            return Err(format!(
                "an input service has not stopped:\n{}",
                describe(&states)
            ));
        }
    }
    let mut output = describe(&states);
    if action == "start" {
        if states
            .iter()
            .any(|state| state.load != "loaded" || state.active != "active")
        {
            return Err(format!(
                "an input service did not become active:\n{}Run 'journalctl --user -u ztype-wayland -u ztype-x11 -n 30' for its startup error",
                output,
            ));
        }
        output.push_str("Ztype is running. Space confirms, Tab changes candidate, Ctrl+Space switches input mode.\n");
        if selected.iter().any(|unit| unit.display == "DISPLAY") {
            output.push_str("X11/XWayland applications use XMODIFIERS=@im=ztype; reopen applications after setting it.\n");
        }
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{collections::VecDeque, os::unix::process::ExitStatusExt};

    const WAYLAND: Unit = Unit {
        name: "ztype-wayland.service",
        display: "WAYLAND_DISPLAY",
    };
    const X11: Unit = Unit {
        name: "ztype-x11.service",
        display: "DISPLAY",
    };

    struct Fake {
        steps: VecDeque<(Vec<String>, Output)>,
    }
    impl Fake {
        fn new() -> Self {
            Self {
                steps: VecDeque::new(),
            }
        }
        fn expect(&mut self, args: &[&str], stdout: &str, stderr: &str, code: i32) {
            self.steps.push_back((
                args.iter().map(|arg| (*arg).into()).collect(),
                Output {
                    status: std::process::ExitStatus::from_raw(code << 8),
                    stdout: stdout.as_bytes().to_vec(),
                    stderr: stderr.as_bytes().to_vec(),
                },
            ));
        }
        fn status(&mut self, unit: Unit, load: &str, active: &str) {
            self.expect(
                &[
                    "--user",
                    "show",
                    unit.name,
                    "--property=LoadState",
                    "--property=ActiveState",
                ],
                &format!("ActiveState={active}\nLoadState={load}\n"),
                "",
                if load == "not-found" { 4 } else { 0 },
            );
        }
        fn finished(&self) {
            assert!(self.steps.is_empty());
        }
    }
    impl Runner for Fake {
        fn systemctl(&mut self, args: &[&str]) -> std::io::Result<Output> {
            let (expected, output) = self
                .steps
                .pop_front()
                .expect("unexpected systemctl operation");
            assert_eq!(args, expected);
            Ok(output)
        }
    }

    #[test]
    fn start_is_idempotent_and_selects_both_supported_connections() {
        let session = Session {
            present: vec!["DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"],
        };
        let mut fake = Fake::new();
        for _ in 0..2 {
            fake.expect(
                &[
                    "--user",
                    "import-environment",
                    "DISPLAY",
                    "WAYLAND_DISPLAY",
                    "XAUTHORITY",
                ],
                "",
                "",
                0,
            );
            fake.expect(&["--user", "start", WAYLAND.name, X11.name], "", "", 0);
            fake.status(WAYLAND, "loaded", "active");
            fake.status(X11, "loaded", "active");
        }
        for _ in 0..2 {
            let output = control("start", &[WAYLAND, X11], &session, &mut fake).unwrap();
            assert!(output.contains("Ztype is running"));
        }
        fake.finished();
    }

    #[test]
    fn successful_start_command_does_not_hide_a_failed_service() {
        let session = Session {
            present: vec!["WAYLAND_DISPLAY"],
        };
        let mut fake = Fake::new();
        fake.expect(
            &["--user", "import-environment", "WAYLAND_DISPLAY"],
            "",
            "",
            0,
        );
        fake.expect(&["--user", "start", WAYLAND.name], "", "", 0);
        fake.status(WAYLAND, "loaded", "failed");
        let error = control("start", &[WAYLAND, X11], &session, &mut fake).unwrap_err();
        assert!(error.contains("did not become active"));
        assert!(error.contains("failed"));
        assert!(!error.contains("Ztype is running"));
        fake.finished();
    }

    #[test]
    fn start_propagates_transport_failure_without_querying_or_claiming_success() {
        let session = Session {
            present: vec!["WAYLAND_DISPLAY"],
        };
        let mut fake = Fake::new();
        fake.expect(
            &["--user", "import-environment", "WAYLAND_DISPLAY"],
            "",
            "bus unavailable",
            1,
        );
        assert!(
            control("start", &[WAYLAND], &session, &mut fake)
                .unwrap_err()
                .contains("bus unavailable")
        );
        fake.finished();
    }

    #[test]
    fn no_graphical_session_or_frontend_never_invokes_systemctl() {
        let mut fake = Fake::new();
        let session = Session { present: vec![] };
        assert!(control("start", &[WAYLAND, X11], &session, &mut fake).is_err());
        assert!(control("start", &[], &session, &mut fake).is_err());
        fake.finished();
    }

    #[test]
    fn stop_from_a_tty_ignores_missing_units_and_checks_the_result() {
        let session = Session { present: vec![] };
        let mut fake = Fake::new();
        fake.status(WAYLAND, "loaded", "active");
        fake.status(X11, "not-found", "inactive");
        fake.expect(&["--user", "stop", WAYLAND.name], "", "", 0);
        fake.status(WAYLAND, "loaded", "inactive");
        fake.status(X11, "not-found", "inactive");
        let output = control("stop", &[WAYLAND, X11], &session, &mut fake).unwrap();
        assert!(output.contains("ztype-wayland.service: inactive"));
        assert!(output.contains("ztype-x11.service: not installed"));
        fake.finished();
    }

    #[test]
    fn stop_does_not_claim_success_while_unit_is_still_running() {
        let session = Session { present: vec![] };
        let mut fake = Fake::new();
        fake.status(WAYLAND, "loaded", "active");
        fake.expect(&["--user", "stop", WAYLAND.name], "", "", 0);
        fake.status(WAYLAND, "loaded", "active");
        assert!(
            control("stop", &[WAYLAND], &session, &mut fake)
                .unwrap_err()
                .contains("has not stopped")
        );
        fake.finished();
    }

    #[test]
    fn status_is_read_only_and_reports_inactive_without_a_display() {
        let session = Session { present: vec![] };
        let mut fake = Fake::new();
        fake.status(WAYLAND, "loaded", "inactive");
        assert_eq!(
            control("status", &[WAYLAND], &session, &mut fake).unwrap(),
            "ztype-wayland.service: inactive\n"
        );
        fake.finished();
    }

    #[test]
    fn incomplete_systemctl_output_is_an_error() {
        let session = Session { present: vec![] };
        let mut fake = Fake::new();
        fake.expect(
            &[
                "--user",
                "show",
                WAYLAND.name,
                "--property=LoadState",
                "--property=ActiveState",
            ],
            "LoadState=loaded\n",
            "",
            0,
        );
        assert!(
            control("status", &[WAYLAND], &session, &mut fake)
                .unwrap_err()
                .contains("incomplete status")
        );
        fake.finished();
    }
}
