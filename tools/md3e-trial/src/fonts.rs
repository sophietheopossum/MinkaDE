//! Noto Sans beside Roboto Flex, in the text MinkaDE's windows show and at the sizes they show
//! it.
//!
//! This page is not part of the showcase copied into `pages/`.

use crate::pages::section;
use anti_webui::prelude::*;
use anti_webui_md3e::color_scheme;

const NOTO: &str = "Noto Sans";
const ROBOTO: &str = "Roboto Flex";

/// The fonts, one column each.
const FACES: [&str; 2] = [NOTO, ROBOTO];

/// One line of a sample: its text, size in logical pixels and weight. `faint` draws it in the
/// color of secondary text.
struct Line {
    text: &'static str,
    size: f32,
    weight: u16,
    faint: bool,
}

const fn line(text: &'static str, size: f32, weight: u16, faint: bool) -> Line {
    Line {
        text,
        size,
        weight,
        faint,
    }
}

/// What a window header, a list and a footer say, as MinkaSyu and MinkaShell say it.
const WINDOW: [Line; 9] = [
    line("System updates", 17.0, 700, false),
    line("23 updates available · 18 unticked", 12.0, 400, true),
    line("checked 86 min ago", 11.0, 400, true),
    line("REPOSITORIES", 10.0, 700, true),
    line("lib32-zlib-ng-compat", 13.0, 400, false),
    line("2.3.2-2 → 2.3.3-2  cachyos-v4 → cachyos", 11.0, 400, true),
    line("xdg-desktop-portal-shojiwm", 13.0, 400, false),
    line("0.1.2-9 → 0.1.2-10 · new commit 351c111", 11.0, 400, true),
    line("Opens rio: paru -Syu --sudoloop", 11.0, 400, true),
];

/// Numbers, dates and money: what a ledger and a monitor are made of.
const FIGURES: [Line; 5] = [
    line("0123456789", 20.0, 400, false),
    line("17/11/2026  18:29  58%  91 °C", 13.0, 400, false),
    line("£1,234.56  −£87.10  €0.99  ×3", 13.0, 400, false),
    line("12.21.2-1  3:1.39.7-2  725356c4", 13.0, 400, false),
    line("15.6 GiB  7.7 GB  120 Hz  4.1 GHz", 12.0, 400, true),
];

/// Letters that are easy to mistake for one another, and a sentence with every letter.
const LETTERS: [Line; 4] = [
    line("Il1 O0 rn m cl d S5 B8 Z2", 16.0, 400, false),
    line("Sphinx of black quartz, judge my vow.", 13.0, 400, false),
    line("SPHINX OF BLACK QUARTZ", 11.0, 500, true),
    line("Ελληνικά · Кириллица · Tiếng Việt", 13.0, 400, false),
];

/// The weights a control label and a title use.
const WEIGHTS: [Line; 4] = [
    line("Light 300 · Update and publish", 14.0, 300, false),
    line("Regular 400 · Update and publish", 14.0, 400, false),
    line("Medium 500 · Update and publish", 14.0, 500, false),
    line("Bold 700 · Update and publish", 14.0, 700, false),
];

fn sample(family: &'static str, l: &Line) -> Text {
    let faint = l.faint;
    text(l.text)
        .font_family(family)
        .text_size(l.size)
        .font_weight(FontWeight(l.weight))
        .whitespace_nowrap()
        .dyn_style(move |s| {
            let c = color_scheme();
            s.text_color(if faint {
                c.on_surface_variant
            } else {
                c.on_surface
            })
        })
}

/// One font's column: its name, then the lines one under another. Lines are never wrapped, so
/// how far each one reaches is how wide the font sets it.
fn column(family: &'static str, lines: &'static [Line]) -> Div {
    let mut column = div()
        .flex()
        .flex_col()
        .flex_1()
        .min_w(0.0_f32)
        .gap(4.0_f32)
        .p(16.0_f32)
        .rounded(12.0_f32)
        .overflow_hidden()
        .dyn_style(|s| s.bg(color_scheme().surface_container))
        .child(
            text(family)
                .font_family(ROBOTO)
                .text_size(11.0_f32)
                .font_weight(FontWeight(500))
                .dyn_style(|s| s.text_color(color_scheme().secondary)),
        );
    for l in lines {
        column = column.child(sample(family, l));
    }
    column
}

fn comparison(title: &str, lines: &'static [Line]) -> Div {
    let mut row = div().flex().flex_row().gap(12.0_f32);
    for family in FACES {
        row = row.child(column(family, lines));
    }
    section(title).child(row)
}

pub fn page() -> Div {
    div()
        .flex()
        .flex_col()
        .gap(28.0_f32)
        .child(comparison("A window's text", &WINDOW))
        .child(comparison("Figures", &FIGURES))
        .child(comparison("Letters", &LETTERS))
        .child(comparison("Weights", &WEIGHTS))
}
