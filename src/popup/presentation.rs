use super::{Diagnostics, Frame, Renderer, Snapshot};
use ztype::input_method::InputMethod;

pub enum Presentation {
    Presented,
    Queued,
    Unavailable,
}

/// A context-bound native surface. Each adapter retains ownership of its
/// connection, focus checks, buffer lifetime and protocol implementation.
pub trait Surface {
    fn supported(&self) -> bool;
    fn present(&mut self, frame: &Frame) -> Result<Presentation, String>;
    fn hide(&mut self) -> Result<(), String>;
}

pub struct Controller {
    renderer: Option<Renderer>,
    failed: bool,
    diagnostics: Diagnostics,
}

impl Controller {
    pub fn new() -> Self {
        Self {
            renderer: None,
            failed: false,
            diagnostics: Diagnostics::new(),
        }
    }

    pub fn sync(
        &mut self,
        surface: &mut impl Surface,
        generation: u64,
        active: bool,
        sensitive: bool,
        composition: &InputMethod<'_>,
    ) {
        let visible =
            active && !sensitive && composition.enabled() && !composition.input().is_empty();
        let mut state = Snapshot {
            requested_visible: visible,
            selected: composition.selected(),
            candidate_count: composition.candidates().len(),
            generation,
            width: 0,
            height: 0,
            presentation_result: "hidden",
        };
        let result = if !visible {
            surface.hide()
        } else if !surface.supported() {
            state.presentation_result = "unavailable";
            Ok(())
        } else if self.failed {
            state.presentation_result = "error";
            Ok(())
        } else {
            (|| -> Result<(), String> {
                if self.renderer.is_none() {
                    self.renderer = Some(Renderer::new()?);
                }
                let frame = self
                    .renderer
                    .as_mut()
                    .expect("renderer initialized")
                    .render(composition)?;
                state.width = frame.width;
                state.height = frame.height;
                state.presentation_result = match surface.present(&frame)? {
                    Presentation::Presented => "presented",
                    Presentation::Queued => "queued",
                    Presentation::Unavailable => "unavailable",
                };
                Ok(())
            })()
        };
        if let Err(error) = result {
            if !self.failed {
                eprintln!(
                    "ztype: candidate popup unavailable ({error}); composition remains active"
                );
            }
            self.failed = true;
            self.renderer = None;
            state.presentation_result = "error";
            let _ = surface.hide();
        }
        self.diagnostics.record(state);
    }
}
