use crate::{MAX_CANDIDATES, MAX_TEXT_BYTES, validate_text};

/// Reuses string capacity across edits; candidate count and size are bounded.
pub struct Candidates {
    values: [String; MAX_CANDIDATES],
    len: usize,
}

impl Default for Candidates {
    fn default() -> Self {
        Self::new()
    }
}
impl Candidates {
    pub fn new() -> Self {
        Self {
            values: std::array::from_fn(|_| String::with_capacity(64)),
            len: 0,
        }
    }
    pub fn clear(&mut self) {
        self.len = 0;
    }
    pub fn len(&self) -> usize {
        self.len
    }
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
    pub fn get(&self, index: usize) -> Option<&str> {
        (index < self.len).then(|| self.values[index].as_str())
    }
    pub fn iter(&self) -> impl Iterator<Item = &str> {
        self.values[..self.len].iter().map(String::as_str)
    }
    pub(crate) fn push(&mut self, value: &str) -> Result<(), String> {
        validate_text(value)?;
        if value.is_empty() || self.len == MAX_CANDIDATES || self.iter().any(|x| x == value) {
            return Ok(());
        }
        self.values[self.len].clear();
        self.values[self.len].push_str(value);
        self.len += 1;
        Ok(())
    }
}

#[derive(Clone)]
pub(crate) struct Entry {
    pub(crate) code: Box<str>,
    pub(crate) text: Box<str>,
    pub(crate) priority: u32,
    pub(crate) learned: u32,
}

/// A bounded, sorted dictionary. Vocabulary is data, independent of the engine.
pub struct Lexicon {
    pub(crate) entries: Vec<Entry>,
    max_code_bytes: usize,
    learning_path: Option<std::path::PathBuf>,
}

impl Lexicon {
    pub fn parse(tsv: &str) -> Result<Self, String> {
        if tsv.len() > crate::dictionary::MAX_DICTIONARY_BYTES {
            return Err("dictionary exceeds 64 MiB".into());
        }
        let mut entries = Vec::new();
        for (line_no, line) in tsv.lines().enumerate() {
            let line = line.trim_end_matches('\r');
            if line.trim().is_empty() || line.trim_start().starts_with('#') {
                continue;
            }
            let mut fields = line.split('\t');
            let code = fields.next().unwrap();
            let text = fields
                .next()
                .ok_or_else(|| format!("dictionary line {} needs code and text", line_no + 1))?;
            let priority = fields
                .next()
                .map_or(Ok(0), str::parse::<u32>)
                .map_err(|_| format!("invalid priority on dictionary line {}", line_no + 1))?;
            if fields.next().is_some()
                || code.is_empty()
                || code.len() > 128
                || !code.bytes().all(|b| {
                    b.is_ascii_lowercase() || b.is_ascii_digit() || matches!(b, b'\'' | b'-' | b'_')
                })
                || text.is_empty()
                || text.chars().any(char::is_control)
            {
                return Err(format!("invalid dictionary entry on line {}", line_no + 1));
            }
            validate_text(text)?;
            entries.push(Entry {
                code: code.into(),
                text: text.into(),
                priority,
                learned: 0,
            });
            if entries.len() > crate::dictionary::MAX_DICTIONARY_ENTRIES {
                return Err("dictionary exceeds 2000000 entries".into());
            }
        }
        if entries.is_empty() {
            return Err("dictionary has no entries".into());
        }
        let mut result = Self {
            entries,
            max_code_bytes: 0,
            learning_path: None,
        };
        result.sort();
        Ok(result)
    }

