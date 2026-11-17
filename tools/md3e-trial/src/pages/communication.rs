//! Progress, shapes, badges, tooltips and snackbars.

use super::{demo_is, row, section};
use anti_webui::prelude::*;
use anti_webui_md3e::color_scheme;
use anti_webui_md3e::components::{
    ButtonStyle, Ink, MaterialShape, RichTooltip, ShapeStyle, Snackbar, badge, badged, button,
    circular_progress, circular_wavy_progress, filled_icon_button, icon, icon_button,
    indeterminate_circular_progress, indeterminate_circular_wavy_progress,
    indeterminate_linear_progress, indeterminate_wavy_progress, linear_progress, loading_indicator,
    outlined_button, rich_tooltip, slider, snackbars, styled_button, styled_shape, text_badge,
    tonal_button, tooltip, wavy_progress,
};
use anti_webui_md3e::icons;

pub fn page() -> Div {
    div()
        .flex()
        .flex_col()
        .gap(24.0)
        .child(progress())
        .child(shapes())
        .child(badges())
        .child(tooltips())
        .child(snacks())
}

fn progress() -> Div {
    let value = signal(0.6f32);
    section("Progress")
        .child(slider(value).w(320.0))
        .child(
            row()
                .gap(24.0)
                .child(circular_progress(move || value.get()))
                .child(circular_wavy_progress(move || value.get()))
                .child(indeterminate_circular_progress())
                .child(indeterminate_circular_wavy_progress())
                .child(loading_indicator()),
        )
        .child(
            div()
                .flex()
                .flex_col()
                .gap(16.0)
                .child(linear_progress(move || value.get()).w(320.0))
                .child(wavy_progress(move || value.get()).w(320.0))
                .child(indeterminate_linear_progress().w(320.0))
                .child(indeterminate_wavy_progress().w(320.0)),
        )
}

/// The catalog, each shape turning into the next under the pointer, and a box of its own with
/// `Ink` that goes through them as it is clicked.
fn shapes() -> Div {
    let all = MaterialShape::ALL;
    let tile = move |i: usize| {
        let hovered = signal(false);
        let (shape, next) = (all[i], all[(i + 1) % all.len()]);
        tooltip(
            styled_shape(
                move || if hovered.get() { next } else { shape },
                ShapeStyle::new().color(|c| c.secondary),
            )
            .w(48.0)
            .h(48.0)
            .on_hover(move |on| hovered.set(on)),
            format!("{shape:?}"),
        )
    };
    let grid =
        div()
            .flex()
            .flex_col()
            .gap(12.0)
            .children(all.chunks(12).enumerate().map(move |(r, chunk)| {
                row().children((0..chunk.len()).map(move |c| tile(r * 12 + c)))
            }));

    let at = signal(0usize);
    let ink = Ink::new(|c| c.on_primary_container, || false);
    let button = div()
        .w(96.0)
        .h(96.0)
        .rounded(28.0)
        .flex()
        .items_center()
        .justify_center()
        .cursor(CursorIcon::Pointer)
        .focusable()
        .access(|a| a.role(Role::Button).label("Next shape"))
        .dyn_style(|s| s.bg(color_scheme().primary_container))
        .on_click(move |_| at.update(|i| *i += 1))
        .child(
            styled_shape(
                move || all[at.get() % all.len()],
                ShapeStyle::new()
                    .color(|c| c.on_primary_container)
                    .rotation(move || at.get() as f32 * 90.0),
            )
            .w(56.0)
            .h(56.0),
        );
    section("Shapes")
        .child(grid)
        .child(row().gap(24.0).child(ink.paint(ink.listen(button))))
}

fn badges() -> Div {
    let count = signal(3);
    section("Badge").child(
        row()
            .gap(24.0)
            .child(badged(icon(icons::NOTIFICATIONS), badge(), false))
            .child(badged(
                icon(icons::MAIL),
                text_badge(move || match count.get() {
                    0 => String::new(),
                    n => n.to_string(),
                }),
                true,
            ))
            .child(tonal_button("Add").on_click(move |_| count.update(|c| *c += 1)))
            .child(outlined_button("Clear").on_click(move |_| count.set(0))),
    )
}

fn tooltips() -> Div {
    section("Tooltip").child(
        row()
            .child(tooltip(icon_button(icons::SEARCH), "Search"))
            .child(tooltip(filled_icon_button(icons::ADD), "Add a new item"))
            .child(rich_tooltip(
                button("Rich tooltip"),
                RichTooltip::new("Rich tooltips say more, and can have something to do in them.")
                    .title("Title")
                    .action("Learn more", || {}),
            )),
    )
}

fn snacks() -> Div {
    let bars = snackbars();
    if demo_is("snackbar") {
        bars.show(
            Snackbar::new("Photo saved")
                .action("Undo", || {})
                .closable(),
        );
    }
    section("Snackbar")
        .child(
            row()
                .child(button("Short").on_click(move |_| bars.show(Snackbar::new("Photo saved"))))
                .child(
                    styled_button("With an action", ButtonStyle::tonal()).on_click(move |_| {
                        bars.show(Snackbar::new("Message archived").action("Undo", || {}));
                    }),
                )
                .child(
                    styled_button("Closable", ButtonStyle::outlined()).on_click(move |_| {
                        bars.show(
                            Snackbar::new("Couldn't reach the server")
                                .action("Retry", || {})
                                .closable(),
                        );
                    }),
                ),
        )
        .child(bars.view())
}
