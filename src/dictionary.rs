//! External system vocabulary and explicitly enabled local personalization.
//!
//! System dictionaries are ordinary UTF-8 TSV data. They can be independently
//! installed and replaced without rebuilding the application or adding a native
//! input-method dependency. Loading vocabulary never enables learning.
use crate::engine::{Engine, Lexicon};
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

pub const MAX_DICTIONARY_BYTES: usize = 64 * 1024 * 1024;
pub const MAX_DICTIONARY_ENTRIES: usize = 2_000_000;
pub const MAX_DICTIONARY_FILES: usize = 32;
const MAX_USER_ENTRIES: usize = 20_000;
const MAX_USER_BYTES: usize = 8 * 1024 * 1024;

fn read_bounded(path: &Path, limit: usize) -> Result<String, String> {
    let file =
        File::open(path).map_err(|e| format!("cannot open dictionary {}: {e}", path.display()))?;
    let mut bytes = Vec::new();
    file.take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| format!("cannot read dictionary {}: {e}", path.display()))?;
    if bytes.len() > limit {
        return Err(format!(
            "dictionary {} exceeds its {} byte limit",
            path.display(),
            limit
        ));
    }
    String::from_utf8(bytes).map_err(|_| format!("dictionary {} must be UTF-8", path.display()))
}

/// Combine system dictionaries and an optional explicitly configured user file.
///
/// An empty system list uses the bundled bootstrap vocabulary. `learn == false`
/// performs no writes, creates no data directory, and does not record typing.
/// With learning enabled, only a successfully committed selected phrase and its
/// spelling/count are stored; surrounding application text is never read.
pub fn load_engine(
    name: &str,
    dictionaries: &[PathBuf],
    user_dictionary: Option<&Path>,
    learn: bool,
) -> Result<Engine, String> {
    if dictionaries.len() > MAX_DICTIONARY_FILES {
        return Err("at most 32 dictionary files may be loaded".into());
    }
    if !matches!(name, "pinyin" | "table") {
        if !dictionaries.is_empty() || user_dictionary.is_some() || learn {
            return Err("external dictionaries and learning require pinyin or table".into());
        }
        return Engine::named(name, None);
    }
    if learn && user_dictionary.is_none() {
        return Err("learning requires an explicit user_dictionary path".into());
    }
    let mut lexicon = if name == "pinyin" {
        Lexicon::parse(include_str!("../data/pinyin.tsv"))?
    } else if let Some(first) = dictionaries.first() {
        Lexicon::parse(&read_bounded(first, MAX_DICTIONARY_BYTES)?)
            .map_err(|e| format!("{}: {e}", first.display()))?
    } else {
        return Err("table engine requires a dictionary".into());
    };
    for path in dictionaries.iter().skip(usize::from(name == "table")) {
        let other = Lexicon::parse(&read_bounded(path, MAX_DICTIONARY_BYTES)?)
            .map_err(|e| format!("{}: {e}", path.display()))?;
        lexicon.extend(other, false)?;
    }
    if let Some(path) = user_dictionary {
        match File::open(path) {
            Ok(_) => {
                let content = read_bounded(path, MAX_USER_BYTES)?;
                if content
                    .lines()
                    .any(|l| !l.trim().is_empty() && !l.trim_start().starts_with('#'))
                {
                    let other = Lexicon::parse(&content)?;
                    if other.len() > MAX_USER_ENTRIES {
                        return Err("user dictionary exceeds 20000 entries".into());
                    }
                    lexicon.extend(other, true)?;
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => {
                return Err(format!(
                    "cannot open user dictionary {}: {e}",
                    path.display()
                ));
            }
        }
        if learn {
            lexicon.set_learning_path(path.to_path_buf());
        }
    }
    Ok(if name == "pinyin" {
        Engine::Pinyin(lexicon)
    } else {
        Engine::Table(lexicon)
    })
}

/// Serialize a short, explicitly enabled user vocabulary using a process lock.
/// Wayland and XIM services merge with the latest file under the same lock, so
/// their successive selections cannot overwrite each other's saved entries.
pub(crate) fn save_selection(path: &Path, code: &str, text: &str) -> Result<u32, String> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let filename = path.file_name().ok_or("user dictionary needs a filename")?;
    let mut lock_name = filename.to_os_string();
    lock_name.push(".lock");
    let lock_path = parent.join(lock_name);
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let lock = options.open(lock_path).map_err(|e| e.to_string())?;
    lock.lock().map_err(|e| e.to_string())?;

    let mut entries = match File::open(path) {
        Ok(_) => {
            let content = read_bounded(path, MAX_USER_BYTES)?;
            if content
                .lines()
                .any(|l| !l.trim().is_empty() && !l.trim_start().starts_with('#'))
            {
                Lexicon::parse(&content)?.entries
            } else {
                Vec::new()
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
        Err(e) => return Err(e.to_string()),
    };
    let count = if let Some(entry) = entries
        .iter_mut()
        .find(|e| e.code.as_ref() == code && e.text.as_ref() == text)
    {
        entry.priority = entry.priority.saturating_add(1);
        entry.priority
    } else {
        if entries.len() >= MAX_USER_ENTRIES {
            return Err("user dictionary reached 20000 entries; learning paused".into());
        }
        entries.push(crate::engine::Entry {
            code: code.into(),
            text: text.into(),
            priority: 1,
            learned: 0,
        });
        1
    };
    entries.sort_unstable_by(|a, b| a.code.cmp(&b.code).then(a.text.cmp(&b.text)));
    let mut content = String::from("# ztype user dictionary: spelling\tselected phrase\tcount\n");
    for entry in entries {
        use std::fmt::Write;
        writeln!(
            content,
            "{}\t{}\t{}",
            entry.code, entry.text, entry.priority
        )
        .expect("String formatting cannot fail");
    }
    if content.len() > MAX_USER_BYTES {
        return Err("user dictionary reached 8 MiB; learning paused".into());
    }
    let mut temporary_name = filename.to_os_string();
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    temporary_name.push(format!(".{}.{stamp}.tmp", std::process::id()));
    let temporary = parent.join(temporary_name);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary).map_err(|e| e.to_string())?;
    let result = (|| {
        file.write_all(content.as_bytes())
            .map_err(|e| e.to_string())?;
        file.sync_data().map_err(|e| e.to_string())?;
        drop(file);
        std::fs::rename(&temporary, path).map_err(|e| e.to_string())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result?;
    Ok(count)
}
