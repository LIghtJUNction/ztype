//! Built-in and dynamically loaded engines share the zinput InputEngine ABI.
use crate::{
    MAX_TEXT_BYTES,
    engine::{Candidates, Engine},
    validate_text,
};
use std::{path::Path, rc::Rc};
use zinput::engine::{
    EngineSelection, InputEngine, InputEngineApi, InputEngineProxy, change, key, phase,
};
use zplugin::{Error, Plugin, UnloadPolicy};

fn copy(text: &str, output: &mut [u8]) -> Result<u64, Error> {
    if output.len() < text.len() {
        return Err(Error::BufferTooSmall);
    }
    output[..text.len()].copy_from_slice(text.as_bytes());
    Ok(text.len() as u64)
}

/// Exportable adapter for the exact same built-in engines used by the app.
pub struct BuiltinEngine {
    engine: Engine,
    context: u64,
    input: String,
    candidates: Candidates,
    selected: usize,
    pending: String,
}
impl BuiltinEngine {
    pub fn new(engine: Engine) -> Self {
        Self {
            engine,
            context: 0,
            input: String::with_capacity(MAX_TEXT_BYTES),
            candidates: Candidates::new(),
            selected: 0,
            pending: String::with_capacity(MAX_TEXT_BYTES),
        }
    }
    fn edit(&mut self, input: String) -> Result<u32, Error> {
        validate_text(&input).map_err(|_| Error::InvalidArgument)?;
        let mut next = Candidates::new();
        self.engine
            .candidates(&input, &mut next)
            .map_err(|_| Error::InvalidArgument)?;
        self.input = input;
        self.candidates = next;
        self.selected = 0;
        Ok(change::PREEDIT | change::CANDIDATES | change::HANDLED)
    }
}
impl InputEngine for BuiltinEngine {
    fn reset(&mut self, context: u64) -> Result<(), Error> {
        self.context = context;
        self.input.clear();
        self.candidates.clear();
        self.pending.clear();
        self.selected = 0;
        Ok(())
    }
    fn process(
        &mut self,
        phase_value: u32,
        key_value: u32,
        modifiers: u32,
        text: &str,
    ) -> Result<u32, Error> {
        if ![phase::PRESS, phase::REPEAT, phase::RELEASE].contains(&phase_value) {
            return Err(Error::InvalidArgument);
        }
        if phase_value == phase::RELEASE || self.context == 0 || modifiers != 0 {
            return Ok(0);
        }
        if !self.pending.is_empty() {
            return Err(Error::Busy);
        }
        match key_value {
            key::TEXT => {
                if self.input.len().saturating_add(text.len()) > MAX_TEXT_BYTES {
                    return Err(Error::InvalidArgument);
                }
                let mut input = self.input.clone();
                input.push_str(text);
                self.edit(input)
            }
            key::BACKSPACE => {
                let mut input = self.input.clone();
                input.pop();
                self.edit(input)
            }
            key::ESCAPE => self.edit(String::new()),
            key::ENTER | key::SPACE if !self.candidates.is_empty() => {
                self.select(self.selected as u32)
            }
            key::DOWN | key::TAB | key::UP if !self.candidates.is_empty() => {
                let len = self.candidates.len();
                self.selected =
                    (self.selected + if key_value == key::UP { len - 1 } else { 1 }) % len;
                Ok(change::CANDIDATES | change::HANDLED)
            }
            _ => Ok(0),
        }
    }
    fn preedit(&self, output: &mut [u8]) -> Result<u64, Error> {
        copy(&self.input, output)
    }
    fn selection(&self) -> Result<EngineSelection, Error> {
        Ok(EngineSelection {
            start: self.input.len() as u64,
            end: self.input.len() as u64,
        })
    }
    fn candidate_count(&self) -> Result<u32, Error> {
        Ok(self.candidates.len() as u32)
    }
    fn candidate(&self, index: u32, output: &mut [u8]) -> Result<u64, Error> {
        copy(
            self.candidates
                .get(index as usize)
                .ok_or(Error::InvalidArgument)?,
            output,
        )
    }
    fn select(&mut self, index: u32) -> Result<u32, Error> {
        if !self.pending.is_empty() {
            return Err(Error::Busy);
        }
        let text = self
            .candidates
            .get(index as usize)
            .ok_or(Error::InvalidArgument)?;
        self.pending.push_str(text);
        self.input.clear();
        self.candidates.clear();
        self.selected = 0;
        Ok(change::PREEDIT | change::CANDIDATES | change::COMMIT | change::HANDLED)
    }
    fn commit(&mut self, output: &mut [u8]) -> Result<u64, Error> {
        let size = copy(&self.pending, output)?;
        self.pending.clear();
        Ok(size)
    }
}

