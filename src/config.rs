//! Configuration shared by CLI, Wayland and XIM.
use std::path::{Path, PathBuf};
use ztype::input_method::InputOptions;

/// Standard XIM contexts have no trustworthy field-purpose hint.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum XimUnknownPurpose {
    #[default]
    Passthrough,
    Manual,
}

impl XimUnknownPurpose {
    fn name(self) -> &'static str {
        match self {
            Self::Passthrough => "passthrough",
            Self::Manual => "manual",
        }
    }
}

#[derive(Clone, Debug)]
pub struct Config {
    pub path: Option<PathBuf>,
    pub engine: String,
    pub dictionaries: Vec<PathBuf>,
    pub explicit_dictionaries: bool,
    pub user_dictionary: Option<PathBuf>,
    pub learning: bool,
    pub input: InputOptions,
    pub xim_unknown_purpose: XimUnknownPurpose,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            path: None,
            engine: "pinyin".into(),
            dictionaries: Vec::new(),
            explicit_dictionaries: false,
            user_dictionary: None,
            learning: false,
            input: InputOptions::default(),
            xim_unknown_purpose: XimUnknownPurpose::default(),
        }
    }
}

fn env_path(name: &str) -> Option<PathBuf> {
    std::env::var_os(name)
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .filter(|p| !name.starts_with("XDG_") || p.is_absolute())
}

fn home() -> Option<PathBuf> {
    env_path("HOME").or_else(|| env_path("USERPROFILE"))
}

pub fn default_path() -> Option<PathBuf> {
    env_path("XDG_CONFIG_HOME")
        .or_else(|| home().map(|p| p.join(".config")))
        .map(|p| p.join("ztype/config"))
}

impl Config {
    pub fn load(explicit: Option<&Path>, disabled: bool) -> Result<Self, String> {
        if disabled {
            if explicit.is_some() {
                return Err("--config and --no-config cannot be combined".into());
            }
            return Ok(Self::default());
        }
        let mut config = Self::default();
        let path = explicit
            .map(PathBuf::from)
            .or_else(|| env_path("ZTYPE_CONFIG"))
            .or_else(default_path);
        if let Some(path) = &path {
            match std::fs::read_to_string(path) {
                Ok(text) => config = Self::parse(&text, path)?,
                Err(e)
                    if e.kind() == std::io::ErrorKind::NotFound
                        && explicit.is_none()
                        && env_path("ZTYPE_CONFIG").is_none() => {}
                Err(e) => return Err(format!("cannot read configuration {}: {e}", path.display())),
            }
        }
        config.path = path;
        if !config.explicit_dictionaries {
            config.dictionaries = discover_dictionaries()?;
        }
        if config.learning && config.user_dictionary.is_none() {
            return Err("learning=true requires an explicit user_dictionary path".into());
        }
        Ok(config)
    }

