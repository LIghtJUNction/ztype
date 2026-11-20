//! Bounded candidate rendering without a window or a desktop connection.
mod presentation;
pub use presentation::{Controller, Presentation, Surface};

use zgpui_raster::{Canvas, Color, Font};
use ztype::input_method::InputMethod;

const MAX_WIDTH: u32 = 640;
const MIN_WIDTH: u32 = 248;
const HEIGHT: u32 = 112;
const MARGIN: u32 = 20;
const GAP: u32 = 8;
const ROW_Y: i32 = 54;
const ROW_HEIGHT: u32 = 40;
const MAX_LABEL_WIDTH: u32 = 312;
const FONT_SIZE: u32 = 17;
const NUMBER_SIZE: u32 = 13;
const NUMBER_GAP: u32 = 8;
const LABEL_PADDING: u32 = 12;

pub struct Frame {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<u8>,
}

pub struct Renderer {
    font: Font,
}

struct Label {
    number: Option<String>,
    number_width: u32,
    text: String,
    width: u32,
}

impl Renderer {
    pub fn new() -> Result<Self, String> {
        Ok(Self {
            font: Font::new("").map_err(|error| error.to_string())?,
        })
    }

    pub fn render(&mut self, input: &InputMethod<'_>) -> Result<Frame, String> {
        let range = input.page_range();
        let mut labels = Vec::with_capacity(range.len());
        // Keep every numbered item on this page visible and mapped to the same
        // numeric key. Only long candidate text is elided, never its shortcut.
        let label_limit = ((MAX_WIDTH - MARGIN * 2 - GAP * range.len().saturating_sub(1) as u32)
            / range.len().max(1) as u32)
            .min(MAX_LABEL_WIDTH);
        for (index, text) in input
            .candidates()
            .iter()
            .enumerate()
            .take(range.end)
            .skip(range.start)
        {
            let number = input
                .number_selection()
                .then(|| (index - range.start + 1).to_string());
            let number_width = match &number {
                Some(number) => self.measure(number, NUMBER_SIZE, false)?.ceil() as u32,
                None => 0,
            };
            let prefix = if number.is_some() {
                number_width + NUMBER_GAP
            } else {
                0
            };
            let text = self.fit(
                &bounded_text(text, false),
                FONT_SIZE,
                true,
                label_limit.saturating_sub(LABEL_PADDING * 2 + prefix),
            )?;
            let width = self.measure(&text, FONT_SIZE, true)?.ceil() as u32;
            labels.push(Label {
                number,
                number_width,
                text,
                width: (width + prefix + LABEL_PADDING * 2).clamp(40, label_limit.max(40)),
            });
        }
        let content = labels.iter().map(|label| label.width).sum::<u32>()
            + GAP * labels.len().saturating_sub(1) as u32;
        let page = if input.candidates().len() > input.page_size() {
            format!(
                "{} / {}",
                input.selected() / input.page_size() + 1,
                input.candidates().len().div_ceil(input.page_size())
            )
        } else {
            String::new()
        };
        let page_width = self.measure(&page, 12, false)?.ceil() as u32;
        let width = (content + MARGIN * 2)
            .max(page_width + MARGIN * 2 + 88)
            .clamp(MIN_WIDTH, MAX_WIDTH);
        let header_width =
            (width - MARGIN * 2).saturating_sub(if page.is_empty() { 0 } else { page_width + 20 });
        let raw = input.input();
        let cursor = input.input_cursor();
        let composing = format!("{}│{}", &raw[..cursor], &raw[cursor..]);
        let header = self.fit(&bounded_text(&composing, true), 14, false, header_width)?;
        let header_mask = self
            .font
            .rasterize(&header, 14, false)
            .map_err(|error| error.to_string())?;
        let page_mask = if page.is_empty() {
            None
        } else {
            Some(
                self.font
                    .rasterize(&page, 12, false)
                    .map_err(|error| error.to_string())?,
            )
        };
        let mut pixels = vec![0_u32; width as usize * HEIGHT as usize];
        let mut canvas = Canvas::new(&mut pixels, width as usize, HEIGHT as usize, width as usize)
            .map_err(|error| error.to_string())?;
        // The card is fully opaque on both ARGB Wayland surfaces and depth-24
        // X11 windows. Rounded selection corners reveal the card, not alpha.
        canvas.clear(Color::rgb(218, 220, 224));
        canvas.rect(1, 1, width - 2, HEIGHT - 2, Color::rgb(255, 255, 255));
        canvas.rect(
            MARGIN as i32,
            44,
            width - MARGIN * 2,
            1,
            Color::rgb(241, 243, 244),
        );
        canvas.mask(&header_mask, MARGIN as i32, 18, Color::rgb(60, 64, 67));
        if let Some(page_mask) = page_mask {
            canvas.mask(
                &page_mask,
                (width - MARGIN - page_width) as i32,
                20,
                Color::rgb(95, 99, 104),
            );
        }
        let mut x = MARGIN as i32;
        for (index, label) in labels.iter().enumerate() {
            let selected = index + range.start == input.selected();
            if selected {
                let fill = Color::rgb(232, 240, 254);
                canvas.rect(x, ROW_Y + 4, label.width, ROW_HEIGHT - 8, fill);
                for (row, inset) in [4_u32, 2, 1, 1].into_iter().enumerate() {
                    for y in [
                        ROW_Y + row as i32,
                        ROW_Y + ROW_HEIGHT as i32 - 1 - row as i32,
                    ] {
                        canvas.rect(x + inset as i32, y, label.width - inset * 2, 1, fill);
                    }
                }
            }
            let mut text_x = x + LABEL_PADDING as i32;
            if let Some(number) = &label.number {
                let mask = self
                    .font
                    .rasterize(number, NUMBER_SIZE, false)
                    .map_err(|error| error.to_string())?;
                canvas.mask(
                    &mask,
                    text_x,
                    ROW_Y + (ROW_HEIGHT as i32 - mask.height() as i32) / 2,
                    if selected {
                        Color::rgb(26, 115, 232)
                    } else {
                        Color::rgb(95, 99, 104)
                    },
                );
                text_x += (label.number_width + NUMBER_GAP) as i32;
            }
            let mask = self
                .font
                .rasterize(&label.text, FONT_SIZE, selected)
                .map_err(|error| error.to_string())?;
            canvas.mask(
                &mask,
                text_x,
                ROW_Y + (ROW_HEIGHT as i32 - mask.height() as i32) / 2,
                if selected {
                    Color::rgb(23, 78, 166)
                } else {
                    Color::rgb(32, 33, 36)
                },
            );
            x += label.width as i32 + GAP as i32;
        }
        // Canvas owns premultiplied 0xAARRGGBB words. The native adapters take
        // explicit little-endian BGRA bytes, independent of the host endian.
        let pixels = pixels.into_iter().flat_map(u32::to_le_bytes).collect();
        Ok(Frame {
            width,
            height: HEIGHT,
            pixels,
        })
    }

