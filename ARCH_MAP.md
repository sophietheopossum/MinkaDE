I'm working on a high-performance, highly customizable Linux desktop environment built around ShojiWM (https://github.com/bea4dev/ShojiWM) for power users. They need a functional, visually distinct compositor pipeline and core layout. With that in mind:

### Core Architecture & Strategy
1. Fork ShojiWM and implement HDR support by extending its custom shader pipeline to flag HDR metadata (PQ/Rec.2020) and handle Wayland color management. All commits must match the existing coding style so they can be merged upstream.
2. Build the companion desktop panels, menus, and system trays using a reactive TypeScript ecosystem (like Astal/Ags) to seamlessly match ShojiWM's TSX architecture. shoji-bar-2 (https://github.com/bea4dev/shoji-bar-2) is an example implementation of a UI in ShojiWM.
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

### Scope & Execution
Focus entirely on the foundational architecture, the HDR shader pipeline, and the dual-screen display management code first. Skip generic utilities like text editors. You are fully empowered to choose the cleanest technical implementation for this desktop environment—if a specific shader approach or library works better than what I suggested, implement it and show me the results, this includes the window manager. 

Lead directly with the concrete architectural layout and the initialization code.
