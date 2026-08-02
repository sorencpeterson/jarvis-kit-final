#!/bin/bash
# <xbar.title>VPN State</xbar.title>
# <xbar.desc>Glanceable Mullvad state. Built for deliberate toggling: the menu bar
# is loud when the tunnel is down so "I'll turn it back on after the call" cannot
# quietly become "it has been off since Tuesday."</xbar.desc>
# Refresh cadence set by filename (.10s.). Reads the local daemon only, no network
# calls on the refresh path, so this is cheap to run every 10 seconds.

MULLVAD="/usr/local/bin/mullvad"
WANT_TZ="America/New_York"

if [ ! -x "$MULLVAD" ]; then
  echo "⛔ | color=#6b7280"
  echo "---"
  echo "mullvad CLI not found at $MULLVAD"
  exit 0
fi

STATUS="$($MULLVAD status 2>/dev/null)"
STATE="$(printf '%s' "$STATUS" | head -1 | awk '{print $1}' | tr -d ':')"
RELAY="$(printf '%s' "$STATUS" | awk -F': +' '/Relay:/{print $2; exit}')"
# NB: split on ': +' breaks here because "IPv4: " matches the separator too and
# truncates the address. Strip the label with sed and keep the whole remainder.
LOC="$(printf '%s' "$STATUS" | sed -n 's/.*Visible location: *//p')"
CITY="$(printf '%s' "$LOC" | awk -F', ' '{print $2}' | awk -F'\\.' '{print $1}')"
COUNTRY="$(printf '%s' "$LOC" | awk -F', ' '{print $1}')"
IP="$(printf '%s' "$LOC" | sed -n 's/.*IPv4: \([0-9.]*\).*/\1/p')"

LOCKDOWN_RAW="$($MULLVAD lockdown-mode get 2>/dev/null)"
case "$LOCKDOWN_RAW" in *": on"*) LOCKDOWN="on" ;; *) LOCKDOWN="off" ;; esac

PIN="$($MULLVAD relay get 2>/dev/null | awk -F': +' '/Location:/{print $2; exit}')"
CUR_TZ="$(readlink /etc/localtime | sed 's|.*zoneinfo/||')"

# ---------- menu bar line ----------
case "$STATE" in
  Connected)
    if [ "$COUNTRY" = "USA" ]; then
      echo "🛡 ${CITY:-US} | color=#3fe7ff font=Menlo"
    else
      echo "⚠️ ${COUNTRY:-?} | color=#ff5d73 font=Menlo"
    fi
    ;;
  Blocked)
    echo "⛔ BLOCKED | color=#ffb454 font=Menlo"
    ;;
  Connecting)
    echo "◌ … | color=#ffb454 font=Menlo"
    ;;
  *)
    # The loud state. This is the one that has to be impossible to miss.
    echo "🔴 VPN OFF | color=#ff5d73 font=Menlo"
    ;;
esac

# ---------- dropdown ----------
echo "---"
case "$STATE" in
  Connected)
    echo "Exit:       ${LOC:-unknown} | font=Menlo size=13"
    echo "Relay:      ${RELAY:-?} | font=Menlo size=13"
    ;;
  *)
    echo "Tunnel is DOWN — real IP is exposed | font=Menlo size=13 color=#ff5d73"
    ;;
esac
echo "Pinned to:  ${PIN:-any} | font=Menlo size=13"

if [ "$LOCKDOWN" = "on" ]; then
  echo "Lockdown:   on  (disconnecting kills all traffic) | font=Menlo size=13 color=#3fe7ff"
else
  echo "Lockdown:   off (drops leak silently) | font=Menlo size=13 color=#ffb454"
fi

if [ "$CUR_TZ" = "$WANT_TZ" ]; then
  echo "Timezone:   $CUR_TZ | font=Menlo size=13"
else
  echo "Timezone:   $CUR_TZ  ≠ $WANT_TZ | font=Menlo size=13 color=#ff5d73"
fi

# Coherence: US exit but non-Eastern clock, or vice versa, is visible to sites.
if [ "$STATE" = "Connected" ] && [ "$COUNTRY" = "USA" ] && [ "$CUR_TZ" != "$WANT_TZ" ]; then
  echo "⚠️ US exit but clock is not Eastern | font=Menlo size=12 color=#ff5d73"
fi

# Sharper coherence check: the clock says Eastern but the exit geolocates to a
# different US timezone. Sites see both, and the mismatch is the tell.
case "$CITY" in
  "New York"|Newark|Boston|Atlanta|Miami|Ashburn|Charlotte|Raleigh|Washington|Philadelphia|Orlando|Tampa|Jacksonville|Detroit|Pittsburgh|Cleveland|"") EAST=1 ;;
  *) EAST=0 ;;
esac
if [ "$STATE" = "Connected" ] && [ "$COUNTRY" = "USA" ] && [ "$CUR_TZ" = "America/New_York" ] && [ "$EAST" = "0" ]; then
  echo "⚠️ Exit is $CITY but clock is Eastern — re-pin | font=Menlo size=12 color=#ff5d73"
fi

echo "---"
echo "Actions | size=11 color=#6b7280"
if [ "$STATE" = "Connected" ]; then
  echo "Disconnect (for a Zoom call) | bash=$MULLVAD param1=disconnect terminal=false refresh=true"
else
  echo "Reconnect now | bash=$MULLVAD param1=connect terminal=false refresh=true"
fi
echo "Re-pin to us nyc | bash=$MULLVAD param1=relay param2=set param3=location param4=us param5=nyc terminal=false refresh=true"
if [ "$LOCKDOWN" = "on" ]; then
  echo "Lockdown off (needed to browse with VPN down) | bash=$MULLVAD param1=lockdown-mode param2=set param3=off terminal=false refresh=true"
else
  echo "Lockdown on | bash=$MULLVAD param1=lockdown-mode param2=set param3=on terminal=false refresh=true"
fi
echo "---"
echo "Verify real exit IP (network call) | bash=/bin/bash param1=-c param2=\"open 'https://am.i.mullvad.net/'\" terminal=false"
echo "Refresh now | refresh=true"
