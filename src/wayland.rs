use std::time::Duration;
use zinput_wayland::{Context, Event, InputMethod as WaylandInput};
use ztype::{
    engine::Engine,
    input_method::{InputMethod, Key},
};

pub fn doctor() -> Result<(), String> {
    let capabilities = zinput_wayland::probe().map_err(|error| error.to_string())?;
    println!("{capabilities:#?}");
    Ok(())
}

pub fn run(engine: Engine) -> Result<(), String> {
    let mut input = WaylandInput::connect().map_err(|error| error.to_string())?;
    let mut storage = [0; zinput_wayland::MAX_TEXT_BYTES];
    let mut composition = InputMethod::new(&mut storage, engine)?;
    let mut popup = Popup::new();
    eprintln!(
        "ztype: native Wayland input method ready; Ctrl+Space toggles input, Tab changes candidate, Space confirms"
    );
    loop {
        input
            .dispatch(Duration::from_millis(500))
            .map_err(|error| error.to_string())?;
        while let Some(event) = input.next_event().map_err(|error| error.to_string())? {
            match event {
                Event::State(context) => {
                    if context.active && !context.sensitive {
                        composition.focus(context.generation)?;
                    } else {
                        composition.reset()?;
                    }
                    popup.sync(&mut input, &context, &composition);
                }
                Event::Key(event) => {
                    if !event.pressed {
                        input
                            .forward_key(&event)
                            .map_err(|error| error.to_string())?;
                        continue;
                    }
                    let context = input.context();
                    if !context.active || context.sensitive {
                        input
                            .forward_key(&event)
                            .map_err(|error| error.to_string())?;
                        continue;
                    }
                    if event.repeat
                        && event.keysym == 0x20
                        && event.modifiers & u32::from(zinput::Modifiers::CTRL) != 0
                    {
                        continue;
                    }
                    let key = match event.keysym {
                        0xff0d | 0xff8d => Key::Enter,
                        0xff08 => Key::Backspace,
                        0xff1b => Key::Escape,
                        0xff09 | 0xfe20 => Key::Tab,
                        0x20 => Key::Space,
                        0xffe1..=0xffee | 0xff7f => Key::Modifier,
                        _ if !event.text.is_empty() => Key::Text(&event.text),
                        _ => Key::Other,
                    };
                    let action = composition.key(key, event.modifiers as u8)?;
                    if action.refresh {
                        update(
                            &mut input,
                            &context,
                            composition.preedit(),
                            action.commit.as_deref(),
                        )?;
                        popup.sync(&mut input, &context, &composition);
                    }
                    if !action.consumed {
                        input
                            .forward_key(&event)
                            .map_err(|error| error.to_string())?;
                    }
                }
            }
        }
    }
}

fn update(
    input: &mut WaylandInput,
    context: &Context,
    preedit: &str,
    committed: Option<&str>,
) -> Result<(), String> {
    let cursor = i32::try_from(preedit.len()).map_err(|_| "preedit is too long")?;
    input
        .update(context, preedit, cursor, committed)
        .map_err(|error| error.to_string())
}

struct Popup {
    #[cfg(feature = "wayland-popup")]
    renderer: Option<crate::popup::Renderer>,
    #[cfg(feature = "wayland-popup")]
    failed: bool,
    #[cfg(feature = "wayland-popup")]
    diagnostics: crate::popup::Diagnostics,
}

impl Popup {
    fn new() -> Self {
        Self {
            #[cfg(feature = "wayland-popup")]
            renderer: None,
            #[cfg(feature = "wayland-popup")]
            failed: false,
            #[cfg(feature = "wayland-popup")]
            diagnostics: crate::popup::Diagnostics::new(),
        }
    }

    fn sync(&mut self, input: &mut WaylandInput, context: &Context, composition: &InputMethod<'_>) {
        #[cfg(feature = "wayland-popup")]
        {
            use crate::popup::{Renderer, Snapshot};
            use zinput_wayland::{PopupFrame, PopupPresentation};
            let visible = context.active
                && !context.sensitive
                && composition.enabled()
                && !composition.input().is_empty();
            let mut state = Snapshot {
                requested_visible: visible,
                selected: composition.selected(),
                candidate_count: composition.candidates().len(),
                generation: context.generation,
                width: 0,
                height: 0,
                presentation_result: "hidden",
            };
            let result = if !visible {
                input.hide_popup().map_err(|error| error.to_string())
            } else if !input.popup_supported() {
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
                    state.presentation_result = match input
                        .present_popup(
                            context,
                            PopupFrame {
                                width: frame.width,
                                height: frame.height,
                                scale: 1,
                                pixels: &frame.pixels,
                            },
                        )
                        .map_err(|error| error.to_string())?
                    {
                        PopupPresentation::Presented => "presented",
                        PopupPresentation::Queued => "queued",
                        PopupPresentation::Unavailable => "unavailable",
                    };
                    Ok(())
                })()
            };
            if let Err(error) = result {
                if !self.failed {
                    eprintln!(
                        "ztype: candidate popup unavailable ({error}); inline composition remains active"
                    );
                }
                self.failed = true;
                self.renderer = None;
                state.presentation_result = "error";
                let _ = input.hide_popup();
            }
            self.diagnostics.record(state);
        }
        #[cfg(not(feature = "wayland-popup"))]
        let _ = (input, context, composition);
    }
}
