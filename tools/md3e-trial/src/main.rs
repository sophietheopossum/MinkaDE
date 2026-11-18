//! A window for trying the Material 3 Expressive widgets of AntiWebUI (`anti-webui-md3e`).
//!
//! ```sh
//! cargo run --release                       # or target/release/md3e-trial
//! cargo run --release -- inputs             # start on a page
//! cargo run --release -- --palette material
//! ```
//!
//! Pages: actions, selection, communication, containment, dragging, navigation, inputs, and
//! fonts (Noto Sans beside Roboto Flex, which is this tool's own page).
//! Palettes: proustite (the one it starts in), material, red. `--light` starts in the light scheme.
//!
//! In the window, the moon button in the bar switches dark and light, and the three buttons in
//! the strip at the bottom switch the palette.
//!
//! `src/pages/` is the `showcase` example of anti-webui-md3e (bea4dev, MIT OR Apache-2.0), copied
//! unchanged from the AntiWebUI revision named in `Cargo.toml`. Edit the pages to try things.
//! `src/fonts.rs` is not from there.
//!
//! `--shot <page> <file.png>` writes a page to a file without opening a window.

// The pages are upstream's and stay as they are, untyped float literals included.
#[allow(float_literal_f32_fallback)]
mod pages;

mod fonts;

use anti_webui::app::headless;
use anti_webui::prelude::*;
use anti_webui_md3e::components::{
    AppBarKind, AppBarScroll, ButtonSize, ButtonStyle, IconButtonStyle, TopAppBar,
    connected_button_group, icon_button, icon_toggle_button, nav_item, top_app_bar_over,
    wide_navigation_rail,
};
use anti_webui_md3e::typography::TextStyle;
use anti_webui_md3e::{
    ColorScheme, color_scheme, icons, motion, set_color_scheme, set_color_scheme_animated,
};
use std::cell::Cell;
use std::rc::Rc;

const PAGES: [(&str, &str); 8] = [
    ("actions", "Actions"),
    ("selection", "Selection"),
    ("communication", "Feedback"),
    ("containment", "Containers"),
    ("dragging", "Dragging"),
    ("navigation", "Navigation"),
    ("inputs", "Inputs"),
    ("fonts", "Fonts"),
];

/// The palettes: what `--palette` calls them, and what their buttons say.
const PALETTES: [(&str, &str); 3] = [
    ("material", "Material"),
    ("proustite", "Proustite"),
    ("red", "Red seed"),
];

/// Proustite's tokens, as in `../../Proustite/Proustite.qml`.
mod proustite {
    pub const GROUND: u32 = 0x0a0709;
    pub const SURFACE: u32 = 0x161013;
    pub const SURFACE_RAISED: u32 = 0x1e161a;
    pub const LINE: u32 = 0x2e2228;
    pub const TEXT: u32 = 0xece5e7;
    pub const TEXT_MUTED: u32 = 0xa3959b;
    pub const TEXT_FAINT: u32 = 0x6e6167;
    pub const RED: u32 = 0xff0000;
    pub const RED_DIM: u32 = 0x8f1e2d;
    pub const PURPLE: u32 = 0xa488c9;
    pub const PURPLE_DIM: u32 = 0x4c3a63;
    pub const GAUGE_DIM: u32 = 0x3a1219;
    pub const WARN_AMBER: u32 = 0xe0a026;
}

/// Proustite's tokens in Material's color roles. The roles Proustite has no token for (the error
/// ones and the scrim) are the ones Material grows from Proustite's red.
fn proustite_scheme() -> ColorScheme {
    use proustite::*;
    ColorScheme {
        primary: rgb(RED),
        on_primary: rgb(GROUND),
        primary_container: rgb(RED_DIM),
        on_primary_container: rgb(TEXT),
        secondary: rgb(PURPLE),
        on_secondary: rgb(GROUND),
        secondary_container: rgb(PURPLE_DIM),
        on_secondary_container: rgb(TEXT),
        tertiary: rgb(WARN_AMBER),
        on_tertiary: rgb(GROUND),
        tertiary_container: rgb(GAUGE_DIM),
        on_tertiary_container: rgb(TEXT),
        surface: rgb(GROUND),
        surface_dim: rgb(GROUND),
        surface_bright: rgb(LINE),
        surface_variant: rgb(SURFACE_RAISED),
        on_surface: rgb(TEXT),
        on_surface_variant: rgb(TEXT_MUTED),
        inverse_surface: rgb(TEXT),
        inverse_on_surface: rgb(GROUND),
        inverse_primary: rgb(RED_DIM),
        surface_container_lowest: rgb(GROUND),
        surface_container_low: rgb(SURFACE),
        surface_container: rgb(SURFACE),
        surface_container_high: rgb(SURFACE_RAISED),
        surface_container_highest: rgb(LINE),
        outline: rgb(TEXT_FAINT),
        outline_variant: rgb(LINE),
        ..ColorScheme::from_seed(rgb(RED), true)
    }
}

/// The scheme of a palette. Proustite has no light scheme, so in light it is the one Material
/// grows from its red, as "Red seed" is in both.
fn scheme(palette: usize, dark: bool) -> ColorScheme {
    match (PALETTES[palette].0, dark) {
        ("proustite", true) => proustite_scheme(),
        ("proustite" | "red", _) => ColorScheme::from_seed(rgb(proustite::RED), dark),
        (_, true) => ColorScheme::dark(),
        (_, false) => ColorScheme::light(),
    }
}

fn page(name: &str) -> Div {
    match name {
        "actions" => pages::actions::page(),
        "selection" => pages::selection::page(),
        "communication" => pages::communication::page(),
        "containment" => pages::containment::page(),
        "dragging" => pages::dragging::page(),
        "navigation" => pages::navigation::page(),
        "inputs" => pages::inputs::page(),
        "fonts" => fonts::page(),
        _ => div(),
    }
}

