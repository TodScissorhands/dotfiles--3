#!/usr/bin/env python3
"""Generate semantic color mappings from the active pywal16 palette.

Reads ~/.cache/wal/colors.json and writes consumer-specific config files
with contrast-enforced semantic color roles. Called by wallpaper-ctl.fish
after pywal16 generates the raw palette.

Does NOT modify colors.json or any pywal16 internals.
"""

import colorsys
import json
import os
import sys
from pathlib import Path

CACHE = Path.home() / ".cache" / "wal"
COLORS_JSON = CACHE / "colors.json"

# Output targets (generated artifacts, not source files)
OUT_GHOSTTY = CACHE / "ghostty.conf"
OUT_WAYBAR = CACHE / "waybar.css"
OUT_NIRI = CACHE / "niri-colors.kdl"
OUT_SWAY = CACHE / "sway-colors"
OUT_FISH = CACHE / "fish-colors.fish"
OUT_SEMANTIC = CACHE / "semantic-colors.json"
OUT_FUZZEL = CACHE / "fuzzel.ini"
OUT_SWAYLOCK = CACHE / "swaylock"
OUT_YAZI = CACHE / "yazi-theme.toml"
OUT_FIREFOX = CACHE / "firefox-colors.css"
OUT_RMPC = CACHE / "rmpc-theme.ron"
OUT_MAKO = CACHE / "mako.conf"


# ---------------------------------------------------------------------------
# Color math
# ---------------------------------------------------------------------------

def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


def relative_luminance(h: str) -> float:
    """WCAG 2.1 relative luminance."""
    r, g, b = (c / 255.0 for c in hex_to_rgb(h))
    channels = []
    for c in (r, g, b):
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def lighten(h: str, amount: float) -> str:
    """Lighten by amount (0.0–1.0)."""
    r, g, b = hex_to_rgb(h)
    return rgb_to_hex(
        int(r + (255 - r) * amount),
        int(g + (255 - g) * amount),
        int(b + (255 - b) * amount),
    )


def darken(h: str, amount: float) -> str:
    """Darken by amount (0.0–1.0)."""
    r, g, b = hex_to_rgb(h)
    return rgb_to_hex(int(r * (1 - amount)), int(g * (1 - amount)), int(b * (1 - amount)))


def blend(a: str, b: str, t: float = 0.5) -> str:
    """Blend a toward b by t (0.0=a, 1.0=b)."""
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    return rgb_to_hex(
        int(ra + (rb - ra) * t),
        int(ga + (gb - ga) * t),
        int(ba + (bb - ba) * t),
    )


def saturate(h: str, amount: float) -> str:
    """Set saturation to amount (0.0–1.0) preserving hue and lightness."""
    r, g, b = hex_to_rgb(h)
    hue, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    nr, ng, nb = colorsys.hls_to_rgb(hue, light, min(1.0, max(0.0, amount)))
    return rgb_to_hex(int(nr * 255), int(ng * 255), int(nb * 255))


def add_saturation(h: str, amount: float) -> str:
    """Add saturation (can be negative) preserving hue and lightness."""
    r, g, b = hex_to_rgb(h)
    hue, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    nr, ng, nb = colorsys.hls_to_rgb(hue, light, min(1.0, max(0.0, sat + amount)))
    return rgb_to_hex(int(nr * 255), int(ng * 255), int(nb * 255))


def ensure_contrast(fg: str, bg: str, min_ratio: float) -> str:
    """Lighten or darken fg until it meets min_ratio against bg.

    Preserves hue by adjusting lightness in HLS space.
    """
    if contrast_ratio(fg, bg) >= min_ratio:
        return fg

    bg_lum = relative_luminance(bg)
    r, g, b = hex_to_rgb(fg)
    hue, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)

    # On dark backgrounds, lighten; on light, darken.
    direction = 1 if bg_lum < 0.5 else -1
    step = 0.02

    for _ in range(80):
        light = max(0.0, min(1.0, light + direction * step))
        nr, ng, nb = colorsys.hls_to_rgb(hue, light, sat)
        candidate = rgb_to_hex(int(nr * 255), int(ng * 255), int(nb * 255))
        if contrast_ratio(candidate, bg) >= min_ratio:
            return candidate

    # Fallback: return very light or very dark
    return "#e0e0e0" if direction > 0 else "#1a1a1a"


def pick_most_saturated(colors: list[str], exclude: set[str] | None = None) -> str:
    """Pick the most saturated color from a list."""
    best, best_sat = colors[0], 0.0
    for c in colors:
        if exclude and c in exclude:
            continue
        r, g, b = hex_to_rgb(c)
        _, _, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if sat > best_sat:
            best, best_sat = c, sat
    return best


def color_hue_distance(a: str, b: str) -> float:
    """Circular hue distance in [0, 0.5]."""
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    ha, _, _ = colorsys.rgb_to_hls(ra / 255, ga / 255, ba / 255)
    hb, _, _ = colorsys.rgb_to_hls(rb / 255, gb / 255, bb / 255)
    d = abs(ha - hb)
    return min(d, 1.0 - d)


# ---------------------------------------------------------------------------
# Semantic mapping
# ---------------------------------------------------------------------------

