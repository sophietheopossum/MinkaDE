//! Text fields, search, menus and pickers.

use super::{demo_is, row, section};
use anti_webui::prelude::*;
use anti_webui_md3e::color_scheme;
use anti_webui_md3e::components::{
    ButtonStyle, Date, DatePickerStyle, MenuStyle, SearchStyle, TextFieldStyle, TimePickerStyle,
    button, date_picker_dialog, dropdown_field, icon, icon_button, list_item, menu, menu_item,
    outlined_text_field, search_bar, styled_button, styled_menu, text_button, text_field,
    time_picker, time_picker_dialog, tonal_button,
};
use anti_webui_md3e::icons;
use anti_webui_md3e::typography::TextStyle;

pub fn page() -> Div {
    div()
        .flex()
        .flex_col()
        .gap(24.0)
        .child(fields())
        .child(searches())
        .child(menus())
        .child(pickers())
}

fn fields() -> Div {
    let name = signal(String::new());
    let mail = signal("someone@example.com".to_string());
    let bad = signal("not a number".to_string());
    let outlined = signal(String::new());
    let price = signal("120".to_string());
    let notes = signal(String::new());
    let fruit = signal("Apple".to_string());
    section("Text fields")
        .child(
            row()
                .items_start()
                .child(text_field(
                    name,
                    TextFieldStyle::new()
                        .label("Name")
                        .placeholder("Your full name")
                        .supporting(|| "As it appears on your card".to_string()),
                ))
                .child(text_field(
                    mail,
                    TextFieldStyle::new()
                        .label("Mail")
                        .leading(icon(icons::MAIL))
                        .trailing(icon_button(icons::CLOSE)),
                ))
                .child(text_field(
                    bad,
                    TextFieldStyle::new()
                        .label("Age")
                        .error_when(|| true)
                        .supporting(|| "Has to be a number".to_string()),
                )),
        )
        .child(
            row()
                .items_start()
                .child(outlined_text_field(
                    outlined,
                    TextFieldStyle::new()
                        .label("Outlined")
                        .placeholder("Type here"),
                ))
                .child(outlined_text_field(
                    price,
                    TextFieldStyle::new()
                        .label("Price")
                        .prefix("$")
                        .suffix("USD")
                        .leading(icon(icons::PAYMENTS)),
                ))
                .child(outlined_text_field(
                    signal("Can't touch this".to_string()),
                    TextFieldStyle::new()
                        .label("Disabled")
                        .disabled_when(|| true),
                )),
        )
        .child(
            row()
                .items_start()
                .child(text_field(
                    notes,
                    TextFieldStyle::new().label("Notes").rows(3),
                ))
                .child(dropdown_field(
                    fruit,
                    vec![
                        "Apple".to_string(),
                        "Banana".to_string(),
                        "Cherry".to_string(),
                        "Grape".to_string(),
                    ],
                    TextFieldStyle::new().label("Fruit"),
                    false,
                )),
        )
}

fn searches() -> Div {
    let docked = signal(String::new());
    let docked_open = signal(demo_is("search"));
    let full = signal(String::new());
    let full_open = signal(false);
    let results = || {
        div().flex().flex_col().children(
            ["Recent search", "Another one", "Something else", "One more"]
                .into_iter()
                .map(list_item)
                .collect::<Vec<_>>(),
        )
    };
    section("Search").child(
        row()
            .child(search_bar(
                docked,
                move || docked_open.get(),
                move |v| docked_open.set(v),
                results,
                SearchStyle::new().placeholder("Search here").width(360.0),
            ))
            .child(search_bar(
                full,
                move || full_open.get(),
                move |v| full_open.set(v),
                results,
                SearchStyle::new()
                    .placeholder("Over the whole window")
                    .width(360.0)
                    .full_screen(),
            )),
    )
}

