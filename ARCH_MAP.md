I'm working on a high-performance, highly customizable Linux desktop environment built around ShojiWM (https://github.com/bea4dev/ShojiWM) for power users. They need a functional, visually distinct compositor pipeline and core layout. With that in mind:

### Core Architecture & Strategy
1. Fork ShojiWM and implement HDR support by extending its custom shader pipeline to flag HDR metadata (PQ/Rec.2020) and handle Wayland color management.
2. Build the companion desktop panels, menus, and system trays using a reactive TypeScript ecosystem (like Astal/Ags) to seamlessly match ShojiWM's TSX architecture. 
3. Create a specialized dual-display mode tailored for the ASUS Zenbook Duo UX482. When active, all panels, system tray widgets (power, audio, notifications), and global menus must pin to the bottom secondary display (1920x515), leaving the main display entirely clear for application windows. Make sure there is a basic KDE-style layout available for other systems.

### Visual & Shader Constraints
* Maintain a high-contrast, black-and-red aesthetic inspired by the "Eternal Darkness" theme, using purple strictly as a subtle tertiary accent.
* The system must heavily leverage ShojiWM's fragment shaders to achieve two distinct effects: a "frosted glass" blur on window titlebars, and a reactive "liquid glass" physics distortion on transparent surfaces (similar to liquid-terminal-config-shojiwm).
* Prioritize raw rendering performance and animation fluidity over complex security sandboxing.

### Scope & Execution
Focus entirely on the foundational architecture, the HDR shader pipeline, and the dual-screen display management code first. Skip generic utilities like text editors. You are fully empowered to choose the cleanest technical implementation for this desktop environment—if a specific shader approach or library works better than what I suggested, implement it and show me the results, this includes the window manager. 

Lead directly with the concrete architectural layout and the initialization code.
