I'm working on a high-performance, highly customizable Linux desktop environment built around ShojiWM (https://github.com/bea4dev/ShojiWM) for power users. They need a functional, visually distinct compositor pipeline and core layout. With that in mind:

### Core Architecture & Strategy
1. Fork ShojiWM and implement HDR support by extending its custom shader pipeline to flag HDR metadata (PQ/Rec.2020) and handle Wayland color management. All commits must match the existing coding style so they can be merged upstream.
2. Build the companion shell as a hybrid of two Wayland-native layer-shell runtimes, both peers on ShojiWM's existing NDJSON IPC socket: Quickshell (Qt6/QML) for widget- and service-heavy surfaces (bar, launcher, control center, notification daemon, system tray, lockscreen), and Guido (Rust + wgpu, https://malpenzibo.github.io/guido/) for latency-sensitive animated overlays (snap previews, drag feedback, OSDs) with spring physics and SDF rendering. All IPC strictly non-blocking; animate client-side toward broadcast state rather than streaming frames. shoji-bar-2 (https://github.com/bea4dev/shoji-bar-2) retired 8/7/2026: the ShojiWM config now spawns MinkaShell as the session shell and MinkaFX (with the MinkaIPC crate) owns the snap preview; the repo remains for historical reference only. Visual references: caelestia-shell (layout/structure), zephyr by flickowoa (animated elements).
3. Create a specialized dual-display mode tailored for the ASUS Zenbook Duo UX482. When active, all panels, system tray widgets (power, audio, notifications), and global menus must pin to the bottom secondary display (1920x515), leaving the main display entirely clear for application windows. Make sure there is a basic KDE-style layout available for other systems.

### Visual & Shader Constraints
* Maintain a high-contrast, black-and-red aesthetic inspired by the "Eternal Darkness" theme, using purple strictly as a subtle tertiary accent.
* The system must heavily leverage ShojiWM's fragment shaders to achieve two distinct effects: a "frosted glass" blur on window titlebars, and a reactive "liquid glass" physics distortion on transparent surfaces (similar to liquid-terminal-config-shojiwm).
* Prioritize raw rendering performance and animation fluidity over complex security sandboxing.

### Deferred / Later
HDR polish (working baseline shipped 2026-07: config `hdr: true` → PQ/BT.2020 signaling + fp16 encode pass; not urgent):
* PQ client-content input transform: surfaces tagged PQ/BT.2020 via `wp_color_management_v1` are still composited as if sRGB — HDR video (mpv) looks washed out until a per-surface input transform lands in the render pipeline.
* Promote `SHOJI_SDR_NITS` (SDR white level on the PQ signal, default 203) from env var into the runtime display config (`sdrNits` per output), same pattern as the `hdr` flag — env vars don't reach DM-launched sessions.
* Consider decoding the SDR composite with pure gamma 2.2 instead of the piecewise sRGB EOTF in `output_encode.frag` (KWin does this; avoids raised shadows / grayish blacks on HDR outputs).
* Send `image_description_changed`/`preferred_changed` to already-bound color-management clients on a live SDR↔HDR switch (needs per-object tracking in `protocols/color_management.rs`).

X11 fractional-scaling rendering mode (baseline shipped 2026-07-10; X11 has one global DPI, so mixed-scale layouts force a tradeoff):
* Expose a user choice in MinkaConf between the two X11 bridge worlds:
* **World A "consistent size"** (current default: logical wl_output modes + `preferred_scale` pinned 1.0 for bridge clients → X apps sized like native apps on every screen, but upscale-soft on any output with scale > 1)
* **World B "crisp"** (stock xwayland-satellite device-pixel behavior → pixel-perfect on every screen, but UI size only correct on outputs matching the global Xft.DPI; optionally expose the DPI knob to pick which screen looks right).
* Plumbing:
* World A is `normalize_to_logical` (smithay fork ClientOutputOverride) + `preferred_scale_for_surface` pin (ShojiWM presentation.rs) — both would become a runtime setting instead of hardcoded.
* World B additionally requires xwayland-satellite ≥ upstream 33c344f (origin normalization) and the single-stable-output enter gating (refresh_space_outputs), which stays on in both worlds.
* MinkaConf should normalize saved display layouts so the bounding box origin is (0,0) (like xrandr) — protects distro satellite < 33c344f and anything else assuming an origin-anchored screen.

### Scope & Execution
Focus entirely on the foundational architecture, the HDR shader pipeline, and the dual-screen display management code first. Skip generic utilities like text editors. You are fully empowered to choose the cleanest technical implementation for this desktop environment—if a specific shader approach or library works better than what I suggested, implement it and show me the results, this includes the window manager. 

Lead directly with the concrete architectural layout and the initialization code.
