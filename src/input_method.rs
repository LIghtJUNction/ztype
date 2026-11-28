//! Keyboard-driven composition shared by native input-method frontends.
use crate::{Session, engine::Engine};
use zinput::ime::{ContextId, TextTarget};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Key<'a> {
    Text(&'a str),
    Space,
    Enter,
    Backspace,
    Delete,
    Left,
    Right,
    Home,
    End,
    Up,
    Down,
    PageUp,
    PageDown,
    Escape,
    Tab,
    Modifier,
    Other,
}

#[derive(Debug, Default, Eq, PartialEq)]
pub struct Action {
    pub consumed: bool,
    pub refresh: bool,
    pub commit: Option<String>,
}

/// User preferences shared by all native frontends.
#[derive(Clone, Copy, Debug)]
pub struct InputOptions {
    pub page_size: usize,
    pub enabled: bool,
    pub chinese_punctuation: bool,
    pub enter_raw: bool,
}

impl Default for InputOptions {
    fn default() -> Self {
        Self {
            page_size: 5,
            enabled: true,
            chinese_punctuation: true,
            enter_raw: true,
        }
    }
}

pub struct InputMethod<'a> {
    session: Session<'a>,
    enabled: bool,
    policy: InputPolicy,
    limit: usize,
    cursor: usize,
    options: InputOptions,
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum InputPolicy {
    Pinyin,
    Kana,
    Table,
}

impl InputPolicy {
    fn accepts(self, text: &str, before_cursor: &str) -> bool {
        !text.is_empty()
            && text.char_indices().all(|(offset, ch)| match self {
                Self::Pinyin if ch == '\u{308}' => {
                    let prefix = if offset == 0 {
                        before_cursor
                    } else {
                        &text[..offset]
                    };
                    prefix.ends_with(['u', 'U'])
                }
                Self::Pinyin => ch.is_alphabetic() || ch == '\'',
                Self::Kana => ch.is_alphabetic() || matches!(ch, '\'' | '-' | '.' | ','),
                Self::Table => ch.is_ascii_alphanumeric() || matches!(ch, '\'' | '-' | '_'),
            })
    }
}

struct Capture<'a> {
    text: String,
    suffix: &'a str,
    limit: usize,
    limit_exceeded: bool,
}

impl<'a> Capture<'a> {
    fn new(suffix: &'a str) -> Self {
        Self {
            text: String::new(),
            suffix,
            limit: crate::MAX_TEXT_BYTES,
            limit_exceeded: false,
        }
    }
}

impl TextTarget for Capture<'_> {
    type Error = &'static str;
    fn commit_text(&mut self, _: ContextId, text: &str) -> Result<(), Self::Error> {
        if text.len().saturating_add(self.suffix.len()) > self.limit {
            self.limit_exceeded = true;
            return Err("commit and punctuation exceed the frontend byte capacity");
        }
        self.text.push_str(text);
        self.text.push_str(self.suffix);
        Ok(())
    }
}

impl<'a> InputMethod<'a> {
    pub fn new(storage: &'a mut [u8], engine: Engine) -> Result<Self, String> {
        Self::with_options(storage, engine, InputOptions::default())
    }

    pub fn with_options(
        storage: &'a mut [u8],
        engine: Engine,
        mut options: InputOptions,
    ) -> Result<Self, String> {
        options.page_size = options.page_size.clamp(1, 9);
        let policy = match &engine {
            Engine::Pinyin(_) => InputPolicy::Pinyin,
            Engine::Kana => InputPolicy::Kana,
            Engine::Table(_) => InputPolicy::Table,
            _ => {
                return Err("native keyboard input supports pinyin, kana and table engines".into());
            }
        };
        let limit = storage.len().min(crate::MAX_TEXT_BYTES);
        Ok(Self {
            session: Session::new(
                storage,
                engine,
                ContextId::new(1).map_err(|e| e.to_string())?,
            )?,
            enabled: options.enabled,
            policy,
            limit,
            cursor: 0,
            options,
        })
    }

