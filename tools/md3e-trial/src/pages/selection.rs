//! Switches, checkboxes, radio buttons, sliders and chips.

use super::{row, section};
use anti_webui::prelude::*;
use anti_webui_md3e::components::{
    CheckState, CheckboxStyle, ChipStyle, RadioStyle, SliderStyle, SwitchStyle, assist_chip,
    checkbox, filter_chip, input_chip, radio_button, range_slider, slider, styled_assist_chip,
    styled_checkbox, styled_filter_chip, styled_radio_button, styled_slider, styled_switch,
    suggestion_chip, switch, tri_state_checkbox,
};
use anti_webui_md3e::icons;

pub fn page() -> Div {
    div()
        .flex()
        .flex_col()
        .gap(24.0)
        .child(switches())
        .child(checkboxes())
        .child(radios())
        .child(sliders())
        .child(chips())
}

fn switches() -> Div {
    let flags = [signal(false), signal(true), signal(false), signal(true)];
    section("Switch").child(
        row()
            .child(switch(flags[0]))
            .child(switch(flags[1]))
            .child(styled_switch(
                flags[2],
                SwitchStyle::new().on_icon(icons::CHECK),
            ))
            .child(styled_switch(
                flags[3],
                SwitchStyle::new()
                    .on_icon(icons::CHECK)
                    .off_icon(icons::CLOSE),
            ))
            .child(styled_switch(
                signal(false),
                SwitchStyle::new().disabled_when(|| true),
            ))
            .child(styled_switch(
                signal(true),
                SwitchStyle::new().disabled_when(|| true),
            )),
    )
}

fn checkboxes() -> Div {
    let items = [signal(true), signal(false), signal(true)];
    // One that stands for the others: it shows what they are and sets them all.
    let all = Binding::new(
        move || match items.iter().filter(|i| i.get()).count() {
            0 => CheckState::Off,
            3 => CheckState::On,
            _ => CheckState::Mixed,
        },
        move |state| {
            for item in items {
                item.set(state == CheckState::On);
            }
        },
    );
    section("Checkbox").child(
        row()
            .child(tri_state_checkbox(all))
            .children(items.map(checkbox))
            .child(styled_checkbox(
                signal(false),
                CheckboxStyle::new().error_when(|| true),
            ))
            .child(styled_checkbox(
                signal(true),
                CheckboxStyle::new().disabled_when(|| true),
            ))
            .child(styled_checkbox(
                signal(false),
                CheckboxStyle::new().disabled_when(|| true),
            )),
    )
}

fn radios() -> Div {
    let chosen = signal(1);
    section("Radio button").child(
        row()
            .children([0, 1, 2].map(|i| radio_button(chosen, i)))
            .child(styled_radio_button(
                signal(true),
                true,
                RadioStyle::new().disabled_when(|| true),
            ))
            .child(styled_radio_button(
                signal(false),
                true,
                RadioStyle::new().disabled_when(|| true),
            )),
    )
}

fn sliders() -> Div {
    let plain = signal(0.4f32);
    let labelled = signal(30.0f32);
    let stepped = signal(4.0f32);
    let centered = signal(0.2f32);
    let (from, to) = (signal(20.0f32), signal(70.0f32));
    let thick = signal(0.6f32);
    let upright = signal(0.5f32);
    section("Slider").child(
        div()
            .flex()
            .flex_row()
            .gap(32.0)
            .child(
                div()
                    .flex()
                    .flex_col()
                    .gap(8.0)
                    .child(slider(plain).w(320.0))
                    .child(
                        styled_slider(
                            labelled,
                            SliderStyle::new()
                                .range(0.0..=100.0)
                                .label(|v| format!("{v:.0}")),
                        )
                        .w(320.0),
                    )
                    .child(
                        styled_slider(stepped, SliderStyle::new().range(0.0..=10.0).steps(9))
                            .w(320.0),
                    )
                    .child(
                        styled_slider(centered, SliderStyle::new().range(-1.0..=1.0).centered())
                            .w(320.0),
                    )
                    .child(range_slider(from, to, SliderStyle::new().range(0.0..=100.0)).w(320.0))
                    .child(
                        styled_slider(
                            thick,
                            SliderStyle::new()
                                .track(36.0)
                                .corner(12.0)
                                .handle_height(52.0),
                        )
                        .w(320.0)
                        .h(56.0),
                    )
                    .child(
                        styled_slider(signal(0.3), SliderStyle::new().disabled_when(|| true))
                            .w(320.0),
                    ),
            )
            .child(styled_slider(upright, SliderStyle::new().vertical())),
    )
}

fn chips() -> Div {
    let filters = [signal(true), signal(false), signal(false)];
    let people = signal(vec!["Alex", "Sam", "Kim"]);
    section("Chips")
        .child(
            row()
                .child(assist_chip("Assist"))
                .child(styled_assist_chip(
                    "With icon",
                    ChipStyle::new().icon(icons::EVENT),
                ))
                .child(styled_assist_chip(
                    "Elevated",
                    ChipStyle::new().icon(icons::SHARE).elevated(),
                ))
                .child(styled_assist_chip(
                    "Disabled",
                    ChipStyle::new().disabled_when(|| true),
                ))
                .child(suggestion_chip("Suggestion")),
        )
        .child(
            row()
                .children(
                    ["Mine", "Shared", "Recent"]
                        .into_iter()
                        .zip(filters)
                        .map(|(label, on)| filter_chip(label, on)),
                )
                .child({
                    let on = signal(false);
                    styled_filter_chip("Favorite", on, ChipStyle::new().icon(icons::FAVORITE))
                }),
        )
        .child(
            row().child(
                for_each(
                    move || people.get(),
                    |name| *name,
                    move |name| {
                        input_chip(
                            name,
                            ChipStyle::new()
                                .icon(icons::PERSON)
                                .trailing(icons::CLOSE, move || {
                                    people.update(|p| p.retain(|n| *n != name))
                                }),
                        )
                    },
                )
                .enter(|s| s.opacity(0.0).scale(0.8))
                .exit(|s| s.opacity(0.0).scale(0.8))
                .moves(Transition::ms(200).ease_out()),
            ),
        )
}
