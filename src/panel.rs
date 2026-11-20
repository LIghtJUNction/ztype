use zgpui::native::{Canvas, Event, Rect, Window};
use zinput::ime::{ContextId, TextTarget};
use ztype::{MAX_TEXT_BYTES, Session, engine::Engine, native::NativeTarget};

const BG: u32 = 0xfffefcff;
const INK: u32 = 0x202124ff;
const MUTED: u32 = 0x5f6368ff;
const GREEN: u32 = 0x087f5bff;
const HOVER: u32 = 0xeff2f6ff;
const ERROR: u32 = 0xb3261eff;

struct CopyTarget(String);
impl TextTarget for CopyTarget {
    type Error = String;
    fn commit_text(&mut self, _: ContextId, text: &str) -> Result<(), String> {
        self.0.clear();
        self.0.push_str(text);
        Ok(())
    }
}

struct Layout {
    width: f32,
    candidates: Vec<(usize, Rect)>,
    pages: Vec<usize>,
    page: usize,
}
impl Layout {
    fn empty(width: f32) -> Self {
        Self {
            width,
            candidates: Vec::new(),
            pages: vec![0],
            page: 0,
        }
    }

    fn measure(canvas: &Canvas<'_>, session: &Session<'_>, valid: bool) -> Self {
        let widths: Vec<f32> = if valid {
            session
                .candidates()
                .iter()
                .map(|text| canvas.measure(text, 16, false) + 30.)
                .collect()
        } else {
            Vec::new()
        };
        let row = (canvas.width - 24.).max(32.);
        let total = widths.iter().sum::<f32>() + widths.len().saturating_sub(1) as f32 * 6.;
        let capacity = (row - if total > row { 60. } else { 0. }).max(32.);
        let mut pages = vec![0];
        let mut used = 0.;
        for (index, width) in widths.iter().enumerate() {
            let width = width.min(capacity);
            if used > 0. && used + 6. + width > capacity {
                pages.push(index);
                used = 0.;
            }
            used += if used > 0. { 6. + width } else { width };
        }
        let page = pages
            .iter()
            .rposition(|start| *start <= session.selected())
            .unwrap_or(0);
        let end = pages.get(page + 1).copied().unwrap_or(widths.len());
        let mut x = 12.;
        let mut candidates = Vec::with_capacity(end - pages[page]);
        for (index, width) in widths.iter().enumerate().take(end).skip(pages[page]) {
            let width = width.min(capacity);
            candidates.push((index, Rect::new(x, 46., width, 30.)));
            x += width + 6.;
        }
        Self {
            width: canvas.width,
            candidates,
            pages,
            page,
        }
    }

    fn menu(&self) -> Rect {
        Rect::new(self.width - 34., 8., 22., 26.)
    }
    fn previous(&self) -> Rect {
        Rect::new(self.width - 62., 47., 22., 28.)
    }
    fn next(&self) -> Rect {
        Rect::new(self.width - 34., 47., 22., 28.)
    }
    fn copy(&self) -> Rect {
        Rect::new(self.width - 66., 83., 54., 22.)
    }
}

fn engine_name(label: &str) -> &str {
    match label {
        "pinyin" => "拼音",
        "kana" => "日语",
        "unicode" => "Unicode",
        "literal" => "原文",
        "table" => "词库",
        "plugin" => "扩展",
        other => other,
    }
}

fn placeholder(label: &str) -> &str {
    match label {
        "pinyin" => "输入拼音，例如 nihao",
        "kana" => "输入罗马字，例如 sushi",
        "unicode" => "输入编码，例如 U+4F60",
        "table" => "输入词条编码",
        _ => "输入要提交的文字",
    }
}

