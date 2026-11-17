//! Moving things about. See `docs/design/23-drag-and-drop.md`.

use super::{photo, row, section};
use anti_webui::prelude::*;
use anti_webui_md3e::color_scheme;
use anti_webui_md3e::components::{
    TextFieldStyle, avatar, card_media, elevated_card, icon, list_item, outlined_card,
    sortable_list, sortable_row, text_field,
};
use anti_webui_md3e::icons;
use anti_webui_md3e::typography::TextStyle;

pub fn page() -> Div {
    div()
        .flex()
        .flex_col()
        .gap(24.0)
        .child(selected_text())
        .child(list())
        .child(pictures())
        .child(boxes())
}

fn selected_text() -> Div {
    let value = signal("Select me and drag me to the field below".to_string());
    let other = signal(String::new());
    section("Selected text").child(
        div()
            .flex()
            .flex_col()
            .gap(16.0)
            .w(560.0)
            .child(text(
                "Select some of this and drag the selection: a picture of the text goes along. \
                 Drop it on a field here or on another program.",
            ))
            .child(text_field(
                value,
                TextFieldStyle::new().label("Drag from here"),
            ))
            .child(text_field(other, TextFieldStyle::new().label("Drop here"))),
    )
}

fn list() -> Div {
    let tasks = signal(
        ["Coffee", "Mail", "A long walk", "Review", "Lunch"]
            .map(str::to_string)
            .to_vec(),
    );
    section("Sortable list").child(
        sortable_list(
            tasks,
            |t: &String| t.clone(),
            |t, row_of| {
                row()
                    .pl(16.0)
                    .child(row_of.grip(div().p(8.0).child(icon(icons::DRAG_INDICATOR))))
                    .child(div().flex_1().child(list_item(t)))
            },
        )
        .w(420.0)
        .rounded(16.0)
        .overflow_hidden(),
    )
}

fn pictures() -> Div {
    let seeds = signal(vec![1u32, 2, 3, 4, 5]);
    section("Sortable row").child(
        sortable_row(
            seeds,
            |s: &u32| *s,
            |s, _| {
                elevated_card()
                    .w(120.0)
                    .child(card_media(photo(s, 240, 160)).h(90.0))
            },
        )
        .gap(12.0),
    )
}

#[derive(Clone)]
struct Person(&'static str);

fn boxes() -> Div {
    let inside = signal(Vec::<&'static str>::new());
    let people = ["Ada", "Grace", "Alan"];
    section("Drag and drop").child(
        row()
            .items_start()
            .child(
                div()
                    .flex()
                    .flex_col()
                    .gap(8.0)
                    .children(people.map(|name| {
                        div()
                            .select_none()
                            .cursor(CursorIcon::Grab)
                            .dragging(|s| s.opacity(0.38))
                            .draggable(move || Person(name))
                            .drag_preview(|p: &Person| {
                                avatar(photo(p.0.len() as u32, 80, 80), p.0).w(56.0).h(56.0)
                            })
                            .child(
                                avatar(photo(name.len() as u32, 80, 80), name)
                                    .w(40.0)
                                    .h(40.0),
                            )
                    })),
            )
            .child(
                outlined_card()
                    .w(280.0)
                    .min_h(140.0)
                    .p(16.0)
                    .flex()
                    .flex_col()
                    .gap(8.0)
                    .dyn_style(|s| {
                        let on = dragged::<Person>().is_some();
                        let c = color_scheme();
                        s.border_color(if on { c.primary } else { c.outline_variant })
                    })
                    .drag_over(|s| s.bg(color_scheme().primary_container))
                    .drop_accepts(move |p: &Person| !inside.with(|i| i.contains(&p.0)))
                    .drop_target(move |p: &Person, _: &DropEvent| inside.update(|i| i.push(p.0)))
                    .child(TextStyle::BODY_MEDIUM.apply(text("Drop people here")))
                    .child(for_each(
                        move || inside.get(),
                        |n: &&'static str| *n,
                        |n: &'static str| TextStyle::TITLE_MEDIUM.apply(text(n)),
                    )),
            ),
    )
}