    fn parse(text: &str, path: &Path) -> Result<Self, String> {
        let mut config = Self::default();
        let directory = path.parent().unwrap_or_else(|| Path::new("."));
        let mut seen = std::collections::HashSet::new();
        for (index, line) in text.lines().enumerate() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let location = || format!("{}:{}", path.display(), index + 1);
            let (key, value) = line
                .split_once('=')
                .ok_or_else(|| format!("{}: expected key=value", location()))?;
            let key = key.trim();
            let value = value.trim();
            let value = if value.len() >= 2
                && ((value.starts_with('"') && value.ends_with('"'))
                    || (value.starts_with('\'') && value.ends_with('\'')))
            {
                &value[1..value.len() - 1]
            } else {
                value
            };
            if key != "dictionary" && !seen.insert(key.to_owned()) {
                return Err(format!("{}: repeated setting {key:?}", location()));
            }
            let boolean = || match value {
                "true" => Ok(true),
                "false" => Ok(false),
                _ => Err(format!("{}: {key} must be true or false", location())),
            };
            match key {
                "engine"
                    if matches!(value, "pinyin" | "kana" | "table" | "unicode" | "literal") =>
                {
                    config.engine = value.into()
                }
                "engine" => return Err(format!("{}: unknown engine {value:?}", location())),
                "dictionary" => {
                    config.explicit_dictionaries = true;
                    config.dictionaries.push(
                        resolve_path(value, directory)
                            .map_err(|e| format!("{}: {e}", location()))?,
                    );
                }
                "user_dictionary" => {
                    config.user_dictionary = Some(
                        resolve_path(value, directory)
                            .map_err(|e| format!("{}: {e}", location()))?,
                    )
                }
                "learning" => config.learning = boolean()?,
                "xim_unknown_purpose" => {
                    config.xim_unknown_purpose = match value {
                        "passthrough" => XimUnknownPurpose::Passthrough,
                        "manual" => XimUnknownPurpose::Manual,
                        _ => {
                            return Err(format!(
                                "{}: xim_unknown_purpose must be passthrough or manual",
                                location()
                            ));
                        }
                    };
                }
                "page_size" => {
                    config.input.page_size = value
                        .parse::<usize>()
                        .ok()
                        .filter(|n| (1..=9).contains(n))
                        .ok_or_else(|| format!("{}: page_size must be 1..9", location()))?;
                }
                "initial_mode" => {
                    config.input.enabled = match value {
                        "chinese" => true,
                        "english" => false,
                        _ => {
                            return Err(format!(
                                "{}: initial_mode must be chinese or english",
                                location()
                            ));
                        }
                    }
                }
                "chinese_punctuation" => config.input.chinese_punctuation = boolean()?,
                "enter_behavior" => {
                    config.input.enter_raw = match value {
                        "raw" => true,
                        "candidate" => false,
                        _ => {
                            return Err(format!(
                                "{}: enter_behavior must be raw or candidate",
                                location()
                            ));
                        }
                    }
                }
                _ => return Err(format!("{}: unknown setting {key:?}", location())),
            }
        }
        Ok(config)
    }

    /// An engine override does not reuse a different engine's custom code table.
    /// Automatic system pinyin vocabulary remains available when selecting pinyin.
    pub fn dictionaries_for(&self, engine: &str) -> Result<Vec<PathBuf>, String> {
        match engine {
            "pinyin" if self.engine != "pinyin" && self.explicit_dictionaries => {
                discover_dictionaries()
            }
            "pinyin" => Ok(self.dictionaries.clone()),
            "table" if self.engine == "table" && self.explicit_dictionaries => {
                Ok(self.dictionaries.clone())
            }
            _ => Ok(Vec::new()),
        }
    }

    /// Learning and user dictionaries belong to the configured dictionary engine.
    pub fn personal_dictionary_for(&self, engine: &str) -> (Option<&Path>, bool) {
        if engine == self.engine && matches!(engine, "pinyin" | "table") {
            (self.user_dictionary.as_deref(), self.learning)
        } else {
            (None, false)
        }
    }

    pub fn describe(&self) -> String {
        let mut text = String::new();
        if let Some(path) = &self.path {
            text.push_str(&format!("config={}\n", path.display()));
        } else {
            text.push_str("config=disabled\n");
        }
        text.push_str(&format!("engine={}\n", self.engine));
        text.push_str(&format!(
            "xim_unknown_purpose={}\n",
            self.xim_unknown_purpose.name()
        ));
        for path in &self.dictionaries {
            text.push_str(&format!("dictionary={}\n", path.display()));
        }
        text.push_str(&format!("dictionary_files={}\n", self.dictionaries.len()));
        if let Some(path) = &self.user_dictionary {
            text.push_str(&format!("user_dictionary={}\n", path.display()));
        }
        text.push_str(&format!(
            "learning={}\npage_size={}\ninitial_mode={}\nchinese_punctuation={}\nenter_behavior={}\n",
            self.learning,
            self.input.page_size,
            if self.input.enabled { "chinese" } else { "english" },
            self.input.chinese_punctuation,
            if self.input.enter_raw { "raw" } else { "candidate" },
        ));
        text
    }
}

fn resolve_path(value: &str, directory: &Path) -> Result<PathBuf, String> {
    if value.is_empty() {
        return Err("dictionary path cannot be empty".into());
    }
    if let Some(rest) = value.strip_prefix("~/") {
        return home()
            .map(|p| p.join(rest))
            .ok_or_else(|| "HOME is unavailable for ~/ path".into());
    }
    let path = PathBuf::from(value);
    Ok(if path.is_absolute() {
        path
    } else {
        directory.join(path)
    })
}

