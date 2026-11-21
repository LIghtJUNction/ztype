use std::io::{self, Read, Write};
use zinput::ime::{ContextId, TextTarget};
use ztype::{MAX_TEXT_BYTES, Session, engine::Engine};

mod config;

#[cfg(feature = "gui")]
mod panel;
#[cfg(all(feature = "candidate-ui", target_os = "linux"))]
mod popup;
#[cfg(target_os = "linux")]
mod service;
#[cfg(all(feature = "wayland", target_os = "linux"))]
mod trial;
#[cfg(all(feature = "wayland", target_os = "linux"))]
mod wayland;
#[cfg(all(feature = "x11", target_os = "linux"))]
mod x11;

#[derive(Default)]
struct Options {
    command: String,
    has_options: bool,
    engine: String,
    text: Option<String>,
    dictionary: Option<String>,
    window: Option<String>,
    plugin: Option<String>,
    candidate: usize,
    config: Option<std::path::PathBuf>,
    no_config: bool,
    runtime: config::Config,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("ztype: {error}");
        std::process::exit(1);
    }
}

fn default_command(
    linux: bool,
    wayland: bool,
    x11: bool,
    wayland_display: bool,
    display: bool,
) -> &'static str {
    if linux && ((wayland && wayland_display) || (x11 && display)) {
        "start"
    } else {
        "panel"
    }
}

fn parse_options(
    command: String,
    mut args: impl Iterator<Item = String>,
) -> Result<Options, String> {
    let mut options = Options {
        command,
        engine: String::new(),
        ..Options::default()
    };
    while let Some(flag) = args.next() {
        options.has_options = true;
        if flag == "--no-config" {
            options.no_config = true;
            continue;
        }
        let value = match flag.as_str() {
            "--engine" | "--text" | "--dict" | "--window" | "--candidate" | "--plugin"
            | "--config" => args
                .next()
                .ok_or_else(|| format!("missing value for {flag}"))?,
            _ => return Err(format!("unknown argument {flag:?}; see ztype help")),
        };
        match flag.as_str() {
            "--engine" => options.engine = value,
            "--text" => options.text = Some(value),
            "--dict" => options.dictionary = Some(value),
            "--window" => options.window = Some(value),
            "--plugin" => options.plugin = Some(value),
            "--config" => options.config = Some(value.into()),
            "--candidate" => {
                options.candidate = value
                    .parse::<usize>()
                    .ok()
                    .and_then(|v| v.checked_sub(1))
                    .filter(|v| *v < ztype::MAX_CANDIDATES)
                    .ok_or_else(|| format!("candidate must be 1..{}", ztype::MAX_CANDIDATES))?
            }
            _ => unreachable!(),
        }
    }
    if matches!(
        options.command.as_str(),
        "config" | "panel" | "try" | "wayland" | "x11" | "candidates" | "convert" | "commit"
    ) {
        options.runtime = config::Config::load(options.config.as_deref(), options.no_config)?;
    }
    if options.engine.is_empty() {
        options.engine.clone_from(&options.runtime.engine);
    }
    Ok(options)
}

