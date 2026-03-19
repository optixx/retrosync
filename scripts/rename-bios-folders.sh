#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: rename-bios-folders.sh [--dry-run] [DIRECTORY]

Rename BIOS/system folders to the Retrosync naming pattern.

Arguments:
  DIRECTORY    Folder containing the directories to rename.
               Defaults to the current working directory.

Options:
  -n, --dry-run  Show what would be renamed without changing anything.
  -h, --help     Show this help.

Example:
  ./scripts/rename-bios-folders.sh --dry-run ~/Dropbox/Software/Bios
  ./scripts/rename-bios-folders.sh ~/Dropbox/Software/Bios
EOF
}

lowercase() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

dry_run=0
target_dir="."

while (($# > 0)); do
  case "$1" in
    -n|--dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      printf 'Unknown option: %s\n\n' "$1" >&2
      usage >&2
      exit 1
      ;;
    *)
      target_dir="$1"
      shift
      if (($# > 0)); then
        printf 'Only one directory may be provided.\n\n' >&2
        usage >&2
        exit 1
      fi
      ;;
  esac
done

if [[ ! -d "$target_dir" ]]; then
  printf 'Directory not found: %s\n' "$target_dir" >&2
  exit 1
fi

declare -A rename_map=(
  ["32X"]="Sega - 32X"
  ["3DO"]="Panasonic - 3DO"
  ["5200"]="Atari - 5200"
  ["7800"]="Atari - 7800"
  ["Dreamcast"]="Sega - Dreamcast"
  ["DS"]="Nintendo - Nintendo DS"
  ["GameCube"]="Nintendo - GameCube"
  ["GBA"]="Nintendo - Game Boy Advance"
  ["Jaguar"]="Atari - Jaguar"
  ["Mame"]="MAME 2003-Plus"
  ["Neogeo"]="SNK - Neo Geo"
  ["Openemu"]="OpenEmu"
  ["PS2"]="Sony - PlayStation 2"
  ["PS3"]="Sony - PlayStation 3"
  ["PSX"]="Sony - PlayStation"
  ["Retroarch"]="RetroArch"
  ["Saturn"]="Sega - Saturn"
  ["SegaCD"]="Sega - Mega-CD"
  ["Snes"]="Nintendo - Super Nintendo Entertainment System"
  ["Switch"]="Nintendo - Switch"
  ["X68000"]="Sharp - X68000"
)

planned=0
renamed=0
skipped=0
errors=0

mapfile -t src_names < <(printf '%s\n' "${!rename_map[@]}" | sort)

for src_name in "${src_names[@]}"; do
  src_path="$target_dir/$src_name"
  dst_name="${rename_map[$src_name]}"
  dst_path="$target_dir/$dst_name"

  if [[ ! -e "$src_path" ]]; then
    printf 'SKIP missing: %s\n' "$src_path"
    ((skipped+=1))
    continue
  fi

  if [[ ! -d "$src_path" ]]; then
    printf 'SKIP not a directory: %s\n' "$src_path"
    ((skipped+=1))
    continue
  fi

  if [[ "$src_path" == "$dst_path" ]]; then
    printf 'SKIP already named: %s\n' "$src_path"
    ((skipped+=1))
    continue
  fi

  case_only_rename=0
  if [[ "$(lowercase "$src_path")" == "$(lowercase "$dst_path")" ]]; then
    case_only_rename=1
  fi

  if [[ -e "$dst_path" && $case_only_rename -eq 0 ]]; then
    printf 'ERROR destination exists: %s -> %s\n' "$src_path" "$dst_path" >&2
    ((errors+=1))
    continue
  fi

  printf '%s %s -> %s\n' \
    "$([[ $dry_run -eq 1 ]] && printf 'DRY-RUN' || printf 'RENAME')" \
    "$src_path" \
    "$dst_path"
  ((planned+=1))

  if [[ $dry_run -eq 0 ]]; then
    if [[ $case_only_rename -eq 1 ]]; then
      tmp_path="$target_dir/.rename-temp-${src_name}-$$"
      if [[ -e "$tmp_path" ]]; then
        printf 'ERROR temporary path exists: %s\n' "$tmp_path" >&2
        ((errors+=1))
        continue
      fi
      mv -- "$src_path" "$tmp_path"
      mv -- "$tmp_path" "$dst_path"
    else
      mv -- "$src_path" "$dst_path"
    fi
    ((renamed+=1))
  fi
done

printf '\nSummary: planned=%d renamed=%d skipped=%d errors=%d\n' \
  "$planned" \
  "$renamed" \
  "$skipped" \
  "$errors"

if [[ $errors -gt 0 ]]; then
  exit 1
fi