    fn sort(&mut self) {
        self.entries
            .sort_unstable_by(|a, b| a.code.cmp(&b.code).then(a.text.cmp(&b.text)));
        self.entries.dedup_by(|a, b| {
            if a.code == b.code && a.text == b.text {
                b.priority = b.priority.max(a.priority);
                b.learned = b.learned.max(a.learned);
                true
            } else {
                false
            }
        });
        self.max_code_bytes = self.entries.iter().map(|e| e.code.len()).max().unwrap_or(0);
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub(crate) fn extend(&mut self, mut other: Self, user: bool) -> Result<(), String> {
        if self.entries.len().saturating_add(other.entries.len())
            > crate::dictionary::MAX_DICTIONARY_ENTRIES
        {
            return Err("combined dictionaries exceed 2000000 entries".into());
        }
        if user {
            for entry in &mut other.entries {
                entry.learned = entry.priority.max(1);
            }
        }
        self.entries.append(&mut other.entries);
        self.sort();
        Ok(())
    }

    pub(crate) fn set_learning_path(&mut self, path: std::path::PathBuf) {
        self.learning_path = Some(path);
    }

    fn compare_rank(a: &Entry, b: &Entry) -> std::cmp::Ordering {
        b.learned
            .cmp(&a.learned)
            .then(b.priority.cmp(&a.priority))
            .then(a.code.len().cmp(&b.code.len()))
            .then(a.code.cmp(&b.code))
            .then(a.text.cmp(&b.text))
    }

    fn start(&self, code: &str) -> usize {
        self.entries
            .partition_point(|entry| entry.code.as_ref() < code)
    }

    fn exact(&self, code: &str) -> impl Iterator<Item = &Entry> {
        self.entries[self.start(code)..]
            .iter()
            .take_while(move |e| e.code.as_ref() == code)
    }

    fn append_ranked<'a>(
        values: impl Iterator<Item = &'a Entry>,
        out: &mut Candidates,
    ) -> Result<(), String> {
        let remaining = MAX_CANDIDATES - out.len();
        if remaining == 0 {
            return Ok(());
        }
        // Prefixes can contain hundreds of thousands of words. Partition in
        // linear time, then sort only the bounded page of useful candidates.
        let mut entries: Vec<_> = values.collect();
        if entries.len() > remaining {
            entries.select_nth_unstable_by(remaining, |a, b| Self::compare_rank(a, b));
            entries.truncate(remaining);
        }
        entries.sort_unstable_by(|a, b| Self::compare_rank(a, b));
        for entry in entries {
            out.push(&entry.text)?;
        }
        Ok(())
    }

    fn lookup(&self, input: &str, out: &mut Candidates) -> Result<(), String> {
        if !input.is_ascii() {
            return out.push(input);
        }
        let code: String = input
            .bytes()
            .filter(|b| *b != b' ')
            .map(|b| b.to_ascii_lowercase() as char)
            .collect();
        if code.is_empty() {
            return Ok(());
        }
        Self::append_ranked(self.exact(&code), out)?;
        Self::append_ranked(
            self.entries[self.start(&code)..]
                .iter()
                .take_while(|e| e.code.starts_with(&code))
                .filter(|e| e.code.as_ref() != code),
            out,
        )?;
        if out.is_empty() {
            out.push(input)?;
        }
        Ok(())
    }

    /// Segment an arbitrary full spelling into dictionary words. Unknown
    /// suffixes preserve the complete literal input; no suffix is discarded.
    fn compose_pinyin(&self, code: &str) -> Option<String> {
        let mut steps = vec![PinyinStep::UNREACHABLE; code.len() + 1];
        steps[code.len()].segments = 0;
        for start in (0..code.len()).rev() {
            if code.as_bytes()[start] == b'\'' {
                if start > 0
                    && start + 1 < code.len()
                    && code.as_bytes()[start - 1] != b'\''
                    && code.as_bytes()[start + 1] != b'\''
                    && steps[start + 1].reachable()
                {
                    steps[start] = PinyinStep {
                        entry: u32::MAX,
                        next: (start + 1) as u16,
                        ..steps[start + 1]
                    };
                }
                continue;
            }
            for end in start + 1..=code.len().min(start + self.max_code_bytes) {
                let tail = steps[end];
                if !tail.reachable() {
                    continue;
                }
                let key = &code[start..end];
                let begin = self.start(key);
                let Some((index, entry)) = self.entries[begin..]
                    .iter()
                    .enumerate()
                    .take_while(|(_, e)| e.code.as_ref() == key)
                    .min_by(|(_, a), (_, b)| Self::compare_rank(a, b))
                    .map(|(i, e)| (begin + i, e))
                else {
                    continue;
                };
                let bytes = entry.text.len() + usize::from(tail.bytes);
                if bytes > MAX_TEXT_BYTES {
                    continue;
                }
                let step = PinyinStep {
                    entry: index as u32,
                    next: end as u16,
                    segments: tail.segments + 1,
                    bytes: bytes as u16,
                    priority: tail
                        .priority
                        .saturating_add(u64::from(entry.priority))
                        .saturating_add(u64::from(entry.learned) * u64::from(u32::MAX)),
                };
                if step.better_than(steps[start]) {
                    steps[start] = step;
                }
            }
        }
        if !steps[0].reachable() {
            return None;
        }
        let mut text = String::with_capacity(usize::from(steps[0].bytes));
        let mut offset = 0;
        while offset < code.len() {
            let step = steps[offset];
            if step.entry != u32::MAX {
                text.push_str(&self.entries[step.entry as usize].text);
            }
            offset = usize::from(step.next);
        }
        Some(text)
    }