fn engine(options: &Options) -> Result<Engine, String> {
    if let Some(path) = &options.plugin {
        if options.dictionary.is_some() || options.engine != "pinyin" {
            return Err("--plugin cannot be combined with --engine or --dict".into());
        }
        #[cfg(feature = "plugins")]
        {
            // SAFETY: native plugins are loaded only from this explicit user
            // request. The documented CLI contract requires a trusted zplugin file.
            let native = unsafe { ztype::plugin::NativeEngine::load(std::path::Path::new(path)) }?;
            return Ok(Engine::Plugin(Box::new(native)));
        }
        #[cfg(not(feature = "plugins"))]
        {
            let _ = path;
            return Err("this build has no plugins feature".into());
        }
    }
    let dictionaries = if let Some(path) = &options.dictionary {
        // An explicit CLI dictionary still reaches the loader, which rejects
        // unsupported engines instead of silently discarding the request.
        vec![std::path::PathBuf::from(path)]
    } else {
        options.runtime.dictionaries_for(&options.engine)?
    };
    let (user_dictionary, learning) = options.runtime.personal_dictionary_for(&options.engine);
    let engine =
        ztype::dictionary::load_engine(&options.engine, &dictionaries, user_dictionary, learning)?;
    let configured_user_vocabulary = user_dictionary.is_some_and(|path| {
        std::fs::metadata(path).is_ok_and(|metadata| metadata.is_file() && metadata.len() > 0)
    });
    if options.engine == "pinyin"
        && matches!(options.command.as_str(), "wayland" | "x11")
        && dictionaries.is_empty()
        && !configured_user_vocabulary
    {
        if !options.no_config {
            return Err(
                "no external pinyin vocabulary is installed; from the Ztype source checkout run python3 scripts/install_dictionary.py, then ztype restart; or set dictionary=PATH in your configuration"
                    .into(),
            );
        }
        eprintln!(
            "ztype: --no-config explicitly uses only the basic bootstrap vocabulary ({} entries)",
            engine.dictionary_entries()
        );
    }
    Ok(engine)
}

