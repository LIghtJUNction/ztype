use std::io::{self, Read, Write};
use zinput::ime::{ContextId, TextTarget};
use ztype::{MAX_TEXT_BYTES, Session, engine::Engine};

#[cfg(feature = "gui")]
mod panel;
#[cfg(all(feature = "wayland", target_os = "linux"))]
mod trial;
#[cfg(all(feature = "wayland", target_os = "linux"))]
mod wayland;

#[derive(Default)]
struct Options {
    command: String,
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

fn parse() -> Result<Options, String> {
    let mut args = std::env::args().skip(1);
    let command = args.next().unwrap_or_else(|| {
        if cfg!(all(feature = "wayland", target_os = "linux"))
            && std::env::var_os("WAYLAND_DISPLAY").is_some()
        {
            "try".into()
        } else {
            "panel".into()
        }
    });
    let mut options = Options {
        command,
        engine: "pinyin".into(),
        ..Options::default()
    };
    while let Some(flag) = args.next() {
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
  try          Open the native typing trial (default on Wayland)\n\
  wayland      Native Wayland input method (Ctrl+Space switches mode)\n\
  wayland-doctor Probe compositor protocols without claiming the input method\n\
  panel        Native composition window (default on X11)\n\
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
XIM, Windows TSF and macOS IMK frontends are not implemented."
            );
            return Ok(());
        }
        "features" => {
            println!(
                "{{\"version\":\"{}\",\"gui\":{},\"desktop\":{},\"plugins\":{},\"wayland\":{}}}",
                env!("CARGO_PKG_VERSION"),
                cfg!(feature = "gui"),
                cfg!(feature = "desktop"),
                cfg!(feature = "plugins"),
                cfg!(all(feature = "wayland", target_os = "linux")),
            );
            return Ok(());
        }
        "--version" | "version" => {
            println!("ztype {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
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
