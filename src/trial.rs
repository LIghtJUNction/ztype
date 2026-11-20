//! Launch the installed, isolated native Wayland typing window.
use std::{os::unix::process::CommandExt, process::Command};

pub fn run(engine: &str, dictionary: Option<&str>) -> Result<(), String> {
    let binary = std::env::current_exe().map_err(|error| error.to_string())?;
    let prefix = binary
        .parent()
        .and_then(std::path::Path::parent)
        .ok_or("cannot locate the Ztype installation prefix")?;
    let launcher = prefix.join("libexec/ztype/try_wayland.py");
    if !launcher.is_file() {
        return Err(
            "the interactive trial is not installed; run python3 scripts/install.py --binary target/release/ztype --with-trial"
                .into(),
        );
    }
    let mut command = Command::new("python3");
    command
        .arg(launcher)
        .arg("--binary")
        .arg(binary)
        .arg("--engine")
        .arg(engine);
    if let Some(path) = dictionary {
        command.arg("--dict").arg(path);
    }
    // Replace this process so closing or interrupting a trial reaches the owner
    // of its compositor and input service, including the launcher's cleanup.
    Err(format!("cannot open the native trial: {}", command.exec()))
}
