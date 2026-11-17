//! Navigation bars, rails, tabs, app bars, toolbars and the drawer.

use super::{demo_is, row, section};
use anti_webui::prelude::*;
use anti_webui_md3e::color_scheme;
use anti_webui_md3e::components::{
    AppBarKind, AppBarScroll, ListItemStyle, TabsStyle, ToolbarStyle, TopAppBar, bottom_app_bar,
    button, drawer_item, fab, floating_toolbar, floating_toolbar_with_fab, icon_button, icon_tab,
    nav_item, navigation_bar, navigation_drawer, navigation_rail, short_navigation_bar,
    styled_list_item, styled_tabs, tab, tabs, tonal_button, top_app_bar, top_app_bar_over,
    wide_navigation_rail,
};
use anti_webui_md3e::icons;
use anti_webui_md3e::typography::TextStyle;

pub fn page() -> Div {
    div()
        .flex()
        .flex_col()
        .gap(24.0)
        .child(bars())
        .child(rails())
        .child(tab_rows())
        .child(app_bars())
        .child(toolbars())
        .child(drawer())
}

fn destinations() -> Vec<anti_webui_md3e::components::NavItem> {
    vec![
        nav_item(icons::HOME, "Home"),
        nav_item(icons::SEARCH, "Search").badge(|| "3".to_string()),
        nav_item(icons::FAVORITE, "Saved"),
        nav_item(icons::PERSON, "Profile"),
    ]
}

fn frame(width: f32) -> Div {
    div()
        .w(width)
        .flex()
        .flex_col()
        .rounded(16.0)
        .overflow_hidden()
        .border(1.0)
        .dyn_style(|s| s.border_color(color_scheme().outline_variant))
}

fn bars() -> Div {
    let one = signal(0usize);
    let two = signal(1usize);
    let three = signal(2usize);
    section("Navigation bars")
        .child(frame(480.0).child(navigation_bar(
            destinations(),
            move || one.get(),
            move |i| one.set(i),
        )))
        .child(frame(480.0).child(short_navigation_bar(
            destinations(),
            move || two.get(),
            move |i| two.set(i),
            false,
        )))
        .child(frame(640.0).child(short_navigation_bar(
            destinations(),
            move || three.get(),
            move |i| three.set(i),
            true,
        )))
}

fn rails() -> Div {
    let one = signal(0usize);
    let two = signal(1usize);
    let open = signal(false);
    section("Navigation rails").child(
        row()
            .items_start()
            .gap(24.0)
            .child(frame(82.0).h(440.0).child(navigation_rail(
                destinations(),
                move || one.get(),
                move |i| one.set(i),
            )))
            .child(
                frame(226.0).h(440.0).child(wide_navigation_rail(
                    destinations(),
                    move || two.get(),
                    move |i| two.set(i),
                    move || open.get(),
                    Some(
                        div().pl(28.0).child(
                            icon_button(if untrack(|| open.get()) {
                                icons::MENU_OPEN
                            } else {
                                icons::MENU
                            })
                            .on_click(move |_| open.update(|v| *v = !*v)),
                        ),
                    ),
                )),
            ),
    )
}

fn tab_rows() -> Div {
    let one = signal(0usize);
    let two = signal(0usize);
    let three = signal(0usize);
    let four = signal(3usize);
    section("Tabs")
        .child(frame(480.0).child(tabs(
            vec![tab("Flights"), tab("Trips"), tab("Explore")],
            one,
        )))
        .child(frame(480.0).child(tabs(
            vec![
                icon_tab(icons::FLIGHT, "Flights"),
                icon_tab(icons::LUGGAGE, "Trips"),
                icon_tab(icons::EXPLORE, "Explore"),
            ],
            two,
        )))
        .child(frame(480.0).child(styled_tabs(
            vec![tab("Overview"), tab("Specifications"), tab("Reviews")],
            three,
            TabsStyle::secondary(),
        )))
        .child(frame(480.0).child(styled_tabs(
            (1..=10).map(|i| tab(format!("Tab number {i}"))).collect(),
            four,
            TabsStyle::primary().scrollable(),
        )))
}