    fn pinyin_candidates(&self, input: &str, out: &mut Candidates) -> Result<(), String> {
        let Some(code) = normalize_pinyin(input) else {
            return out.push(input);
        };
        if code.is_empty() {
            return Ok(());
        }
        Self::append_ranked(self.exact(&code), out)?;
        if out.is_empty()
            && let Some(text) = self.compose_pinyin(&code)
        {
            out.push(&text)?;
        }
        Self::append_ranked(
            self.entries[self.start(&code)..]
                .iter()
                .take_while(|e| e.code.starts_with(&code))
                .filter(|e| e.code.as_ref() != code),
            out,
        )?;
        if out.is_empty() {
            out.push(input)?;
        }
        Ok(())
    }

    fn record_selection(&mut self, input: &str, text: &str) -> Result<(), String> {
        let Some(path) = self.learning_path.as_ref() else {
            return Ok(());
        };
        let Some(code) = normalize_pinyin(input) else {
            return Ok(());
        };
        if code.is_empty() || code.len() > 128 || text == input {
            return Ok(());
        }
        let count = crate::dictionary::save_selection(path, &code, text)?;
        let start = self.start(&code);
        if let Some(entry) = self.entries[start..]
            .iter_mut()
            .take_while(|e| e.code.as_ref() == code)
            .find(|e| e.text.as_ref() == text)
        {
            entry.learned = count;
        } else if self.entries.len() < crate::dictionary::MAX_DICTIONARY_ENTRIES {
            self.max_code_bytes = self.max_code_bytes.max(code.len());
            self.entries.insert(
                start,
                Entry {
                    code: code.into(),
                    text: text.into(),
                    priority: 0,
                    learned: count,
                },
            );
        }
        Ok(())
    }
}

fn normalize_pinyin(input: &str) -> Option<String> {
    let mut code = String::with_capacity(input.len());
    for ch in input.chars() {
        match ch {
            'A'..='Z' => code.push(ch.to_ascii_lowercase()),
            'a'..='z' | '\'' | '0'..='9' | '-' | '_' => code.push(ch),
            ' ' => {}
            'ü' | 'Ü' => code.push('v'),
            ':' if code.ends_with('u') => {
                code.pop();
                code.push('v');
            }
            _ => return None,
        }
    }
    Some(code)
}

#[derive(Clone, Copy)]
struct PinyinStep {
    priority: u64,
    entry: u32,
    next: u16,
    segments: u16,
    bytes: u16,
}

impl PinyinStep {
    const UNREACHABLE: Self = Self {
        priority: 0,
        entry: u32::MAX,
        next: 0,
        segments: u16::MAX,
        bytes: 0,
    };

    fn reachable(self) -> bool {
        self.segments != u16::MAX
    }

    fn better_than(self, other: Self) -> bool {
        self.segments < other.segments
            || (self.segments == other.segments
                && (self.priority > other.priority
                    || (self.priority == other.priority && self.bytes < other.bytes)))
    }
}

