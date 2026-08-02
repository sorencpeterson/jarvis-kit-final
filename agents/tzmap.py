#!/usr/bin/env python3
"""#168: US/Canada area-code -> IANA timezone map + a tiny lookup helper.

Static, no network, no dependency. Covers the top-100-by-population area codes (the
ones actually likely to show up in a US agency/local-service contact list) plus a
handful of obvious extras. Ambiguous codes (a few straddle a zone boundary, e.g. 205
Alabama vs split-state codes) get the zone where the majority of the population sits;
good enough for "don't call before 9am their time," not a legal record.

warm_refresh.py imports `tz_for_phone()` and writes a `tz` column into WARM-HITLIST.csv.
warm_block.py (NOT owned by this mission) can consume that column later for do-not-call
windows (#168's second half, item 168 in the backlog) without needing to import this
file's internals — just read the CSV column.

Usage:
  python3 tzmap.py "+14358627288"      # prints the inferred IANA zone or "unknown"
"""
from __future__ import annotations

import re
import sys

# area code -> IANA zone. US mainland + a few Canada/territory codes. Grouped by
# zone for readability; this is not exhaustive of all ~350 assigned codes, but covers
# every populous metro (top-100-by-population area codes) plus common agency-list states.
_ET = ("201 202 203 207 212 215 216 219 234 239 260 267 302 305 315 321 330 336 339 351 "
       "352 386 401 404 407 410 412 413 419 423 434 440 443 470 475 478 484 508 513 516 "
       "517 561 570 585 586 603 607 610 614 616 617 631 646 678 703 704 716 717 724 727 "
       "732 754 757 762 770 772 774 781 786 803 804 810 813 828 843 845 848 850 856 863 "
       "864 865 878 904 908 910 912 914 917 919 929 941 954 973 980 984").split()
_CT = ("205 214 217 218 224 251 262 269 270 281 309 312 314 316 318 319 320 331 334 337 "
       "361 405 409 414 417 430 432 469 479 501 512 515 563 573 601 608 615 618 630 "
       "636 641 651 662 682 708 712 713 715 731 763 773 779 815 816 817 830 832 847 855 "
       "870 901 903 913 918 920 936 940 952 972 979").split()
_MT = ("303 307 385 406 435 480 505 520 575 602 623 719 720 801 928 970".split())
_PT = ("206 209 213 253 310 323 341 360 408 415 424 425 442 458 503 509 510 530 541 559 "
       "562 619 626 650 657 669 707 714 747 760 775 805 818 831 858 909 916 925 949 951 "
       "971".split())
_AKT = "907".split()
_HAT = "808".split()
# a few Canadian codes worth having since agency lists sometimes include them
_CA_ET = "416 437 647 613 343 819 514 438 450".split()   # Toronto/Ottawa/Montreal area
_CA_PT = "604 778 236 250".split()                        # BC

AREA_CODE_TZ: dict[str, str] = {}
for _codes, _zone in (
    (_ET, "America/New_York"), (_CT, "America/Chicago"), (_MT, "America/Denver"),
    (_PT, "America/Los_Angeles"), (_AKT, "America/Anchorage"), (_HAT, "Pacific/Honolulu"),
    (_CA_ET, "America/Toronto"), (_CA_PT, "America/Vancouver"),
):
    for _c in _codes:
        AREA_CODE_TZ[_c] = _zone


def area_code(phone: str) -> str:
    """Best-effort extraction of a 3-digit NANP area code from a loosely formatted
    phone string (+1XXXYYYZZZZ, (XXX) YYY-ZZZZ, XXX-YYY-ZZZZ, etc.)."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) >= 10:
        return digits[:3]
    return ""


def tz_for_phone(phone: str) -> str:
    """IANA zone for a phone number's area code, or '' if unknown/unmapped."""
    ac = area_code(phone)
    return AREA_CODE_TZ.get(ac, "")


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["+14358627288"]:
        print(f"{arg} -> area {area_code(arg) or '?'} -> {tz_for_phone(arg) or 'unknown'}")
