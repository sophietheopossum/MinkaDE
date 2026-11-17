//! The pages of the showcase.

pub mod actions;
pub mod communication;
pub mod containment;
pub mod dragging;
pub mod inputs;
pub mod navigation;
pub mod selection;

use anti_webui::prelude::*;
use anti_webui_md3e::color_scheme;
use anti_webui_md3e::typography::TextStyle;

/// Whether a shot asks for this to be open from the start. See `main`.
pub fn demo_is(name: &str) -> bool {
    std::env::var("SHOWCASE_OPEN").is_ok_and(|v| v == name)
}

/// A row of things to try.
pub fn row() -> Div {
    div().flex().flex_row().items_center().gap(12.0)
}

/// A title with what it is about below it.
pub fn section(title: &str) -> Div {
    div().flex().flex_col().gap(12.0).child(
        TextStyle::TITLE_MEDIUM
            .apply(text(title.to_string()))
            .dyn_style(|s| s.text_color(color_scheme().primary)),
    )
}

/// A made-up photo: hills under a sky, in what shades `seed` says. No files to carry around.
pub fn photo(seed: u32, width: u32, height: u32) -> ImageSource {
    let hue = seed as f32 * 0.7;
    let mut pixels = Vec::with_capacity((width * height * 4) as usize);
    for y in 0..height {
        for x in 0..width {
            let (u, v) = (x as f32 / width as f32, y as f32 / height as f32);
            let wave = |freq: f32, shift: f32| (u * freq + shift + hue).sin() * 0.5 + 0.5;
            let far = 0.45 + 0.12 * wave(6.0, 1.0);
            let near = 0.68 + 0.1 * wave(9.0, 4.0);
            let aspect = width as f32 / height as f32;
            let sun = (((u - 0.72) * aspect).powi(2) + (v - 0.25).powi(2)).sqrt();
            let (r, g, b) = if v > near {
                (0.16 + 0.1 * hue.sin().abs(), 0.42 - 0.2 * (v - near), 0.3)
            } else if v > far {
                (0.3 + 0.1 * wave(3.0, 0.0), 0.5, 0.45 + 0.1 * hue.cos())
            } else if sun < 0.08 {
                (1.0, 0.92, 0.6)
            } else {
                (
                    0.45 + 0.4 * v + 0.1 * hue.sin(),
                    0.6 + 0.25 * v,
                    0.95 - 0.1 * v,
                )
            };
            pixels.extend([
                (r.clamp(0.0, 1.0) * 255.0) as u8,
                (g.clamp(0.0, 1.0) * 255.0) as u8,
                (b.clamp(0.0, 1.0) * 255.0) as u8,
                255,
            ]);
        }
    }
    ImageSource::rgba(width, height, pixels)
}