pub enum Engine {
    Pinyin(Lexicon),
    Table(Lexicon),
    Kana,
    Unicode,
    Literal,
    #[cfg(feature = "plugins")]
    Plugin(Box<crate::plugin::NativeEngine>),
}
impl Engine {
    pub(crate) fn reset_context(&mut self, context: u64) -> Result<(), String> {
        #[cfg(feature = "plugins")]
        if let Self::Plugin(engine) = self {
            return engine.reset_context(context);
        }
        let _ = context;
        Ok(())
    }
    pub(crate) fn resolve<'a>(
        &mut self,
        index: usize,
        shown: &'a str,
    ) -> Result<std::borrow::Cow<'a, str>, String> {
        #[cfg(feature = "plugins")]
        if let Self::Plugin(engine) = self {
            return engine.resolve(index, shown).map(std::borrow::Cow::Owned);
        }
        let _ = index;
        Ok(std::borrow::Cow::Borrowed(shown))
    }
    pub fn dictionary_entries(&self) -> usize {
        match self {
            Self::Pinyin(table) | Self::Table(table) => table.len(),
            _ => 0,
        }
    }

    pub(crate) fn record_selection(&mut self, input: &str, text: &str) {
        if let Self::Pinyin(table) | Self::Table(table) = self
            && let Err(error) = table.record_selection(input, text)
        {
            // Text is already delivered. A storage failure must not invite a
            // duplicate commit; report it without including the selected text.
            eprintln!("ztype: user dictionary was not saved: {error}");
        }
    }

    pub fn named(name: &str, dictionary: Option<&str>) -> Result<Self, String> {
        match (name, dictionary) {
            ("pinyin", None) => Ok(Self::Pinyin(Lexicon::parse(include_str!(
                "../data/pinyin.tsv"
            ))?)),
            ("pinyin", Some(tsv)) => Ok(Self::Pinyin(Lexicon::parse(tsv)?)),
            ("table", Some(tsv)) => Ok(Self::Table(Lexicon::parse(tsv)?)),
            ("table", None) => Err("table engine requires --dict PATH".into()),
            (_, Some(_)) => Err("--dict is supported by pinyin and table engines".into()),
            ("kana", None) => Ok(Self::Kana),
            ("unicode", None) => Ok(Self::Unicode),
            ("literal", None) => Ok(Self::Literal),
            _ => Err(format!(
                "unknown engine {name:?}; use pinyin, kana, unicode, literal or table"
            )),
        }
    }
    pub fn candidates(&mut self, input: &str, output: &mut Candidates) -> Result<(), String> {
        validate_text(input)?;
        output.clear();
        if input.is_empty() {
            return Ok(());
        }
        match self {
            #[cfg(feature = "plugins")]
            Self::Plugin(engine) => engine.candidates(input, output)?,
            Self::Pinyin(table) => table.pinyin_candidates(input, output)?,
            Self::Table(table) => table.lookup(input, output)?,
            Self::Literal => output.push(input)?,
            Self::Kana => {
                let hiragana = romaji(input)?;
                let katakana: String = hiragana
                    .chars()
                    .map(|c| {
                        if ('\u{3041}'..='\u{3096}').contains(&c) {
                            char::from_u32(c as u32 + 0x60).unwrap()
                        } else {
                            c
                        }
                    })
                    .collect();
                output.push(&hiragana)?;
                output.push(&katakana)?;
                output.push(input)?;
            }
            Self::Unicode => {
                let mut text = String::new();
                for scalar in input.split_whitespace() {
                    let hex = scalar
                        .strip_prefix("U+")
                        .or_else(|| scalar.strip_prefix("u+"))
                        .unwrap_or(scalar);
                    if hex.is_empty()
                        || hex.len() > 6
                        || !hex.bytes().all(|b| b.is_ascii_hexdigit())
                    {
                        return Err(format!("invalid Unicode scalar {scalar:?}"));
                    }
                    let value =
                        u32::from_str_radix(hex, 16).map_err(|_| "invalid Unicode number")?;
                    let c = char::from_u32(value)
                        .filter(|c| !c.is_control())
                        .ok_or_else(|| format!("unsupported Unicode scalar {scalar:?}"))?;
                    text.push(c);
                }
                output.push(&text)?;
            }
        }
        Ok(())
    }
}

/// Bounded Hepburn-style conversion. Unknown characters are preserved.
/// This is transliteration, not Japanese word/kanji prediction.
pub fn romaji(input: &str) -> Result<String, String> {
    validate_text(input)?;
    let lower = input.to_ascii_lowercase();
    let mut rest = lower.as_str();
    let mut out = String::with_capacity(input.len());
    while !rest.is_empty() {
        let bytes = rest.as_bytes();
        if bytes.len() >= 2 && bytes[0] == bytes[1] && b"bcdfghjklmpqrstvwxyz".contains(&bytes[0]) {
            out.push('っ');
            rest = &rest[1..];
            continue;
        }
        if rest.starts_with("n'") {
            out.push('ん');
            rest = &rest[2..];
            continue;
        }
        if rest.starts_with('n')
            && (rest.len() == 1
                || (bytes.len() >= 2 && !b"aiueoy".contains(&bytes[1]) && bytes[1] != b'\''))
        {
            out.push('ん');
            rest = if rest == "nn" { "" } else { &rest[1..] };
            continue;
        }
        if let Some(&(roman, kana)) = SYLLABLES.iter().find(|(roman, _)| rest.starts_with(roman)) {
            out.push_str(kana);
            rest = &rest[roman.len()..];
        } else {
            let ch = rest.chars().next().unwrap();
            out.push(ch);
            rest = &rest[ch.len_utf8()..];
        }
        if out.len() > MAX_TEXT_BYTES {
            return Err("converted text exceeds 4096 UTF-8 bytes".into());
        }
    }
    validate_text(&out)?;
    Ok(out)
}

