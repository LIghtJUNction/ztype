//! XIM delivers committed UTF-8 directly to X11 and XWayland applications.
use std::time::Duration;
use zinput_x11::{Context, Event, InputMethod as X11Input};
use ztype::{
    engine::Engine,
    input_method::{InputMethod, InputOptions, Key},
};

pub fn run(engine: Option<Engine>, options: InputOptions) -> Result<(), String> {
    let server = std::env::var("ZTYPE_XIM_NAME").unwrap_or_else(|_| "ztype".into());
    let mut input = X11Input::connect_named(&server).map_err(|error| error.to_string())?;
    let mut storage = [0; zinput_x11::MAX_TEXT_BYTES];
    let mut composition = engine
        .map(|engine| {
            InputMethod::with_options(
                &mut storage,
                engine,
                InputOptions {
                    enabled: false,
                    ..options
                },
            )
        })
        .transpose()?;
    let mut focus = None;
    crate::service::ready()?;
    let mut popup = crate::popup::Controller::new(None);
    let policy = if composition.is_some() {
        "manual (Ctrl+Space opts in for this focus only)"
    } else {
        "passthrough"
    };
    eprintln!(
        "ztype: native XIM input method ready; XMODIFIERS=@im={server}; field purpose=unknown; policy={policy}; learning=off"
    );
    loop {
        input
            .dispatch(Duration::from_millis(500))
            .map_err(|error| error.to_string())?;
        while let Some(event) = input.next_event().map_err(|error| error.to_string())? {
            let Some(composition) = composition.as_mut() else {
                if let Event::Key(key) = event {
                    input.forward_key(&key).map_err(|error| error.to_string())?;
                }
                continue;
            };
            match event {
                Event::State(context) => {
                    // A reset or refocus can replace a field inside the same client.
                    // Consent is local to this adapter snapshot, including its generation.
                    if focus != Some(context) {
                        composition.disable()?;
                        if context.active && !context.sensitive {
                            composition.focus(context.generation)?;
                        }
                        focus = Some(context);
                    }
                    popup.sync(
                        &mut X11Surface {
                            input: &mut input,
                            context: &context,
                        },
                        context.generation,
                        context.active,
                        context.sensitive,
                        composition,
                    );
                }
                Event::Key(event) => {
                    let context = event.context;
                    if focus != Some(context) {
                        continue;
                    }
                    if !event.pressed || !context.active || context.sensitive {
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
                        0xffff => Key::Delete,
                        0xff51 => Key::Left,
                        0xff53 => Key::Right,
                        0xff50 => Key::Home,
                        0xff57 => Key::End,
                        0xff52 => Key::Up,
                        0xff54 => Key::Down,
                        0xff55 => Key::PageUp,
                        0xff56 => Key::PageDown,
                        0xff1b => Key::Escape,
                        0xff09 | 0xfe20 => Key::Tab,
                        0x20 => Key::Space,
                        0xffe1..=0xffee | 0xff7f => Key::Modifier,
                        _ if !event.text.is_empty() => Key::Text(&event.text),
                        _ => Key::Other,
                    };
                    let action = composition.key(key, event.modifiers as u8)?;
                    if action.refresh {
                        let cursor = i32::try_from(composition.preedit_cursor())
                            .map_err(|_| "preedit is too long")?;
                        input
                            .update(
                                &context,
                                composition.preedit(),
                                cursor,
                                action.commit.as_deref(),
                            )
                            .map_err(|error| error.to_string())?;
                        popup.sync(
                            &mut X11Surface {
                                input: &mut input,
                                context: &context,
                            },
                            context.generation,
                            context.active,
                            context.sensitive,
                            composition,
                        );
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

struct X11Surface<'a> {
    input: &'a mut X11Input,
    context: &'a Context,
}

impl crate::popup::Surface for X11Surface<'_> {
    fn supported(&self) -> bool {
        self.input.popup_supported()
    }
    fn hide(&mut self) -> Result<(), String> {
        self.input.hide_popup().map_err(|error| error.to_string())
    }
    fn present(
        &mut self,
        frame: &crate::popup::Frame,
    ) -> Result<crate::popup::Presentation, String> {
        use crate::popup::Presentation;
        use zinput_x11::{PopupFrame, PopupPresentation};
        let result = self
            .input
            .present_popup(
                self.context,
                PopupFrame {
                    width: frame.width,
                    height: frame.height,
                    scale: 1,
                    pixels: &frame.pixels,
                },
            )
            .map_err(|error| error.to_string())?;
        Ok(match result {
            PopupPresentation::Presented => Presentation::Presented,
            PopupPresentation::Queued => Presentation::Queued,
            PopupPresentation::Unavailable => Presentation::Unavailable,
        })
    }
}