fn centered(canvas: &mut Canvas<'_>, rect: Rect, text: &str, size: i32, color: u32) {
    let width = canvas.measure(text, size, false);
    canvas.text(
        text,
        rect.x + (rect.w - width) / 2.,
        rect.y + (rect.h - size as f32 * 1.3) / 2.,
        size,
        false,
        color,
    );
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
    let mut window = Window::new("ztype compose", 520, 112)?;
    let destination = target_id
        .map(|id| format!("目标 {id} · 输入时切换窗口"))
        .unwrap_or_else(|| "未选目标 · 复制后保留窗口，直到粘贴".into());
    let mut storage = [0u8; MAX_TEXT_BYTES];
    let mut session = Session::new(&mut storage, engine, context)?;
    if let Some(initial) = initial {
        session.set_input(initial)?;
    }
    let mut draft = session.input().to_owned();
    let mut status = String::new();
    let mut status_error = false;
    let mut pointer = (-1., -1.);
    let mut pressed = None;
    let mut show_help = false;
    let mut layout = Layout::empty(520.);
    let mut event = None;
    loop {
        let valid = session.input() == draft;
        let mut edited = false;
        let mut commit = false;
        let mut copy = false;
        match event.take() {
            Some(Event::Close) => break,
            Some(Event::Move(x, y)) => pointer = (x, y),
            Some(Event::Down(x, y)) => {
                pointer = (x, y);
                pressed = Some((x, y));
            }
            Some(Event::Key { code: 27, .. }) => {
                if show_help {
                    show_help = false;
                } else {
                    session.clear()?;
                    draft.clear();
                    status.clear();
                    status_error = false;
                }
            }
            Some(Event::Key { code: 13, .. }) => {
                show_help = false;
                commit = true;
            }
            Some(Event::Key { code: 9, shift, .. }) if valid => {
                show_help = false;
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
                code: 65 | 97,
                ..
            }) => {
                draft.clear();
                edited = true;
            }
            Some(Event::Key {
                control: true,
                code: 67 | 99,
                ..
            }) => copy = true,
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
                        status = "文字过长，请缩短后输入。".into();
                        status_error = true;
                    }
                }
            }
            Some(Event::Up(x, y)) => {
                pointer = (x, y);
                if let Some((down_x, down_y)) = pressed.take() {
                    let clicked = |rect: Rect| rect.contains(down_x, down_y) && rect.contains(x, y);
                    if clicked(layout.menu()) {
                        show_help = !show_help;
                    } else if show_help {
                        copy = clicked(layout.copy());
                    } else if valid {
                        for (index, rect) in &layout.candidates {
                            if clicked(*rect) {
                                session.select(*index)?;
                            }
                        }
                        if layout.pages.len() > 1 {
                            if clicked(layout.next()) {
                                session
                                    .select(layout.pages[(layout.page + 1) % layout.pages.len()])?;
                            }
                            if clicked(layout.previous()) {
                                session.select(
                                    layout.pages[(layout.page + layout.pages.len() - 1)
                                        % layout.pages.len()],
                                )?;
                            }
                        }
                    }
                }
            }
            _ => {}
        }
        if edited {
            show_help = false;
            match session.set_input(&draft) {
                Ok(()) => {
                    status.clear();
                    status_error = false;
                }
                Err(error) => {
                    status = error;
                    status_error = true;
                }
            }
        }
        let draft_valid = session.input() == draft;
        let ready = draft_valid && !session.candidates().is_empty();
        if (copy || commit) && !draft_valid {
            status = "请先完成有效输入。".into();
            status_error = true;
        }
        if copy
            && ready
            && let Some(text) = session.candidates().get(session.selected())
        {
            window.copy(text);
            show_help = false;
            status = "已复制，粘贴前请保留此窗口。".into();
            status_error = false;
        }
        if commit && ready {
            let result = if let Some(target) = target.as_mut() {
                session
                    .commit(target)
                    .map(|_| "已提交到目标窗口。".to_owned())
            } else {
                let mut target = CopyTarget(String::new());
                session.commit(&mut target).map(|_| {
                    window.copy(&target.0);
                    "已复制，粘贴前请保留此窗口。".into()
                })
            };
            match result {
                Ok(message) => {
                    status = message;
                    status_error = false;
                    draft.clear();
                }
                Err(error) => {
                    status = error;
                    status_error = true;
                }
            }
        }

        let ready = draft_valid && !session.candidates().is_empty();
        let hover = |rect: Rect| rect.contains(pointer.0, pointer.1);
        let mut canvas = window.begin(BG);
        layout = Layout::measure(&canvas, &session, draft_valid);

        let input = Rect::new(12., 7., (layout.width - 116.).max(32.), 31.);
        canvas.clip(input);
        let text_width = canvas.measure(&draft, 18, false);
        let text_x = input.x - (text_width - input.w + 3.).max(0.);
        if draft.is_empty() {
            canvas.text(placeholder(label), input.x + 2., 11., 15, false, MUTED);
        } else {
            canvas.text(
                &draft,
                text_x,
                8.,
                18,
                false,
                if draft_valid { INK } else { ERROR },
            );
            canvas.line(
                (text_x, 33.),
                (text_x + text_width, 33.),
                1.,
                if draft_valid { INK } else { ERROR },
            );
        }
        let caret = if draft.is_empty() {
            input.x
        } else {
            text_x + text_width + 1.
        };
        canvas.line((caret, 11.), (caret, 31.), 1., GREEN);
        canvas.unclip();
        canvas.ellipsis(
            engine_name(label),
            layout.width - 88.,
            16.,
            48.,
            11,
            false,
            MUTED,
        );
        if hover(layout.menu()) || show_help {
            canvas.rect(layout.menu(), 5., HOVER);
        }
        centered(
            &mut canvas,
            layout.menu(),
            if show_help { "×" } else { "⋯" },
            19,
            MUTED,
        );

        if show_help {
            canvas.rect(Rect::new(6., 41., layout.width - 12., 66.), 6., HOVER);
            let action = if target.is_some() { "输入" } else { "复制" };
            canvas.text(
                &format!("Enter {action}  ·  Tab / Shift+Tab 选词"),
                12.,
                46.,
                11,
                false,
                INK,
            );
            canvas.text(
                "Ctrl+A 清空  ·  Ctrl+C 复制  ·  Esc 清空 / 关闭说明",
                12.,
                66.,
                11,
                false,
                MUTED,
            );
            canvas.ellipsis(&destination, 12., 87., layout.width - 92., 11, false, MUTED);
            if ready && hover(layout.copy()) {
                canvas.rect(layout.copy(), 5., 0xd9eee5ff);
            }
            centered(
                &mut canvas,
                layout.copy(),
                "复制",
                11,
                if ready { GREEN } else { MUTED },
            );
        } else {
            for (index, rect) in &layout.candidates {
                let selected = session.selected() == *index;
                if selected || hover(*rect) {
                    canvas.rect(*rect, 6., if selected { GREEN } else { HOVER });
                }
                canvas.text(
                    &(index + 1).to_string(),
                    rect.x + 8.,
                    rect.y + 10.,
                    11,
                    false,
                    if selected { BG } else { MUTED },
                );
                if let Some(text) = session.candidates().get(*index) {
                    canvas.ellipsis(
                        text,
                        rect.x + 23.,
                        rect.y + 5.,
                        rect.w - 30.,
                        16,
                        false,
                        if selected { BG } else { INK },
                    );
                }
            }
            if session.candidates().is_empty() && draft_valid {
                canvas.text(
                    if label == "pinyin" {
                        "nihao → 你好"
                    } else {
                        "输入后在这里选词"
                    },
                    12.,
                    52.,
                    14,
                    false,
                    MUTED,
                );
            }
            if layout.pages.len() > 1 {
                for (rect, text) in [(layout.previous(), "‹"), (layout.next(), "›")] {
                    if hover(rect) {
                        canvas.rect(rect, 5., HOVER);
                    }
                    centered(&mut canvas, rect, text, 20, MUTED);
                }
                canvas.text(
                    &format!("{} / {}", layout.page + 1, layout.pages.len()),
                    layout.width - 43.,
                    90.,
                    10,
                    false,
                    MUTED,
                );
            }
            let hint = if status.is_empty() {
                if target.is_some() {
                    "Enter 输入  ·  Tab 选词  ·  ⋯ 查看目标与快捷键"
                } else {
                    "Enter 复制  ·  Tab 选词  ·  ⋯ 更多说明"
                }
            } else {
                &status
            };
            canvas.ellipsis(
                hint,
                12.,
                90.,
                layout.width - if layout.pages.len() > 1 { 68. } else { 24. },
                10,
                false,
                if status_error { ERROR } else { MUTED },
            );
        }
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
