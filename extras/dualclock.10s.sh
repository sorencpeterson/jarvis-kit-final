#!/bin/bash
# <xbar.title>Dual Clock — Eastern + North Macedonia</xbar.title>
# <xbar.desc>Always-visible menu bar clock for two timezones.</xbar.desc>
# Refresh cadence is set by the filename (.10s. = every 10 seconds).
# Uses real IANA timezones so EST/EDT and CET/CEST auto-switch with DST.

ET=$(TZ="America/New_York" date +"%-I:%M%p")
MK=$(TZ="Europe/Skopje" date +"%-I:%M%p")

# --- menu bar line ---
echo "ET ${ET} · MK ${MK}"

# --- dropdown detail ---
echo "---"
TZ="America/New_York" date +"Eastern:        %-I:%M %p %Z   %a %b %-d | font=Menlo size=13"
TZ="Europe/Skopje"    date +"N. Macedonia:   %-I:%M %p %Z   %a %b %-d | font=Menlo size=13"
echo "---"
# Work-hours helper: 9-5 ET is 3pm-11pm in Skopje
echo "9-5 ET  =  3:00 PM - 11:00 PM MK | font=Menlo size=12 color=#9ca3af"
echo "---"
echo "Refresh now | refresh=true"
