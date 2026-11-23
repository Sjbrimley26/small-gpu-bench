#!/usr/bin/env bash
# Leak scanner for this repo: fails (exit 1) if a tracked file contains something that should
# not be published.
#
# The rules here are generic. The lab also runs a stricter scanner before a push, whose rule
# list holds the private names and hosts this repo must stay clear of; that one is deliberately
# not shipped, because the rule list is itself the thing it protects.
#
#   scripts/check_public.sh                 scan this checkout (tracked files only)
#   scripts/check_public.sh --repo PATH     scan another checkout
#   scripts/check_public.sh --allow FILE    waive approved strings, one per line:
#                                           <check-name|*><TAB><literal string>   ('#' = comment)
#   scripts/check_public.sh --selftest      prove every rule still matches its sample
#
# Files come from `git ls-files`, so `git add` a new file before scanning it.
set -u
usage() { echo "usage: check_public.sh [--repo PATH] [--allow FILE] | --selftest" >&2; exit 2; }

SELF="$(realpath "$0")"
REPO=""; ALLOW=""; TEST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) [ $# -ge 2 ] || usage; REPO="$2"; shift 2 ;;
    --allow) [ $# -ge 2 ] || usage; ALLOW="$(realpath "$2")"; shift 2 ;;
    --selftest) TEST=1; shift ;;
    *) usage ;;
  esac
done

# name -> extended regex (grep -E -i). Split strings keep this file from matching itself.
declare -A PAT=(
  [home-path]='(/home/|/Users/)[A-Za-z0-9._-]+'
  [lan-192]='192[.]168[.]'
  [lan-172]='(^|[^0-9.])172[.](1[6-9]|2[0-9]|3[01])[.][0-9]{1,3}[.][0-9]{1,3}([^0-9]|$)'
  [lan-10]='(^|[^0-9.])10[.][0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}([^0-9]|$)'
  [cgnat]='(^|[^0-9.])100[.](6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])[.][0-9]{1,3}[.][0-9]{1,3}([^0-9]|$)'
  [secret-openai]='sk-[A-Za-z0-9_-]{20,}'
  [secret-github]='(ghp_|github_pat_)[A-Za-z0-9_]{20,}'
  [secret-aws]='AKIA[0-9A-Z]{16}'
  [secret-slack]='xox[baprs]-'
  [private-key]='-----BEGIN [A-Z ]*PRIVATE KEY-----'
)
EMAIL='[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+([.][A-Za-z0-9-]+)*[.][A-Za-z]{2,}'
EMAIL_OK='@(example[.](com|org|net|test)|[A-Za-z0-9-]+[.]example|[A-Za-z0-9.-]+[.]invalid)\>'

# live_emails TEXT -> the addresses in TEXT that are not on a reserved example domain
live_emails() { printf '%s\n' "$1" | grep -oE -- "$EMAIL" | grep -vEi -- "$EMAIL_OK" || true; }

if [ "$TEST" -eq 1 ]; then
  FAIL=0
  t() {  # name bad good
    if printf '%s\n' "$2" | grep -qE -i -- "${PAT[$1]}"; then :; else
      echo "selftest FAIL: [$1] does not catch its sample"; FAIL=1; fi
    if printf '%s\n' "$3" | grep -qE -i -- "${PAT[$1]}"; then
      echo "selftest FAIL: [$1] matches its negative sample"; FAIL=1; fi
  }
  t home-path "/home/alice/src/notes.md" "~/src/notes.md"
  t lan-192 "server 192.168.1.20" "server 192.169.1.20"
  t lan-172 "nas 172.20.4.9" "nas 172.32.4.9"
  t lan-10 "box 10.4.2.11" "box 10.4.2"
  t cgnat "tailnet 100.101.5.7" "tailnet 100.63.5.7"
  t secret-openai "key sk-abcdefghijklmnopqrstuvwx" "key sk-short"
  t secret-github "token ghp_abcdefghijklmnopqrstuvwxyz0123456789" "token ghp_short"
  t secret-aws "id AKIAABCDEFGHIJKLMNOP" "id AKIA1234"
  t secret-slack "hook xoxb-1234" "hook box-1234"
  t private-key "-----BEGIN OPENSSH PRIVATE KEY-----" "-----BEGIN PUBLIC KEY-----"
  if [ -z "$(live_emails 'write to dana@acme.io')" ]; then
    echo "selftest FAIL: [email] does not catch its sample"; FAIL=1; fi
  if [ -n "$(live_emails 'write to dana@example.com')" ]; then
    echo "selftest FAIL: [email] flags a reserved example domain"; FAIL=1; fi
  if [ "$FAIL" -eq 0 ]; then echo "selftest: $(( ${#PAT[@]} + 1 )) rules OK"; fi
  exit "$FAIL"
