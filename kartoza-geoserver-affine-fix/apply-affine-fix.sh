#!/usr/bin/env bash
# Kartoza sources hooks with `|| true`, so every failure explicitly exits.
# Run before Tomcat starts, never against a running JVM.
affine_fix_main() {
    local bundle="${AFFINE_FIX_DIR:-/opt/affine-fix}"
    local lib="${AFFINE_FIX_LIB_DIR:-${CATALINA_HOME:-/usr/local/tomcat}/webapps/${GEOSERVER_CONTEXT_ROOT:-geoserver}/WEB-INF/lib}"
    local backup="${AFFINE_FIX_BACKUP_DIR:-${CATALINA_HOME:-/usr/local/tomcat}/affine-fix-backup}"
    local mode="${AFFINE_FIX_MODE:-apply}"
    local name original patched extra current wanted tmp count i
    local -a names=() originals=() patched_hashes=() currents=()

    affine_fix_die() { printf 'AFFINE_FIX ERROR: %s\n' "$*" >&2; exit 1; }
    affine_fix_hash() {
        local result
        result=$(sha256sum -- "$1") || affine_fix_die "Cannot hash $1"
        printf '%s' "${result%% *}"
    }
    [[ "$mode" == apply || "$mode" == restore ]] || affine_fix_die "Unknown mode: $mode"
    [[ -f "$bundle/jars.sha256.tsv" && -d "$lib" ]] || affine_fix_die 'Missing bundle manifest or library directory'
    [[ -w "$lib" ]] || affine_fix_die "Library directory is not writable: $lib"

    # Validate the entire pair before replacing either library.
    while read -r name original patched extra; do
        [[ -z "$name" || "$name" == \#* ]] && continue
        [[ "$name" =~ ^(gt-render-34\.5|affine-0\.9\.1)\.jar$ && -z "$extra" ]] || affine_fix_die 'Unexpected manifest entry'
        [[ "$original" =~ ^[0-9a-f]{64}$ && "$patched" =~ ^[0-9a-f]{64}$ ]] || affine_fix_die 'Invalid checksum'
        [[ -f "$bundle/jars/$name" && -f "$lib/$name" && ! -L "$lib/$name" ]] || affine_fix_die "Missing file or unsupported library symlink: $name"
        [[ "$(affine_fix_hash "$bundle/jars/$name")" == "$patched" ]] || affine_fix_die "Patched payload checksum mismatch: $name"
        current=$(affine_fix_hash "$lib/$name")
        [[ "$current" == "$original" || "$current" == "$patched" ]] || affine_fix_die "Unsupported original JAR checksum: $name"
        for extra in "${names[@]}"; do
            [[ "$extra" != "$name" ]] || affine_fix_die "Duplicate manifest entry: $name"
        done
        if [[ "$mode" == restore && "$current" == "$patched" ]]; then
            [[ -f "$backup/$original/$name" ]] || affine_fix_die "Missing rollback file: $name"
            [[ "$(affine_fix_hash "$backup/$original/$name")" == "$original" ]] || affine_fix_die "Rollback checksum mismatch: $name"
        fi
        names+=("$name"); originals+=("$original"); patched_hashes+=("$patched"); currents+=("$current")
    done < "$bundle/jars.sha256.tsv"
    [[ ${#names[@]} == 2 && " ${names[*]} " == *' gt-render-34.5.jar '* && " ${names[*]} " == *' affine-0.9.1.jar '* ]] || affine_fix_die 'Manifest must contain exactly the expected two libraries'
    # Old copies ending in .jar must not stay on the application classpath.
    for name in gt-render affine; do
        count=$(find "$lib" -maxdepth 1 -name "$name-*.jar" -printf '.\n' | wc -l) || affine_fix_die 'Cannot inspect library duplicates'
        [[ "$count" == 1 ]] || affine_fix_die "Duplicate or unexpected $name JARs"
    done

    for ((i=0; i<${#names[@]}; i++)); do
        name=${names[i]}; original=${originals[i]}; patched=${patched_hashes[i]}; current=${currents[i]}
        if [[ "$mode" == apply ]]; then
            wanted=$patched
            if [[ "$current" == "$original" ]]; then
                mkdir -p -- "$backup/$original" || affine_fix_die 'Cannot create backup directory'
                if [[ ! -e "$backup/$original/$name" ]]; then
                    cp -p -- "$lib/$name" "$backup/$original/$name" || affine_fix_die "Cannot back up $name"
                fi
                [[ "$(affine_fix_hash "$backup/$original/$name")" == "$original" ]] || affine_fix_die "Backup checksum mismatch: $name"
            fi
            extra="$bundle/jars/$name"
        else
            wanted=$original
            extra="$backup/$original/$name"
        fi
        if [[ "$current" != "$wanted" ]]; then
            tmp=$(mktemp "$lib/.affine-fix.XXXXXX") || affine_fix_die 'Cannot stage replacement'
            cp -- "$extra" "$tmp" && chmod 0644 "$tmp" || affine_fix_die "Cannot stage $name"
            [[ "$(affine_fix_hash "$tmp")" == "$wanted" ]] || affine_fix_die "Staged checksum mismatch: $name"
            mv -f -- "$tmp" "$lib/$name" || affine_fix_die "Cannot replace $name"
        fi
        [[ "$(affine_fix_hash "$lib/$name")" == "$wanted" ]] || affine_fix_die "Final checksum mismatch: $name"
        printf 'AFFINE_FIX %s %s %s\n' "$mode" "$name" "$wanted"
    done
}
affine_fix_main
unset -f affine_fix_main affine_fix_hash affine_fix_die
