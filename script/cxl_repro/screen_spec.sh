#!/bin/bash
# screen_spec.sh -- run the valgrind/Pin coordinate screen over the SPEC CPU2017
# candidates we have not traced yet.
#
# The binaries live in <bench>/exe/ and the inputs in the refspeed run directory, so
# each screen runs from the run directory with an absolute path to the executable.
# Reference inputs are used because that is what the eventual traces will use; a screen
# on a different input could report a different rep-string ratio.
#
# These are full reference runs under valgrind, so hours each. They also produce the
# basic-block vectors SimPoint needs, so nothing is wasted: the screen is a by-product
# of work we have to do anyway.
set -u
S="$(cd "$(dirname "$0")" && pwd)/count_screen.sh"
D=/home/hwpark/workspace/benchmark/speccpu2017/cpu2017/benchspec/CPU
R=run/run_base_refspeed_phw-m64.0000

start() { # name bench exe args...
  local name=$1 bench=$2 exe=$3; shift 3
  local dir="$D/$bench/$R"
  [ -d "$dir" ] || { echo "no run dir for $bench"; return; }
  ( cd "$dir" && setsid nohup "$S" "$name" -- "$D/$bench/exe/$exe" "$@" \
      > /dev/null 2>&1 < /dev/null & )
  echo "launched $name"
}

start pop2 628.pop2_s speed_pop2_base.phw-m64
start cam4 627.cam4_s cam4_s_base.phw-m64
start imagick 638.imagick_s imagick_s_base.phw-m64 \
  -limit disk 0 refspeed_input.tga -resize 817% -rotate -2.76 -shave 540x375 \
  -alpha remove -auto-level -contrast-stretch 1x1% -colorspace Lab -channel R \
  -equalize +channel -colorspace sRGB -define histogram:unique-colors=false \
  -adaptive-blur 0x5 -despeckle -auto-gamma -adaptive-sharpen 55 -enhance \
  -brightness-contrast 10x10 -resize 30% /tmp/imagick_screen_out.tga

# bwaves reads its parameters from stdin, so it needs a shell to do the redirect
( cd "$D/603.bwaves_s/$R" && setsid nohup bash -c \
    "'$S' bwaves -- '$D/603.bwaves_s/exe/speed_bwaves_base.phw-m64' bwaves_1 < bwaves_1.in" \
    > /dev/null 2>&1 < /dev/null & )
echo "launched bwaves"
