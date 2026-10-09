#!/usr/bin/env fish
# Apply and restore the selected desktop wallpaper for Niri/Sway + awww.

set -g themes_dir "$HOME/.config/wallpapers/themes"
set -g state_file "$HOME/.cache/wallpaper-state.json"
set -g lock_sync "$HOME/.config/fish/scripts/wallpaper-lock-random.fish"

# Concurrency & Coalescing locks in $XDG_RUNTIME_DIR (falls back to /tmp)
if set -q XDG_RUNTIME_DIR; and test -d "$XDG_RUNTIME_DIR"
    set -g runtime_dir "$XDG_RUNTIME_DIR"
else
    set -g runtime_dir "/tmp"
end
set -g state_lock "$runtime_dir/wallpaper-state.lock"
set -g worker_lock "$runtime_dir/wallpaper-worker.lock"

function fail --argument-names message
    echo "wallpaper-ctl: $message" >&2
    exit 1
end

function require_command --argument-names name
    command -q "$name"; or fail "'$name' is not installed or is not on PATH"
end

function theme_images --argument-names theme
    set -l directory "$themes_dir/$theme/home"
    test -d "$directory"; or return
    for image in "$directory"/*.{png,jpg,jpeg,webp,PNG,JPG,JPEG,WEBP}
        test -f "$image"; and echo "$image"
    end
end

function normalise_index --argument-names index count
    if test "$count" -eq 0
        echo 0
        return
    end
    echo (math "($index % $count + $count) % $count")
end

function load_state
    set -g current_theme ''
    set -g home_index 0
    if test -f "$state_file"
        set -g current_theme (jq -r '.theme // empty' "$state_file" 2>/dev/null)
        set -g home_index (jq -r '.home_index // 0 | if type == "number" then floor else 0 end' "$state_file" 2>/dev/null)
    end
end

function save_state
    command mkdir -p (path dirname "$state_file")
    set -l temporary "$state_file.tmp"
    jq -n --arg theme "$current_theme" --argjson home_index "$home_index" \
        '{theme: $theme, home_index: $home_index}' > "$temporary"
        or fail "could not write state file"
    command mv "$temporary" "$state_file"; or fail "could not save state file"
end

function ensure_awww
    require_command awww
    require_command awww-daemon
    if awww query >/dev/null 2>&1
        return
    end

    command awww-daemon --no-cache >/dev/null 2>&1 &
    set -l attempts 0
    while test "$attempts" -lt 20
        awww query >/dev/null 2>&1; and return
        set attempts (math "$attempts + 1")
        command sleep 0.1
    end
    fail "awww-daemon did not become ready"
end

function apply_home --argument-names selected_image
    set -l images (theme_images "$current_theme")
    set -l image_count (count $images)
    test "$image_count" -gt 0; or fail "theme '$current_theme' has no supported images in home/"

    set -g home_index (normalise_index "$home_index" "$image_count")
    ensure_awww
    # Verified awww syntax: `awww img [OPTIONS] <path>`.
    awww img --transition-type fade --transition-duration 0.5 "$selected_image"
        or fail "awww could not set the desktop image"
end

function apply_and_sync
    set -l selected_image (current_image)
    require_command wal
    command wal -i "$selected_image" --cols16 darken -n -e
        or fail "pywal16 could not generate the color scheme"
    command python3 "$HOME/.config/wal/semantic-colors.py"
        or fail "semantic-colors.py could not generate semantic colors"
    
    # Live-reload rmpc theme if it is currently running (fails silently if closed)
    command rmpc remote set theme "$HOME/.cache/wal/rmpc-theme.ron" >/dev/null 2>&1
    apply_home "$selected_image"
    save_state
    if pgrep -x mako >/dev/null
        command makoctl reload >/dev/null 2>&1 &
    end
    "$lock_sync"; or fail "could not mirror the desktop image to the lockscreen"
    # The compositor may leave stale session variables in the user manager;
    # prefer the live Niri IPC socket, then the live Sway fallback.
    if set -q NIRI_SOCKET; and niri msg version >/dev/null 2>&1
        niri msg action load-config-file >/dev/null; or fail "could not reload Niri after pywal16 generation"
    else if set -q SWAYSOCK; and swaymsg -t get_version >/dev/null 2>&1
        swaymsg reload >/dev/null; or fail "could not reload Sway after pywal16 generation"
    end
    schedule_next_prewarm
end

function schedule_next_prewarm
    set -l images (theme_images "$current_theme")
    set -l image_count (count $images)
    test "$image_count" -gt 0; or return
    set -l next_index (normalise_index (math "$home_index + 1") "$image_count")
    set -l array_index (math "$next_index + 1")
    set -l next_image "$images[$array_index]"
    command /home/tod/.config/fish/scripts/wallpaper-prewarm.py "$next_image" >/dev/null 2>&1 &
end

function current_image
    set -l images (theme_images "$current_theme")
    set -l image_count (count $images)
    test "$image_count" -gt 0; or fail "theme '$current_theme' has no supported images in home/"
    set -g home_index (normalise_index "$home_index" "$image_count")
    set -l array_index (math "$home_index + 1")
    echo "$images[$array_index]"
end

function set_selection --argument-names theme index
    test -d "$themes_dir/$theme"; or fail "theme '$theme' does not exist"
    set -l images (theme_images "$theme")
    test (count $images) -gt 0; or fail "theme '$theme' has no supported images in home/"
    string match -qr '^-?[0-9]+$' -- "$index"; or fail "image index must be an integer"
    set -g current_theme "$theme"
    set -g home_index (normalise_index "$index" (count $images))
end

function set_path --argument-names image
    test -f "$image"; or fail "'$image' is not a file"
    set -l resolved (path resolve "$image")
    string match -qi -- "$themes_dir/*" "$resolved"; or fail "image must be inside $themes_dir"
    string match -rqi '\.(png|jpe?g|webp)$' -- "$resolved"; or fail "'$image' is not a supported image"

    set -l relative (string replace -- "$themes_dir/" '' "$resolved")
    set -l parts (string split / -- "$relative")
    if test (count $parts) -lt 3
        fail "image must be in a theme's home/ folder"
    end
    if test "$parts[2]" != home
        fail "image must be in a theme's home/ folder"
    end

    set -l images (theme_images "$parts[1]")
    set -l position (contains -i -- "$resolved" $images)
    test -n "$position"; or fail "could not find image in theme's sorted home/ list"
    set_selection "$parts[1]" (math "$position - 1")
end

# Coalescing worker execution loop
function worker_loop
    while true
        # Read the current target from state file under state_lock
        set -l target (flock -x "$state_lock" "$HOME/.config/fish/scripts/wallpaper-ctl.fish" __get_target__)
        set -l parts (string split ' ' -- "$target")
        set -g current_theme "$parts[1]"
        set -g home_index "$parts[2]"

        apply_and_sync

        # Check if a newer target arrived while apply_and_sync was running
        set -l latest (flock -x "$state_lock" "$HOME/.config/fish/scripts/wallpaper-ctl.fish" __get_target__)

        if test "$target" = "$latest"
            break
        end
    end
end

# Attempt to acquire worker lock non-blockingly; if busy, return immediately
function coalesced_apply
    flock -x -n "$worker_lock" "$HOME/.config/fish/scripts/wallpaper-ctl.fish" __worker__
    return 0
end

require_command jq
require_command flock
test -d "$themes_dir"; or fail "theme directory does not exist: $themes_dir"

switch "$argv[1]"
    case __get_target__
        load_state
        echo "$current_theme $home_index"
    case __worker__
        worker_loop
    case __mutate_next__
        load_state
        test -n "$current_theme"; or fail "no selected theme; open the wallpaper picker first"
        set -l images (theme_images "$current_theme")
        test (count $images) -gt 0; or fail "theme '$current_theme' has no supported images in home/"
        set -g home_index (math "$home_index + 1")
        save_state
    case __mutate_path__
        test (count $argv) -eq 2; or fail "usage: __mutate_path__ <image>"
        load_state
        set_path "$argv[2]"
        save_state
    case __mutate_selection__
        test (count $argv) -eq 3; or fail "usage: __mutate_selection__ <theme> <index>"
        load_state
        set_selection "$argv[2]" "$argv[3]"
        save_state
    case set-path
        test (count $argv) -eq 2; or fail "usage: wallpaper-ctl.fish set-path <image>"
        flock -x "$state_lock" "$HOME/.config/fish/scripts/wallpaper-ctl.fish" __mutate_path__ "$argv[2]"
        coalesced_apply
    case set-image
        test (count $argv) -eq 3; or fail "usage: wallpaper-ctl.fish set-image <theme> <index>"
        flock -x "$state_lock" "$HOME/.config/fish/scripts/wallpaper-ctl.fish" __mutate_selection__ "$argv[2]" "$argv[3]"
        coalesced_apply
    case next-image
        flock -x "$state_lock" "$HOME/.config/fish/scripts/wallpaper-ctl.fish" __mutate_next__
        coalesced_apply
    case restore
        load_state
        test -n "$current_theme"; or fail "no saved wallpaper selection; open the wallpaper picker first"
        # Immediate paint on startup: bypass fade and daemon spawn wait before theme sync
        set -l selected_image (current_image)
        ensure_awww
        awww img --transition-type none "$selected_image" >/dev/null 2>&1
        coalesced_apply
    case '*'
        fail "usage: wallpaper-ctl.fish {set-path <image>|set-image <theme> <index>|next-image|restore}"
end
