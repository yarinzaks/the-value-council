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

#: Four-digit SIC code → Hebrew, where the major group is too coarse to
#: answer the question. Kept to the codes that mislead rather than
#: extended for its own sake: every code absent from here is answered
#: by its group below, and a group that answers well needs no entries.
BY_CODE: dict[str, str] = {
    # ---- 28: "chemicals" covers pharma, biotech and fertiliser ----
    "2833": "כימיקלים רפואיים",
    "2834": "תרופות",
    "2835": "אבחון רפואי",
    "2836": "מוצרים ביולוגיים",
    "2840": "סבון ומוצרי ניקיון",
    "2844": "קוסמטיקה ומוצרי טיפוח",
    "2860": "כימיקלים אורגניים",
    "2870": "כימיקלים לחקלאות",
    # ---- 36/35: components, semiconductors and computers differ ---
    "3674": "מוליכים למחצה",
    "3571": "מחשבים",
    "3572": "אחסון נתונים",
    "3576": "ציוד תקשורת למחשבים",
    # ---- 37: making a car is not making an aeroplane -------------
    "3711": "ייצור רכב",
    "3713": "מרכבים לרכב מסחרי",
    "3714": "חלקי רכב",
    "3721": "מטוסים",
    "3724": "מנועי מטוסים",
    "3728": "חלקי מטוסים",
    "3730": "בניית אוניות",
    "3751": "אופנועים ואופניים",
    "3760": "טילים וחלליות",
    # ---- 38: medical devices, not "instruments" ------------------
    "3841": "מכשור רפואי",
    "3842": "אספקה אורתופדית",
    "3843": "ציוד לרפואת שיניים",
    "3844": "ציוד הקרנה רפואי",
    "3845": "ציוד אלקטרו-רפואי",
    "3851": "אופטיקה ומשקפיים",
    # ---- 60-67: finance is many trades, not one ------------------
    "6021": "בנקאות מסחרית",
    "6022": "בנקאות מסחרית",
    "6029": "בנקאות מסחרית",
    "6035": "בנקאות למשכנתאות",
    "6036": "בנקאות למשכנתאות",
    "6141": "אשראי צרכני",
    "6153": "אשראי עסקי",
    "6162": "בנקאות משכנתאות",
    "6189": "איגוח נכסים",
    "6199": "שירותים פיננסיים",
    "6211": "ברוקראז' והנפקות",
    "6221": "מסחר בסחורות",
    "6282": "ייעוץ השקעות",
    "6311": "ביטוח חיים",
    "6321": "ביטוח בריאות",
    "6331": "ביטוח כללי",
    "6351": "ביטוח אשראי",
    "6411": "סוכנויות ביטוח",
    "6500": 'נדל"ן',
    "6512": 'ניהול נדל"ן מניב',
    "6519": 'החכרת נדל"ן',
    "6531": 'תיווך וניהול נדל"ן',
    "6552": 'ייזום נדל"ן',
    "6770": "חברת רכישה ייעודית (SPAC)",
    "6792": "תמלוגי נפט וגז",
    "6794": "רישוי פטנטים ומותגים",
    "6795": "תמלוגי כרייה",
    "6798": 'קרן השקעות בנדל"ן (REIT)',
    # ---- 73: "business services" is where software lives ---------
    "7370": "שירותי מידע ועיבוד נתונים",
    "7371": "פיתוח תוכנה בהזמנה",
    "7372": "תוכנה",
    "7373": "אינטגרציית מערכות מחשוב",
    "7374": "עיבוד נתונים ואירוח",
    "7389": "שירותים עסקיים",
    "7311": "פרסום ושיווק",
    "7310": "פרסום ושיווק",
    "7350": "השכרת ציוד",
    "7359": "השכרת ציוד",
    "7363": "השכרת כוח אדם",
    "7361": "השכרת כוח אדם",
    "7812": "הפקות קולנוע וטלוויזיה",
    # ---- 36 is components; an appliance is not a component -------
    "3613": "ציוד חשמל תעשייתי",
    "3630": "מוצרי חשמל ביתיים",
    "3651": "מוצרי אודיו ווידאו",
    # ---- 50/51 wholesale, where the goods matter -----------------
    "5122": "סיטונאות תרופות",
    "5150": "סיטונאות תוצרת חקלאית",
    "5159": "סיטונאות תוצרת חקלאית",
    # ---- retail, where "miscellaneous" says nothing --------------
    "5661": "קמעונאות הנעלה",
    "5731": "קמעונאות אלקטרוניקה",
    "5944": "קמעונאות תכשיטים",
    "5961": "קמעונאות מקוונת ובהזמנה",
    # ---- the rest ------------------------------------------------
    "2011": "עיבוד בשר ועוף",
    "2015": "עיבוד בשר ועוף",
    "2080": "משקאות",
    "2851": "צבעים וציפויים",
    "3743": "ציוד רכבות",
    "6361": 'ביטוח זכויות בנדל"ן',
    # ---- the rest, where the group reads oddly -------------------
    "1040": "כריית זהב וכסף",
    "1311": "הפקת נפט וגז",
    "1381": "קידוח נפט וגז",
    "1389": "שירותי שדות נפט",
    "2911": "זיקוק נפט",
    "4813": "תקשורת קווית",
    "4812": "תקשורת סלולרית",
    "4832": "שידורי רדיו",
    "4833": "שידורי טלוויזיה",
    "4841": "טלוויזיה בכבלים",
    "4911": "חברות חשמל",
    "4922": "הולכת גז טבעי",
    "4924": "חלוקת גז טבעי",
    "4931": "חשמל ותשתיות",
    "4941": "אספקת מים",
    "4953": "טיפול בפסולת",
    "5812": "מסעדות",
    "5912": "קמעונאות תרופות",
    "8731": "מחקר ופיתוח",
    "8742": "ייעוץ ניהולי",
}