fi

[ -n "$REPO" ] || REPO="."
cd "$REPO" 2>/dev/null || { echo "no such repo: $REPO" >&2; exit 2; }
if [ -n "$ALLOW" ] && [ ! -f "$ALLOW" ]; then echo "allowlist not found: $ALLOW" >&2; exit 2; fi

# strip_allowed CHECK TEXT -> TEXT with every approved string for CHECK (and for '*') removed
strip_allowed() {
  local check="$1" text="$2" c s
  [ -n "$ALLOW" ] || { printf '%s' "$text"; return; }
  while IFS=$'\t' read -r c s; do
    case "$c" in ''|'#'*) continue ;; esac
    [ "$c" = "$check" ] || [ "$c" = "*" ] || continue
    [ -n "$s" ] || continue
    text="$(printf '%s' "$text" | S="$s" perl -pe 's/\Q$ENV{S}\E//g')"
  done < "$ALLOW"
  printf '%s' "$text"
}

# Gate precisely what ships: git-tracked files, minus the scanner itself and build junk.
SELF_NAME="$(basename "$SELF")"
mapfile -t FILES < <(git ls-files 2>/dev/null | grep -v '__pycache__' | grep -v "$SELF_NAME" | sort)
if [ ${#FILES[@]} -eq 0 ]; then
  mapfile -t FILES < <(find . \( -path ./.git -o -name __pycache__ \) -prune -o -type f \
    ! -name "$SELF_NAME" ! -name '*.pyc' -print | sed 's|^\./||' | sort)
fi
if [ ${#FILES[@]} -eq 0 ]; then echo "check_public: no files found under $REPO" >&2; exit 2; fi

FAIL=0
WAIVED=0
report() { printf 'LEAK [%s] %s\n' "$1" "$2" | cut -c1-200; FAIL=1; }

for name in "${!PAT[@]}"; do
  hits="$(grep -nIiE -- "${PAT[$name]}" "${FILES[@]}" /dev/null 2>/dev/null)"
  while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    if [ -n "$ALLOW" ]; then
      rest="$(strip_allowed "$name" "${hit#*:*:}")"
      if ! printf '%s\n' "$rest" | grep -qiE -- "${PAT[$name]}"; then WAIVED=$((WAIVED+1)); continue; fi
    fi
    report "$name" "$hit"
  done <<< "$hits"
done

while IFS= read -r hit; do
  line="$(strip_allowed email "${hit#*:*:}")"
  if [ -n "$(live_emails "$line")" ]; then report "email" "$hit"
  elif [ -n "$ALLOW" ] && [ -n "$(live_emails "${hit#*:*:}")" ]; then WAIVED=$((WAIVED+1)); fi
done < <(grep -nIE -- "$EMAIL" "${FILES[@]}" /dev/null 2>/dev/null)

if [ "$FAIL" -eq 0 ]; then
  echo "check_public: OK (${#FILES[@]} files scanned, 0 findings${ALLOW:+, $WAIVED waived by $(basename "$ALLOW")})"
else
  echo "check_public: FAILED - fix the lines above before publishing"
fi
exit "$FAIL"
