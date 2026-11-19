//! Native delivery reuses zuse; ztype does not bind operating-system APIs.
use zinput::ime::{ContextId, TextTarget};

pub struct NativeTarget {
    desktop: zuse::platform::Desktop,
    window: String,
    context: ContextId,
}

impl NativeTarget {
    pub fn connect(window: String, context: ContextId) -> Result<Self, String> {
        if window.is_empty() || window.contains('\0') {
            return Err("a nonempty window identifier is required".into());
        }
        Ok(Self {
            desktop: zuse::platform::Desktop::connect()?,
            window,
            context,
        })
    }
    pub fn backend(&self) -> &'static str {
        self.desktop.backend()
    }
}

impl TextTarget for NativeTarget {
    type Error = String;
    fn commit_text(&mut self, context: ContextId, text: &str) -> Result<(), String> {
        if context != self.context {
            return Err("destination context changed".into());
        }
        crate::validate_text(text)?;
        // Keyboard insertion follows the foreground input path. Focus belongs
        // to the explicitly selected destination, never an inferred window.
        self.desktop.focus(&self.window)?;
        self.desktop.type_text(text)
    }
}