    pub fn enabled(&self) -> bool {
        self.enabled
    }

    pub fn input(&self) -> &str {
        self.session.input()
    }

    /// Borrow the current candidate list without a second composition state.
    pub fn candidates(&self) -> &crate::engine::Candidates {
        self.session.candidates()
    }

    pub fn selected(&self) -> usize {
        self.session.selected()
    }

    /// Table digits are input codes; pinyin and kana digits select candidates.
    pub fn number_selection(&self) -> bool {
        self.policy != InputPolicy::Table
    }

    pub fn page_size(&self) -> usize {
        self.options.page_size
    }

    /// Absolute candidate indices displayed together and selected by 1..9.
    pub fn page_range(&self) -> std::ops::Range<usize> {
        let start = self.selected() / self.page_size() * self.page_size();
        start..(start + self.page_size()).min(self.candidates().len())
    }

    pub fn input_cursor(&self) -> usize {
        self.cursor
    }

    pub fn preedit_cursor(&self) -> usize {
        if self.cursor < self.input().len() {
            self.cursor
        } else {
            self.preedit().len()
        }
    }

    pub fn preedit(&self) -> &str {
        if self.cursor < self.input().len() {
            return self.input();
        }
        self.session
            .candidates()
            .get(self.session.selected())
            .filter(|text| text.len() <= self.limit)
            .unwrap_or_else(|| self.session.input())
    }

    pub fn reset(&mut self) -> Result<(), String> {
        self.session.clear()?;
        self.cursor = 0;
        Ok(())
    }

    /// Cancel pending composition and require an explicit mode toggle to resume.
    pub fn disable(&mut self) -> Result<(), String> {
        self.enabled = false;
        self.reset()
    }

    pub fn focus(&mut self, generation: u64) -> Result<(), String> {
        let context = ContextId::new(generation).map_err(|e| e.to_string())?;
        if self.session.context() != context {
            self.session.set_context(context)?;
            self.cursor = 0;
        }
        Ok(())
    }

    fn edit(&mut self, value: &str, cursor: usize) -> Result<Action, String> {
        self.session.set_input(value)?;
        self.cursor = cursor;
        Ok(Action {
            consumed: true,
            refresh: true,
            commit: None,
        })
    }

    fn move_candidate(&mut self, backwards: bool) -> Result<Action, String> {
        let count = self.candidates().len();
        if count > 0 {
            let next = if backwards {
                (self.selected() + count - 1) % count
            } else {
                (self.selected() + 1) % count
            };
            self.session.select(next)?;
        }
        Ok(Action {
            consumed: true,
            refresh: true,
            commit: None,
        })
    }

    fn move_page(&mut self, backwards: bool) -> Result<Action, String> {
        let count = self.candidates().len();
        if count > 0 {
            let pages = count.div_ceil(self.page_size());
            let page = self.selected() / self.page_size();
            let next = if backwards {
                (page + pages - 1) % pages
            } else {
                (page + 1) % pages
            };
            self.session.select(next * self.page_size())?;
        }
        Ok(Action {
            consumed: true,
            refresh: true,
            commit: None,
        })
    }