    fn measure(&mut self, text: &str, size: u32, bold: bool) -> Result<f32, String> {
        let width = self
            .font
            .measure(text, size, bold)
            .map_err(|error| error.to_string())?;
        if width.is_finite() && width >= 0. {
            Ok(width)
        } else {
            Err("font returned an invalid advance".into())
        }
    }

    fn fit(&mut self, text: &str, size: u32, bold: bool, width: u32) -> Result<String, String> {
        if self.measure(text, size, bold)? <= width as f32 {
            return Ok(text.into());
        }
        let ends: Vec<usize> = text
            .char_indices()
            .map(|(index, _)| index)
            .chain([text.len()])
            .collect();
        let mut low = 0;
        let mut high = ends.len() - 1;
        while low < high {
            let middle = low + (high - low).div_ceil(2);
            let shortened = format!("{}…", &text[..ends[middle]]);
            if self.measure(&shortened, size, bold)? <= width as f32 {
                low = middle;
            } else {
                high = middle - 1;
            }
        }
        Ok(format!("{}…", &text[..ends[low]]))
    }
}

/// Explicit opt-in acceptance metadata. Text and surrounding content never
/// enter this file; a submitted frame still requires screenshot verification.
pub struct Diagnostics {
    path: Option<std::path::PathBuf>,
    sequence: u64,
}

pub struct Snapshot {
    pub requested_visible: bool,
    pub selected: usize,
    pub candidate_count: usize,
    pub generation: u64,
    pub width: u32,
    pub height: u32,
    pub presentation_result: &'static str,
}

impl Diagnostics {
    pub fn new() -> Self {
        Self {
            path: std::env::var_os("ZTYPE_POPUP_STATE_PATH")
                .filter(|path| !path.is_empty())
                .map(Into::into),
            sequence: 0,
        }
    }

