use zinput::ime::{ContextId, TextTarget};
use ztype::{MAX_TEXT_BYTES, Session, engine::Engine, native::NativeTarget};
use zui::{Event, Rect, Window};

const BG: u32 = 0x101215ff;
const SURFACE: u32 = 0x1c2026ff;
const INK: u32 = 0xe8eaedff;
const MUTED: u32 = 0x9aa0a6ff;
const BLUE: u32 = 0x8ab4f8ff;
const LINE: u32 = 0x343a42ff;

struct CopyTarget(String);
impl TextTarget for CopyTarget {
    type Error = String;
    fn commit_text(&mut self, _: ContextId, text: &str) -> Result<(), String> {
        self.0.clear();
        self.0.push_str(text);
        Ok(())
    }
}

pub fn run(
    engine: Engine,
    label: &str,
    target_id: Option<&str>,
    initial: Option<&str>,
) -> Result<(), String> {
    let context = ContextId::new(1).map_err(|e| format!("{e:?}"))?;
    let mut target = target_id
        .map(|id| NativeTarget::connect(id.into(), context))
        .transpose()?;
    let mut window = Window::new("ztype compose", 740, 520)?;
    let mut storage = [0u8; MAX_TEXT_BYTES];
    let mut session = Session::new(&mut storage, engine, context)?;
    if let Some(initial) = initial {
        session.set_input(initial)?;
    }
    let mut draft = session.input().to_owned();
    let mut status: String = if target.is_some() {
        "Send focuses the selected target window.".into()
    } else {
        "No target: Enter copies the result. Keep this window open to paste.".into()
    };
    let mut event = None;
    loop {
        let mut edited = false;
        let mut commit = false;
        let mut copy = false;
        match event.take() {
            Some(Event::Close) => break,
            Some(Event::Key { code: 27, .. }) => {
                session.clear()?;
                draft.clear();
                status = "Cleared.".into();
            }
            Some(Event::Key { code: 13, .. }) => commit = true,
            Some(Event::Key { code: 9, shift, .. }) => {
                let len = session.candidates().len();
                if len > 0 {
                    session.select((session.selected() + if shift { len - 1 } else { 1 }) % len)?;
                }
            }
            Some(Event::Key { code: 8, .. }) => {
                draft.pop();
                edited = true;
            }
            Some(Event::Key {
                control: true,
                text,
                ..
            }) if text.eq_ignore_ascii_case("a") => {
                draft.clear();
                edited = true;
            }
            Some(Event::Key {
                control: true,
                text,
                ..
            }) if text.eq_ignore_ascii_case("c") => copy = true,
            Some(Event::Key {
                control: false,
                text,
                ..
            })
            | Some(Event::Text(text)) => {
                if !text.is_empty() && !text.chars().any(char::is_control) {
                    if draft.len().saturating_add(text.len()) <= MAX_TEXT_BYTES {
                        draft.push_str(&text);
                        edited = true;
                    } else {
                        status = "Text exceeds the 4096-byte composition limit.".into();
                    }
                }
            }
            Some(Event::Up(x, y)) => {
                let mut hit = None;
                for index in 0..session.candidates().len() {
                    let rect = candidate_rect(index);
                    if rect.contains(x, y) {
                        hit = Some(index);
                    }
                }
                if let Some(index) = hit {
                    session.select(index)?;
                }
                if Rect::new(32., 440., 150., 42.).contains(x, y) {
                    commit = true;
                }
                if Rect::new(196., 440., 120., 42.).contains(x, y) {
                    copy = true;
                }
            }
            _ => {}
        }
        if edited {
            match session.set_input(&draft) {
                Ok(()) => status.clear(),
                Err(error) => status = error,
            }
        }
        let draft_valid = session.input() == draft;
        if (copy || commit) && !draft_valid {
            status = "Complete valid input before committing.".into();
        }
        if copy
            && draft_valid
            && let Some(text) = session.candidates().get(session.selected())
        {
            window.copy(text);
            status = "Copied. Keep this window open until pasted.".into();
        }
        if commit && draft_valid && !session.candidates().is_empty() {
            let result = if let Some(target) = target.as_mut() {
                session
                    .commit(target)
                    .map(|bytes| format!("Submitted {bytes} UTF-8 bytes to the selected window."))
            } else {
                let mut target = CopyTarget(String::new());
                session.commit(&mut target).map(|_| {
                    window.copy(&target.0);
                    "Copied. Keep this window open until pasted.".into()
                })
            };
            match result {
                Ok(message) => {
                    status = message;
                    draft.clear();
                }
                Err(error) => status = error,
            }
        }
        let mut canvas = window.begin(BG);
        canvas.text("ztype", 32., 24., 28, true, INK);
        canvas.text(
            &format!("{label}  /  local composition"),
            34.,
            66.,
            13,
            false,
            MUTED,
        );
        canvas.rect(Rect::new(32., 108., 676., 58.), 10., SURFACE);
        canvas.rect(Rect::new(32., 164., 676., 2.), 0., BLUE);
        canvas.clip(Rect::new(48., 114., 644., 45.));
        canvas.text(
            if draft.is_empty() {
                "Type to compose…"
            } else {
                &draft
            },
            48.,
            122.,
            22,
            false,
            if draft.is_empty() { MUTED } else { INK },
        );
        canvas.unclip();
        canvas.text("CANDIDATES", 34., 192., 11, true, MUTED);
        for (index, text) in session
            .candidates()
            .iter()
            .enumerate()
            .filter(|_| draft_valid)
        {
            let rect = candidate_rect(index);
            let selected = session.selected() == index;
            canvas.rect(rect, 8., if selected { 0x24364eff } else { SURFACE });
            canvas.text(
                &format!("{}", index + 1),
                rect.x + 12.,
                rect.y + 13.,
                12,
                false,
                if selected { BLUE } else { MUTED },
            );
            canvas.ellipsis(
                text,
                rect.x + 34.,
                rect.y + 10.,
                rect.w - 46.,
                18,
                false,
                INK,
            );
        }
        canvas.text(
            "Enter commit    Tab next    Esc clear    Ctrl+C copy",
            34.,
            391.,
            13,
            false,
            MUTED,
        );
        canvas.rect(Rect::new(32., 440., 150., 42.), 8., BLUE);
        canvas.text(
            if target.is_some() {
                "Focus & send"
            } else {
                "Copy result"
            },
            49.,
            451.,
            15,
            true,
            BG,
        );
        canvas.rect(Rect::new(196., 440., 120., 42.), 8., LINE);
        canvas.text("Copy", 233., 451., 15, false, INK);
        canvas.ellipsis(&status, 334., 446., 374., 12, false, MUTED);
        drop(canvas);
        window.present();
        event = loop {
            if let Some(event) = window.event(1000) {
                break Some(event);
            }
        };
    }
    Ok(())
}

fn candidate_rect(index: usize) -> Rect {
    Rect::new(
        32. + (index % 3) as f32 * 230.,
        220. + (index / 3) as f32 * 51.,
        216.,
        43.,
    )
}