/// Owns the generated proxy's library lease; no self-referential pointers.
pub struct NativeEngine {
    proxy: InputEngineProxy<'static>,
    context: u64,
    input: String,
    prepared: Option<(usize, String)>,
    immediate: Option<String>,
}
impl NativeEngine {
    /// Load native code chosen explicitly by the user.
    ///
    /// # Safety
    /// The file must be a trusted zplugin library satisfying the loader's ABI
    /// contract. Interface IDs/signatures are checked; native code is not sandboxed.
    pub unsafe fn load(path: &Path) -> Result<Self, String> {
        // SAFETY: the caller accepts the native library contract above.
        let library =
            Rc::new(unsafe { Plugin::load_path(path, UnloadPolicy::Pinned) }.map_err(err)?);
        let instance = Plugin::create_owned(&library, InputEngineApi::SPEC).map_err(err)?;
        let proxy = InputEngineApi::wrap(instance).map_err(err)?;
        Ok(Self {
            proxy,
            context: 0,
            input: String::new(),
            prepared: None,
            immediate: None,
        })
    }
    pub(crate) fn reset_context(&mut self, context: u64) -> Result<(), String> {
        self.input.clear();
        self.prepared = None;
        self.immediate = None;
        self.context = context;
        self.proxy.reset(context).map_err(err)
    }
    pub(crate) fn candidates(
        &mut self,
        input: &str,
        output: &mut Candidates,
    ) -> Result<(), String> {
        self.proxy.reset(self.context).map_err(err)?;
        self.input.clear();
        self.input.push_str(input);
        self.prepared = None;
        self.immediate = None;
        let changes = self
            .proxy
            .process(phase::PRESS, key::TEXT, 0, input)
            .map_err(err)?;
        let mut buffer = [0; MAX_TEXT_BYTES];
        if changes & change::COMMIT != 0 {
            let size = self.proxy.commit(&mut buffer).map_err(err)?;
            let text = checked(&buffer, size)?.to_owned();
            output.push(&text)?;
            self.immediate = Some(text);
            return Ok(());
        }
        let count = self.proxy.candidate_count().map_err(err)?;
        if count > crate::MAX_CANDIDATES as u32 {
            return Err("plugin candidate page exceeds 9 entries".into());
        }
        for index in 0..count {
            let size = self.proxy.candidate(index, &mut buffer).map_err(err)?;
            // Do not silently deduplicate/reindex plugin candidate positions.
            let text = checked(&buffer, size)?;
            if text.is_empty() || output.iter().any(|other| other == text) {
                return Err("plugin candidate page has empty or duplicate entries".into());
            }
            output.push(text)?;
        }
        Ok(())
    }
    pub(crate) fn resolve(&mut self, index: usize, shown: &str) -> Result<String, String> {
        if let Some((prepared_index, text)) = &self.prepared {
            if index == *prepared_index {
                return Ok(text.clone());
            }
            let input = self.input.clone();
            let mut page = Candidates::new();
            self.candidates(&input, &mut page)?;
            if page.get(index) != Some(shown) {
                return Err("plugin candidates changed; edit input to refresh".into());
            }
        }
        let text = if let Some(text) = &self.immediate {
            if index != 0 {
                return Err("plugin direct commit has one candidate".into());
            }
            text.clone()
        } else {
            let mut changes = self.proxy.select(index as u32).map_err(err)?;
            if changes & change::COMMIT == 0 {
                changes = self
                    .proxy
                    .process(phase::PRESS, key::ENTER, 0, "")
                    .map_err(err)?;
            }
            if changes & change::COMMIT == 0 {
                return Err("plugin did not commit the selected candidate".into());
            }
            let mut buffer = [0; MAX_TEXT_BYTES];
            let size = self.proxy.commit(&mut buffer).map_err(err)?;
            checked(&buffer, size)?.to_owned()
        };
        validate_text(&text)?;
        if text.is_empty() {
            return Err("plugin produced an empty commit".into());
        }
        self.prepared = Some((index, text.clone()));
        Ok(text)
    }
}

fn checked(buffer: &[u8], size: u64) -> Result<&str, String> {
    let size = usize::try_from(size).map_err(|_| "plugin output length overflow")?;
    let bytes = buffer
        .get(..size)
        .ok_or("plugin output length exceeds buffer")?;
    let text = std::str::from_utf8(bytes).map_err(|_| "plugin returned invalid UTF-8")?;
    validate_text(text)?;
    Ok(text)
}
fn err(error: Error) -> String {
    format!("engine plugin: {error:?}")
}