    fn punctuation<'b>(&self, text: &'b str) -> &'b str {
        if self.policy != InputPolicy::Pinyin || !self.options.chinese_punctuation {
            return text;
        }
        match text {
            "," => "，",
            "." => "。",
            "!" => "！",
            "?" => "？",
            ";" => "；",
            ":" => "：",
            "(" => "（",
            ")" => "）",
            "[" => "【",
            "]" => "】",
            "\\" => "、",
            _ => text,
        }
    }

    fn accept_raw(&mut self) -> Result<Action, String> {
        let text = self.input().to_owned();
        self.reset()?;
        Ok(Action {
            consumed: true,
            refresh: true,
            commit: Some(text),
        })
    }

    fn accept(&mut self) -> Result<Action, String> {
        self.accept_with_suffix("")
    }

    fn accept_with_suffix(&mut self, suffix: &str) -> Result<Action, String> {
        let mut output = Capture::new(suffix);
        output.limit = self.limit;
        if let Err(error) = self.session.commit(&mut output) {
            if output.limit_exceeded {
                // The target rejected the complete output before copying it;
                // Session retains the composition for editing or plain acceptance.
                return Ok(Action {
                    consumed: true,
                    ..Action::default()
                });
            }
            return Err(error);
        }
        self.cursor = 0;
        Ok(Action {
            consumed: true,
            refresh: true,
            commit: Some(output.text),
        })
    }

    pub fn key(&mut self, key: Key<'_>, modifiers: u8) -> Result<Action, String> {
        use zinput::Modifiers;
        let command = modifiers
            & (Modifiers::CTRL
                | Modifiers::ALT
                | Modifiers::SUPER
                | Modifiers::HYPER
                | Modifiers::META);
        if key == Key::Space && command == Modifiers::CTRL && modifiers & Modifiers::SHIFT == 0 {
            self.reset()?;
            self.enabled = !self.enabled;
            return Ok(Action {
                consumed: true,
                refresh: true,
                commit: None,
            });
        }
        if !self.enabled || key == Key::Modifier {
            return Ok(Action::default());
        }
        if command != 0 {
            let refresh = !self.session.input().is_empty();
            if refresh {
                self.reset()?;
            }
            return Ok(Action {
                refresh,
                ..Action::default()
            });
        }

        let composing = !self.session.input().is_empty();
        match key {
            Key::Enter
                if composing && self.policy == InputPolicy::Pinyin && self.options.enter_raw =>
            {
                self.accept_raw()
            }
            Key::Space | Key::Enter if composing => self.accept(),
            Key::Backspace if composing => {
                let mut text = self.input().to_owned();
                let previous = text[..self.cursor]
                    .char_indices()
                    .next_back()
                    .map_or(0, |(i, _)| i);
                text.replace_range(previous..self.cursor, "");
                self.edit(&text, previous)
            }
            Key::Delete if composing => {
                let mut text = self.input().to_owned();
                if let Some(ch) = text[self.cursor..].chars().next() {
                    text.replace_range(self.cursor..self.cursor + ch.len_utf8(), "");
                }
                self.edit(&text, self.cursor)
            }
            Key::Left | Key::Right | Key::Home | Key::End if composing => {
                self.cursor = match key {
                    Key::Left => self.input()[..self.cursor]
                        .char_indices()
                        .next_back()
                        .map_or(0, |(i, _)| i),
                    Key::Right => {
                        self.cursor
                            + self.input()[self.cursor..]
                                .chars()
                                .next()
                                .map_or(0, char::len_utf8)
                    }
                    Key::Home => 0,
                    _ => self.input().len(),
                };
                Ok(Action {
                    consumed: true,
                    refresh: true,
                    commit: None,
                })
            }
            Key::Up if composing => self.move_candidate(true),
            Key::Down if composing => self.move_candidate(false),
            Key::PageUp if composing => self.move_page(true),
            Key::PageDown if composing => self.move_page(false),
            Key::Text("-") if composing && self.policy == InputPolicy::Pinyin => {
                self.move_page(true)
            }
            Key::Text("=") if composing && self.policy == InputPolicy::Pinyin => {
                self.move_page(false)
            }
            Key::Escape if composing => {
                self.reset()?;
                Ok(Action {
                    consumed: true,
                    refresh: true,
                    commit: None,
                })
            }
            Key::Tab if composing => self.move_candidate(modifiers & Modifiers::SHIFT != 0),
            Key::Text(text)
                if self.policy != InputPolicy::Table
                    && composing
                    && text.len() == 1
                    && matches!(text.as_bytes()[0], b'1'..=b'9') =>
            {
                let offset = usize::from(text.as_bytes()[0] - b'1');
                let index = self.page_range().start + offset;
                if offset < self.page_size() && index < self.session.candidates().len() {
                    self.session.select(index)?;
                    self.accept()
                } else {
                    Ok(Action {
                        consumed: true,
                        ..Action::default()
                    })
                }
            }
            Key::Text(text) if self.policy.accepts(text, &self.input()[..self.cursor]) => {
                let mut value = self.session.input().to_owned();
                if value.len().saturating_add(text.len()) > self.limit {
                    return Ok(Action {
                        consumed: true,
                        ..Action::default()
                    });
                }
                value.insert_str(self.cursor, text);
                // Kana can expand one ASCII byte to three UTF-8 bytes. Check
                // the converted size before changing Session: a failed engine
                // update invalidates its previous candidate, and the native
                // event loop must not exit for an ordinary capacity boundary.
                // Short input always fits; only near-capacity edits need the
                // extra conversion. Accepted kana text cannot contain NUL and
                // its input size was checked above, so a conversion error here
                // can only mean that the converted text exceeds the engine cap.
                if self.policy == InputPolicy::Kana
                    && value.len().saturating_mul(3) > self.limit
                    && !crate::engine::romaji(&value).is_ok_and(|kana| kana.len() <= self.limit)
                {
                    return Ok(Action {
                        consumed: true,
                        ..Action::default()
                    });
                }
                self.edit(&value, self.cursor + text.len())
            }
            Key::Text(text)
                if composing && !text.is_empty() && text.chars().all(|ch| !ch.is_control()) =>
            {
                self.accept_with_suffix(self.punctuation(text))
            }
            Key::Text(text) if self.punctuation(text) != text => Ok(Action {
                consumed: true,
                refresh: true,
                commit: Some(self.punctuation(text).to_owned()),
            }),
            _ => {
                if composing {
                    self.reset()?;
                }
                Ok(Action {
                    refresh: composing,
                    ..Action::default()
                })
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pinyin<'a>(buffer: &'a mut [u8]) -> InputMethod<'a> {
        InputMethod::new(buffer, Engine::named("pinyin", None).unwrap()).unwrap()
    }

    #[test]
    fn frontend_capacity_keeps_long_input_and_candidates_editable() {
        let mut bytes = [0; 4000];
        let dictionary = format!("a\t{}\t1\n", "中".repeat(1334));
        let mut ime = InputMethod::new(
            &mut bytes,
            Engine::named("table", Some(&dictionary)).unwrap(),
        )
        .unwrap();
        ime.key(Key::Text("a"), 0).unwrap();
        assert_eq!(ime.preedit(), "a");
        assert!(ime.key(Key::Enter, 0).unwrap().commit.is_none());
        assert_eq!(ime.input(), "a");
        ime.key(Key::Backspace, 0).unwrap();
        assert_eq!(ime.input(), "");
        ime.key(Key::Text(&"b".repeat(4000)), 0).unwrap();
        assert_eq!(ime.input().len(), 4000);
        ime.key(Key::Text("b"), 0).unwrap();
        assert_eq!(ime.input().len(), 4000);
    }

    #[test]
    fn composition_waits_for_acceptance_and_enter_is_consumed_once() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = pinyin(&mut bytes);
        let edit = ime.key(Key::Text("nihao"), 0).unwrap();
        assert!(edit.consumed);
        assert_eq!(edit.commit, None);
        assert_eq!(ime.preedit(), "你好");
        let accepted = ime.key(Key::Space, 0).unwrap();
        assert_eq!(accepted.commit.as_deref(), Some("你好"));
        assert!(ime.preedit().is_empty());
        assert!(!ime.key(Key::Enter, 0).unwrap().consumed);
    }

    #[test]
    fn decomposed_pinyin_umlaut_stays_in_preedit_until_confirmation() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let engine = Engine::named("pinyin", Some("lu\t路\nlv\t吕\n")).unwrap();
        let mut ime = InputMethod::new(&mut bytes, engine).unwrap();

        let type_umlaut = |ime: &mut InputMethod<'_>| {
            for (text, preedit) in [("lu", "路"), ("\u{308}", "吕")] {
                let edit = ime.key(Key::Text(text), 0).unwrap();
                assert_eq!(edit.commit, None);
                assert!(edit.consumed);
                assert_eq!(ime.preedit(), preedit);
            }
        };
        type_umlaut(&mut ime);
        assert_eq!(ime.key(Key::Escape, 0).unwrap().commit, None);
        assert!(ime.preedit().is_empty());
        type_umlaut(&mut ime);
        ime.focus(2).unwrap();
        assert!(ime.preedit().is_empty());
        assert_eq!(ime.key(Key::Space, 0).unwrap(), Action::default());

        let spelling = "LU\u{308}";
        assert_eq!(ime.key(Key::Text(spelling), 0).unwrap().commit, None);
        assert_eq!(
            ime.key(Key::Enter, 0).unwrap().commit.as_deref(),
            Some(spelling)
        );
        ime.key(Key::Text(spelling), 0).unwrap();
        assert_eq!(
            ime.key(Key::Space, 0).unwrap().commit.as_deref(),
            Some("吕")
        );
        assert_eq!(ime.key(Key::Enter, 0).unwrap(), Action::default());
    }

    #[test]
    fn focus_change_and_shortcut_discard_unaccepted_text() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = pinyin(&mut bytes);
        ime.key(Key::Text("nihao"), 0).unwrap();
        ime.focus(2).unwrap();
        assert_eq!(ime.preedit(), "");
        ime.key(Key::Text("nihao"), 0).unwrap();
        let copy = ime.key(Key::Text("c"), zinput::Modifiers::CTRL).unwrap();
        assert!(!copy.consumed);
        assert!(copy.refresh);
        assert!(copy.commit.is_none());
        assert_eq!(ime.preedit(), "");
    }

    #[test]
    fn mode_toggle_preserves_plain_english_shortcuts_and_returns_to_pinyin() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = pinyin(&mut bytes);
        assert!(
            ime.key(Key::Space, zinput::Modifiers::CTRL)
                .unwrap()
                .consumed
        );
        assert!(!ime.enabled());
        assert!(!ime.key(Key::Text("n"), 0).unwrap().consumed);
        ime.key(Key::Space, zinput::Modifiers::CTRL).unwrap();
        ime.key(Key::Text("nihao"), 0).unwrap();
        assert_eq!(
            ime.key(Key::Text("1"), 0).unwrap().commit.as_deref(),
            Some("你好")
        );
    }

    #[test]
    fn backspace_edits_phonetic_input_and_escape_never_commits() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = pinyin(&mut bytes);
        ime.key(Key::Text("nihaox"), 0).unwrap();
        ime.key(Key::Backspace, 0).unwrap();
        assert_eq!(ime.input(), "nihao");
        assert_eq!(ime.preedit(), "你好");
        assert!(ime.key(Key::Escape, 0).unwrap().commit.is_none());
        assert_eq!(ime.input(), "");
    }

    #[test]
    fn oversized_keystroke_does_not_drop_the_valid_composition() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = pinyin(&mut bytes);
        ime.key(Key::Text("nihao"), 0).unwrap();
        let text = "a".repeat(crate::MAX_TEXT_BYTES);
        assert!(ime.key(Key::Text(&text), 0).unwrap().consumed);
        assert_eq!(ime.input(), "nihao");
    }

    #[test]
    fn punctuation_follows_the_confirmed_candidate_once() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = pinyin(&mut bytes);
        ime.key(Key::Text("nihao"), 0).unwrap();
        assert_eq!(
            ime.key(Key::Text("!"), 0).unwrap().commit.as_deref(),
            Some("你好！")
        );
        assert_eq!(ime.input(), "");
    }
    #[test]
    fn kana_receives_long_vowels_and_japanese_punctuation() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = InputMethod::new(&mut bytes, Engine::Kana).unwrap();
        for ch in "ko-hi-,nihongo.".chars() {
            let mut utf8 = [0; 4];
            let action = ime.key(Key::Text(ch.encode_utf8(&mut utf8)), 0).unwrap();
            assert!(action.consumed);
            assert_eq!(action.commit, None);
        }
        assert_eq!(ime.preedit(), "こーひー、にほんご。");
        assert_eq!(
            ime.key(Key::Space, 0).unwrap().commit.as_deref(),
            Some("こーひー、にほんご。")
        );
    }

    #[test]
    fn kana_expansion_rejection_preserves_the_selected_candidate() {
        let mut bytes = [0; 4000];
        let mut ime = InputMethod::new(&mut bytes, Engine::Kana).unwrap();
        let original = "a".repeat(1333);
        ime.key(Key::Text(&original), 0).unwrap();
        ime.key(Key::Tab, 0).unwrap();
        let katakana = "ア".repeat(1333);
        assert_eq!(ime.preedit(), katakana);

        assert_eq!(
            ime.key(Key::Text("a"), 0).unwrap(),
            Action {
                consumed: true,
                ..Action::default()
            }
        );
        assert_eq!(ime.input(), original);
        assert_eq!(ime.preedit(), katakana);
        assert_eq!(ime.key(Key::Space, 0).unwrap().commit, Some(katakana));
    }

    #[test]
    fn kana_expansion_checks_small_buffers_and_complete_multi_character_keys() {
        let mut bytes = [0; 6];
        let mut ime = InputMethod::new(&mut bytes, Engine::Kana).unwrap();
        ime.key(Key::Text("a"), 0).unwrap();
        let rejected = ime.key(Key::Text("aka"), 0).unwrap();
        assert!(rejected.consumed);
        assert!(!rejected.refresh);
        assert_eq!(ime.input(), "a");
        assert_eq!(ime.preedit(), "あ");
        // The exact output size permits this edit although its worst-case
        // three-byte expansion would exceed the six-byte frontend buffer.
        ime.key(Key::Text("ka"), 0).unwrap();
        assert_eq!(ime.input(), "aka");
        assert_eq!(ime.preedit(), "あか");
        assert_eq!(
            ime.key(Key::Space, 0).unwrap().commit.as_deref(),
            Some("あか")
        );

        let mut bytes = [0; 2];
        let mut ime = InputMethod::new(&mut bytes, Engine::Kana).unwrap();
        let rejected = ime.key(Key::Text("a"), 0).unwrap();
        assert!(rejected.consumed);
        assert!(!rejected.refresh);
        assert!(ime.input().is_empty());
        ime.key(Key::Text("k"), 0).unwrap();
        assert_eq!(ime.preedit(), "k");
    }

    #[test]
    fn kana_expansion_over_engine_capacity_keeps_the_composition_editable() {
        let mut bytes = [0; 4000];
        let mut ime = InputMethod::new(&mut bytes, Engine::Kana).unwrap();
        ime.key(Key::Text("ka"), 0).unwrap();
        let rejected = ime.key(Key::Text(&"a".repeat(1366)), 0).unwrap();
        assert!(rejected.consumed);
        assert!(!rejected.refresh);
        assert_eq!(ime.input(), "ka");
        assert_eq!(ime.preedit(), "か");
        ime.key(Key::Backspace, 0).unwrap();
        ime.key(Key::Text("i"), 0).unwrap();
        assert_eq!(ime.preedit(), "き");
        assert_eq!(
            ime.key(Key::Enter, 0).unwrap().commit.as_deref(),
            Some("き")
        );
    }

    #[test]
    fn table_codes_keep_digits_underscores_and_hyphens() {
        let dictionary =
            "a1\t第一\t100\na1\t第二\t90\nfoo_bar\t下划线\nfoo-bar\t短横线\n123\t数字\n";
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = InputMethod::new(
            &mut bytes,
            Engine::named("table", Some(dictionary)).unwrap(),
        )
        .unwrap();
        for (code, expected) in [
            ("a1", "第一"),
            ("foo_bar", "下划线"),
            ("foo-bar", "短横线"),
            ("123", "数字"),
        ] {
            for ch in code.chars() {
                let mut utf8 = [0; 4];
                let action = ime.key(Key::Text(ch.encode_utf8(&mut utf8)), 0).unwrap();
                assert!(action.consumed);
                assert_eq!(action.commit, None, "{code}");
            }
            assert_eq!(ime.input(), code);
            assert_eq!(
                ime.key(Key::Space, 0).unwrap().commit.as_deref(),
                Some(expected)
            );
        }
        ime.key(Key::Text("a1"), 0).unwrap();
        ime.key(Key::Tab, 0).unwrap();
        assert_eq!(
            ime.key(Key::Space, 0).unwrap().commit.as_deref(),
            Some("第二")
        );
    }

    #[test]
    fn modifier_presses_preserve_candidates_and_shift_tab_moves_back() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = pinyin(&mut bytes);
        ime.key(Key::Text("ta"), 0).unwrap();
        ime.key(Key::Tab, 0).unwrap();
        assert_eq!(ime.preedit(), "她");
        for modifiers in [
            zinput::Modifiers::CTRL,
            zinput::Modifiers::ALT,
            zinput::Modifiers::SHIFT,
        ] {
            assert_eq!(
                ime.key(Key::Modifier, modifiers).unwrap(),
                Action::default()
            );
            assert_eq!(ime.input(), "ta");
            assert_eq!(ime.preedit(), "她");
        }
        ime.key(Key::Tab, zinput::Modifiers::SHIFT).unwrap();
        assert_eq!(ime.preedit(), "他");
        let shortcut = ime.key(Key::Text("c"), zinput::Modifiers::CTRL).unwrap();
        assert!(!shortcut.consumed);
        assert!(shortcut.refresh);
        assert_eq!(ime.input(), "");
    }

    #[test]
    fn sentence_suffix_limits_count_bytes_and_preserve_rejected_composition() {
        for (text, suffix, fits) in [
            ("界".repeat(1365), "!", true),
            (format!("{}x", "界".repeat(1364)), "界", true),
            ("界".repeat(1365), "🙂", false),
            ("x".repeat(crate::MAX_TEXT_BYTES), "!", false),
        ] {
            let dictionary = format!("x\t{text}\n");
            let mut bytes = [0; crate::MAX_TEXT_BYTES];
            let mut ime = InputMethod::new(
                &mut bytes,
                Engine::named("table", Some(&dictionary)).unwrap(),
            )
            .unwrap();
            ime.key(Key::Text("x"), 0).unwrap();
            let action = ime.key(Key::Text(suffix), 0).unwrap();
            assert!(action.consumed);
            if fits {
                assert_eq!(action.commit, Some(format!("{text}{suffix}")));
                assert!(ime.input().is_empty());
            } else {
                assert_eq!(action.commit, None);
                assert_eq!(ime.input(), "x");
                assert_eq!(ime.preedit(), text);
                assert_eq!(ime.key(Key::Space, 0).unwrap().commit, Some(text));
            }
        }
    }

    #[test]
    fn capture_validates_actual_commit_before_copying_or_consuming() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let context = ContextId::new(1).unwrap();
        let mut session = Session::new(&mut bytes, Engine::Literal, context).unwrap();
        let text = "x".repeat(crate::MAX_TEXT_BYTES);
        session.set_input(&text).unwrap();
        let mut output = Capture::new("!");
        assert!(session.commit(&mut output).is_err());
        assert!(output.limit_exceeded);
        assert!(output.text.is_empty());
        assert_eq!(session.input(), text);
        let mut output = Capture::new("");
        assert_eq!(session.commit(&mut output).unwrap(), crate::MAX_TEXT_BYTES);
        assert_eq!(output.text, text);
        assert!(session.input().is_empty());
    }

    #[test]
    fn backspace_keeps_valid_utf8_after_multibyte_input() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = pinyin(&mut bytes);
        ime.key(Key::Text("你界"), 0).unwrap();
        ime.key(Key::Backspace, 0).unwrap();
        assert_eq!(ime.input(), "你");
        assert_eq!(
            ime.key(Key::Space, 0).unwrap().commit.as_deref(),
            Some("你")
        );
    }

    #[test]
    fn unsupported_native_engines_are_rejected_before_keyboard_input() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        assert!(InputMethod::new(&mut bytes, Engine::Unicode).is_err());
        assert!(InputMethod::new(&mut bytes, Engine::Literal).is_err());
    }
}

