//! Reusable composition logic for the ztype application.
//!
//! zinput owns focus and revision validity. This crate adds bounded candidate
//! engines and delivery; operating-system integration stays in the Z adapters.
pub mod dictionary;
pub mod engine;
pub mod input_method;
#[cfg(feature = "desktop")]
pub mod native;
#[cfg(feature = "plugins")]
pub mod plugin;

use engine::{Candidates, Engine};
use zinput::ime::{CandidatePage, Composition, ContextId, Selection, TextTarget, Token};

pub const MAX_TEXT_BYTES: usize = 4096;
pub const MAX_CANDIDATES: usize = 128;

/// Application-level composition with caller-owned preedit storage.
pub struct Session<'a> {
    composition: Composition<'a>,
    engine: Engine,
    candidates: Candidates,
    pending_candidates: Candidates,
    input: String,
    context: ContextId,
    token: Option<Token>,
    selected: usize,
    engine_valid: bool,
}

impl<'a> Session<'a> {
    pub fn new(
        storage: &'a mut [u8],
        mut engine: Engine,
        context: ContextId,
    ) -> Result<Self, String> {
        engine.reset_context(context.get())?;
        let mut composition = Composition::new(storage);
        composition.set_focus(Some(context));
        Ok(Self {
            composition,
            engine,
            candidates: Candidates::new(),
            pending_candidates: Candidates::new(),
            input: String::with_capacity(MAX_TEXT_BYTES),
            context,
            token: None,
            selected: 0,
            engine_valid: true,
        })
    }

    pub fn input(&self) -> &str {
        &self.input
    }
    pub fn candidates(&self) -> &Candidates {
        &self.candidates
    }
    pub fn selected(&self) -> usize {
        self.selected
    }
    pub fn context(&self) -> ContextId {
        self.context
    }

    pub fn select(&mut self, index: usize) -> Result<(), String> {
        if index >= self.candidates.len() {
            return Err("candidate index is out of range".into());
        }
        self.selected = index;
        Ok(())
    }

    /// Replace input atomically. Invalid or oversized edits leave the old input.
    pub fn set_input(&mut self, input: &str) -> Result<(), String> {
        validate_text(input)?;
        if input.len() > self.composition.capacity() {
            return Err("preedit storage is too small".into());
        }
        self.engine_valid = false;
        self.engine
            .candidates(input, &mut self.pending_candidates)?;
        let token = match self.token {
            Some(token) => token,
            None => self.composition.begin().map_err(ime_error)?,
        };
        self.token = Some(
            self.composition
                .set_preedit(
                    token,
                    input,
                    Selection {
                        start: input.len(),
                        end: input.len(),
                    },
                )
                .map_err(ime_error)?,
        );
        self.input.clear();
        self.input.push_str(input);
        std::mem::swap(&mut self.candidates, &mut self.pending_candidates);
        self.selected = 0;
        self.engine_valid = true;
        Ok(())
    }

    pub fn clear(&mut self) -> Result<(), String> {
        if let Some(token) = self.token.take() {
            self.composition.cancel(token).map_err(ime_error)?;
        }
        self.input.clear();
        self.candidates.clear();
        self.selected = 0;
        self.engine_valid = false;
        self.engine.reset_context(self.context.get())?;
        self.engine_valid = true;
        Ok(())
    }

    /// Changing the destination invalidates all pending text and candidates.
    pub fn set_context(&mut self, context: ContextId) -> Result<(), String> {
        if self.context != context {
            self.composition.set_focus(Some(context));
            self.context = context;
            self.input.clear();
            self.candidates.clear();
            self.token = None;
            self.selected = 0;
            self.engine_valid = false;
            self.engine.reset_context(context.get())?;
            self.engine_valid = true;
        }
        Ok(())
    }

    /// Deliver a candidate and consume it only after delivery succeeds.
    ///
    /// Target errors are never retried automatically: a target may have accepted
    /// only part of a string. The retained composition permits a user decision.
    pub fn commit<T: TextTarget>(&mut self, target: &mut T) -> Result<usize, String>
    where
        T::Error: std::fmt::Display,
    {
        let token = self.token.ok_or("there is no active composition")?;
        if !self.engine_valid {
            return Err("engine state needs a valid input update before commit".into());
        }
        let shown = self
            .candidates
            .get(self.selected)
            .ok_or("candidate index is out of range")?;
        let selected = self.engine.resolve(self.selected, shown)?;
        validate_text(&selected)?;
        let refs = [selected.as_ref()];
        let page = CandidatePage::new(token, &refs, Some(0)).map_err(ime_error)?;
        let text = page.get(0).map_err(ime_error)?;
        // Candidates are validated when generated. The current token and context
        // cannot change while this exclusive borrow is held.
        target
            .commit_text(self.context, text)
            .map_err(|e| format!("text delivery failed: {e}"))?;
        let mut output = [0u8; MAX_TEXT_BYTES];
        let commit = self
            .composition
            .commit_candidate(&page, 0, &mut output)
            .map_err(ime_error)?;
        self.engine.record_selection(&self.input, &selected);
        self.token = None;
        self.input.clear();
        self.candidates.clear();
        self.selected = 0;
        Ok(commit.bytes_written)
    }
}

pub fn validate_text(text: &str) -> Result<(), String> {
    if text.len() > MAX_TEXT_BYTES {
        return Err(format!("text exceeds {MAX_TEXT_BYTES} UTF-8 bytes"));
    }
    if text.contains('\0') {
        return Err("text contains a NUL character".into());
    }
    Ok(())
}

fn ime_error(error: zinput::ime::Error) -> String {
    format!("composition error: {error:?}")
}
