//! Buttons, icon buttons, FABs, groups and split buttons.

use super::{row, section};
use anti_webui::prelude::*;
use anti_webui_md3e::components::{
    ButtonKind, ButtonSize, ButtonStyle, FabColor, FabSize, FabStyle, IconButtonStyle,
    IconButtonWidth, MenuStyle, SplitButtonStyle, button, button_group, connected_button_group,
    elevated_button, extended_fab, fab, fab_menu, fab_menu_item, filled_icon_button, icon_button,
    icon_toggle_button, menu_item, outlined_button, outlined_icon_button, split_button,
    styled_button, styled_extended_fab, styled_fab, styled_icon_button, styled_menu,
    styled_toggle_button, text_button, toggle_button, tonal_button, tonal_icon_button,
};
use anti_webui_md3e::icons;

pub fn page() -> Div {
    div()
        .flex()
        .flex_col()
        .gap(24.0)
        .child(buttons())
        .child(icon_buttons())
        .child(fabs())
        .child(groups())
        .child(splits())
}

fn buttons() -> Div {
    let bold = signal(true);
    let notify = signal(false);
    section("Buttons")
        .child(
            row()
                .child(button("Filled"))
                .child(tonal_button("Tonal"))
                .child(elevated_button("Elevated"))
                .child(outlined_button("Outlined"))
                .child(text_button("Text"))
                .child(styled_button(
                    "Disabled",
                    ButtonStyle::filled().disabled_when(|| true),
                )),
        )
        .child(
            row().children(
                [
                    ButtonSize::ExtraSmall,
                    ButtonSize::Small,
                    ButtonSize::Medium,
                    ButtonSize::Large,
                ]
                .map(|size| {
                    styled_button("Save", ButtonStyle::filled().size(size).icon(icons::SAVE))
                }),
            ),
        )
        .child(
            row()
                .child(toggle_button("Bold", bold))
                .child(styled_toggle_button(
                    "Notify",
                    notify,
                    ButtonStyle::tonal().icon(icons::NOTIFICATIONS),
                )),
        )
}

fn icon_buttons() -> Div {
    let liked = [signal(true), signal(false), signal(true), signal(false)];
    section("Icon buttons")
        .child(
            row()
                .child(icon_button(icons::SEARCH))
                .child(filled_icon_button(icons::ADD))
                .child(tonal_icon_button(icons::EDIT))
                .child(outlined_icon_button(icons::DELETE))
                .child(styled_icon_button(
                    icons::MENU,
                    IconButtonStyle::filled()
                        .size(ButtonSize::Medium)
                        .width(IconButtonWidth::Wide),
                ))
                .child(styled_icon_button(
                    icons::STAR,
                    IconButtonStyle::tonal().size(ButtonSize::Large).square(),
                )),
        )
        .child(
            row().children(
                [
                    IconButtonStyle::standard(),
                    IconButtonStyle::filled(),
                    IconButtonStyle::tonal(),
                    IconButtonStyle::outlined(),
                ]
                .into_iter()
                .zip(liked)
                .map(|(style, on)| icon_toggle_button(icons::FAVORITE, on, style))
                .collect::<Vec<_>>(),
            ),
        )
}

fn fabs() -> Div {
    let open = signal(true);
    let menu_open = signal(false);
    section("FAB").child(
        row()
            .items_end()
            .child(fab(icons::ADD))
            .child(styled_fab(
                icons::EDIT,
                FabStyle::new()
                    .size(FabSize::Medium)
                    .color(FabColor::Tertiary),
            ))
            .child(styled_fab(
                icons::NAVIGATION,
                FabStyle::new()
                    .size(FabSize::Large)
                    .color(FabColor::SecondaryContainer),
            ))
            .child(extended_fab(icons::ADD, "Create"))
            .child(
                styled_extended_fab(
                    icons::EDIT,
                    "Compose",
                    FabStyle::new().expanded_when(move || open.get()),
                )
                .on_click(move |_| open.update(|v| *v = !*v)),
            )
            .child(fab_menu(
                icons::ADD,
                move || menu_open.get(),
                move || menu_open.update(|v| *v = !*v),
                vec![
                    fab_menu_item(icons::IMAGE, "Image", move || menu_open.set(false)),
                    fab_menu_item(icons::VIDEOCAM, "Video", move || menu_open.set(false)),
                    fab_menu_item(icons::DESCRIPTION, "Document", move || menu_open.set(false)),
                ],
            )),
    )
}

fn groups() -> Div {
    let tab = signal(0usize);
    section("Button groups")
        .child(button_group([
            button("Back"),
            tonal_button("Play"),
            button("Next"),
        ]))
        .child(connected_button_group(
            ["Day", "Week", "Month", "Year"],
            ButtonStyle::filled(),
            move |i| tab.get() == i,
            move |i| tab.set(i),
        ))
}

fn splits() -> Div {
    let open = signal(false);
    let tonal_open = signal(false);
    let anchor = node_ref();
    section("Split buttons").child(
        row()
            .child(
                split_button(
                    "Send",
                    move || open.get(),
                    || {},
                    move || open.update(|v| *v = !*v),
                    SplitButtonStyle::new(ButtonKind::Filled).icon(icons::SEND),
                )
                .node_ref(anchor)
                .child(styled_menu(
                    move || open.get(),
                    move || open.set(false),
                    vec![vec![
                        menu_item("Send now").icon(icons::SEND),
                        menu_item("Schedule").icon(icons::SCHEDULE),
                        menu_item("Save as draft").icon(icons::DRAFTS),
                    ]],
                    MenuStyle::new()
                        .anchor(anchor)
                        .placement(Placement::BelowEnd),
                )),
            )
            .child(split_button(
                "Tonal",
                move || tonal_open.get(),
                || {},
                move || tonal_open.update(|v| *v = !*v),
                SplitButtonStyle::new(ButtonKind::Tonal).size(ButtonSize::Medium),
            ))
            .child(split_button(
                "Outlined",
                || false,
                || {},
                || {},
                SplitButtonStyle::new(ButtonKind::Outlined),
            )),
    )
}
