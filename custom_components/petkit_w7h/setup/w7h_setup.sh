#!/bin/sh
# Petkit Eversweet Ultra (W7H): switch to local operation with Home Assistant (petkit_w7h).
#
# Run on the fountain as root (telnet), served by your Home Assistant:
#   wget -qO- __HA_URL__/setup.sh | sh                   switch to Home Assistant
#   wget -qO- __HA_URL__/setup.sh | sh -s -- --dry-run   check only, change nothing
#   wget -qO- __HA_URL__/setup.sh | sh -s -- --cloud     back to the Petkit cloud
#
# Only the server addresses in /opt/user.conf are changed. The original file is kept
# as /opt/user.conf.stock and restored by --cloud. Tested with firmware 456.

HA="__HA_URL__"
CONF=/opt/user.conf
STOCK=/opt/user.conf.stock
PKTOOL=/app/bin/pktool
TMP=/tmp/w7h_setup.$$
export LD_LIBRARY_PATH=/soc/usr/lib:/soc/lib:/app/bin:/syslib/lib:/app/lib:/opt/syslib:/alg

MODE=install
case "${1:-}" in
    "") ;;
    --dry-run) MODE=dry ;;
    --cloud) MODE=cloud ;;
    *) echo "Unknown option: $1 (allowed: --dry-run, --cloud)"; exit 2 ;;
esac

say() { echo "[w7h] $*"; }
die() { echo "[w7h] ERROR: $*"; rm -f "$TMP"; exit 1; }

[ -x "$PKTOOL" ] || die "$PKTOOL not found. Is this an Eversweet Ultra (W7H)?"
[ -f "$CONF" ] || die "$CONF not found."

if [ "$MODE" = cloud ]; then
    [ -f "$STOCK" ] || die "No backup $STOCK found, nothing to restore."
    cp "$STOCK" "$CONF" && sync || die "Restore failed."
    say "Original configuration restored. Rebooting ..."
    sleep 2
    reboot
    exit 0
fi

"$PKTOOL" cfg dump "$CONF" "$TMP" >/dev/null 2>&1 || die "Could not read $CONF."
ID=$(sed -n 's/^[[:space:]]*"dev_id":[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$TMP" | head -n 1)
SECRET=$(sed -n 's/^[[:space:]]*"dev_srt":[[:space:]]*"\([^"]*\)".*/\1/p' "$TMP" | head -n 1)
[ -n "$ID" ] && [ "$ID" != 0 ] || die "No device ID found. Pair the fountain with the Petkit app first."
echo "$SECRET" | grep -Eq '^[A-Za-z0-9._~+/=-]{8,128}$' \
    || die "Unexpected format of the device secret. Please use the manual setup (README)."
say "Device ID $ID, device secret found (${#SECRET} characters)."

# Hand ID and secret to Home Assistant. It always answers with "OK ..." or "ERROR ...".
BODY="{\"id\":$ID,\"secret\":\"$SECRET\""
[ "$MODE" = dry ] && BODY="$BODY,\"dry_run\":true"
REPLY=$(wget -qO- -T 15 --header "Content-Type: application/json" --post-data "$BODY}" "$HA/setup" 2>&1)
case "$REPLY" in
    OK*) say "Home Assistant: $REPLY" ;;
    *) die "Home Assistant at $HA did not accept the data: ${REPLY:-no answer}" ;;
esac

API="$HA/6/"
if [ "$MODE" = dry ]; then
    CURRENT=$(sed -n 's/^[[:space:]]*"apiServers":[[:space:]]*\[\([^]]*\)\].*/\1/p' "$TMP" | head -n 1)
    rm -f "$TMP"
    say "Current server: $CURRENT"
    say "Dry run: would switch the server to $API and reboot. Nothing was changed."
    exit 0
fi

[ -f "$STOCK" ] || cp "$CONF" "$STOCK" || die "Could not back up $CONF."
sed -i "s|\(\"apiServers\":[[:space:]]*\)\[[^]]*\]|\1[\"$API\", \"\", \"\"]|" "$TMP"
sed -i 's|\("ipServers":[[:space:]]*\)\[[^]]*\]|\1["", "", ""]|' "$TMP"
sed -i 's|\("dns":[[:space:]]*\)\[[^]]*\]|\1["", "", "", "", "", "", "", "", "", ""]|' "$TMP"
"$PKTOOL" cfg save "$TMP" "$CONF" >/dev/null 2>&1 || die "Could not write $CONF (original is still in place)."
sync
"$PKTOOL" cfg dump "$CONF" "$TMP" >/dev/null 2>&1
if ! grep -q "\"$API\"" "$TMP"; then
    cp "$STOCK" "$CONF"
    sync
    die "Check after writing failed, original configuration restored."
fi
rm -f "$TMP"
say "Done. The fountain now uses $API"
say "Rebooting, it shows up in Home Assistant about a minute from now ..."
sleep 2
reboot
