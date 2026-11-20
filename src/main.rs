use std::io::{self, Read, Write};
use zinput::ime::{ContextId, TextTarget};
use ztype::{MAX_TEXT_BYTES, Session, engine::Engine};

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

fn parse() -> Result<Options, String> {
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
    let mut options = Options {
        command,
        engine: "pinyin".into(),
        ..Options::default()
    };
    while let Some(flag) = args.next() {
        options.has_options = true;
        let value = match flag.as_str() {
            "--engine" | "--text" | "--dict" | "--window" | "--candidate" | "--plugin" => args
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
            "--candidate" => {
                options.candidate = value
                    .parse::<usize>()
                    .ok()
                    .and_then(|v| v.checked_sub(1))
                    .filter(|v| *v < ztype::MAX_CANDIDATES)
                    .ok_or("candidate must be 1..9")?
            }
            _ => unreachable!(),
        }
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
    let dictionary = match &options.dictionary {
        Some(path) => {
            let file =
                std::fs::File::open(path).map_err(|e| format!("cannot open dictionary: {e}"))?;
            let mut bytes = Vec::new();
            file.take(16 * 1024 * 1024 + 1)
                .read_to_end(&mut bytes)
                .map_err(|e| e.to_string())?;
            if bytes.len() > 16 * 1024 * 1024 {
                return Err("dictionary exceeds 16 MiB".into());
            }
            Some(String::from_utf8(bytes).map_err(|_| "dictionary must be UTF-8")?)
        }
        None => None,
    };
    Engine::named(&options.engine, dictionary.as_deref())
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
    let options = parse()?;
    match options.command.as_str() {
        "help" | "--help" | "-h" => {
            println!(
                "ztype — multilingual input with Project Z\n\n\
Usage: ztype COMMAND [OPTIONS]\n\n\
  start        Start installed input services (default in supported Linux sessions)\n\
  stop         Stop installed Ztype input services\n\
  status       Show actual installed input service state\n\
  try          Open the optional separate typing trial\n\
  wayland      Run the native Wayland input service in this terminal\n\
  x11          Run the native XIM service for X11/XWayland applications\n\
  wayland-doctor Probe compositor protocols without claiming the input method\n\
  panel        Open the explicit native composition window\n\
  candidates   Show candidates for --text\n\
  convert      Write chosen candidate to stdout\n\
  commit       Focus --window and send the chosen candidate\n\
  windows      List native target windows as JSON\n\
  doctor       Print actual native backend capabilities\n\
  features     Print compiled features as JSON\n\n\
Options:\n\
  --engine pinyin|kana|unicode|literal|table    Default: pinyin\n\
  --dict PATH         UTF-8 TSV table for pinyin/table\n\
  --text TEXT         Composition; '-' reads bounded UTF-8 stdin\n\
  --candidate 1..9    Candidate to convert/commit (default: 1)\n\
  --window ID         Explicit target from 'ztype windows'\n\n\
  --plugin PATH       Explicit trusted native zinput engine library\n\n\
Examples:\n\
  ztype convert --text nihao\n\
  ztype convert --engine kana --text konnichiha\n\
  ztype convert --engine unicode --text 'U+1F30D U+4F60 U+597D'\n\
  ztype panel --window 0x0400001\n\n\
Linux X11/XWayland native adapter; application acceptance varies.\n\
Native Wayland requires input-method-v2 and virtual-keyboard-v1.\n\
X11/XWayland clients use XMODIFIERS=@im=ztype.\n\
Windows TSF and macOS IMK frontends are not implemented."
            );
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
        "start" | "stop" | "status" => {
            if options.has_options {
                return Err("start, stop and status do not accept conversion options; use the foreground wayland or x11 commands to choose an engine".into());
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
                    return Err("the native trial accepts --engine and --dict only".into());
                }
                if !matches!(options.engine.as_str(), "pinyin" | "kana" | "table") {
                    return Err("the native trial supports pinyin, kana and table".into());
                }
                return trial::run(&options.engine, options.dictionary.as_deref());
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
                    wayland::run(engine(&options)?)
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
                return x11::run(engine(&options)?);
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