fn input(options: &Options) -> Result<String, String> {
    let text = options
        .text
        .as_deref()
        .ok_or("--text is required; use --text - to read UTF-8 from stdin")?;
    if text != "-" {
        ztype::validate_text(text)?;
        return Ok(text.into());
    }
    let mut bytes = Vec::new();
    io::stdin()
        .take((MAX_TEXT_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    let text = String::from_utf8(bytes).map_err(|_| "stdin must be UTF-8")?;
    ztype::validate_text(&text)?;
    Ok(text)
}

struct StdoutTarget;
impl TextTarget for StdoutTarget {
    type Error = io::Error;
    fn commit_text(&mut self, _: ContextId, text: &str) -> io::Result<()> {
        let mut out = io::stdout().lock();
        out.write_all(text.as_bytes())?;
        out.write_all(b"\n")
    }
}

fn run() -> Result<(), String> {
    let mut args = std::env::args().skip(1);
    let command = args.next().unwrap_or_else(|| {
        default_command(
            cfg!(target_os = "linux"),
            cfg!(feature = "wayland"),
            cfg!(feature = "x11"),
            std::env::var_os("WAYLAND_DISPLAY").is_some_and(|value| !value.is_empty()),
            std::env::var_os("DISPLAY").is_some_and(|value| !value.is_empty()),
        )
        .into()
    });
    if command == "gtk3-wayland" {
        #[cfg(all(feature = "wayland", target_os = "linux"))]
        return wayland::launch_gtk3(std::env::args_os().skip(2));
        #[cfg(not(all(feature = "wayland", target_os = "linux")))]
        return Err("this build has no native Wayland input-method frontend".into());
    }
    let options = parse_options(command, args)?;
    match options.command.as_str() {
        "help" | "--help" | "-h" => {
            print!(
                r#"ztype — native input with Project Z

Usage: ztype COMMAND [OPTIONS]

  start          Start installed input services
  restart        Reload input services after upgrades or settings changes
  stop           Stop installed input services
  status         Show service state and detect an outdated running binary
  config         Show resolved preferences and dictionary paths
  wayland        Run the Wayland input service in this terminal
  x11            Run the X11/XWayland XIM service in this terminal
  gtk3-wayland PROGRAM [ARGS...]
                 Launch a fresh GTK3 client using native Wayland text input
  wayland-doctor Probe compositor protocols without claiming an input seat
  candidates     Show ranked candidates for --text
  convert        Write a selected candidate to stdout
  panel          Open the optional composition panel
  try            Open the separate typing trial
  commit         Focus --window and send the chosen candidate
  windows        List native target windows as JSON
  doctor         Show native desktop backend capabilities
  features       Show compiled features as JSON

Options:
  --engine NAME       pinyin, kana, table, unicode or literal
  --dict PATH         Explicit UTF-8 TSV dictionary
  --config PATH       Read a named configuration
  --no-config         Disable user settings and automatic dictionary discovery
  --text TEXT         Composition; '-' reads bounded UTF-8 stdin
  --candidate INDEX   Candidate number (1..128; default 1)
  --window ID         Target from 'ztype windows'
  --plugin PATH       Explicit trusted native engine library

Native input:
  Space / 1..9 selects from the current page.
  PageUp/PageDown or -/= turns pages; Tab/Up/Down selects candidates.
  Left/Right/Home/End, Backspace and Delete edit the composition.
  Enter sends raw pinyin; Ctrl+Space switches Chinese/English mode.
  Set page_size, initial_mode, enter_behavior and punctuation in 'ztype config'.

Wayland needs input-method-v2 and virtual-keyboard-v1.
X11/XWayland uses the configured XIM endpoint (default XMODIFIERS=@im=ztype).
Windows TSF and macOS IMK frontends are not implemented.
"#
            );
            return Ok(());
        }
        "config" => {
            let mut runtime = options.runtime.clone();
            runtime.dictionaries = if let Some(path) = &options.dictionary {
                if !matches!(options.engine.as_str(), "pinyin" | "table") {
                    return Err("--dict is supported by pinyin and table engines".into());
                }
                vec![path.into()]
            } else {
                options.runtime.dictionaries_for(&options.engine)?
            };
            let (user_dictionary, learning) =
                options.runtime.personal_dictionary_for(&options.engine);
            runtime.user_dictionary = user_dictionary.map(std::path::Path::to_path_buf);
            runtime.learning = learning;
            runtime.engine.clone_from(&options.engine);
            print!("{}", runtime.describe());
            return Ok(());
        }
        "features" => {
            println!(
                "{{\"version\":\"{}\",\"gui\":{},\"desktop\":{},\"plugins\":{},\"wayland\":{},\"wayland_popup\":{},\"x11\":{},\"service_control\":{}}}",
                env!("CARGO_PKG_VERSION"),
                cfg!(feature = "gui"),
                cfg!(feature = "desktop"),
                cfg!(feature = "plugins"),
                cfg!(all(feature = "wayland", target_os = "linux")),
                cfg!(all(feature = "wayland-popup", target_os = "linux")),
                cfg!(all(feature = "x11", target_os = "linux")),
                cfg!(target_os = "linux"),
            );
            return Ok(());
        }
        "--version" | "version" => {
            println!("ztype {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        "start" | "restart" | "stop" | "status" => {
            if options.has_options {
                return Err("start, restart, stop and status do not accept conversion options; use the foreground wayland or x11 commands to choose an engine".into());
            }
            #[cfg(target_os = "linux")]
            return service::run(&options.command);
            #[cfg(not(target_os = "linux"))]
            return Err("installed native input service control is supported on Linux".into());
        }
        "try" => {
            #[cfg(all(feature = "wayland", target_os = "linux"))]
            {
                if options.window.is_some()
                    || options.text.is_some()
                    || options.plugin.is_some()
                    || options.candidate != 0
                {
                    return Err(
                        "the native trial accepts --engine, --dict, --config and --no-config only"
                            .into(),
                    );
                }
                if !matches!(options.engine.as_str(), "pinyin" | "kana" | "table") {
                    return Err("the native trial supports pinyin, kana and table".into());
                }
                return trial::run(
                    &options.engine,
                    options.dictionary.as_deref(),
                    options
                        .runtime
                        .path
                        .as_deref()
                        .filter(|path| path.is_file()),
                    options.no_config,
                );
            }
            #[cfg(not(all(feature = "wayland", target_os = "linux")))]
            return Err("this build has no native Wayland trial frontend".into());
        }
        "wayland" | "wayland-doctor" => {
            #[cfg(all(feature = "wayland", target_os = "linux"))]
            {
                return if options.command == "wayland-doctor" {
                    wayland::doctor()
                } else {
                    if options.window.is_some() || options.text.is_some() {
                        return Err(
                            "Wayland input uses the focused text field; omit --window and --text"
                                .into(),
                        );
                    }
                    if options.plugin.is_some()
                        || !matches!(options.engine.as_str(), "pinyin" | "kana" | "table")
                    {
                        return Err("Wayland supports pinyin, kana and table engines; use panel or convert for Unicode, literal and native plugins".into());
                    }
                    wayland::run(engine(&options)?, options.runtime.input)
                };
            }
            #[cfg(not(all(feature = "wayland", target_os = "linux")))]
            return Err("this build has no native Wayland input-method frontend".into());
        }
        "x11" => {
            #[cfg(all(feature = "x11", target_os = "linux"))]
            {
                if options.window.is_some() || options.text.is_some() {
                    return Err(
                        "XIM input uses the focused text field; omit --window and --text".into(),
                    );
                }
                if options.plugin.is_some()
                    || !matches!(options.engine.as_str(), "pinyin" | "kana" | "table")
                {
                    return Err("XIM supports pinyin, kana and table engines; use panel or convert for Unicode, literal and native plugins".into());
                }
                return x11::run(engine(&options)?, options.runtime.input);
            }
            #[cfg(not(all(feature = "x11", target_os = "linux")))]
            return Err("this build has no native X11 input-method frontend".into());
        }
        "windows" | "doctor" => {
            #[cfg(feature = "desktop")]
            {
                let desktop = zuse::platform::Desktop::connect()?;
                println!(
                    "{}",
                    if options.command == "windows" {
                        desktop.windows()?
                    } else {
                        desktop.capabilities()
                    }
                );
                return Ok(());
            }
            #[cfg(not(feature = "desktop"))]
            return Err("this build has no desktop feature".into());
        }
        "panel" => {
            #[cfg(feature = "gui")]
            return panel::run(
                engine(&options)?,
                if options.plugin.is_some() {
                    "plugin"
                } else {
                    &options.engine
                },
                options.window.as_deref(),
                options
                    .text
                    .as_ref()
                    .map(|_| input(&options))
                    .transpose()?
                    .as_deref(),
            );
            #[cfg(not(feature = "gui"))]
            return Err("this build has no gui feature; use convert or candidates".into());
        }
        "candidates" | "convert" | "commit" => {}
        unknown => return Err(format!("unknown command {unknown:?}; see ztype help")),
    }
    let context = ContextId::new(1).map_err(|e| format!("{e:?}"))?;
    let mut storage = [0; MAX_TEXT_BYTES];
    let mut session = Session::new(&mut storage, engine(&options)?, context)?;
    session.set_input(&input(&options)?)?;
    if options.command == "candidates" {
        for (index, text) in session.candidates().iter().enumerate() {
            println!("{}\t{}", index + 1, text);
        }
        return Ok(());
    }
    session.select(options.candidate)?;
    if options.command == "convert" {
        session.commit(&mut StdoutTarget)?;
        return Ok(());
    }
    #[cfg(feature = "desktop")]
    {
        let window = options
            .window
            .ok_or("commit requires --window ID from 'ztype windows'")?;
        let mut target = ztype::native::NativeTarget::connect(window, context)?;
        let size = session.commit(&mut target)?;
        println!("submitted {size} UTF-8 bytes via {}", target.backend());
        Ok(())
    }
    #[cfg(not(feature = "desktop"))]
    Err("this build has no desktop feature".into())
}

#[cfg(test)]
mod service_defaults_tests {
    use super::default_command;

    #[test]
    fn graphical_native_build_defaults_to_real_service() {
        assert_eq!(default_command(true, true, false, true, true), "start");
        assert_eq!(default_command(true, false, true, false, true), "start");
        assert_eq!(default_command(true, true, true, true, true), "start");
    }

    #[test]
    fn unsupported_or_nongraphical_build_does_not_default_to_a_trial() {
        assert_eq!(default_command(true, false, false, true, true), "panel");
        assert_eq!(default_command(true, true, false, false, true), "panel");
        assert_eq!(default_command(true, true, true, false, false), "panel");
        assert_eq!(default_command(false, true, true, true, true), "panel");
    }
}