const SYLLABLES: &[(&str, &str)] = &[
    ("kya", "きゃ"),
    ("kyu", "きゅ"),
    ("kyo", "きょ"),
    ("gya", "ぎゃ"),
    ("gyu", "ぎゅ"),
    ("gyo", "ぎょ"),
    ("sha", "しゃ"),
    ("shu", "しゅ"),
    ("sho", "しょ"),
    ("sya", "しゃ"),
    ("syu", "しゅ"),
    ("syo", "しょ"),
    ("cha", "ちゃ"),
    ("chu", "ちゅ"),
    ("cho", "ちょ"),
    ("tya", "ちゃ"),
    ("tyu", "ちゅ"),
    ("tyo", "ちょ"),
    ("nya", "にゃ"),
    ("nyu", "にゅ"),
    ("nyo", "にょ"),
    ("hya", "ひゃ"),
    ("hyu", "ひゅ"),
    ("hyo", "ひょ"),
    ("bya", "びゃ"),
    ("byu", "びゅ"),
    ("byo", "びょ"),
    ("pya", "ぴゃ"),
    ("pyu", "ぴゅ"),
    ("pyo", "ぴょ"),
    ("mya", "みゃ"),
    ("myu", "みゅ"),
    ("myo", "みょ"),
    ("rya", "りゃ"),
    ("ryu", "りゅ"),
    ("ryo", "りょ"),
    ("shi", "し"),
    ("chi", "ち"),
    ("tsu", "つ"),
    ("dzu", "づ"),
    ("dji", "ぢ"),
    ("ltsu", "っ"),
    ("xtsu", "っ"),
    ("la", "ぁ"),
    ("li", "ぃ"),
    ("lu", "ぅ"),
    ("le", "ぇ"),
    ("lo", "ぉ"),
    ("xa", "ぁ"),
    ("xi", "ぃ"),
    ("xu", "ぅ"),
    ("xe", "ぇ"),
    ("xo", "ぉ"),
    ("ja", "じゃ"),
    ("ju", "じゅ"),
    ("jo", "じょ"),
    ("ji", "じ"),
    ("fa", "ふぁ"),
    ("fi", "ふぃ"),
    ("fe", "ふぇ"),
    ("fo", "ふぉ"),
    ("fu", "ふ"),
    ("va", "ゔぁ"),
    ("vi", "ゔぃ"),
    ("vu", "ゔ"),
    ("ve", "ゔぇ"),
    ("vo", "ゔぉ"),
    ("ka", "か"),
    ("ki", "き"),
    ("ku", "く"),
    ("ke", "け"),
    ("ko", "こ"),
    ("ga", "が"),
    ("gi", "ぎ"),
    ("gu", "ぐ"),
    ("ge", "げ"),
    ("go", "ご"),
    ("sa", "さ"),
    ("si", "し"),
    ("su", "す"),
    ("se", "せ"),
    ("so", "そ"),
    ("za", "ざ"),
    ("zi", "じ"),
    ("zu", "ず"),
    ("ze", "ぜ"),
    ("zo", "ぞ"),
    ("ta", "た"),
    ("ti", "ち"),
    ("tu", "つ"),
    ("te", "て"),
    ("to", "と"),
    ("da", "だ"),
    ("di", "ぢ"),
    ("du", "づ"),
    ("de", "で"),
    ("do", "ど"),
    ("na", "な"),
    ("ni", "に"),
    ("nu", "ぬ"),
    ("ne", "ね"),
    ("no", "の"),
    ("ha", "は"),
    ("hi", "ひ"),
    ("hu", "ふ"),
    ("he", "へ"),
    ("ho", "ほ"),
    ("ba", "ば"),
    ("bi", "び"),
    ("bu", "ぶ"),
    ("be", "べ"),
    ("bo", "ぼ"),
    ("pa", "ぱ"),
    ("pi", "ぴ"),
    ("pu", "ぷ"),
    ("pe", "ぺ"),
    ("po", "ぽ"),
    ("ma", "ま"),
    ("mi", "み"),
    ("mu", "む"),
    ("me", "め"),
    ("mo", "も"),
    ("ya", "や"),
    ("yu", "ゆ"),
    ("yo", "よ"),
    ("ra", "ら"),
    ("ri", "り"),
    ("ru", "る"),
    ("re", "れ"),
    ("ro", "ろ"),
    ("wa", "わ"),
    ("wo", "を"),
    ("wi", "うぃ"),
    ("we", "うぇ"),
    ("a", "あ"),
    ("i", "い"),
    ("u", "う"),
    ("e", "え"),
    ("o", "お"),
    ("-", "ー"),
    (",", "、"),
    (".", "。"),
];
