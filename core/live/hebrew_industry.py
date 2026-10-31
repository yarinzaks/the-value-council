"""A company's industry in Hebrew, for the notification.

Why not the SEC's own title, translated
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``data_bundled/sic_titles.json`` holds the SEC's 444 registered
industry titles, and they are legal nomenclature rather than prose:
``APPAREL & OTHER FINISHD PRODS OF FABRICS & SIMILAR MATL``,
``AIR-COND & WARM AIR HEATG EQUIP & COMM & INDL REFRIG EQUIP``. Those
are hard to read in English, and a faithful Hebrew rendering of one
would be hard to read in Hebrew too. The notification has one line for
this. It should say what the company does.

So the mapping is to a clean sector name, at SIC's own level of
abstraction rather than the SEC's level of registration detail.

Two levels, because one is not enough
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SIC's first two digits are the major industry group — 71 of them cover
all 8,290 tickers in the bundle, which is complete coverage from 71
translations rather than 444. But a group is sometimes too coarse to be
useful: group 28 is "chemicals and allied products" and holds both Eli
Lilly and a fertiliser maker, and a reader told "chemicals" about a
pharmaceutical company has been given the taxonomy instead of the
answer.

:data:`BY_CODE` therefore refines the four-digit codes where the group
misleads, and :data:`BY_GROUP` catches everything else. Below both, the
SEC's English title — so a code added to a future bundle arrives
readable rather than blank.
"""

from __future__ import annotations

from core.live.hebrew_bundle import bundle

#: Four-digit SIC code → Hebrew, where the major group is too coarse to
#: answer the question. Kept to the codes that mislead rather than
#: extended for its own sake: every code absent from here is answered
#: by its group below, and a group that answers well needs no entries.
BY_CODE: dict[str, str] = dict(bundle()["industry_by_code"])

#: SIC major group (first two digits) → Hebrew. 71 of these cover every
#: ticker in the bundle, which is what makes the English fallback in
#: :func:`industry_he` a guard rather than a routine path.
BY_GROUP: dict[str, str] = dict(bundle()["industry_by_group"])


def industry_he(code: str | int | None, english: str | None = None) -> str | None:
    """``code``'s industry in Hebrew, falling back to ``english``.

    The four-digit code first, then its major group, then whatever the
    SEC called it. ``None`` only when there is no code and no title —
    the notification then leaves the line out rather than printing a
    placeholder.
    """
    if code is not None:
        digits = str(code).strip().zfill(4)
        if digits.isdigit():
            exact = BY_CODE.get(digits)
            if exact is not None:
                return exact
            group = BY_GROUP.get(digits[:2])
            if group is not None:
                return group
    return english or None


__all__ = ["BY_CODE", "BY_GROUP", "industry_he"]
