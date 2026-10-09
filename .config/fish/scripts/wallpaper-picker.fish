#!/usr/bin/env fish
# Open the wallpaper library in Yazi, using the user's configured terminal.

set -l themes_dir "$HOME/.config/wallpapers/themes"

function fail --argument-names message
    echo "wallpaper-picker: $message" >&2
    exit 1
end

command -q ghostty; or fail "'ghostty' is not installed or is not on PATH"
command -q yazi; or fail "'yazi' is not installed or is not on PATH"
test -d "$themes_dir"; or fail "theme directory does not exist: $themes_dir"

# The stable window title is matched by the termfilechooser floating rule in
# both the Sway and Niri configurations.
exec env YAZI_CONFIG_HOME="$HOME/.config/yazi-wallpaper" ghostty --title='Wallpaper Picker' -e yazi "$themes_dir"