fn menus() -> Div {
    let plain = signal(false);
    let groups = signal(false);
    let sort = signal(0usize);
    let vibrant = signal(false);
    section("Menus").child(
        row()
            .child(
                button("Plain")
                    .on_pointer_down(move |_| plain.set(true))
                    .child(menu(
                        move || plain.get(),
                        move || plain.set(false),
                        vec![vec![
                            menu_item("Cut")
                                .icon(icons::CONTENT_CUT)
                                .trailing("Ctrl+X")
                                .on_click(move || plain.set(false)),
                            menu_item("Copy")
                                .icon(icons::CONTENT_COPY)
                                .trailing("Ctrl+C")
                                .on_click(move || plain.set(false)),
                            menu_item("Paste")
                                .icon(icons::CONTENT_PASTE)
                                .trailing("Ctrl+V")
                                .disabled_when(|| true),
                        ]],
                    )),
            )
            .child(
                tonal_button("Groups")
                    .on_pointer_down(move |_| groups.set(true))
                    .child(menu(
                        move || groups.get(),
                        move || groups.set(false),
                        vec![
                            ["Name", "Date", "Size"]
                                .into_iter()
                                .enumerate()
                                .map(|(i, label)| {
                                    menu_item(label)
                                        .selected_when(move || sort.get() == i)
                                        .on_click(move || sort.set(i))
                                })
                                .collect(),
                            vec![
                                menu_item("Settings")
                                    .icon(icons::SETTINGS)
                                    .supporting("For this list only")
                                    .on_click(move || groups.set(false)),
                            ],
                        ],
                    )),
            )
            .child(
                styled_button("Vibrant", ButtonStyle::outlined())
                    .on_pointer_down(move |_| vibrant.set(true))
                    .child(styled_menu(
                        move || vibrant.get(),
                        move || vibrant.set(false),
                        vec![vec![
                            menu_item("One").on_click(move || vibrant.set(false)),
                            menu_item("Two").on_click(move || vibrant.set(false)),
                        ]],
                        MenuStyle::new().vibrant(),
                    )),
            ),
    )
}

fn pickers() -> Div {
    let date = signal(Some(Date::new(2026, 11, 4)));
    let date_open = signal(demo_is("date"));
    let time = signal((9u32, 41u32));
    let time_open = signal(demo_is("time"));
    let shown = signal((16u32, 5u32));
    let say = move || {
        let (h, m) = time.get();
        format!("{h:02}:{m:02}")
    };
    section("Pickers")
        .child(
            row()
                .child(button("Choose a date").on_click(move |_| date_open.set(true)))
                .child(tonal_button("Choose a time").on_click(move |_| time_open.set(true)))
                .child(
                    TextStyle::BODY_LARGE
                        .apply(text(move || match date.get() {
                            Some(d) => format!("{}-{:02}-{:02}", d.year, d.month, d.day),
                            None => "No date".to_string(),
                        }))
                        .dyn_style(|s| s.text_color(color_scheme().on_surface_variant)),
                )
                .child(
                    TextStyle::BODY_LARGE
                        .apply(text(say))
                        .dyn_style(|s| s.text_color(color_scheme().on_surface_variant)),
                ),
        )
        .child(
            time_picker(
                move || shown.get(),
                move |t| shown.set(t),
                TimePickerStyle::new(),
            )
            .self_start(),
        )
        .child(date_picker_dialog(
            move || date_open.get(),
            move || date_open.set(false),
            move || date.get(),
            move |d| date.set(Some(d)),
            DatePickerStyle::new(),
            move || {
                vec![
                    text_button("Cancel").on_click(move |_| date_open.set(false)),
                    text_button("OK").on_click(move |_| date_open.set(false)),
                ]
            },
        ))
        .child(time_picker_dialog(
            move || time_open.get(),
            move || time_open.set(false),
            move || time.get(),
            move |t| time.set(t),
            TimePickerStyle::new().twenty_four_hours(),
            move || {
                vec![
                    text_button("Cancel").on_click(move |_| time_open.set(false)),
                    text_button("OK").on_click(move |_| time_open.set(false)),
                ]
            },
        ))
}
