#!/usr/bin/env bash
set -euo pipefail
systemd-analyze verify /etc/systemd/system/streams-aio-public-sync.service /etc/systemd/system/streams-aio-public-sync.timer
[[ "$(systemctl show streams-aio-public-sync.service -p User --value)" == pi ]]
systemctl show streams-aio-public-sync.service -p ExecStart --value | grep -Fq 'sync-public-stack.py --source /home/pi/streams-aio --seamless --publish'
sudo -n systemctl start streams-aio-public-sync.service
[[ "$(systemctl show streams-aio-public-sync.service -p Result --value)" == success ]]
[[ "$(systemctl show streams-aio-public-sync.service -p ExecMainStatus --value)" == 0 ]]
echo 'SYSTEMD_PUBLIC_SYNC_RUN PASS'
systemctl is-enabled --quiet streams-aio-public-sync.timer
systemctl is-active --quiet streams-aio-public-sync.timer
systemctl list-timers streams-aio-public-sync.timer --no-pager
echo 'SYSTEMD_PUBLIC_SYNC_TIMER PASS'