fn discover_dictionaries() -> Result<Vec<PathBuf>, String> {
    let mut roots = Vec::new();
    // System dictionaries precede user dictionaries, allowing deliberate local overrides.
    if let Some(paths) = std::env::var_os("XDG_DATA_DIRS").filter(|p| !p.is_empty()) {
        roots.extend(std::env::split_paths(&paths).filter(|p| p.is_absolute()));
    } else if cfg!(unix) {
        roots.extend([
            PathBuf::from("/usr/local/share"),
            PathBuf::from("/usr/share"),
        ]);
    }
    if let Some(user) = env_path("XDG_DATA_HOME").or_else(|| home().map(|p| p.join(".local/share")))
    {
        roots.push(user);
    }
    dictionaries_in(&roots)
}

fn dictionaries_in(roots: &[PathBuf]) -> Result<Vec<PathBuf>, String> {
    let mut output = Vec::new();
    for root in roots {
        let directory = root.join("ztype/dictionaries");
        let entries = match std::fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(e) => {
                return Err(format!(
                    "cannot list dictionaries in {}: {e}",
                    directory.display()
                ));
            }
        };
        let mut paths = Vec::new();
        for entry in entries {
            let entry = entry.map_err(|e| format!("cannot read dictionary directory: {e}"))?;
            let path = entry.path();
            if path.extension().is_some_and(|extension| extension == "tsv") && path.is_file() {
                paths.push(path);
            }
        }
        paths.sort();
        for path in paths {
            if !output.contains(&path) {
                output.push(path);
            }
        }
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_xim_purpose_requires_explicit_manual_policy() {
        let path = Path::new("/fixture/config");
        assert_eq!(
            Config::parse("initial_mode=chinese", path)
                .unwrap()
                .xim_unknown_purpose,
            XimUnknownPurpose::Passthrough
        );
        let manual = Config::parse("xim_unknown_purpose=manual", path).unwrap();
        assert_eq!(manual.xim_unknown_purpose, XimUnknownPurpose::Manual);
        assert!(
            manual
                .describe()
                .lines()
                .any(|s| s == "xim_unknown_purpose=manual")
        );
        assert!(Config::parse("xim_unknown_purpose=normal", path).is_err());
    }

    #[test]
    fn configuration_controls_engine_paths_and_native_behavior() {
        let config = Config::parse(
            "engine=pinyin\ndictionary=words.tsv\ndictionary=\"/opt/dictionary one.tsv\"\nlearning=false\npage_size=7\ninitial_mode=english\nchinese_punctuation=false\nenter_behavior=candidate\n",
            Path::new("/tmp/preferences/config"),
        ).unwrap();
        assert_eq!(
            config.dictionaries,
            [
                PathBuf::from("/tmp/preferences/words.tsv"),
                PathBuf::from("/opt/dictionary one.tsv")
            ]
        );
        assert_eq!(config.input.page_size, 7);
        assert!(!config.input.enabled);
        assert!(!config.input.chinese_punctuation);
        assert!(!config.input.enter_raw);
        assert!(!config.learning);
    }

    #[test]
    fn invalid_settings_explain_the_file_line_and_setting() {
        for source in [
            "page_size=10",
            "learning=yes",
            "unknown=1",
            "engine=missing",
            "engine=pinyin\nengine=kana",
            "dictionary=",
        ] {
            let error = Config::parse(source, Path::new("/tmp/config")).unwrap_err();
            assert!(error.contains("/tmp/config:"), "{error}");
        }
    }

    #[test]
    fn no_config_is_reproducible_and_never_enables_history() {
        let config = Config::load(None, true).unwrap();
        assert!(!config.learning);
        assert!(config.dictionaries.is_empty());
        assert!(config.user_dictionary.is_none());
        assert!(Config::load(Some(Path::new("unused")), true).is_err());
    }

    #[test]
    fn discovery_loads_all_tables_in_stable_order_and_ignores_source_archives() {
        let root =
            std::env::temp_dir().join(format!("ztype-dictionary-discovery-{}", std::process::id()));
        let directory = root.join("ztype/dictionaries");
        std::fs::create_dir_all(&directory).unwrap();
        std::fs::write(directory.join("b.tsv"), "").unwrap();
        std::fs::write(directory.join("a.tsv"), "").unwrap();
        std::fs::write(directory.join("original.yaml"), "").unwrap();
        let paths = dictionaries_in(std::slice::from_ref(&root)).unwrap();
        assert_eq!(paths, [directory.join("a.tsv"), directory.join("b.tsv")]);
        std::fs::remove_dir_all(root).unwrap();
    }
}