def compute_semantic(colors: dict) -> dict:
    """Derive semantic color roles from pywal16's palette."""
    bg = colors["special"]["background"]
    fg = colors["special"]["foreground"]
    c = colors["colors"]

    # Pywal16 cols16 darken palette structure:
    # color0  = background
    # color1-6 = dark variants (sorted by brightness)
    # color7  = light gray
    # color8  = mid gray (bright black)
    # color9-14 = bright variants of 1-6
    # color15 = foreground

    bg_lum = relative_luminance(bg)
    is_dark = bg_lum < 0.3

    # --- Background variants ---
    background = bg
    background_alt = lighten(bg, 0.08) if is_dark else darken(bg, 0.06)

    # --- Foreground hierarchy with contrast enforcement ---
    foreground = ensure_contrast(fg, bg, 7.0)
    foreground_secondary = ensure_contrast(c["color7"], bg, 4.5)
    foreground_muted = ensure_contrast(c["color8"], bg, 3.5)
    foreground_disabled = ensure_contrast(
        blend(c["color8"], bg, 0.35), bg, 2.5
    )

    # --- Accent: pick most saturated palette color, ensure readable ---
    mid_colors = [c[f"color{i}"] for i in range(1, 7)]
    bright_colors = [c[f"color{i}"] for i in range(9, 15)]

    accent_base = pick_most_saturated(bright_colors)
    accent = ensure_contrast(accent_base, bg, 4.5)

    # Secondary accent: pick most saturated with hue distance from accent
    accent_secondary_candidates = sorted(
        bright_colors,
        key=lambda x: (
            -color_hue_distance(x, accent_base),
            -colorsys.rgb_to_hls(*[v / 255 for v in hex_to_rgb(x)])[2],
        ),
    )
    acc2_base = accent_secondary_candidates[0] if accent_secondary_candidates else c["color14"]
    accent_secondary = ensure_contrast(acc2_base, bg, 4.5)

    # If both accents ended up identical, shift the secondary
    if accent == accent_secondary:
        for cand in accent_secondary_candidates[1:]:
            shifted = ensure_contrast(cand, bg, 4.5)
            if shifted != accent:
                accent_secondary = shifted
                break

    # --- Selection: strong contrast pair ---
    # On dark themes, blend accent color toward bg so foreground text stands out.
    # On light themes, darken it so dark text stands out.
    sel_bg_base = c["color4"] if is_dark else c["color12"]
    if is_dark:
        selection_bg = add_saturation(blend(sel_bg_base, bg, 0.5), 0.2)
        # If even this doesn't give enough room, darken further
        if contrast_ratio(foreground, selection_bg) < 5.0:
            selection_bg = add_saturation(blend(sel_bg_base, bg, 0.65), 0.2)
    else:
        selection_bg = darken(sel_bg_base, 0.1)
    selection_fg = ensure_contrast(foreground, selection_bg, 5.5)

    # --- Cursor: high-visibility, accent-tinted ---
    cursor = ensure_contrast(accent, bg, 5.0)

    # --- Borders ---
    border = ensure_contrast(blend(c["color4"], fg, 0.25), bg, 2.5)
    border_inactive = ensure_contrast(blend(c["color8"], bg, 0.4), bg, 1.8)

    # --- Semantic status colors ---
    # Find the palette color closest to each target hue, then saturate and
    # contrast-enforce it. If the palette has no suitable color, synthesize one.
    all_palette = [c[f"color{i}"] for i in range(1, 15)]

    def find_by_hue(target_hue: float, fallback_hex: str, sat_boost: float = 0.3,
                    max_dist: float = 0.12) -> str:
        """Find palette color nearest target hue, boost saturation, ensure contrast."""
        best, best_dist = None, 1.0
        for pc in all_palette:
            r, g, b = hex_to_rgb(pc)
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            dist = min(abs(h - target_hue), 1.0 - abs(h - target_hue))
            if dist < best_dist and s > 0.08:
                best, best_dist = pc, dist
        if best is None or best_dist > max_dist:
            # No palette color near this hue; use fallback directly
            best = fallback_hex
        boosted = add_saturation(lighten(best, 0.3) if is_dark else best, sat_boost)
        return ensure_contrast(boosted, bg, 4.5)

    # Target hues: green≈0.33, yellow≈0.17, red≈0.0, blue≈0.58
    success = find_by_hue(0.33, "#5faf5f", max_dist=0.10)
    warning = find_by_hue(0.12, "#d7af5f", max_dist=0.15)
    error = find_by_hue(0.0, "#d75f5f", sat_boost=0.4, max_dist=0.08)
    info = find_by_hue(0.58, "#5f87d7", max_dist=0.12)

    # --- ANSI palette: ensure each color is distinguishable ---
    # Dark variants (1-6): lighten 45% (matching original template)
    # Bright variants (9-14): lighten 60% (matching original template)
    # Then contrast-enforce all against background
    ansi = {}
    ansi[0] = bg
    for i in range(1, 7):
        ansi[i] = ensure_contrast(lighten(c[f"color{i}"], 0.45), bg, 3.0)
    ansi[7] = ensure_contrast(c["color7"], bg, 3.0)
    ansi[8] = ensure_contrast(c["color8"], bg, 2.5)
    for i in range(9, 15):
        ansi[i] = ensure_contrast(lighten(c[f"color{i}"], 0.60), bg, 4.0)
    ansi[15] = foreground

    return {
        "background": background,
        "background_alt": background_alt,
        "foreground": foreground,
        "foreground_secondary": foreground_secondary,
        "foreground_muted": foreground_muted,
        "foreground_disabled": foreground_disabled,
        "accent": accent,
        "accent_secondary": accent_secondary,
        "selection_background": selection_bg,
        "selection_foreground": selection_fg,
        "cursor": cursor,
        "border": border,
        "border_inactive": border_inactive,
        "success": success,
        "warning": warning,
        "error": error,
        "info": info,
        "ansi": ansi,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_ghostty(sem: dict) -> None:
    a = sem["ansi"]
    lines = [
        "# Generated by semantic-colors.py. Included by ~/.config/ghostty/config.ghostty.",
        f"background = {sem['background']}",
        f"foreground = {sem['foreground']}",
        f"cursor-color = {sem['cursor']}",
        f"selection-background = {sem['selection_background']}",
        f"selection-foreground = {sem['selection_foreground']}",
        f"window-titlebar-background = {sem['background']}",
        f"window-titlebar-foreground = {sem['foreground']}",
    ]
    for i in range(16):
        lines.append(f"palette = {i}={a[i]}")
    OUT_GHOSTTY.write_text("\n".join(lines) + "\n")


def write_waybar(sem: dict) -> None:
    lines = [
        "/* Generated by semantic-colors.py. Do not edit ~/.cache/wal/waybar.css directly. */",
        f"@define-color bg {sem['background']};",
        f"@define-color bg-alt {sem['background_alt']};",
        f"@define-color fg {sem['foreground']};",
        f"@define-color fg-secondary {sem['foreground_secondary']};",
        f"@define-color fg-muted {sem['foreground_muted']};",
        f"@define-color fg-disabled {sem['foreground_disabled']};",
        f"@define-color accent {sem['accent']};",
        f"@define-color accent-secondary {sem['accent_secondary']};",
        f"@define-color border-color {sem['border']};",
        f"@define-color border-inactive {sem['border_inactive']};",
        f"@define-color success {sem['success']};",
        f"@define-color warning {sem['warning']};",
        f"@define-color error {sem['error']};",
        f"@define-color info {sem['info']};",
        # Backward-compatible aliases the existing style.css expects
        f"@define-color dark0 {sem['background']};",
        f"@define-color dark1 {sem['foreground_muted']};",
        f"@define-color dark2 {sem['background_alt']};",
        f"@define-color dark3 {sem['foreground_muted']};",
        f"@define-color light0 {sem['foreground']};",
        f"@define-color light1 {sem['foreground_secondary']};",
        f"@define-color light2 {sem['foreground_secondary']};",
        f"@define-color light3 {sem['accent']};",
        f"@define-color light4 {sem['accent_secondary']};",
        f"@define-color light5 {sem['accent']};",
        f"@define-color light6 {sem['accent_secondary']};",
        f"@define-color critical {sem['error']};",
        f"@define-color module-bg alpha({sem['background']}, 0.90);",
        f"@define-color module-border alpha({sem['border']}, 0.60);",
    ]
    OUT_WAYBAR.write_text("\n".join(lines) + "\n")


def write_sway(sem: dict) -> None:
    lines = [
        "# Generated by semantic-colors.py. Included by ~/.config/sway/config.",
        # client.CLASS       border                  background              text                       indicator              child_border
        f"client.focused          {sem['accent']} {sem['background_alt']} {sem['foreground']} {sem['accent']} {sem['accent']}",
        f"client.focused_inactive {sem['border_inactive']} {sem['background_alt']} {sem['foreground_secondary']} {sem['border_inactive']} {sem['border_inactive']}",
        f"client.unfocused        {sem['border_inactive']} {sem['background']} {sem['foreground_muted']} {sem['border_inactive']} {sem['border_inactive']}",
        f"client.urgent           {sem['error']} {sem['error']} {sem['foreground']} {sem['error']} {sem['error']}",
        f"client.placeholder      {sem['border_inactive']} {sem['background']} {sem['foreground_secondary']} {sem['border_inactive']} {sem['border_inactive']}",
    ]
    OUT_SWAY.write_text("\n".join(lines) + "\n")

def write_niri(sem: dict) -> None:
    """Generate the border palette included by the Niri configuration."""
    lines = [
        "// Generated by semantic-colors.py; included by ~/.config/niri/config.kdl.",
        "layout {",
        "    border {",
        f'        active-color "{sem["accent"]}"',
        f'        inactive-color "{sem["border_inactive"]}"',
        f'        urgent-color "{sem["error"]}"',
        "    }",
        "}",
    ]
    OUT_NIRI.write_text("\n".join(lines) + "\n")


def write_fish(sem: dict) -> None:
    def strip(h: str) -> str:
        return h.lstrip("#")

    lines = [
        "# Generated by semantic-colors.py. Sourced by ~/.config/fish/config.fish.",
        f"set -g fish_color_normal {strip(sem['foreground'])}",
        f"set -g fish_color_command {strip(sem['accent'])}",
        f"set -g fish_color_param {strip(sem['foreground'])}",
        f"set -g fish_color_quote {strip(sem['success'])}",
        f"set -g fish_color_redirection {strip(sem['accent_secondary'])}",
        f"set -g fish_color_comment {strip(sem['foreground_muted'])}",
        f"set -g fish_color_error {strip(sem['error'])}",
        f"set -g fish_color_operator {strip(sem['info'])}",
        f"set -g fish_color_autosuggestion {strip(sem['foreground_disabled'])}",
        f"set -g fish_color_cwd {strip(sem['accent'])}",
        f"set -g fish_color_cwd_root {strip(sem['error'])}",
        f"set -g fish_pager_color_prefix {strip(sem['warning'])} --bold",
        f"set -g fish_pager_color_completion {strip(sem['foreground'])}",
        # Prompt semantic colors for fish_prompt.fish
        f"set -g wal_accent {strip(sem['accent'])}",
        f"set -g wal_accent_secondary {strip(sem['accent_secondary'])}",
        f"set -g wal_fg {strip(sem['foreground'])}",
        f"set -g wal_fg_muted {strip(sem['foreground_muted'])}",
        f"set -g wal_success {strip(sem['success'])}",
        f"set -g wal_error {strip(sem['error'])}",
        f"set -g wal_warning {strip(sem['warning'])}",
        f"set -g wal_info {strip(sem['info'])}",
    ]
    OUT_FISH.write_text("\n".join(lines) + "\n")


def write_fuzzel(sem: dict) -> None:
    """Generate ~/.cache/wal/fuzzel.ini with semantic colors."""
    def rgba(h: str, a: str = "ff") -> str:
        return h.lstrip("#") + a

    lines = [
        "# Generated by semantic-colors.py. Source: ~/.config/wal/semantic-colors.py",
        "[colors]",
        f"background={rgba(sem['background'], 'eb')}",
        f"text={rgba(sem['foreground'])}",
        f"prompt={rgba(sem['accent'])}",
        f"placeholder={rgba(sem['foreground_muted'])}",
        f"input={rgba(sem['foreground'])}",
        f"match={rgba(sem['success'])}",
        f"selection={rgba(sem['selection_background'], 'd0')}",
        f"selection-text={rgba(sem['selection_foreground'])}",
        f"selection-match={rgba(sem['accent'])}",
        f"border={rgba(sem['border'])}",
    ]
    OUT_FUZZEL.write_text("\n".join(lines) + "\n")


def write_yazi(sem: dict) -> None:
    """Generate ~/.cache/wal/yazi-theme.toml with semantic colors."""
    bg = sem["background"]
    bg_alt = sem["background_alt"]
    fg = sem["foreground"]
    fg2 = sem["foreground_secondary"]
    fg_m = sem["foreground_muted"]
    acc = sem["accent"]
    acc2 = sem["accent_secondary"]
    sel_bg = sem["selection_background"]
    sel_fg = sem["selection_foreground"]
    bdr = sem["border"]
    succ = sem["success"]
    warn = sem["warning"]
    err = sem["error"]
    info = sem["info"]

    lines = [
        "# Generated by semantic-colors.py. Source: ~/.config/wal/semantic-colors.py",
        "[mgr]",
        f'cwd = {{ fg = "{acc}", bold = true }}',
        f'hovered = {{ fg = "{sel_fg}", bg = "{sel_bg}" }}',
        'preview_hovered = { underline = true }',
        f'find_keyword = {{ fg = "{warn}", bold = true }}',
        f'find_position = {{ fg = "{info}", bold = true }}',
        f'marker_selected = {{ fg = "{succ}", bg = "{succ}" }}',
        f'marker_copied = {{ fg = "{warn}", bg = "{warn}" }}',
        f'marker_cut = {{ fg = "{err}", bg = "{err}" }}',
        f'tab_active = {{ fg = "{bg}", bg = "{acc}", bold = true }}',
        f'tab_inactive = {{ fg = "{fg2}", bg = "{bg_alt}" }}',
        f'count_selected = {{ fg = "{bg}", bg = "{succ}" }}',
        f'count_copied = {{ fg = "{bg}", bg = "{warn}" }}',
        f'count_cut = {{ fg = "{bg}", bg = "{err}" }}',
        'border_symbol = "│"',
        f'border_style = {{ fg = "{bdr}" }}',
        "",
        "[status]",
        'separator_open = ""',
        'separator_close = ""',
        f'separator_style = {{ fg = "{bg_alt}", bg = "{bg_alt}" }}',
        f'mode_normal = {{ fg = "{bg}", bg = "{acc}", bold = true }}',
        f'mode_select = {{ fg = "{bg}", bg = "{succ}", bold = true }}',
        f'mode_unset = {{ fg = "{bg}", bg = "{warn}", bold = true }}',
        f'progress_label = {{ fg = "{fg}", bold = true }}',
        f'progress_normal = {{ fg = "{acc}", bg = "{bg_alt}" }}',
        f'progress_error = {{ fg = "{err}", bg = "{bg_alt}" }}',
        "",
        "[select]",
        f'border = {{ fg = "{acc}" }}',
        f'active = {{ fg = "{sel_fg}", bg = "{sel_bg}" }}',
        f'inactive = {{ fg = "{fg2}" }}',
        "",
        "[input]",
        f'border = {{ fg = "{acc}" }}',
        f'title = {{ fg = "{acc2}" }}',
        f'value = {{ fg = "{fg}" }}',
        'selected = { reversed = true }',
        "",
        "[completion]",
        f'border = {{ fg = "{acc}" }}',
        f'active = {{ fg = "{sel_fg}", bg = "{sel_bg}" }}',
        f'inactive = {{ fg = "{fg2}" }}',
        "",
        "[which]",
        f'mask = {{ bg = "{bg}" }}',
        f'cand = {{ fg = "{acc}" }}',
        f'rest = {{ fg = "{fg2}" }}',
        f'desc = {{ fg = "{acc2}" }}',
        'separator = "  "',
        f'separator_style = {{ fg = "{fg_m}" }}',
        "",
        "[help]",
        f'on = {{ fg = "{acc}" }}',
        f'run = {{ fg = "{succ}" }}',
        f'desc = {{ fg = "{fg2}" }}',
        f'hovered = {{ fg = "{sel_fg}", bg = "{sel_bg}", bold = true }}',
        f'footer = {{ fg = "{bg}", bg = "{acc}" }}',
    ]
    OUT_YAZI.write_text("\n".join(lines) + "\n")


def write_swaylock(sem: dict) -> None:
    """Generate ~/.cache/wal/swaylock with semantic colors."""
    def rgba(h: str, a: str = "ff") -> str:
        return h.lstrip("#") + a

    bg = sem["background"]
    fg = sem["foreground"]
    acc = sem["accent"]
    acc2 = sem["accent_secondary"]
    warn = sem["warning"]
    err = sem["error"]
    info = sem["info"]
    source_path = Path.home() / ".config" / "swaylock" / "config"
    try:
        base_config = source_path.read_text().strip()
    except Exception:
        base_config = ""

    lines = [
        "# Generated by semantic-colors.py. Source: ~/.config/wal/semantic-colors.py",
        base_config,
        "",
        "# --- Dynamic Semantic Colors ---",
        # Normal/idle state
        f"inside-color={rgba(bg, 'cc')}",
        f"ring-color={rgba(acc, 'cc')}",
        f"line-color={rgba(bg, '00')}",
        f"separator-color={rgba(bg, '00')}",
        f"text-color={rgba(fg)}",
        # Clearing (backspace)
        f"inside-clear-color={rgba(warn, 'cc')}",
        f"ring-clear-color={rgba(warn, 'cc')}",
        f"text-clear-color={rgba(bg)}",
        # Verifying
        f"inside-ver-color={rgba(info, 'cc')}",
        f"ring-ver-color={rgba(info, 'cc')}",
        f"text-ver-color={rgba(bg)}",
        # Wrong password
        f"inside-wrong-color={rgba(err, 'cc')}",
        f"ring-wrong-color={rgba(err, 'cc')}",
        f"text-wrong-color={rgba(bg)}",
        # Key highlights
        f"key-hl-color={rgba(acc2, 'cc')}",
        f"bs-hl-color={rgba(err, 'cc')}",
        f"caps-lock-key-hl-color={rgba(warn, 'cc')}",
        f"caps-lock-bs-hl-color={rgba(warn, 'cc')}",
        # Layout
        f"layout-bg-color={rgba(bg, '00')}",
        f"layout-border-color={rgba(bg, '00')}",
        f"layout-text-color={rgba(fg)}",
    ]
    OUT_SWAYLOCK.write_text("\n".join(lines) + "\n")


def write_firefox(sem: dict) -> None:
    """Generate ~/.cache/wal/firefox-colors.css with semantic colors."""
    lines = [
        "/* Generated by semantic-colors.py. Source: ~/.config/wal/semantic-colors.py */",
        ":root {",
        f"  --uc-color-base:    {sem['background']};",
        f"  --uc-color-surface: {sem['background_alt']};",
        f"  --uc-color-accent:  {sem['accent']};",
        f"  --uc-color-text:    {sem['foreground']};",
        f"  --uc-color-hover:   {sem['selection_background']};",
        f"  --uc-tab-hover-text: {sem['accent']};",
        "}"
    ]
    OUT_FIREFOX.write_text("\n".join(lines) + "\n")


def write_mako(sem: dict) -> None:
    """Generate ~/.cache/wal/mako.conf with semantic colors."""
    def rgba(h: str, a: str = "ff") -> str:
        return "#" + h.lstrip("#") + a

    bg = sem["background"]
    fg = sem["foreground"]
    fg_m = sem["foreground_muted"]
    acc = sem["accent"]
    bdr = sem["border"]
    warn = sem["warning"]
    err = sem["error"]

    lines = [
        "# Generated by semantic-colors.py. Included by ~/.config/mako/config.",
        f"background-color={rgba(bg, 'f2')}",
        f"text-color={fg}",
        f"border-color={bdr}",
        f"progress-color=over {rgba(acc, 'd9')}",
        "",
        "[urgency=low]",
        f"border-color={bdr}",
        f"text-color={fg_m}",
        "",
        "[urgency=normal]",
        f"border-color={acc}",
        "",
        "[urgency=critical]",
        f"border-color={err}",
        f"text-color={warn}",
        "default-timeout=0",
    ]
    OUT_MAKO.write_text("\n".join(lines) + "\n")


def write_rmpc(sem: dict) -> None:
    """Generate ~/.cache/wal/rmpc-theme.ron with semantic colors."""
    bg = sem["background"]
    bg_alt = sem["background_alt"]
    fg = sem["foreground"]
    fg2 = sem["foreground_secondary"]
    fg_m = sem["foreground_muted"]
    acc = sem["accent"]
    acc2 = sem["accent_secondary"]
    sel_bg = sem["selection_background"]
    bdr = sem["border"]
    bdr_in = sem["border_inactive"]
    succ = sem["success"]
    warn = sem["warning"]
    err = sem["error"]
    info = sem["info"]

    lines = [
        "#![enable(implicit_some)]",
        "#![enable(unwrap_newtypes)]",
        "#![enable(unwrap_variant_newtypes)]",
        "(",
        "    default_album_art_path: None,",
        "    show_song_table_header: false,",
        "    draw_borders: true,",
        '    format_tag_separator: " | ",',
        "    multiple_tag_resolution_strategy: First,",
        "    song_table_album_separator: Underline,",
        "    browser_column_widths: [20, 38, 42],",
        "    background_color: None,",
        f'    text_color: Some("{fg}"),',
        "    header_background_color: None,",
        "    modal_background_color: None,",
        "    modal_backdrop: false,",
        f'    preview_label_style: (fg: "{acc2}"),',
        f'    preview_metadata_group_style: (fg: "{acc}", modifiers: "Bold"),',
        f'    highlighted_item_style: (fg: "{acc}", modifiers: "Bold"),',
        f'    current_item_style: (fg: "{bg}", bg: "{acc}", modifiers: "Bold"),',
        f'    borders_style: (fg: "{bdr}"),',
        f'    highlight_border_style: (fg: "{acc}"),',
        "    symbols: (",
        '        song: " ",',
        '        dir: " ",',
        '        playlist: "󰲂 ",',
        '        marker: "* ",',
        '        ellipsis: "...",',
        "        song_style: None,",
        "        dir_style: None,",
        "        playlist_style: None,",
        "    ),",
        "    level_styles: (",
        f'        info: (fg: "{acc}", bg: "{bg}"),',
        f'        warn: (fg: "{warn}", bg: "{bg}"),',
        f'        error: (fg: "{err}", bg: "{bg}"),',
        f'        debug: (fg: "{succ}", bg: "{bg}"),',
        f'        trace: (fg: "{info}", bg: "{bg}"),',
        "    ),",
        "    progress_bar: (",
        '        symbols: ["█", "█", "█", " ", "█"],',
        f'        track_style: (fg: "{bdr_in}"),',
        f'        elapsed_style: (fg: "{acc}"),',
        f'        thumb_style: (fg: "{succ}"),',
        "        use_track_when_empty: true,",
        "    ),",
        "    scrollbar: (",
        '        symbols: ["", "", "", ""],',
        f'        track_style: (fg: "{bg_alt}"),',
        f'        ends_style: (fg: "{info}"),',
        f'        thumb_style: (fg: "{acc}"),',
        "    ),",
        "    tab_bar: (",
        "        enabled: true,",
        f'        active_style: (fg: "{bg}", bg: "{acc}", modifiers: "Bold"),',
        f'        inactive_style: (fg: "{fg2}", bg: "{bg}"),',
        "    ),",
        "    lyrics: (",
        "        timestamp: false",
        "    ),",
        "    browser_song_format: [",
        "        (",
        "            kind: Group([",
        "                (kind: Property(Track)),",
        '                (kind: Text(" ")),',
        "            ])",
        "        ),",
        "        (",
        "            kind: Group([",
        "                (kind: Property(Artist)),",
        '                (kind: Text(" - ")),',
        "                (kind: Property(Title)),",
        "            ]),",
        "            default: (kind: Property(Filename))",
        "        ),",
        "    ],",
        "    song_table_format: [",
        "        (",
        f'            prop: (kind: Property(Title), style: (fg: "{acc2}"),',
        f'                highlighted_item_style: (fg: "{bg}", modifiers: "Bold"),',
        f'                default: (kind: Property(Filename), style: (fg: "{fg_m}"))',
        "            ),",
        '            width: "70%",',
        "        ),",
        "        (",
        f'            prop: (kind: Property(Album), style: (fg: "{fg}"),',
        f'                default: (kind: Text("Unknown Album"), style: (fg: "{info}"))',
        "            ),",
        '            width: "30%",',
        "        ),",
        "    ],",
        "    layout: Split(",
        "        direction: Vertical,",
        "        panes: [",
        "            (",
        '                size: "3",',
        '                borders: "TOP | BOTTOM",',
        "                pane: Pane(Tabs),",
        "            ),",
        "            (",
        '                size: "3",',
        '                borders: "ALL",',
        "                pane: Split(",
        "                    direction: Vertical,",
        "                    panes: [",
        "                        (",
        '                            size: "1",',
        "                            pane: Split(",
        "                                direction: Horizontal,",
        "                                panes: [",
        '                                    (size: "25%", pane: Component("header_state")),',
        '                                    (size: "50%", pane: Component("header_title")),',
        '                                    (size: "25%", pane: Component("header_volume")),',
        "                                ],",
        "                            ),",
        "                        ),",
        "                        (",
        '                            size: "1",',
        "                            pane: Split(",
        "                                direction: Horizontal,",
        "                                panes: [",
        '                                    (size: "25%", pane: Component("header_time")),',
        '                                    (size: "50%", pane: Component("header_artist")),',
        '                                    (size: "25%", pane: Component("header_states")),',
        "                                ],",
        "                            ),",
        "                        ),",
        "                    ],",
        "                ),",
        "            ),",
        "            (",
        "                pane: Pane(TabContent),",
        '                size: "100%",',
        "            ),",
        "            (",
        '                size: "3",',
        "                pane: Split(",
        "                    direction: Vertical,",
        "                    panes: [",
        '                        (size: "2", pane: Pane(ProgressBar)),',
        '                        (size: "1", pane: Component("footer")),',
        "                    ],",
        "                ),",
        "            ),",
        "        ],",
        "    ),",
        "    components: {",
        '        "state": Pane(Property(',
        "            content: [",
        f'                (kind: Text("["), style: (fg: "{acc}", modifiers: "Bold")),',
        f'                (kind: Property(Status(StateV2( ))), style: (fg: "{acc}", modifiers: "Bold")),',
        f'                (kind: Text("]"), style: (fg: "{acc}", modifiers: "Bold")),',
        "            ], align: Left,",
        "        )),",
        '        "title": Pane(Property(',
        "            content: [",
        '                (kind: Property(Song(Title)), style: (modifiers: "Bold"),',
        '                    default: (kind: Text("No Song"), style: (modifiers: "Bold"))),',
        "            ], align: Center, scroll_speed: 1",
        "        )),",
        '        "volume": Split(',
        "            direction: Horizontal,",
        "            panes: [",
        '                (size: "1", pane: Pane(Property(content: [(kind: Text(""))]))),',
        '                (size: "100%", pane: Pane(Volume(kind: Slider(symbols: (filled: "─", thumb: "●", track: "─"))))),',
        f'                (size: "3", pane: Pane(Property(content: [(kind: Property(Status(Volume)), style: (fg: "{info}"))], align: Right))),',
        f'                (size: "2", pane: Pane(Property(content: [(kind: Text("%"), style: (fg: "{info}"))]))),',
        "            ]",
        "        ),",
        '        "elapsed_and_bitrate": Pane(Property(',
        "            content: [",
        "                (kind: Property(Status(Elapsed))), ",
        '                (kind: Text(" / ")), ',
        "                (kind: Property(Status(Duration))), ",
        "                (kind: Group([",
        '                    (kind: Text(" (")), ',
        "                    (kind: Property(Status(Bitrate))), ",
        '                    (kind: Text(" kbps)")),',
        "                ])),",
        "            ],",
        "            align: Left,",
        "        )),",
        '        "artist_and_album": Pane(Property(',
        "            content: [",
        f'                (kind: Property(Song(Artist)), style: (fg: "{succ}", modifiers: "Bold"),',
        f'                    default: (kind: Text("Unknown"), style: (fg: "{succ}", modifiers: "Bold"))),',
        '                (kind: Text(" - ")),',
        '                (kind: Property(Song(Album)), default: (kind: Text("Unknown Album"))),',
        "            ], align: Center, scroll_speed: 1",
        "        )),",
        '        "states": Split(',
        "            direction: Horizontal,",
        "            panes: [",
        "                (",
        '                    size: "1",',
        "                    pane: Pane(Empty())",
        "                ),",
        "                (",
        '                    size: "100%",',
        f'                    pane: Pane(Property(content: [(kind: Property(Status(InputBuffer())), style: (fg: "{acc}"), align: Left)]))',
        "                ),",
        "                (",
        '                    size: "6",',
        "                    pane: Pane(Property(content: [",
        f'                        (kind: Text("["), style: (fg: "{acc}", modifiers: "Bold")),',
        "                        (kind: Property(Status(RepeatV2(",
        '                            on_label: "z",',
        '                            off_label: "z",',
        f'                            on_style: (fg: "{warn}", modifiers: "Bold"),',
        f'                            off_style: (fg: "{info}", modifiers: "Dim"),',
        "                        )))),",
        "                        (kind: Property(Status(RandomV2(",
        '                            on_label: "x",',
        '                            off_label: "x",',
        f'                            on_style: (fg: "{warn}", modifiers: "Bold"),',
        f'                            off_style: (fg: "{info}", modifiers: "Dim"),',
        "                        )))),",
        "                        (kind: Property(Status(ConsumeV2(",
        '                            on_label: "",',
        '                            off_label: "",',
        '                            oneshot_label: "",',
        f'                            on_style: (fg: "{warn}", modifiers: "Bold"),',
        f'                            off_style: (fg: "{info}", modifiers: "Dim"),',
        f'                            oneshot_style: (fg: "{err}", modifiers: "Dim"),',
        "                        )))),",
        "                        (kind: Property(Status(SingleV2(",
        '                            on_label: "v",',
        '                            off_label: "v",',
        '                            oneshot_label: "v",',
        f'                            on_style: (fg: "{warn}", modifiers: "Bold"),',
        f'                            off_style: (fg: "{info}", modifiers: "Dim"),',
        f'                            oneshot_style: (fg: "{err}", modifiers: "Bold"),',
        "                        )))),",
        f'                        (kind: Text("]"), style: (fg: "{acc}", modifiers: "Bold")),',
        "                        ],",
        "                        align: Right",
        "                    ))",
        "                ),",
        "            ]",
        "        ),",
        '        "input_mode": Pane(Property(',
        "            content: [",
        "                (kind: Transform(Replace(content: (kind: Property(Status(InputMode()))), replacements: [",
        f'                    (match: "Normal", replace: (kind: Text(" NORMAL "), style: (fg: "{bg}", bg: "{acc}"))),',
        f'                    (match: "Insert", replace: (kind: Text(" INSERT "), style: (fg: "{bg}", bg: "{succ}"))),',
        "                ])))",
        "            ], align: Center",
        "        )),",
        '        "header_state": Pane(Property(',
        "            content: [",
        "                (kind: Property(Status(StateV2(",
        '                    playing_label: " ",',
        '                    paused_label: " ",',
        '                    stopped_label: " "',
        f'                ))), style: (fg: "{acc}", modifiers: "Bold")),',
        "            ], align: Left,",
        "        )),",
        '        "header_title": Pane(Property(',
        "            content: [",
        f'                (kind: Property(Song(Title)), style: (fg: "{fg}", modifiers: "Bold"),',
        f'                    default: (kind: Property(Song(Filename)), style: (fg: "{fg}", modifiers: "Bold")))',
        "            ], align: Center, scroll_speed: 1,",
        "        )),",
        '        "header_volume": Pane(Property(',
        "            content: [",
        f'                (kind: Text("[") , style: (fg: "{fg_m}")),',
        "                (kind: Property(Status(RepeatV2(",
        '                    on_label: "",',
        '                    off_label: "",',
        f'                    on_style: (fg: "{warn}", modifiers: "Bold"),',
        f'                    off_style: (fg: "{fg_m}", modifiers: "Dim"),',
        "                )))),",
        f'                (kind: Text(" "), style: (fg: "{fg_m}")),',
        "                (kind: Property(Status(RandomV2(",
        '                    on_label: "",',
        '                    off_label: "",',
        f'                    on_style: (fg: "{warn}", modifiers: "Bold"),',
        f'                    off_style: (fg: "{fg_m}", modifiers: "Dim"),',
        "                )))),",
        f'                (kind: Text("]  "), style: (fg: "{fg_m}")),',
        f'                (kind: Text("󰕾 "), style: (fg: "{acc}", modifiers: "Bold")),',
        f'                (kind: Property(Status(Volume)), style: (fg: "{acc}", modifiers: "Bold")),',
        f'                (kind: Text("% "), style: (fg: "{acc}", modifiers: "Bold")),',
        "            ], align: Right,",
        "        )),",
        '        "header_time": Pane(Property(',
        "            content: [",
        f'                (kind: Property(Status(Elapsed)), style: (fg: "{fg}")),',
        f'                (kind: Text(" / "), style: (fg: "{fg_m}")),',
        f'                (kind: Property(Status(Duration)), style: (fg: "{fg}")),',
        "            ], align: Left,",
        "        )),",
        '        "header_artist": Pane(Property(',
        "            content: [",
        f'                (kind: Property(Song(Artist)), style: (fg: "{succ}", modifiers: "Bold"),',
        f'                    default: (kind: Text("Unknown Artist"), style: (fg: "{err}", modifiers: "Bold")))',
        "            ], align: Center, scroll_speed: 1,",
        "        )),",
        '        "header_states": Pane(Empty()),',
        '        "footer": Pane(Property(',
        "            content: [",
        f'                (kind: Text("Queue "), style: (fg: "{acc}", modifiers: "Bold")),',
        f'                (kind: Property(Status(QueueLength(thousands_separator: ","))), style: (fg: "{fg}")),',
        f'                (kind: Text(" tracks  ·  "), style: (fg: "{fg_m}")),',
        f'                (kind: Property(Status(QueueTimeTotal())), style: (fg: "{fg}")),',
        f'                (kind: Text("  ·  "), style: (fg: "{fg_m}")),',
        f'                (kind: Text("Left "), style: (fg: "{acc}", modifiers: "Bold")),',
        f'                (kind: Property(Status(QueueTimeRemaining())), style: (fg: "{fg}")),',
        f'                (kind: Text("  ·  "), style: (fg: "{fg_m}")),',
        f'                (kind: Text("Bitrate "), style: (fg: "{acc}", modifiers: "Bold")),',
        f'                (kind: Property(Status(Bitrate)), style: (fg: "{fg}")),',
        f'                (kind: Text(" kbps  ·  "), style: (fg: "{fg_m}")),',
        f'                (kind: Property(Status(SampleRate())), style: (fg: "{fg}")),',
        f'                (kind: Text(" Hz"), style: (fg: "{fg_m}")),',
        "            ], align: Center,",
        "        )),",
        '        "header_left": Split(',
        "            direction: Vertical,",
        "            panes: [",
        '                (size: "1", pane: Component("state")),',
        '                (size: "1", pane: Component("elapsed_and_bitrate")),',
        "            ]",
        "        ),",
        '        "header_center": Split(',
        "            direction: Vertical,",
        "            panes: [",
        '                (size: "1", pane: Component("title")),',
        '                (size: "1", pane: Component("artist_and_album")),',
        "            ]",
        "        ),",
        '        "header_right": Split(',
        "            direction: Vertical,",
        "            panes: [",
        '                (size: "1", pane: Component("volume")),',
        '                (size: "1", pane: Component("states")),',
        "            ]",
        "        ),",
        '        "progress_bar": Split(',
        "            direction: Horizontal,",
        "            panes: [",
        "                (",
        '                    size: "1",',
        "                    pane: Pane(Empty())",
        "                ),",
        "                (",
        '                    size: "100%",',
        "                    pane: Pane(ProgressBar)",
        "                ),",
        "                (",
        '                    size: "1",',
        "                    pane: Pane(Empty())",
        "                ),",
        "            ]",
        "        )",
        "    },",
        ")",
    ]
    OUT_RMPC.write_text("\n".join(lines) + "\n")

def write_semantic_json(sem: dict) -> None:
    """Write the full semantic mapping for future consumers (Stage 2+)."""
    OUT_SEMANTIC.write_text(json.dumps(sem, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not COLORS_JSON.exists():
        print(f"semantic-colors: {COLORS_JSON} not found", file=sys.stderr)
        return 1

    with open(COLORS_JSON) as f:
        colors = json.load(f)

    sem = compute_semantic(colors)

    write_ghostty(sem)
    write_waybar(sem)
    write_sway(sem)
    write_niri(sem)
    write_fish(sem)
    write_semantic_json(sem)
    write_fuzzel(sem)
    write_yazi(sem)
    write_swaylock(sem)
    write_firefox(sem)
    write_mako(sem)
    write_rmpc(sem)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