    pub fn record(&mut self, state: Snapshot) {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let Some(path) = &self.path else {
            return;
        };
        self.sequence = self.sequence.wrapping_add(1);
        let temporary =
            path.with_extension(format!("{}.{}.tmp", std::process::id(), self.sequence));
        let content = format!(
            "{{\"schema\":\"ztype.popup-state.v1\",\"sequence\":{},\"requested_visible\":{},\"selected\":{},\"candidate_count\":{},\"generation\":{},\"width\":{},\"height\":{},\"presentation_result\":\"{}\"}}\n",
            self.sequence,
            state.requested_visible,
            state.selected,
            state.candidate_count,
            state.generation,
            state.width,
            state.height,
            state.presentation_result,
        );
        let mut created = false;
        let result = (|| -> std::io::Result<()> {
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&temporary)?;
            created = true;
            file.write_all(content.as_bytes())?;
            drop(file);
            std::fs::rename(&temporary, path)
        })();
        if result.is_err() {
            if created {
                let _ = std::fs::remove_file(&temporary);
            }
            self.path = None;
            eprintln!("ztype: popup acceptance metadata could not be written; disabling it");
        }
    }
}

fn bounded_text(text: &str, tail: bool) -> String {
    const LIMIT: usize = 128;
    let clean = |ch: char| if ch.is_control() { ' ' } else { ch };
    let mut chars: Vec<char> = if tail {
        text.chars().rev().take(LIMIT + 1).map(clean).collect()
    } else {
        text.chars().take(LIMIT + 1).map(clean).collect()
    };
    let truncated = chars.len() > LIMIT;
    chars.truncate(LIMIT);
    if tail {
        chars.reverse();
        let mut output = String::new();
        if truncated {
            output.push('…');
        }
        output.extend(chars);
        output
    } else {
        let mut output: String = chars.into_iter().collect();
        if truncated {
            output.push('…');
        }
        output
    }
}

#[cfg(test)]
fn page_range(widths: &[u32], selected: usize, capacity: u32) -> (usize, usize) {
    let mut start = 0;
    let mut used = 0;
    for (index, width) in widths.iter().copied().enumerate() {
        if index > start && used + GAP + width > capacity {
            if selected < index {
                return (start, index);
            }
            start = index;
            used = 0;
        }
        used += if used == 0 { width } else { GAP + width };
    }
    (start, widths.len())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ztype::{engine::Engine, input_method::Key};

    #[test]
    fn long_multilingual_labels_remain_bounded_and_selected_page_visible() {
        let widths = [MAX_LABEL_WIDTH; 9];
        for selected in 0..9 {
            let (start, end) = page_range(&widths, selected, MAX_WIDTH - MARGIN * 2);
            assert!(start <= selected && selected < end);
            assert!(
                widths[start..end].iter().sum::<u32>() + GAP * (end - start - 1) as u32
                    <= MAX_WIDTH - MARGIN * 2
            );
        }
        let text = "你かな\n".repeat(1024);
        for tail in [true, false] {
            let bounded = bounded_text(&text, tail);
            assert!(bounded.chars().count() <= 129);
            assert!(!bounded.chars().any(char::is_control));
        }
    }

    #[test]
    fn real_chinese_and_long_table_candidates_render_without_a_display() {
        let mut renderer = Renderer::new().unwrap();
        let mut storage = [0; ztype::MAX_TEXT_BYTES];
        let mut ime =
            InputMethod::new(&mut storage, Engine::named("pinyin", None).unwrap()).unwrap();
        ime.key(Key::Text("nihao"), 0).unwrap();
        let frame = renderer.render(&ime).unwrap();
        assert!((MIN_WIDTH..=MAX_WIDTH).contains(&frame.width));
        assert_eq!(
            frame.pixels.len(),
            frame.width as usize * HEIGHT as usize * 4
        );
        assert!(
            frame
                .pixels
                .as_chunks::<4>()
                .0
                .iter()
                .all(|pixel| pixel[3] == 255)
        );
        assert!(
            frame
                .pixels
                .as_chunks::<4>()
                .0
                .iter()
                .any(|pixel| pixel[0] > 200 && pixel[1] > 200)
        );

        let dictionary = format!("a\t{}\n", "你かな".repeat(400));
        let mut storage = [0; ztype::MAX_TEXT_BYTES];
        let mut table = InputMethod::new(
            &mut storage,
            Engine::named("table", Some(&dictionary)).unwrap(),
        )
        .unwrap();
        table.key(Key::Text("a"), 0).unwrap();
        assert!(!table.number_selection());
        let frame = renderer.render(&table).unwrap();
        assert!(frame.pixels.len() <= (MAX_WIDTH * HEIGHT * 4) as usize);
    }
}
