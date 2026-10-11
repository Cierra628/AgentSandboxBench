#!/bin/sh
set -eu
export PATH=/bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
exec </dev/ttyS0 >/dev/ttyS0 2>&1
stty -echo
target=/merged/known.bin
for word in $(cat /proc/cmdline); do
    case "$word" in asb_target=*) target=${word#asb_target=} ;; esac
done
mkdir -p /base /checkpoint /upper /merged
mount -t ext4 -o ro,dax=always /dev/pmem0 /base
mount -t ext4 -o ro,dax=always /dev/pmem1 /checkpoint
mount -t ext4 /dev/vda /upper
mkdir -p /upper/root /upper/work
mount -t overlay overlay -o lowerdir=/checkpoint/delta:/base,upperdir=/upper/root,workdir=/upper/work /merged
cat /proc/mounts
echo ASB_READY
while read -r command; do
    case "$command" in
        READ) sha256sum "$target"; echo ASB_READ_DONE ;;
        MAPREAD) /bin/dax-read read "$target"; echo ASB_MAP_DONE ;;
        MAPRW) /bin/dax-read rw "$target"; echo ASB_MAP_DONE ;;
        MAPWRITE) /bin/dax-read write "$target"; echo ASB_MAP_DONE ;;
        DONE) echo ASB_DONE; poweroff -f ;;
        *) exit 1 ;;
    esac
done
