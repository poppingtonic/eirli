#!/bin/bash
# SCRIPT AUTOGENERATED BY nfs_generate_mount_cmd.sh; DO NOT EDIT MANUALLY

# This is a script to mount the il-representations project filesystem from
# server 'repl-nfs-server' (zone 'us-west1-b'). It will only work on GCP.

set -e

if mountpoint '/data/il-representations/'; then
    echo "'/data/il-representations/' is already a mountpoint; skipping remount"
    exit 0
fi

if [ -z "$(cat /proc/filesystems | grep 'nfsd$')" ]; then
    echo "This machine does not seem to have NFS support. Will attempt to"\
        "install NFS packages."
    apt-get update -y && apt-get install -y nfs-common
fi

mkdir -p '/data/il-representations/' \
    && echo '10.217.224.122:/vol1' '/data/il-representations/' nfs defaults,_netdev 0 0 >> /etc/fstab \
    && mount -a \
    && chmod go+rw '/data/il-representations/'

echo "Done! Mount should be accessible on '/data/il-representations/'"
