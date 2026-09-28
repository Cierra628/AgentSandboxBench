#!/bin/sh
set -eu
export PATH=/bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
exec </dev/ttyS0 >/dev/ttyS0 2>&1
stty -echo
branch=B
for word in $(cat /proc/cmdline); do
    case "$word" in asb_branch=*) branch=${word#asb_branch=} ;; esac
done
mkdir -p /base /checkpoint /upper /merged
mount -t ext4 -o ro,dax=always /dev/pmem0 /base
mount -t ext4 -o ro,dax=always /dev/pmem1 /checkpoint
mount -t ext4 /dev/vda /upper
mkdir -p /upper/root /upper/work
mount -t overlay overlay -o lowerdir=/checkpoint/delta:/base,upperdir=/upper/root,workdir=/upper/work /merged
echo ASB_MOUNTS
cat /proc/mounts
test "$(cat /sys/block/pmem1/queue/dax)" = 1
before=$(sha256sum /merged/known.bin | cut -d' ' -f1)
echo ASB_GUEST_BEFORE_BEGIN
echo "ASB_PAGE_KIB $(/bin/busybox awk '/^KernelPageSize:/ {print $2; exit}' /proc/self/smaps)"
cat /proc/iomem
cat /proc/meminfo
echo ASB_GUEST_END
echo "ASB_READY branch=$branch hash=$before kernel=$(uname -r)"
read -r command
test "$command" = GO
if [ "$branch" = A ]; then
    printf X | dd of=/merged/known.bin bs=1 count=1 conv=notrunc
    # Flush only this guest's filesystem state before the readback.
    sync
fi
after=$(sha256sum /merged/known.bin | cut -d' ' -f1)
lower=$(sha256sum /checkpoint/delta/known.bin | cut -d' ' -f1)
echo ASB_GUEST_AFTER_BEGIN
echo "ASB_PAGE_KIB $(/bin/busybox awk '/^KernelPageSize:/ {print $2; exit}' /proc/self/smaps)"
cat /proc/iomem
cat /proc/meminfo
echo ASB_GUEST_END
echo "ASB_AFTER branch=$branch hash=$after lower=$lower"
read -r command
test "$command" = DONE
echo ASB_DONE
poweroff -f