/// The whole window: a rail that opens, a bar with the title and the dark switch, the page, and
/// a strip with the palette buttons below it.
fn app(first: usize, palette_at_start: usize, dark_at_start: bool) -> Div {
    let current = signal(first);
    let dark = signal(dark_at_start);
    let palette = signal(palette_at_start);
    let rail_open = signal(false);
    // The first scheme is there at once; the ones after it come in over a few frames.
    let shown = Rc::new(Cell::new(false));
    effect(move || {
        let colors = scheme(palette.get(), dark.get());
        if shown.replace(true) {
            set_color_scheme_animated(colors, motion::default_effects());
        } else {
            set_color_scheme(colors);
        }
    });
    let destinations = vec![
        nav_item(icons::TOUCH_APP, PAGES[0].1),
        nav_item(icons::TOGGLE_ON, PAGES[1].1),
        nav_item(icons::NOTIFICATIONS, PAGES[2].1),
        nav_item(icons::DASHBOARD, PAGES[3].1),
        nav_item(icons::PAN_TOOL, PAGES[4].1),
        nav_item(icons::EXPLORE, PAGES[5].1),
        nav_item(icons::KEYBOARD, PAGES[6].1),
        nav_item(icons::TEXT_FIELDS, PAGES[7].1),
    ];
    let content = for_each(
        move || vec![current.get()],
        |i| *i,
        move |i| {
            let bar = TopAppBar::new(AppBarKind::LargeFlexible, PAGES[i].1)
                .subtitle("Material 3 Expressive on AntiWebUI")
                .action(icon_toggle_button(
                    icons::DARK_MODE,
                    dark,
                    IconButtonStyle::standard(),
                ))
                .action(icon_button(icons::MORE_VERT));
            top_app_bar_over(
                bar,
                AppBarScroll::ExitUntilCollapsed,
                div()
                    .flex()
                    .flex_col()
                    .child(page(PAGES[i].0).p(24.0_f32).pb(120.0_f32)),
            )
            .absolute()
            .left(0.0)
            .top(0.0)
            .right(0.0)
            .bottom(0.0)
            .dyn_style(|s| s.bg(color_scheme().surface))
        },
    )
    .enter(|s| s.opacity(0.0_f32).translate_y(24.0_f32))
    .exit(|s| s.opacity(0.0_f32))
    .transition(Transition::ms(220).cubic_bezier(0.05, 0.7, 0.1, 1.0));
    let palettes = connected_button_group(
        PALETTES.map(|p| p.1),
        ButtonStyle::tonal().size(ButtonSize::ExtraSmall),
        move |i| palette.get() == i,
        move |i| palette.set(i),
    );
    div()
        .size_full()
        .flex()
        .flex_row()
        .dyn_style(|s| {
            let c = color_scheme();
            s.bg(c.surface).text_color(c.on_surface)
        })
        .child(wide_navigation_rail(
            destinations,
            move || current.get(),
            move |i| current.set(i),
            move || rail_open.get(),
            Some(
                div().pl(28.0_f32).child(
                    icon_button(icons::MENU).on_click(move |_| rail_open.update(|v| *v = !*v)),
                ),
            ),
        ))
        .child(
            div()
                .flex()
                .flex_col()
                .flex_grow(1.0_f32)
                .flex_basis(0.0)
                .min_w(0.0)
                .child(
                    div()
                        .relative()
                        .flex_grow(1.0_f32)
                        .flex_basis(0.0)
                        .min_h(0.0)
                        .overflow_hidden()
                        .child(content),
                )
                .child(
                    div()
                        .flex()
                        .flex_row()
                        .flex_none()
                        .items_center()
                        .gap(16.0_f32)
                        .px(24.0_f32)
                        .py(10.0_f32)
                        .dyn_style(|s| {
                            let c = color_scheme();
                            s.bg(c.surface_container).text_color(c.on_surface_variant)
                        })
                        .child(TextStyle::LABEL_LARGE.apply(text("Palette")))
                        .child(palettes),
                ),
        )
}

fn usage() -> ! {
    eprintln!(
        "usage: md3e-trial [page] [--palette material|proustite|red] [--light]\n       \
         md3e-trial --shot <page> <file.png> [--palette ...] [--light]\n\
         pages: {}",
        PAGES.map(|p| p.0).join(", ")
    );
    std::process::exit(2)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "--help" || a == "-h") {
        usage();
    }
    let page_index = |name: &str| PAGES.iter().position(|p| p.0 == name);
    let dark = !args.iter().any(|a| a == "--light");
    let palette = match args.iter().position(|a| a == "--palette") {
        Some(i) => args
            .get(i + 1)
            .and_then(|name| PALETTES.iter().position(|p| p.0 == name))
            .unwrap_or_else(|| usage()),
        None => PALETTES
            .iter()
            .position(|p| p.0 == "proustite")
            .unwrap_or(0),
    };
    if let Some(i) = args.iter().position(|a| a == "--shot") {
        let (Some(name), Some(file)) = (args.get(i + 1), args.get(i + 2)) else {
            usage()
        };
        let first = page_index(name).unwrap_or_else(|| usage());
        headless::render_png(file, 2340, 1800, 1.8, move || app(first, palette, dark))
            .expect("a shot");
        return;
    }
    let first = args.iter().find_map(|a| page_index(a)).unwrap_or(0);
    App::new()
        .title("Material 3 Expressive trial")
        .app_id("md3e-trial")
        .size(1300, 900)
        .run(move || app(first, palette, dark))
        .unwrap();
}