fn app_bars() -> Div {
    let back = || icon_button(icons::ARROW_BACK);
    let more = || icon_button(icons::MORE_VERT);
    section("App bars")
        .child(
            frame(480.0).child(top_app_bar(
                TopAppBar::new(AppBarKind::Small, "Small")
                    .navigation(back())
                    .action(more()),
            )),
        )
        .child(
            frame(480.0).child(top_app_bar(
                TopAppBar::new(AppBarKind::Center, "Centered")
                    .navigation(back())
                    .action(more()),
            )),
        )
        .child(
            row()
                .items_start()
                .child(
                    frame(320.0).h(280.0).child(
                        top_app_bar_over(
                            TopAppBar::new(AppBarKind::LargeFlexible, "Scroll me")
                                .subtitle("Large flexible")
                                .navigation(back())
                                .action(more()),
                            AppBarScroll::ExitUntilCollapsed,
                            long_list(),
                        )
                        .h(280.0),
                    ),
                )
                .child(
                    frame(320.0).h(280.0).child(
                        top_app_bar_over(
                            TopAppBar::new(AppBarKind::Small, "Enter always")
                                .navigation(back())
                                .action(more()),
                            AppBarScroll::EnterAlways,
                            long_list(),
                        )
                        .h(280.0),
                    ),
                ),
        )
        .child(frame(480.0).child(bottom_app_bar(
            vec![
                icon_button(icons::CHECK_BOX),
                icon_button(icons::BRUSH),
                icon_button(icons::MIC),
                icon_button(icons::IMAGE),
            ],
            Some(fab(icons::ADD)),
        )))
}

fn long_list() -> Div {
    div().flex().flex_col().children(
        (1..=20)
            .map(|i| {
                styled_list_item(
                    format!("Item {i}"),
                    ListItemStyle::new().leading(anti_webui_md3e::components::icon(icons::STAR)),
                )
            })
            .collect::<Vec<_>>(),
    )
}

fn toolbars() -> Div {
    let open = signal(true);
    let with_fab = signal(true);
    let actions = || {
        vec![
            icon_button(icons::FORMAT_BOLD),
            icon_button(icons::FORMAT_ITALIC),
            icon_button(icons::FORMAT_UNDERLINED),
            icon_button(icons::LINK),
        ]
    };
    section("Floating toolbars")
        .child(
            row()
                .h(72.0)
                .child(tonal_button("Open or close").on_click(move |_| open.update(|v| *v = !*v)))
                .child(floating_toolbar(
                    actions(),
                    ToolbarStyle::new().expanded_when(move || open.get()),
                )),
        )
        .child(
            row()
                .h(72.0)
                .child(floating_toolbar(actions(), ToolbarStyle::new().vibrant())),
        )
        .child(row().h(96.0).child(floating_toolbar_with_fab(
            actions(),
            icons::ADD,
            move || with_fab.update(|v| *v = !*v),
            ToolbarStyle::new().expanded_when(move || with_fab.get()),
        )))
}

fn drawer() -> Div {
    let open = signal(demo_is("drawer"));
    let chosen = signal(0usize);
    section("Navigation drawer")
        .child(row().child(button("Open the drawer").on_click(move |_| open.set(true))))
        .child(navigation_drawer(
            move || open.get(),
            move || open.set(false),
            move || {
                div()
                    .flex()
                    .flex_col()
                    .px(12.0)
                    .pt(24.0)
                    .w(280.0)
                    .gap(2.0)
                    .child(
                        TextStyle::TITLE_SMALL
                            .apply(div().pl(16.0).pb(16.0).child(text("Mail")))
                            .dyn_style(|s| s.text_color(color_scheme().on_surface_variant)),
                    )
                    .children(
                        [
                            (icons::INBOX, "Inbox"),
                            (icons::SEND, "Sent"),
                            (icons::DRAFTS, "Drafts"),
                            (icons::DELETE, "Trash"),
                        ]
                        .into_iter()
                        .enumerate()
                        .map(|(i, (symbol, label))| {
                            drawer_item(symbol, label, move || chosen.get() == i)
                                .on_click(move |_| chosen.set(i))
                        })
                        .collect::<Vec<_>>(),
                    )
            },
        ))
}