#[cfg(test)]
mod daily_input_tests {
    use super::*;
    #[test]
    fn cursor_editing_keeps_composition_and_utf8_boundaries() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = InputMethod::new(&mut bytes, Engine::named("pinyin", None).unwrap()).unwrap();
        ime.key(Key::Text("niha"), 0).unwrap();
        ime.key(Key::Home, 0).unwrap();
        assert_eq!(ime.preedit_cursor(), 0);
        ime.key(Key::End, 0).unwrap();
        ime.key(Key::Text("xo"), 0).unwrap();
        ime.key(Key::Left, 0).unwrap();
        ime.key(Key::Backspace, 0).unwrap();
        assert_eq!(ime.input(), "nihao");
        ime.key(Key::End, 0).unwrap();
        assert_eq!(
            ime.key(Key::Space, 0).unwrap().commit.as_deref(),
            Some("你好")
        );
        ime.key(Key::Text("你界"), 0).unwrap();
        ime.key(Key::Home, 0).unwrap();
        ime.key(Key::Delete, 0).unwrap();
        assert_eq!(ime.input(), "界");
        assert_eq!(ime.input_cursor(), 0);
    }
    #[test]
    fn enter_commits_raw_pinyin_and_punctuation_works_without_composition() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let mut ime = InputMethod::new(&mut bytes, Engine::named("pinyin", None).unwrap()).unwrap();
        ime.key(Key::Text("Rust"), 0).unwrap();
        assert_eq!(
            ime.key(Key::Enter, 0).unwrap().commit.as_deref(),
            Some("Rust")
        );
        assert!(!ime.key(Key::Enter, 0).unwrap().consumed);
        assert_eq!(
            ime.key(Key::Text(","), 0).unwrap().commit.as_deref(),
            Some("，")
        );
        ime.key(Key::Space, zinput::Modifiers::CTRL).unwrap();
        assert!(!ime.key(Key::Text(","), 0).unwrap().consumed);
    }
    #[test]
    fn page_navigation_and_digits_address_the_visible_page() {
        let dict = (0..14)
            .map(|i| format!("ma\t词{i}\t{}\n", 100 - i))
            .collect::<String>();
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let options = InputOptions {
            page_size: 5,
            ..InputOptions::default()
        };
        let mut ime = InputMethod::with_options(
            &mut bytes,
            Engine::named("pinyin", Some(&dict)).unwrap(),
            options,
        )
        .unwrap();
        ime.key(Key::Text("ma"), 0).unwrap();
        assert_eq!(ime.page_range(), 0..5);
        ime.key(Key::PageDown, 0).unwrap();
        assert_eq!(ime.page_range(), 5..10);
        assert_eq!(
            ime.key(Key::Text("2"), 0).unwrap().commit.as_deref(),
            Some("词6")
        );
    }
    #[test]
    fn configured_english_and_ascii_punctuation_are_respected() {
        let mut bytes = [0; crate::MAX_TEXT_BYTES];
        let options = InputOptions {
            enabled: false,
            chinese_punctuation: false,
            enter_raw: false,
            page_size: 3,
        };
        let mut ime =
            InputMethod::with_options(&mut bytes, Engine::named("pinyin", None).unwrap(), options)
                .unwrap();
        assert!(!ime.key(Key::Text("n"), 0).unwrap().consumed);
        ime.key(Key::Space, zinput::Modifiers::CTRL).unwrap();
        ime.key(Key::Text("nihao"), 0).unwrap();
        assert_eq!(
            ime.key(Key::Enter, 0).unwrap().commit.as_deref(),
            Some("你好")
        );
        assert!(!ime.key(Key::Text(","), 0).unwrap().consumed);
    }
}