#: SIC major group (first two digits) → Hebrew. 71 of these cover every
#: ticker in the bundle, which is what makes the English fallback below
#: a guard rather than a routine path.
BY_GROUP: dict[str, str] = {
    "01": "גידולים חקלאיים",
    "02": "חקלאות ובעלי חיים",
    "07": "שירותי חקלאות",
    "08": "ייעור",
    "09": "דיג",
    "10": "כריית מתכות",
    "12": "כריית פחם",
    "13": "נפט וגז",
    "14": "כריית מינרלים",
    "15": "בנייה וקבלנות",
    "16": "תשתיות ובנייה כבדה",
    "17": "קבלנות מתמחה בבנייה",
    "20": "מזון ומשקאות",
    "21": "טבק",
    "22": "טקסטיל",
    "23": "ביגוד",
    "24": "עץ ומוצריו",
    "25": "רהיטים",
    "26": "נייר ומוצריו",
    "27": "דפוס והוצאה לאור",
    "28": "כימיקלים",
    "29": "זיקוק נפט",
    "30": "גומי ופלסטיק",
    "31": "עור ומוצריו",
    "32": "חומרי בניין וזכוכית",
    "33": "מתכות בסיסיות",
    "34": "מוצרי מתכת",
    "35": "מכונות תעשייתיות",
    "36": "רכיבים אלקטרוניים",
    "37": "רכב וציוד תחבורה",
    "38": "מכשור ומדידה",
    "39": "תעשייה מגוונת",
    "40": "רכבות",
    "41": "תחבורה ציבורית",
    "42": "הובלה ולוגיסטיקה",
    "44": "תחבורה ימית",
    "45": "תעופה",
    "46": "צינורות הולכה",
    "47": "שירותי תחבורה",
    "48": "תקשורת ושידור",
    "49": "חשמל, גז ותשתיות",
    "50": "סחר סיטונאי — ציוד",
    "51": "סחר סיטונאי — מוצרי צריכה",
    "52": "קמעונאות חומרי בניין",
    "53": "חנויות כלבו",
    "54": "קמעונאות מזון",
    "55": "סחר ברכב ותחנות דלק",
    "56": "קמעונאות ביגוד",
    "57": "קמעונאות רהיטים",
    "58": "מסעדנות",
    "59": "קמעונאות מגוונת",
    "60": "בנקאות",
    "61": "אשראי ומימון",
    "62": "ברוקראז' ושוקי הון",
    "63": "ביטוח",
    "64": "סוכנויות ביטוח",
    "65": 'נדל"ן',
    "67": "חברות החזקה והשקעה",
    "70": "בתי מלון ואירוח",
    "72": "שירותים אישיים",
    "73": "שירותים עסקיים",
    "75": "שירותי רכב",
    "76": "שירותי תחזוקה",
    "78": "קולנוע ווידאו",
    "79": "בידור ופנאי",
    "80": "שירותי בריאות",
    "81": "שירותים משפטיים",
    "82": "חינוך והכשרה",
    "83": "שירותים חברתיים",
    "87": "הנדסה וייעוץ",
    "89": "שירותים אחרים",
    "99": "לא מסווג",
}


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
