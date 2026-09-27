#!/bin/sh
# Exporterar alla delar till stl/ och bilder till bilder/ (kräver OpenSCAD)
cd "$(dirname "$0")"
mkdir -p stl bilder
for d in bottenplatta krokarm sparr blockerare klockkopp fingerring; do
  openscad -o "stl/$d.stl" -D "del=\"$d\"" "$@" grindslappare.scad
done
for u in false true; do
  n=$([ "$u" = true ] && echo utlost || echo last)
  openscad -o "bilder/montage_$n.png" -D utlost=$u --imgsize=1400,1000 \
    --camera=70,-25,20,55,0,20,420 --colorscheme=Tomorrow "$@" grindslappare.scad
  openscad -o "bilder/framifran_$n.png" -D utlost=$u --imgsize=1400,1000 \
    --camera=70,-25,0,0,0,0,380 --projection=o --colorscheme=Tomorrow "$@" grindslappare.scad
done
