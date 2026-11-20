//! Control the installed input services without opening a trial or a panel.
use std::{
    ffi::OsStr,
    os::{
        linux::net::SocketAddrExt,
        unix::{
            ffi::OsStrExt,
            fs::MetadataExt,
            net::{SocketAddr, UnixDatagram},
        },
    },
    process::{Command, Output},
    time::Duration,
};

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
            present: [
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "XAUTHORITY",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "XDG_STATE_HOME",
                "XDG_DATA_DIRS",
                "ZTYPE_XIM_NAME",
            ]
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

/// Report readiness only after dictionaries, engine and native registration succeeded.
pub fn ready() -> Result<(), String> {
    notify_ready(std::env::var_os("NOTIFY_SOCKET").as_deref())
}

fn notify_ready(socket: Option<&OsStr>) -> Result<(), String> {
    let Some(socket) = socket else {
        return Ok(());
    };
    let bytes = socket.as_bytes();
    let address = match bytes.first() {
        Some(b'@') if bytes.len() > 1 => SocketAddr::from_abstract_name(&bytes[1..]),
        Some(b'/') => SocketAddr::from_pathname(socket),
        _ => {
            return Err(
                "NOTIFY_SOCKET must name an absolute path or a Linux abstract socket".into(),
            );
        }
    }
    .map_err(|error| format!("invalid readiness socket: {error}"))?;
    let sender = UnixDatagram::unbound()
        .map_err(|error| format!("cannot create readiness socket: {error}"))?;
    sender
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|error| format!("cannot set readiness timeout: {error}"))?;
    let written = sender
        .send_to_addr(b"READY=1", &address)
        .map_err(|error| format!("cannot notify input service readiness: {error}"))?;
    if written != b"READY=1".len() {
        return Err("incomplete input service readiness notification".into());
    }
    Ok(())
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
    substate: String,
    result: String,
    main_status: Option<i32>,
    pid: u32,
    current_binary: Option<bool>,
}

impl UnitState {
    fn installed(&self) -> bool {
        self.load != "not-found"
    }
}

fn process_uses_current_binary(pid: u32) -> Option<bool> {
    if pid == 0 {
        return None;
    }
    let running = std::fs::metadata(format!("/proc/{pid}/exe")).ok()?;
    let current = std::fs::metadata("/proc/self/exe").ok()?;
    Some(running.dev() == current.dev() && running.ino() == current.ino())
}

fn query(runner: &mut impl Runner, unit: Unit) -> Result<UnitState, String> {
    let output = runner
        .systemctl(&[
            "--user",
            "show",
            unit.name,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=Result",
            "--property=ExecMainStatus",
            "--property=MainPID",
        ])
        .map_err(|error| format!("cannot read {} status: {error}", unit.name))?;
    let text = String::from_utf8_lossy(&output.stdout);
    let mut load = None;
    let mut active = None;
    let mut substate = String::new();
    let mut result = String::new();
    let mut main_status = None;
    let mut pid = 0;
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("LoadState=") {
            load = Some(value.to_owned());
        } else if let Some(value) = line.strip_prefix("ActiveState=") {
            active = Some(value.to_owned());
        } else if let Some(value) = line.strip_prefix("SubState=") {
            substate = value.to_owned();
        } else if let Some(value) = line.strip_prefix("Result=") {
            result = value.to_owned();
        } else if let Some(value) = line.strip_prefix("ExecMainStatus=") {
            main_status = value.parse().ok();
        } else if let Some(value) = line.strip_prefix("MainPID=") {
            pid = value
                .parse()
                .map_err(|_| format!("invalid MainPID for {}", unit.name))?;
        }
    }
    drop(text);
    // systemctl may use a nonzero status when a unit is not installed. Preserve
    // that explicit state so stop/status remain useful from a terminal session.
    if load.as_deref() != Some("not-found") {
        checked(output, &format!("read {} status", unit.name))?;
    }
    match (load, active) {
        (Some(load), Some(active)) if !load.is_empty() && !active.is_empty() => Ok(UnitState {
            unit,
            load,
            active,
            substate,
            result,
            main_status,
            pid,
            current_binary: process_uses_current_binary(pid),
        }),
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
        output.push_str(&format!("{}: {status}", state.unit.name));
        if state.installed()
            && !state.substate.is_empty()
            && !matches!(state.substate.as_str(), "running" | "dead")
        {
            output.push_str(&format!(" ({})", state.substate));
        }
        if !state.result.is_empty() && state.result != "success" {
            output.push_str(&format!("; result={}", state.result));
            if let Some(code) = state.main_status {
                output.push_str(&format!("; exit={code}"));
            }
        }
        if state.active == "active" && state.current_binary == Some(false) {
            output.push_str("; running an older or different executable; run 'ztype restart'");
        }
        output.push('\n');
    }
    output
}

