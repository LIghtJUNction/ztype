use std::time::Duration;
use zinput_wayland::{Context, Event, InputMethod as WaylandInput};
use ztype::{
    engine::Engine,
    input_method::{InputMethod, InputOptions, Key},
};

pub fn launch_gtk3(mut args: impl Iterator<Item = std::ffi::OsString>) -> Result<(), String> {
    use std::os::unix::process::CommandExt;

    let program = args
        .next()
        .ok_or("usage: ztype gtk3-wayland PROGRAM [ARGS...]")?;
    // An inherited WAYLAND_SOCKET is a single client connection. Leave that
    // file descriptor and its protocol stream untouched for GTK.
    if std::env::var_os("WAYLAND_SOCKET").is_none() {
        let caps = zinput_wayland::probe().map_err(|error| error.to_string())?;
        require_input_protocols(caps)?;
    }
    let error = std::process::Command::new(&program)
        .args(args)
        .env("GDK_BACKEND", "wayland")
        .env("GTK_IM_MODULE", "wayland")
        .exec();
    Err(format!("cannot launch {program:?}: {error}"))
}

pub fn doctor() -> Result<(), String> {
    let capabilities = zinput_wayland::probe().map_err(|error| error.to_string())?;
    println!("{capabilities:#?}");
    require_input_protocols(capabilities)
}

fn require_input_protocols(caps: zinput_wayland::Capabilities) -> Result<(), String> {
    if caps.supported() {
        Ok(())
    } else {
        Err(format!(
            "unsupported Wayland compositor for ztype (input-method-v2={}, virtual-keyboard-v1={}, seat={})",
            caps.input_method, caps.virtual_keyboard, caps.seat
        ))
    }
}

pub fn run(engine: Engine, options: InputOptions) -> Result<(), String> {
    let mut input = WaylandInput::connect().map_err(|error| error.to_string())?;
    let mut storage = [0; zinput_wayland::MAX_TEXT_BYTES];
    let mut composition = InputMethod::with_options(&mut storage, engine, options)?;
    crate::service::ready()?;
    #[cfg(feature = "wayland-popup")]
    let mut popup = crate::popup::Controller::new();
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
                    #[cfg(feature = "wayland-popup")]
                    popup.sync(
                        &mut WaylandSurface {
                            input: &mut input,
                            context: &context,
                        },
                        context.generation,
                        context.active,
                        context.sensitive,
                        &composition,
                    );
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
                        update(
                            &mut input,
                            &context,
                            composition.preedit(),
                            composition.preedit_cursor(),
                            action.commit.as_deref(),
                        )?;
                        #[cfg(feature = "wayland-popup")]
                        popup.sync(
                            &mut WaylandSurface {
                                input: &mut input,
                                context: &context,
                            },
                            context.generation,
                            context.active,
                            context.sensitive,
                            &composition,
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

fn update(
    input: &mut WaylandInput,
    context: &Context,
    preedit: &str,
    cursor: usize,
    committed: Option<&str>,
) -> Result<(), String> {
    let cursor = i32::try_from(cursor).map_err(|_| "preedit is too long")?;
    input
        .update(context, preedit, cursor, committed)
        .map_err(|error| error.to_string())
}

#[cfg(feature = "wayland-popup")]
struct WaylandSurface<'a> {
    input: &'a mut WaylandInput,
    context: &'a Context,
}

#[cfg(feature = "wayland-popup")]
impl crate::popup::Surface for WaylandSurface<'_> {
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
        use zinput_wayland::{PopupFrame, PopupPresentation};
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
