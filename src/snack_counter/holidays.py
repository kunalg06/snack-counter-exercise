"""Public holiday calendars for each office.

Norway can be computed: every public holiday is either a fixed date or a fixed offset from
Easter Sunday. India can't: many Karnataka holidays follow lunar calendars (and Muslim
festivals move with moon sighting), so they come from the state government's yearly
notification. In production both would come from the company's HR holiday calendar;
this module is the stand-in.
"""

from __future__ import annotations

from datetime import date, timedelta


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian / Meeus-Jones-Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 (name from the published algorithm)
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def norway_holidays(year: int) -> frozenset[date]:
    """Norwegian public holidays (helligdager) plus 1 and 17 May."""
    easter = easter_sunday(year)
    from_easter = [-3, -2, 0, 1, 39, 49, 50]  # Maundy Thu, Good Fri, Easter, Easter Mon, Ascension, Whitsun, Whit Mon
    fixed = [(1, 1), (5, 1), (5, 17), (12, 25), (12, 26)]
    return frozenset(
        [easter + timedelta(days=n) for n in from_easter] + [date(year, m, d) for m, d in fixed]
    )


# Company practice in Oslo, not law: the office is closed on Christmas Eve.
OSLO_COMPANY_DAYS_OFF = frozenset({date(2026, 12, 24), date(2027, 12, 24)})

# Karnataka general holidays from the state government notifications for 2026 and 2027
# (Republic/Independence/Gandhi Jayanti are national). Muslim festival dates may shift by a
# day with moon sighting. Update every year from the notification or the company HR list.
KARNATAKA_GENERAL_HOLIDAYS: frozenset[date] = frozenset({
    # 2026
    date(2026, 1, 14),   # Makara Sankranti
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 19),   # Ugadi
    date(2026, 3, 21),   # Khutub-e-Ramzan
    date(2026, 3, 31),   # Mahaveera Jayanthi
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. B.R. Ambedkar Jayanthi
    date(2026, 4, 20),   # Basava Jayanthi
    date(2026, 5, 1),    # May Day
    date(2026, 5, 28),   # Bakrid
    date(2026, 6, 26),   # Last day of Muharram
    date(2026, 8, 15),   # Independence Day
    date(2026, 8, 26),   # Eid-e-Milad
    date(2026, 9, 14),   # Varasiddhi Vinayaka Vrata (Ganesh Chaturthi)
    date(2026, 10, 2),   # Gandhi Jayanthi
    date(2026, 10, 20),  # Mahanavami / Ayudha Pooja
    date(2026, 10, 21),  # Vijayadasami
    date(2026, 11, 1),   # Kannada Rajyotsava (Sunday)
    date(2026, 11, 10),  # Balipadyami / Deepavali
    date(2026, 11, 27),  # Kanakadasa Jayanthi
    date(2026, 12, 25),  # Christmas
    # 2027
    date(2027, 1, 15),   # Makara Sankranti
    date(2027, 1, 26),   # Republic Day
    date(2027, 3, 6),    # Maha Shivaratri
    date(2027, 3, 10),   # Ramzan
    date(2027, 3, 26),   # Good Friday
    date(2027, 4, 7),    # Ugadi
    date(2027, 4, 14),   # Dr. B.R. Ambedkar Jayanthi
    date(2027, 5, 1),    # Labour Day
    date(2027, 5, 17),   # Bakrid
    date(2027, 6, 16),   # Last day of Muharram
    date(2027, 8, 15),   # Independence Day (Sunday)
    date(2027, 9, 4),    # Varasiddhi Vinayaka Vrata
    date(2027, 9, 29),   # Mahalaya Amavasya
    date(2027, 10, 2),   # Gandhi Jayanti
    date(2027, 10, 15),  # Valmiki Jayanti
    date(2027, 10, 28),  # Naraka Chaturdashi
    date(2027, 10, 30),  # Deepavali
    date(2027, 11, 1),   # Kannada Rajyotsava
    date(2027, 11, 16),  # Kanakadasa Jayanti
    date(2027, 12, 25),  # Christmas (Saturday)
})