fn control(
    action: &str,
    units: &[Unit],
    session: &Session,
    runner: &mut impl Runner,
) -> Result<String, String> {
    if !matches!(action, "start" | "restart" | "stop" | "status") {
        return Err(format!("unknown service action {action:?}"));
    }
    if units.is_empty() {
        return Err("this build has no native Linux input service; use 'ztype help' for command-line conversion".into());
    }
    let selected: Vec<Unit> = units
        .iter()
        .copied()
        .filter(|unit| !matches!(action, "start" | "restart") || session.has(unit.display))
        .collect();
    if selected.is_empty() {
        return Err(
            "no supported graphical session is available; start Ztype from the desktop session"
                .into(),
        );
    }
    if matches!(action, "start" | "restart") {
        let mut import = vec!["--user", "import-environment"];
        import.extend(session.present.iter().copied());
        invoke(runner, &import, "refresh the graphical session connection")?;
        let mut start = vec!["--user", action];
        start.extend(selected.iter().map(|unit| unit.name));
        invoke(
            runner,
            &start,
            &format!("{action} the installed input services"),
        )?;
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
    if matches!(action, "start" | "restart") {
        if states.iter().any(|state| {
            state.load != "loaded"
                || state.active != "active"
                || state.pid == 0
                || (!state.substate.is_empty() && state.substate != "running")
        }) {
            return Err(format!(
                "an input service did not become active:\n{}Run 'journalctl --user -u ztype-wayland -u ztype-x11 -n 30' for its startup error",
                output,
            ));
        }
        if states
            .iter()
            .any(|state| state.current_binary == Some(false))
        {
            return Err(format!(
                "an input service is still using a different executable:\n{output}Run 'ztype restart' from the installed binary after an upgrade"
            ));
        }
        output.push_str("Ztype is running. Space confirms, Tab changes candidate, Ctrl+Space switches input mode.\n");
        if selected.iter().any(|unit| unit.display == "DISPLAY") {
            let server = std::env::var("ZTYPE_XIM_NAME").unwrap_or_else(|_| "ztype".into());
            output.push_str(&format!("X11/XWayland applications use XMODIFIERS=@im={server}; reopen applications after setting it.\n"));
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
                    "--property=SubState",
                    "--property=Result",
                    "--property=ExecMainStatus",
                    "--property=MainPID",
                ],
                &format!("ActiveState={active}\nLoadState={load}\nSubState={}\nResult=success\nExecMainStatus=0\nMainPID={}\n",
                    if active == "active" { "running" } else { "dead" },
                    if active == "active" { std::process::id() } else { 0 }),
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
    fn readiness_reaches_a_real_filesystem_datagram_socket() {
        let directory = std::env::temp_dir().join(format!("ztype-notify-{}", std::process::id()));
        std::fs::create_dir(&directory).unwrap();
        struct Cleanup(std::path::PathBuf);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = std::fs::remove_file(self.0.join("notify"));
                let _ = std::fs::remove_dir(&self.0);
            }
        }
        let _cleanup = Cleanup(directory.clone());
        let path = directory.join("notify");
        let receiver = UnixDatagram::bind(&path).unwrap();
        receiver
            .set_read_timeout(Some(Duration::from_secs(1)))
            .unwrap();
        notify_ready(Some(path.as_os_str())).unwrap();
        let mut bytes = [0; 64];
        let count = receiver.recv(&mut bytes).unwrap();
        assert_eq!(&bytes[..count], b"READY=1");
    }

    #[test]
    fn readiness_reaches_a_real_abstract_datagram_socket() {
        let name = format!("ztype-notify-abstract-{}", std::process::id());
        let address = SocketAddr::from_abstract_name(name.as_bytes()).unwrap();
        let receiver = UnixDatagram::bind_addr(&address).unwrap();
        receiver
            .set_read_timeout(Some(Duration::from_secs(1)))
            .unwrap();
        let destination = format!("@{name}");
        notify_ready(Some(OsStr::new(&destination))).unwrap();
        let mut bytes = [0; 64];
        let count = receiver.recv(&mut bytes).unwrap();
        assert_eq!(&bytes[..count], b"READY=1");
    }

    #[test]
    fn foreground_readiness_is_optional_and_unreachable_notifications_fail() {
        assert!(notify_ready(None).is_ok());
        assert!(
            notify_ready(Some(OsStr::new("relative")))
                .unwrap_err()
                .contains("NOTIFY_SOCKET")
        );
        let absent = format!("@ztype-no-receiver-{}", std::process::id());
        assert!(
            notify_ready(Some(OsStr::new(&absent)))
                .unwrap_err()
                .contains("cannot notify")
        );
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
    fn restart_replaces_a_running_service_and_preserves_config_paths() {
        let session = Session {
            present: vec!["WAYLAND_DISPLAY", "XDG_CONFIG_HOME", "XDG_DATA_HOME"],
        };
        let mut fake = Fake::new();
        fake.expect(
            &[
                "--user",
                "import-environment",
                "WAYLAND_DISPLAY",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
            ],
            "",
            "",
            0,
        );
        fake.expect(&["--user", "restart", WAYLAND.name], "", "", 0);
        fake.status(WAYLAND, "loaded", "active");
        assert!(
            control("restart", &[WAYLAND, X11], &session, &mut fake)
                .unwrap()
                .contains("Ztype is running")
        );
        fake.finished();
    }

    #[test]
    fn stale_process_and_failure_diagnostics_are_actionable() {
        let state = UnitState {
            unit: WAYLAND,
            load: "loaded".into(),
            active: "active".into(),
            substate: "running".into(),
            result: "success".into(),
            main_status: Some(0),
            pid: 1,
            current_binary: Some(false),
        };
        assert!(describe(&[state]).contains("older or different executable; run 'ztype restart'"));
        let state = UnitState {
            unit: X11,
            load: "loaded".into(),
            active: "failed".into(),
            substate: "failed".into(),
            result: "exit-code".into(),
            main_status: Some(2),
            pid: 0,
            current_binary: None,
        };
        assert!(describe(&[state]).contains("result=exit-code; exit=2"));
        assert_eq!(process_uses_current_binary(std::process::id()), Some(true));
    }

    #[test]
    fn active_unit_without_a_process_is_not_reported_as_running() {
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
        fake.expect(
            &[
                "--user",
                "show",
                WAYLAND.name,
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                "--property=Result",
                "--property=ExecMainStatus",
                "--property=MainPID",
            ],
            "LoadState=loaded\nActiveState=active\nSubState=exited\nMainPID=0\n",
            "",
            0,
        );
        assert!(
            control("start", &[WAYLAND], &session, &mut fake)
                .unwrap_err()
                .contains("did not become active")
        );
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
                "--property=SubState",
                "--property=Result",
                "--property=ExecMainStatus",
                "--property=MainPID",
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
