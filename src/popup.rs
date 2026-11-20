//! Bounded candidate rendering without a window or a desktop connection.
use zgpui_raster::{Canvas, Color, Font};
use ztype::input_method::InputMethod;

const MAX_WIDTH: u32 = 640;
const MIN_WIDTH: u32 = 196;
const HEIGHT: u32 = 84;
const MARGIN: u32 = 12;
const GAP: u32 = 6;
const ROW_Y: i32 = 37;
const ROW_HEIGHT: u32 = 34;
const MAX_LABEL_WIDTH: u32 = 312;
const FONT_SIZE: u32 = 16;

pub struct Frame {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<u8>,
}

pub struct Renderer {
    font: Font,
}

struct Label {
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
        let mut labels = Vec::with_capacity(input.candidates().len());
        for (index, text) in input.candidates().iter().enumerate() {
            let text = if input.number_selection() {
                format!("{}  {}", index + 1, bounded_text(text, false))
            } else {
                bounded_text(text, false)
            };
            let text = self.fit(&text, FONT_SIZE, true, MAX_LABEL_WIDTH - 20)?;
            let width = self.measure(&text, FONT_SIZE, true)?.ceil() as u32;
            labels.push(Label {
                text,
                width: (width + 20).clamp(32, MAX_LABEL_WIDTH),
            });
        }
        // Measure every label at the selected weight so Tab keeps the layout
        // stable. A long dictionary entry cannot
        // request an unbounded shared-memory buffer or glyph bitmap.
        let content = labels.iter().map(|label| label.width).sum::<u32>()
            + GAP * labels.len().saturating_sub(1) as u32;
        let width = (content + MARGIN * 2).clamp(MIN_WIDTH, MAX_WIDTH);
        let widths: Vec<u32> = labels.iter().map(|label| label.width).collect();
        let (start, end) = page_range(&widths, input.selected(), width - MARGIN * 2);
        let page = if labels.len() > end || start > 0 {
            format!("{}–{} / {}", start + 1, end, labels.len())
        } else {
            String::new()
        };
        let page_width = self.measure(&page, 12, false)?.ceil() as u32;
        let header_width = width - MARGIN * 2 - if page.is_empty() { 0 } else { page_width + 16 };
        let header = self.fit(&bounded_text(input.input(), true), 13, false, header_width)?;
        let header_mask = self
            .font
            .rasterize(&header, 13, false)
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
        canvas.clear(Color::rgb(71, 75, 82));
        canvas.rect(1, 1, width - 2, HEIGHT - 2, Color::rgb(32, 33, 36));
        canvas.mask(&header_mask, MARGIN as i32, 9, Color::rgb(189, 193, 198));
        if let Some(page_mask) = page_mask {
            canvas.mask(
                &page_mask,
                (width - MARGIN - page_width) as i32,
                10,
                Color::rgb(154, 160, 166),
            );
        }
        let mut x = MARGIN as i32;
        for (index, label) in labels.iter().enumerate().take(end).skip(start) {
            let selected = index == input.selected();
            if selected {
                canvas.rect(x, ROW_Y, label.width, ROW_HEIGHT, Color::rgb(60, 78, 104));
                canvas.rect(
                    x,
                    ROW_Y + ROW_HEIGHT as i32 - 2,
                    label.width,
                    2,
                    Color::rgb(138, 180, 248),
                );
            }
            let mask = self
                .font
                .rasterize(&label.text, FONT_SIZE, selected)
                .map_err(|error| error.to_string())?;
            let y = ROW_Y + (ROW_HEIGHT as i32 - mask.height() as i32) / 2;
            canvas.mask(
                &mask,
                x + 9,
                y,
                if selected {
                    Color::rgb(232, 240, 254)
                } else {
                    Color::rgb(232, 234, 237)
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
