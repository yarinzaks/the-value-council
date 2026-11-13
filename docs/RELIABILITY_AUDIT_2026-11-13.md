# דוח אמינות סופי — The Value Council

**תאריך:** 2026-11-13 · **HEAD:** `1622e85b` (main) · **היקף:** 70 תכונות נמדדו בארבע שכבות, כל ממצא עבר ביקורת ספקן עצמאית.

**השורה התחתונה:** המערכת **אינה אמינה כרגע** ואין לעבור לשלב הסוכנים. 51 מתוך 70 תכונות שבורות. שתי תכונות קריטיות שבורות (כללי המכירה של כל הסוכנים אינם מורצים; הפונדמנטלס קפואים מאז 2026-04-28), העמלה שונה מעמלת הברוקר פי ~6.4, פנקס העסקאות אינו רישום של ביצועים, ופוזיציה אחת (LEG) יושבת בספר במחיר בן 78 יום.

> סימון ראיות: **VERIFIED** = פקודה הורצה ותוצאה צוטטה (על ידי המבקר ואומתה מחדש על ידי הספקן). **UNVERIFIED** = לא ניתן היה למדוד offline; נאמר מה יאמת. אף תכונה שהוגדרה UNVERIFIED לא שודרגה ל-HOLDS אלא בניסוי שהורץ בפועל (RK-08, ראו הערה שם).

---

## טבלת סיכום

| שכבה | תכונות שנבדקו | מחזיקות (HOLDS) | שבורות (BROKEN) | לא מאומתות |
|---|---|---|---|---|
| מנוע (engine: trades + events) | 18 | 7 | 11 | 0 |
| נתונים (data: prices + fundamentals) | 20 | 8 | 12 | 0 |
| אסטרטגיה (strategy fidelity) | 19 | 0 | 19 | 0 |
| רישום (bookkeeping) | 13 | 4 | 9 | 0 |
| **סה"כ** | **70** | **19** | **51** | **0** |

ברמת התכונה אין פריטים UNVERIFIED; בתוך תכונות רבות יש תת-טענות UNVERIFIED (רובן דורשות רשת: EDGAR, yfinance, GitHub Actions). הן מרוכזות בסעיף 5.

חומרה (אחרי תיקוני הספקן) מתוך 51 השבורות: קריטי 2 · גבוה 20 · בינוני 24 · נמוך 5.

---

## 1. שכבת המנוע (engine)

### 1.1 מה מחזיק

| ID | תכונה | ראיה |
|---|---|---|
| ENG-1a | אין שבר מניה באף פוזיציה נוכחית (13 ספרים, 250 פוזיציות) | VERIFIED. `frac.py` על `git show HEAD:data/portfolios/*.json` → `TOTAL positions 250 bad 0`. `portfolio.py:458` `math.floor(max_notional / price)`, `:257` `math.floor(wanted)`. |
| ENG-3 | מזומן אף פעם לא שלילי; `total_nav == cash + Σ(shares × current_price)` בכל גרסת ספר שאי פעם נעשה לה commit | VERIFIED. `eng3_walk.py` על 723 commits / 3,984 blobs: `negative_cash_count 0, nav_mismatch_count 0, max_nav_dev $0.0097`. 1,515 snapshots: `nav == cash + invested` בתוך $0.01. |
| ENG-4 | ריצה שנייה באותו יום לא סוחרת פעמיים | VERIFIED. 52,714 שורות יומן: 0 כפילויות ב-(agent, day, ticker, decision) לביצועים; 844 שורות פנקס: 0 כפילויות. שומר ב-`runner.py:988`. הסתייגות: 675 כפילויות של שורות **כוונה** (BUY/WATCH) מ-2026-05-06..08-05, לפני התיקון. |
| ENG-6b | המתכנן whole-share אף פעם לא פותח שם שמניה אחת ממנו עולה על ה-slot | VERIFIED. 68 פתיחות מאז 2026-11-02 הורצו מחדש דרך `reachable_targets` — 0 הפרות; מרווח הדוק ביותר 9.4% (john_neff LII). חולשה: שם שנשמט לא מותיר שום עקבה ביומן, רק ב-log. |
| CE-2 | אף ספר לא מחזיק מנפיק תחת שני סימולים; אין פוזיציה קפואה תחת סימול שפרש | VERIFIED כעת (0 כפילויות; ASGN לא מוחזק). היסטורית שבור: ASGN+EFOR הוחזקו יחד ב-4 ספרים 2026-05-06..08-07, תוקן ידנית ב-`f897dc47`. המנוע **אינו** עוקב אחר rename בעצמו — רק `scripts/migrate_ticker.py` ידני. |
| CE-3a | כל דיבידנד עם ex-date בחלון ההחזקה שולם, אף אחד לא פעמיים, אף אחד לא על מניות שנקנו אחרי ex | VERIFIED עד אופק ה-cache המקומי (2026-10-30): `TOTAL missed=0`, 0 כפילויות; replay של 150 זיכויים היסטוריים — 150/150 תואמים `shares × amount` במספר המניות ביום ה-ex. 9 זיכויים אחרי 10-30 — UNVERIFIED (ה-cache המקומי נגמר). |
| CE-4 | אין שורת יומן/פנקס/snapshot בסוף שבוע או חג אחרי תיקון 2026-10-31 | VERIFIED. `ce4_verify.py` עם `core.market_calendar.why_closed`: אחרי התיקון 0/0/0. לפני: 2,157 שורות יומן, 42 שורות פנקס, 55 snapshots בימי סגירה. עדיין אין חג אמיתי אחרי התיקון (הבא: 11-26). |

### 1.2 מה שבור

**ENG-2 — העמלה אינה עמלת הברוקר.** חומרה: גבוהה. VERIFIED.
`core/live/portfolio.py:31` `DEFAULT_COST_BPS = 0.001` (0.1% מהברוטו, שני הצדדים). `grep cost_bps scripts/ .github/` — אין override. סכום `cumulative_costs` ב-13 הספרים: **$876.98**. אירועי שינוי מניות בהיסטוריית git: 2,260 → ב-$2.50 לפקודה = **$5,650.00**, פער $4,773 (פי 6.4). לפי ספר: neff 342 אירועים / $83.59 במנוע / $855 בברוקר; dreman 300 / $86.70 / $750; marks 283 / $103 / $707. $2.50 על slot טיפוסי של $333 = 75bps לצד, לא 10. אף שורת פנקס (844) לא נושאת `cost_paid == 2.50`.

**ENG-7 — פנקס העסקאות אינו רישום ביצועים.** חומרה: בינונית. VERIFIED.
HEAD `data/trades`: 844 שורות = 810 `reconstructed` (כולן SELL, `cost_paid 0.0`) + 34 `caught-up`, **0 `executed`**. הרץ קורא ל-`record_day` מאז 2026-09-03 (`f1ec18ef`) אבל ה-workflow לא עשה `git add data/trades` עד `9558bc0f` (2026-11-13 14:06, אחרי הריצה האחרונה). `git log --author=github-actions -- data/trades | wc -l` → 0. בנוסף: 122 עותקי iCloud בשם `<day> 2.json` ש-`read_ledger` (`trade_ledger.py:202`, glob `*.json`) קורא → 1,088 שורות במקום 844 בריצה מקומית.

**ENG-5 — רצפת החזקה של 30 יום לא נאכפת במסלול ה-trim.** חומרה: בינונית. VERIFIED.
`_rebalance` (`runner.py:1494-1531`) בונה trims מ-drift בלבד, בלי `pos_age_days`. נמדד: 12 שורות TRIM, 2 על פוזיציות בנות פחות מ-30 יום — market_core BAC (7 ימים, 2026-11-09, 4→3 מניות) ו-seth_klarman TTD (11 ימים, 2026-11-06, 54→42). 22 יציאות מלאות ב-2026-08-07 מתחת לרצפה — PLAUSIBLE שהריצה עשתה checkout לפני merge של PR #15; UNVERIFIED (דורש GitHub Actions API).

**ENG-6a — פוזיציות חורגות מרצועת ±25% ואין מזומן לסגור under-weights.** חומרה: נמוכה. VERIFIED (עם תיקון הספקן).
לפי הגדרת המנוע (יעדים אחרי `reachable_targets`, רק שמות ברשימת היום): schloss 1/4 (FOR -26%), buffett 1/8 (NEU -46%), neff 11/27, marks 11/18, dreman 5/23, klarman 1/19. מסלול ADD צריך מזומן והספרים ריקים (klarman $4.83, dreman $6.19, marks $8.26, neff $14.21). **תיקון למקור:** BUR של schloss בתוך הרצועה (יעד 25% אחרי שמיטת SEB, לא 20%).

**ENG-1b — שברי מניה בגרסאות ספר היסטוריות.** חומרה: בינונית. VERIFIED.
4,071 גרסאות ספר ב-364 commits: 40,547 שורות שבריות, 2026-08-07 (ריצת open `1421cc78`) עד 2026-10-31, כל 13 הספרים. הסתיים ב-`786c1878` + `76405a9d` "floor the thirteen books". פנקס: 224 שורות שבריות (192 reconstructed, 32 caught-up), אף אחת אחרי 10-30. ה-32 caught-up הן ביצועים אמיתיים של לוטים שבריים (SEB 0.428104, ABM 0.038831, GIS 0.236022) — פקודות שאי אפשר להציב בברוקר.

**CE-0 — ה-sqlite המקומי אינו ה-cache שהריצות החיות מתמחרות ממנו.** חומרה: נמוכה (תנאי מקדים לביקורת). VERIFIED.
`max(trade_date)` = 2026-11-02 (רק SPY/QQQ), 185/186 שמות מוחזקים נגמרים 2026-10-30; `max(ex_date) dividends` = 2026-10-30. הספרים: `last_close_date 2026-11-12`. הריצות רצות ב-GitHub Actions עם `actions/cache`. כל מה שאחרי 10-30 נמדד מול `data/prices/*.json`.

**CE-1a / PD-09 / SF-X4 — LEG: פוזיציה קפואה 78 יום.** חומרה: גבוהה. VERIFIED (שלוש זוויות, מסקנה אחת).
seth_klarman: LEG 17 מניות, entry 2026-08-16 (**יום ראשון**) @ 9.66, `current_price 9.20`. בר אחרון: sqlite 2026-08-26, `data/prices/LEG.json` 2026-08-27, אין שורה ב-`no_price_data`. הספר נשא 9.2 ב-commits של 09-15, 10-15, 11-12. ביומן: LEG רק ב-08-16/08-17. ה-log של 2026-10-31: `"2026-10-30: cannot sell LEG — no fresh price; holding position"` (פעמיים). `MAX_CARRY_FORWARD_DAYS=5` → `get_price_on` מחזיר None → `mark_to_market` שומר mark אחרון, `runner.py:1139-1144` מסרב למכור — לנצח. `scripts/settle_cash_merger.py` בעצמו מתעד את הבור הזה ולא הופעל. אין דגל "unpriced" ב-dashboard (רק `isStale` ברמת סוכן). $156.40 = 1.34% מ-NAV. מה קרה ל-LEG — UNVERIFIED (רשת). אין שורת פנקס ל-LEG בכלל. ווליום בבר האחרון 13.66M מול ממוצע 2.0M — רמז לפעולה תאגידית.

**CE-1b — נמכרו/נקנו ב-marks ישנים; הפנקס רושם יציאות במחיר שלא התקבל.** חומרה: גבוהה. VERIFIED (והספקן החמיר).
היסטורי: ASGN (בר אחרון 2026-06-18 @ 19.08) נקנה ונמכר ב-howard_marks 4 פעמים ב-19.08 בדיוק (06-23/25, 06-26/07-15, 07-16/21, 07-23/08-10), כש-snapshots אומרים `trade_count 0`. THR: פנקס `SELL 4 @ 61.14, realized -32.60` (close של 05-29), אבל commit `f897dc47` הזיז cash 242.86→498.42 = 4×$63.89 (תמורת מיזוג). **ממצא הספקן:** 0 מ-45 שורות הפנקס אחרי 2026-10-31 תואמות את מחיר היציאה ביומן (DXC פנקס 13.78 vs יומן $14.48; CPB 23.13 vs $22.70; MSFT 520.99 vs 553.8); reconciliation מזומן של buffett מאשר את מחיר היומן (44.64 + 60×22.70×0.999 − 16×83.5×1.001 = 67.94 ✓). כלומר: הרץ כן מוכר בציטוט טרי, אבל **הפנקס המשוחזר רושם את ה-mark הקודם**, ישן וחדש. הודעת commit `62fffd07` מודה בזה.

**CE-3b — `paid_dividends` נושא מפתחות לפוזיציות שכבר לא מוחזקות.** חומרה: נמוכה. VERIFIED.
13 מפתחות (7 orphans: SEB×2, GD, COST, LLY, MU, KNSL; 6 out-of-window: JNJ, JPM, ORCL, ABBV, REPX, LII). הסיבה: `76405a9d` (`scripts/floor_fractional_lots.py`, בלי `paid_dividends` בכלל) סגר לוטים תת-מנייתיים בלי ה-`pop()` ש-`sell()` עושה (`portfolio.py:266-270`). אין השפעה על מזומן.

**CE-3c — טבלת הדיבידנדים לא שלמה ו-CTVA ביחידות שגויות.** חומרה: בינונית. VERIFIED.
פערים >180 יום ברבעוניים: ACN 273d, AGO 294d, ALV 180d, SIG 273d, TGLS 274d, FBP 182d — כולם לפני רצפת 2026-08-10, אין חסר-תשלום עדיין. **CTVA:** דיבידנד 0.18 (2025-12-01) → 0.027007 (2026-03/06/09) = ×0.15, אותו גורם של קפיצת המחיר 67.03→10.17 ב-2026-01-02. dreman (30 מניות) ו-marks (33) נכנסו 2026-10-01 — ב-ex-date הבא (~12-01) יזוכו ~$0.81 במקום ~$5.40. UNVERIFIED מה הדיבידנד המוצהר.

**CE-5 — ההיסטוריה ב-cache לא ביחידה אחת; למנוע אין טיפול ב-split.** חומרה: בינונית. VERIFIED.
קפיצות ב-close **וגם** ב-adj_close: BUR 03-26→03-27 7.83→4.14, CTVA 2025-12-31→2026-01-02 67.03→10.17, EFOR/ASGN 04-22→04-23 40.43→19.53. `_write_cache` עושה `INSERT OR REPLACE` רק לחלון שנמשך — בניגוד ל-docstring ב-`data_loader.py:485-487`. `data/prices/{BUR,CTVA,EFOR}.json` נושאים את הקפיצה ל-dashboard. השפעה על ספרים היום: 0 (כל המחזיקים נכנסו אחרי הקפיצה). `grep -i split core/live/portfolio.py` → רק שורה 108 (timestamps). ה-split הראשון בתוך חלון החזקה יכפיל/יחצה NAV של השורה.

---

## 2. שכבת הנתונים (data)

### 2.1 מחירים

**מחזיק:**

| ID | תכונה | ראיה |
|---|---|---|
| PD-01 | לכל שם מוחזק יש בר בסשן המלא האחרון; אף שם מוחזק לא ב-`no_price_data` | VERIFIED. 186 שמות, `held in no_price_data: set()`, 185/186 עם בר ב-2026-10-30 (היוצא: LEG). |
| PD-02 | היקום שהסורק באמת סורק מכוסה ב-cache | **REFUTED → HOLDS.** המקור מדד את `full_market_universe_index.json` הגולמי (6,601, 47% לא מתומחרים). הרץ בונה `FullMarketUniverse(require_current_listing=True)` (`runner.py:694`) → 3,492 שמות, 3,466 (99.3%) עם ברים בשלושת הסשנים, 20 (0.6%) בלי אף אחד (5 ב-`no_price_data`, 15 כנראה delisted — UNVERIFIED). |
| PD-04 | ביצוע ו-marking משתמשים באותה עמודה (adj_close) | VERIFIED על 250/250 פוזיציות: `current_price == data/prices/<T>.json` נקודה אחרונה (2026-11-12). "fills באותו מחיר" — קריאת קוד בלבד (אין fills `executed` בפנקס). |
| PD-07 | ציטוט intraday מוחלף ב-close מסודר | VERIFIED. AAPL 2026-11-12: 358.625 (open 15:53Z) → 355.72 (19:04Z) → 354.88 (21:33Z). **הסתייגות חדשה:** SPY/QQQ מתפרסמים ב-`data/prices` אבל לא מוחזקים → לא מרועננים ב-16:30; הערך ליום הנוכחי הוא ציטוט ~10:50 ET עד ריצת ה-open הבאה. ה-dashboard "vs S&P 500" משווה NAV סגור ל-benchmark intraday ל-~24 שעות (ולאורך סוף שבוע). |
| PD-10 | `data/prices/*.json` מסכימים עם ה-cache | VERIFIED 176/186; 9 שמות (C, CAG, CSV, FHI, MSM, MTDR, MWA, RUSHA, SIRI) שונים בגורם קבוע = re-adjustment דיבידנדי אחרי 10-30, לא פגם ייצוא. |

**שבור:**

**PD-05 — סדרת מחירים לא ביחידה אחת לאורך שנים.** חומרה: גבוהה. VERIFIED.
`get_price_on` מושך לפי שנה קלנדרית (`data_loader.py:761-766`); כל שנה מתואמת ליום משיכתה, ישנות לא משוכתבות. APH: 2025-12-31 close 135.14 → 2026-01-02 69.86 (2:1 חל רק על 2026). `data/prices/APH.json` (משיכה אחת ב-CI) ל-2025-11-12: 70.58 vs cache 141.15 — יחס 0.5 בדיוק על 34 ברים. RUSHA: 3:2 לא משוכתב. גבול 2025-12-31→2026-01-02: 86 קפיצות >60% מתוך 5,186 vs 8-14 בגבולות אמצע שנה; 31 עם חתימת split. טבלת הדיבידנדים באותו פגם (APH 0.25→0.125). 94/329 סדרות מפורסמות לא מסכימות עם ה-cache בגורם קבוע. כל `trailing_return`/`momentum`/`volatility` שחוצה גבול שנה שגוי בגורם ה-split.

**PD-03 — ה-cache המקומי אינו ה-production cache.** חומרה: בינונית. VERIFIED. `VALUE_COUNCIL_DATA_DIR: ${{ github.workspace }}/data`, `actions/cache@v4` מפתח `prices-v2-<market>-<run_id>`. ה-artifact היחיד: `data/cron_logs/daily.*.log` ל-30 יום. היסטוריית המחירים של הספר החי לא ניתנת לביקורת מהריפו.

**PD-06 — `entry_price` עמוס סמנטית ו-`entry_date` בסופי שבוע.** חומרה: בינונית. VERIFIED.
167/172 fills ראשונים בתוך [low, high] של יום הכניסה (5 החריגים מ-commit הבראשית `0c677a40` = close של יום קודם). 75/250 פוזיציות עם `entry_price` שונה מה-fill הראשון (cost-averaging: `portfolio.py:476-484`). 15 פוזיציות עם `entry_date` בסוף שבוע (14 של pabrai שבת 08-15 מ-`aaaabe83`, LEG ראשון 08-16). `data/prices` ב-adj_close vs entry גולמי → `PositionPriceChart.tsx:112` מצייר marker ביחידה אחרת מהקו (GIS 05-06 close 35.07 / adj 33.83).

**PD-08 — אין timestamp כתיבה בשורות ה-cache.** חומרה: נמוכה. **UNVERIFIED → BROKEN** (מדיד ישירות מהסכמה). `prices(ticker, trade_date, open, high, low, close, adj_close, volume)` — אין `fetched_at`; רק `no_price_data.checked_at`.

**PD-11 — הפנקס רושם שברי מניה.** חומרה: בינונית. VERIFIED. 224/844 שורות (26.5%), 13 סוכנים. 618/620 שורות שלמות עם `cost_paid 0.0`. (חופף ENG-1b / RK-11.)

### 2.2 פונדמנטלס

**מחזיק:**

| ID | תכונה | ראיה |
|---|---|---|
| FUND-1 | Point-in-time: אף fact עם `filed > as_of` | VERIFIED. 440 טעינות (150 טיקרים × 4 תאריכים) נגד עותק של ה-sqlite האמיתי: 0 look-ahead ב-`source_filing`, ב-facts, וב-TTM. `edgar_cache.py:264`; `point_in_time.py:499`; `ttm.py:188`. 50 payloads cached שונים מחישוב טרי (staleness של נוסחה, לא look-ahead). UNVERIFIED: בדיקת poisoning מלאה. |
| FUND-3 | כיסוי: לכל חבר יקום ולכל החזקה יש פונדמנטלס | VERIFIED. 3,726 (ברירת מחדל) / 3,490 (live) חברים, 0 ללא parquet; 186/186 מוחזקים מחזירים `PointInTimeFinancials`. |
| FUND-7 | יחידות וסימן: USD, A=L+E, EPS×shares≈NI, דיבידנדים/capex חיוביים | VERIFIED על 186: 0 mismatch יחידות, 0 שליליים, 0 "באלפים". A=L+E עם NCI: 21/137 פער >1% — כולם מתיישבים ל-cent מול `MinorityInterest` חוץ מ-VRTS (2.4%). `total_liabilities=None` ב-49/186 (26%) — פגם כיסוי, לא יחידות. |

**שבור:**

**FUND-2 — הפונדמנטלס קפואים מאז 2026-04-28.** חומרה: **קריטית**. VERIFIED (כולל בפלט החי).
8,290 קבצי parquet, כולם mtime 2026-04-28. גיל ה-filing החדש ביותר ל-3,726 חברי יקום: min 199d, חציון 259d, max 525d, `<=120d: 0`. ההפרכה ("ה-Mac לא מה שרץ ב-CI") נכשלה: `net_income` ביומן ההחלטות (מסונכרן מ-GHA) חושב מחדש מה-cache המקומי → **14,547/14,547 תואמים, 0 אי-התאמות**. 1,388 החלטות (785 BUY) על 24 טיקרים עם 10-K בן >395 יום בזמן ההחלטה (EBF 476d, DXC 474d, UVV 459d, CAG 418d). כל החלטה חיה מאז ~מאי 2026 חסרת 2-3 רבעוני 10-Q. **תיקון למקור:** מסלול refresh קיים (`prefetch_full_us_market.py --use-calendar`, `cache-refresh.yml` שבועי) — הוא פשוט לא מנחית facts טריים. למה — UNVERIFIED (release asset `cache-latest`, לוגים — רשת). אין freshness gate ב-`runner.py`.

**FUND-4 — D/E=0 על חברות עם חוב.** חומרה: גבוהה. VERIFIED.
`_compute_total_debt` קורא 6 concepts בלבד; `has_complete_balance_sheet` הופך היעדר לאפס. 10 החזקות (הספקן הוסיף COLL) עם חוב בתגים שלא נקראים: FISV `DebtAndCapitalLeaseObligations` $28,997M (liab/eq 2.11), APH $14,565M, SIRI $8,648M, HRI $7,958M (6.07), WH $2,560M (7.94), RPD convertible $892M (10.16), ALRM $490M, GOLD $300M, CRTO $37M, COLL $809M (4.49). ה-`why_en` בספר klarman: `"HRI ... D/E 0.00"`, `"RPD ... D/E 0.00"`; `klarman/filters.py:37` `DEFAULT_MAX_DE = 0.7` — נכנסו רק על האפס השקרי. GM — UNVERIFIED.

**FUND-5 — שווי שוק = מחיר × מניות ביחידות split שונות.** חומרה: גבוהה. VERIFIED.
11 סוכנים: `price * fin.shares_outstanding` (`prefer_annual=True`), מחיר split-adjusted. היסטורי: NVDA 2024-06-05 $306bn vs $3,007bn (×9.84), WMT ×3.0, AVGO ×9.94, AAPL 2020 ×3.85. חי היום: BMNR 10-K 384M מניות vs 10-Q 538M → mcap מוערך 28.6% נמוך (howard_marks). רק market_core משתמש ב-`shares_known_at`. כל `min_market_cap` ב-`runner.py:407-504` וכל backtest של 11 הסוכנים בנוי על זה.

**FUND-6 — מאזן 10-K ישן נבחר על פני 10-Q חדש, ו-`source_filing` מטעה.** חומרה: בינונית. VERIFIED. 50/182 החזקות: AAPL 10-K pe 2025-09-27 כש-10-Q pe 2025-12-27 ב-cache; CALM pe 2025-05-31 vs 2026-02-28 (3 רבעונים). ה-payload ב-sqlite תחת accession של 10-Q נושא ערכי 10-K.

**FUND-8 — גלאי ה-split מחזיר ספירות לא שפויות (הסיבה לא מה שחשבו).** חומרה: בינונית. VERIFIED, סיפור סיבתי **מתוקן**. AOS=75.7M (אמיתי ~141M), FOXA=**1**. `detect_splits` החזיר `[]` לשניהם: ל-AOS סדרת dei נגמרת 2015-08-10 (ה-split של 2016 לא נראה), ל-FOXA שורת dei אחת עם ערך 1 (2019). false positive אמיתי: **RYN** 2026-02-23 ×2.0 ללא חצייה במחיר (22.66→21.95) = הנפקה. "6 מתוך 186" — לא שוחזר (24/179 מול weighted-average).

**FUND-9 — רשימת ה-filings שהלוודר החי קורא לא מסכימה עם ה-facts.** **REFUTED → BROKEN** (המקור אמר HOLDS). חומרה: בינונית (הועלתה). VERIFIED. 10 החזקות (ABBV, AMZN, CVX, GOOGL, JNJ, KO, META, MSFT, NVDA, WMT) — ה-sqlite נושא רק 5 שורות סינתטיות `FMP-<T>-FY-...` בלי 10-Q. `PointInTimeLoader.list_filings` מחזיר cache ללא תנאי (`point_in_time.py:481-484`). הלוודר החי בוחר ל-MSFT `10-K 2025-07-30 FMP-MSFT-FY-2025-06-30` כשה-parquet היה בוחר `10-Q 2026-01-28` (שני רבעונים); JNJ רבעון. מקור השורות: כנראה `agents/greenblatt/run_validation.py` עם `FMPAdapter` — UNVERIFIED.

---

## 3. שכבת האסטרטגיה (strategy fidelity)

**אף סוכן לא עושה את מה שה-playbook שלו אומר. 19/19 שבור.** הדפוס החוזר (SF-X1): לאף סוכן אין לוגיקת יציאה משלו; ה-`select()` של graham, schloss, buffett, klarman, fisher, neff, dreman, marks, market_core מתעלם מ-`held`; המכירה היחידה במנוע היא "השם לא ברשימת היעד של היום" אחרי רצפת 30 יום (`runner.py:1083-1152`, `:1675` `criteria_met=["left today's target list"]`).

### 3.1 תכונות רוחביות

**SF-X1-EXITS — אף כלל מכירה מה-playbooks לא מורץ.** חומרה: **קריטית**. VERIFIED.
823 שורות EXIT/SELL/TRIM (בלי כפילויות iCloud): `criteria_met` הם בדיוק 3 מחרוזות — `'rotated out of target portfolio'` 503, `"left today's target list"` 308, `'drifted outside the 25% rebalance band'` 12. `exit_trigger` = None ב-823/823, בעוד 26,353/28,536 שורות BUY נושאות `exit_trigger`. אף יציאה לא מצטטת target price, Graham Number, intrinsic value, P/E-vs-market או שבר פונדמנטלי. `grep last_forced_exits` → רק `agents/council/strategy.py`. `lynch/garp.py` כותב `last_exits` שאיש לא קורא. יציאות אחרי 2026-08-05: neff 51, dreman 53, marks 44, schloss 43, klarman 37, buffett 17, fisher 15, greenblatt 13, lynch 10, graham 9.

**SF-X2-CASH-POSTURE — מזומן שהדוקטרינה שומרת מוצא.** חומרה: גבוהה. VERIFIED. klarman מתכוון 92% deployed → ספר 99.96% ($4.83); marks 80% ב-Neutral → 0.08%. `plan_whole_share_buys` (`runner.py:231-303`) מאגם את כל המזומן ומוסיף מניות עד 1.25× slot (סימולציה: 96.1% מ-92% בריצה אחת); `_rebalance` ADD מוציא מזומן; שמות מחוץ ליעד תחת הרצפה תופסים הון. **תיקונים למקור:** Fisher אינו הפרה (180%→1.0 מתוכנן); `reachable_targets` **אינו** משכלל ל-100% (Σ=0.92 נשמר). Snapshots: klarman 12.9% מזומן מאי-אוגוסט → 0.0% תוך 4 ימי מסחר מ-08-07.

**SF-X3-TRIM-WINNERS — הרצועה 25% מקצצת מנצחים בניגוד לדוקטרינה.** חומרה: בינונית. VERIFIED. Lynch EXEL (Fast Grower, PEG 0.50, +51%) קוצץ 2026-11-03 כשהספר מחזיק 15 מפסידים (POWL -27%, CSV -24%...). `playbook.md:321-327` "do NOT trim it to rebalance". `rebalance_band` רק ב-`runner.py`, אין מתג לסוכן. Fisher/Buffett: 0 TRIM — הפרה רדומה.

**SF-X5-WHOLE-SHARE-REACH — ספר $10k לא יכול להחזיק את התיק שהדוקטרינה מתארת.** חומרה: גבוהה. VERIFIED. SEB ($3,721) יעד ל-graham, schloss, dreman — לא נגיש לשלושתם (slot $1,986 / $1,987 / $400). market_core: 8/25 לא נגישים (LLY $1,417 vs $393, COST $1,012 vs $140...), TSLA/XOM נגישים אבל אין מזומן. `reachable_targets` מעביר משקל לשורדים → graham 5 שמות ב-15.8-20.9% (cap 10% ב-`playbook.md:229`), schloss 5 ב-11.7-25.9% (cap 5%, `:286`). אין per-position cap ב-`core/live`.

**SF-X4 — ראו LEG לעיל (CE-1a).**

**SF-X6-LLM-OFF — החצי האיכותי של 5 ה-playbooks ההיברידיים לא מריץ החלטות.** חומרה: בינונית. VERIFIED. `--llm` ברירת מחדל `off`; `vars.LLM_MODE` ב-CI — UNVERIFIED. ממצא הספקן: memos **כן** נכתבים ביומן מאז 2026-10-30 (shadow), אבל ב-shadow אינם משנים בחירה; 90 ה-memos של 2026-10-31 אמרו WATCH/REJECT לכולם, והיומן רשם BUY ל-84 מהם ב-10-30 ו-76 ב-11-12. Lynch מחזיק CALM/RYN/PIPR/GRBK ש-memo סיווג Cyclical/WATCH.

**SF-X7-JOURNAL-COHORT — כוונות של benjamin_graham_2 נרשמות תחת benjamin_graham; pabrai לא רושם כוונות.** חומרה: נמוכה. VERIFIED. `net_net.py:99` `name = "benjamin_graham"`, `:357/:395` `agent=self.name`; `runner.py:436-441` לא משנה שם; `MohnishPabrai` ללא `decision_logger`.

### 3.2 לפי סוכן (כולם BROKEN)

| סוכן | חומרה | מה נמדד (VERIFIED) | מה חסר מול ה-playbook |
|---|---|---|---|
| **Graham** | גבוהה | ספר: 5 שמות Defensive ב-15.8-20.9%, מזומן 6.3%. יומן 11-12: 6 DEFENSIVE / 0 NET_NET (SEB לא נגיש). 823 יציאות — 0 מצטטות Graham Number/NCAV. NET_NET היסטורי: ACMR בלבד (26 שורות; 14 שורות 06-19 עם `P/NCAV=nan` — פגם נפרד). | cap 10%; מזומן 25-75%; מכירה ב-Graham Number / 2 שנים; P/E 3-שנתי; #3/#5; Enterprising; EPV. `playbook_audit.py` לא מחובר. |
| **Schloss** | גבוהה | ספר: 5 שמות 11.7-25.9%, מזומן $20.29. Snapshots: 30 שמות עד 2026-08-05, 3-8 אחרי (tangible P/B<0.75 + distressed + NI>=0 + $500M → 6 שורדים). BHF נמכר 11-12 ב-−2.6% "left today's target list". | 30-40 שמות ב-2-3%, אף פעם >5%; מכירה ב-40-60% רווח / 1.2× book; הוספה 50% בירידה 20%; **לא** למכור על ירידת מחיר; `min_years_public=0` במקום 15. |
| **Buffett** | גבוהה | 10 פוזיציות, מזומן 0.75%, משקלים 3.0-13.8%, 1/N. 12 מועמדים → playbook אומר 25%+ מזומן. CPB: BUY 11-06, WATCH (עובר 6 שערים, MoS 43%) 11-09, EXIT אותו יום. SFM נמכר 10-29 ב-−42.8%, נקנה מחדש 11-03. 18 מכירות ייחודיות, כולן <365 יום. | "forever"; conviction weights top-3 50-65%; cash posture; moats/management (memos ב-shadow). |
| **Klarman** | בינונית | 92% deployed מתוכנן → 99.96% בפועל. `select()` מעביר `held` רק ל-LLM. 34 EXIT, 0 מזכירות intrinsic value (RPD +61%, MPC +32%, SWKS +32% על rank-slip). LEG קפוא. | scale-out 80→110% IV; cash-as-residual; failure scenarios. פנקס/יומן לא עקביים (PCRX +43.8% ביומן, −0.2% בפנקס). |
| **Lynch** | נמוכה | **REFUTED → BROKEN** (המקור: HOLDS). 30 פוזיציות, 93.6% מושקע, 0 BUY עם PEG>1 ✓. אבל: 2 EXIT ב-2026-09-07 (ETD, CRS) שהמקור פספס. CRS: EPS 7.42 מ-3.70, CAGR 3-שנתי +155% — נמכר ב-−14% כי בסיס ה-5 שנים התגלגל ל-FY2021 (EPS −4.76) ב-08-19, ו-`exits.py` מתייג "no longer passes the quality gates". ארטיפקט חלון. יחזור כל אוגוסט. EXEL קוצץ (SF-X3). | רק 3 קטגוריות מ-6 בלי LLM. |
| **Fisher** | גבוהה | 5 מ-15 נקודות (1,3,5,6,13); 15 BUY כולם Tier A → 180% → 6.67%. 16 פוזיציות, מזומן 0.31%. 17 מכירות מאז 08-05, חציון 64.5 יום, CRM נמכר 09-07 (+33%) ו-11-04, נקנה מחדש פעמיים. | "never sell"; 12/15 + point 15; tiers A/B/C; מזומן 5-15%. |
| **Neff** | בינונית | soft score 35/70 במקום "all 7" (`DEFAULT_MIN_TOTAL_SCORE=35`); criterion 6 = 5 נקודות קבועות. 36 פוזיציות vs 30, מזומן 0.14%. 53 מכירות מאז 08-05, חציון 33 יום, min 2; 54 מכירות ≤7 ימים, 0 ≥365. | החזקה 2-4 שנים; trim ב-80% market P/E; ריכוז סקטור/top-10. "154 SELLs" במקור = 123 + 31 כפילי iCloud. |
| **Dreman** | בינונית | 2-of-4 + battery + Rule 18 ✓ (29 פוזיציות, 19 תעשיות, max 11.3%). פיננסים 60-67 יחד 33.2% (אין cap סקטור). 53 EXIT, כולן "left today's target list", `exit_trigger` None. | trim/exit ב-80/100% market P/E; add 25% בירידה 15%; hard stop. CTVA ביומן $9.47 P/B 0.26 — ראו CE-5. |
| **Marks** | בינונית | Neutral → 18 שמות / 80%; ספר 26 פוזיציות, 0.08% מזומן. **סיבה מתוקנת:** posture מתנדנד Cool↔Neutral 8 פעמים מאז 08-05 (55 Cool / 9 Neutral), הרצפה שומרת שמות של posture קודם → איחוד; מזומן 11.12% (11-05) → 0.08% (11-06). 47 מכירות, חציון 42 יום. אין intrinsic value בכלל. | scale-out 25/25/full; add on decline; cash by posture. memos כן נכתבים מאז 10-30. |
| **Greenblatt** | בינונית (ייתכן גבוהה) | 30/30, מזומן 4.3%, 365-day hold ✓. **דליפה אומתה:** `GreenblattLive._collect_targets` (`agent_adapter.py:184`) בונה יעדים רק מ-`top_scores` → שם מוגן שלא ניקד נעדר → נמכר. log 2026-10-31: `"1 held name(s) no longer scoring but still inside the holding period: CRMD"` → CRMD EXIT 10-30 ב-−26% אחרי 177 יום. אותו log: ריצות שבהן **כל 30** לא ניקדו. | floor $1B (wired $500M); Mode B לא קיים. |
| **Market Core** | בינונית | 25 BUY, ספר 17 פוזיציות; 8 לא נגישים, 2 ללא מזומן (SF-X5). | 25 שמות cap-weighted כפי שה-docstring מגדיר. |
| **Pabrai (Council)** | בינונית | **REFUTED → BROKEN** (המקור: UNVERIFIED; `~/Documents/Fintest/COUNCIL_SELECTION.md` נקרא). ✓ regime §9.1, cooling-off, לוח Feb/May/Aug/Nov, 5 fills של 11-02 בדיוק מהרשימה של 10-30. ✗ §6 "Nothing else sells": YELP 10-06, ZUMZ 10-26, CRTO/HOG/CLMB 10-30, NATR 11-10 נמכרו בין rebalances עם `"left today's target list"` — E8 רבעוני רץ יומית דרך `pipeline.run_selection`. ✗ SIG/REPX קוצצו 10-30 ע"י רצועת הרץ (שברי מניה, לא ביומן). "19 FILLs ב-11-02" = 5; 14/19 פוזיציות מ-08-15 (לפני כלל ה-cooling-off). | — |

---

## 4. שכבת הרישום (bookkeeping)

### 4.1 מחזיק

| ID | תכונה | ראיה |
|---|---|---|
| RK-07 | `snapshot.nav == total_nav` של הספר באותו commit | VERIFIED. 1,514/1,515 שווים ל-cent נגד הספר ב-commit שכתב את ה-snapshot; החריג: pabrai 2026-08-14 seed ידני (`agent: "the_council"`). 9 הפרשים ב-10-30 = catch-up של 10-31 (`4b0f6ad0`); "$0.01 ב-08-15" = ארטיפקט timezone. |
| RK-08 | ריצה חוזרת על אותו cache = אותן עסקאות | **UNVERIFIED → HOLDS (בהיקף הניסוי בלבד).** הספקן הריץ את הניסוי שהמקור הציע: 2 clones, רשת חסומה (proxy 127.0.0.1:9; 3,822 "Connection refused"), `--llm off --as-of 2026-11-02 --force` פעמיים → 9 עסקאות זהות ב-4 סוכנים, `diff -rq` על trades/decisions/snapshots/memos זהה; 13 ספרים נבדלים רק ב-`last_updated`/`last_open_run`. **לא** מכוסה: `--llm enforce`, רשת חיה, חציית חצות. `as_of 2026-11-12` היה מנוון (0/3490 מתומחרים). תופעת לוואי: greenblatt נכשל `"list index out of range"` (`runner.py:922`) דטרמיניסטית ב-11-12 — לא נחקר. |
| RK-10 | אין סודות ב-logs / workflow / data | VERIFIED. 0 התאמות לדפוסי token ב-4 logs, ב-`git grep HEAD -- data .github`, וב-`git log --all -p`. `SEC_USER_AGENT` = אימייל קשר (נדרש). UNVERIFIED: logs של GitHub Actions ברמוט. |
| RK-13 | בדיקות היחידה של התחום עוברות | VERIFIED. `159 passed, 8 skipped` (parametrize gating). תיקון ניסוח: NaN ביומן **נכפה** ל-null, לא נדחה. 3 קבצי בדיקה כפולים `test_* 2.py` לא מעוקבים — `pytest` חשוף יכול לאסוף אותם. |

### 4.2 שבור

**RK-01 — אף עסקה מבוצעת לא הגיעה לריפו כשורת `executed`.** חומרה: גבוהה. VERIFIED. דוגמה: commit בוט `f7c9cec2` (11-12 open) מסיר BHF 48 מניות מ-walter_schloss, מוסיף יומן ו-snapshot, `grep -c data/trades` ב-stat → 0. השורה היחידה: `reconstructed` ב-45.33 כשהיומן אומר exit $45.35. התיקון `9558bc0f` קיים; אף ריצת בוט אחריו — UNVERIFIED.

**RK-02 — שינויי מניות ללא שורת פנקס.** חומרה: גבוהה. VERIFIED. 2,235 שינויים; 0 שורות פנקס ללא שינוי ✓; 211 SELL ללא שורה — 202 מ-`76405a9d`, 9 של pabrai; **1,178/1,196 BUY ללא שורה** (backfill משחזר SELL בלבד). **שורש pabrai נסגר:** `backfill_trade_ledger.py` `_history()` מריץ `git log --reverse --follow` שמחזיר commit אחד ל-`mohnish_pabrai.json` (rename מ-`the_council.json`; בלי `--reverse` → 211) → dry run מדפיס 0. NATR 11-10 = החמצה של ריצה חיה (RK-01).

**RK-03 — הקוראים בפייתון רואים ימים פעמיים על ה-Mac.** חומרה: בינונית. VERIFIED. 147 עותקי iCloud `<day> 2.json` (122 trades, 14 decisions, 5 memos, 3 prices, 2 snapshots, 1 council), gitignored, 146 זהים, **1 שונה**: `data/decisions/benjamin_graham/2026-10-30 2.json` (SEB BUY נוסף). `read_ledger('david_dreman')` 139 vs 106; `load_snapshots('seth_klarman', root=data/snapshots)` 137 עם 11-06 פעמיים (גם pabrai). ה-dashboard מסנן (`day-files.ts:22`); הפייתון לא.

**RK-04 — יומן/פנקס/ספר לא מסכימים ב-30 הסשנים האחרונים.** חומרה: בינונית. VERIFIED. 103 שינויי ספר ללא שורת יומן, כולם עד 2026-11-02 (91 drifts שבריים, 12 top-ups שלמים ב-`3bd40db3`). התיקון `610045e8` (11-03, `_log_rebalance`) **כבר ב-main** ועובד (0 לא-תואמים מ-11-03); ההיסטוריה לא תוקנה. `except Exception: logger.debug` ב-`runner.py:1611, 1658, 1683` עדיין בולע. 3 SELL של pabrai ללא פנקס.

**RK-05 — rationale של ADD/TRIM ציין מניות שהספר לא לקח.** חומרה: **נמוכה** (הורדה). VERIFIED: 4 שורות, כולן 2026-11-03 (UVV 6.8852 vs +6; NUTX 1.0184 vs +1; AAPL 1.3442 vs +1; EXEL 1.2157 vs −1). הקוד **כבר תוקן** ב-`5eac0f5c`; 40 שורות ADD/TRIM מ-11-04 שלמות. ה-EXIT rationale עדיין בלי מספר מניות.

**RK-06 — snapshot חסר ל-2026-08-06; 55 snapshots בימי סגירה.** חומרה: בינונית. VERIFIED. 08-06: הריצה היחידה `8ac0c8bf` 00:51Z תייגה אותה 08-07 ונדרסה. 55 קבצים מעוקבים ב-05-25/06-19/07-03/09-07/08-15/08-16; 40 שורות פנקס בחגים + 2 בראשון. השומר קיים (21 tests ✓), ההיסטוריה לא נוקתה.

**RK-09 — ה-workflow לא עושה commit לכל רשומה.** חומרה: נמוכה. VERIFIED. `*.log` gitignored → `data/cron_logs` רק `.gitkeep`; artifact 30 יום רק `daily.*.log`. `data/memos` לא ברשימת ה-add. `git pull --rebase ... || true` ואז `git push` ללא תנאי.

**RK-11 — הרישום מכיל שברי מניה.** VERIFIED (= ENG-1b/PD-11). אין בדיקת אינווריאנט integrality.

**RK-12 — docstring של backfill מתעד `--dry-run` שלא קיים.** חומרה: נמוכה. VERIFIED: `error: unrecognized arguments: --dry-run`.

---

## 5. מה נשאר לא מאומת (UNVERIFIED) ומה יסגור את זה

| נושא | מה יאמת |
|---|---|
| מה קרה ל-LEG (CE-1a) | EDGAR 8-K/Form 25 אחרי 2026-08-27 (רשת) |
| BUR 03-27, CTVA 01-02, EFOR 04-23 — split/spin-off/שגיאת ספק | לוח splits של הספק (רשת) |
| דיבידנד CTVA האמיתי; 9 זיכויים אחרי 10-30 | הצהרת המנפיק / runtime cache מ-CI |
| למה `cache-refresh.yml` לא מנחית facts טריים (FUND-2) | `gh release view cache-latest`, לוגי ה-workflow |
| ערך `vars.LLM_MODE` | `gh variable list` |
| SHA ש-job 2026-08-07 15:19Z עשה לו checkout (ENG-5) | GitHub Actions API |
| הריצה הבאה כותבת `source=executed` (ENG-7, RK-01) | `git show <first bot commit after 11-13> --stat \| grep data/trades` |
| GM כאפס-שקרי (FUND-4); מקור שורות FMP (FUND-9) | סריקת תגים ידנית; `git log -S FMP-` |
| דטרמיניזם תחת `--llm enforce` / רשת חיה / חציית חצות (RK-08) | חזרה על הניסוי בתנאים אלה |
| SPY/QQQ ב-11-12 ≠ close אמיתי (PD-07) | diff של `data/prices/SPY.json` אחרי ריצת ה-open הבאה |

---

## 6. מה לתקן, לפי סדר

הסדר: קודם מה שמשקר על הכסף (עמלה, פנקס, מחיר קפוא), אחר כך מה שמחליט על בסיס נתונים שגויים (פונדמנטלס, splits, חוב), ורק אז הפער בין ה-playbooks לקוד. בכל פריט: קובץ + הבדיקה שתוכיח שתוקן.

1. **עמלה $2.50 לפקודה במקום 10bps** — `core/live/portfolio.py` (`buy`/`sell`: `cost = flat_commission`), `core/live/runner.py` (`DailyRunner` מעביר `commission_usd=2.50`; `plan_whole_share_buys`/`_rebalance` כוללים עמלה בבדיקת כדאיות). בדיקה: `core/live/tests/test_portfolio.py::test_commission_is_flat_per_order` ($5 ו-$5,000 שתיהן $2.50); `test_runner.py::test_one_share_trim_below_commission_is_skipped`. החלטה מתועדת אם לשחזר `cumulative_costs` רטרואקטיבית (~$4.8k).

2. **LEG: לסגור את הפוזיציה הקפואה ולהוסיף התראה** — `scripts/settle_cash_merger.py` או write-off אחרי בירור; `core/live/runner.py` (`_run_one`: אחרי 5 סשנים בלי בר → `logger.error` + `unpriced=True` על `Position`); dashboard מציג "unpriced". בדיקה: `core/live/tests/test_runner.py::test_held_name_without_bar_for_6_sessions_is_flagged_not_marked`; `scripts/tests/test_verify_run_state.py::test_unpriced_position_fails_verify`.

3. **הפנקס רושם ביצועים אמיתיים** — `.github/workflows/daily-paper-trading.yml` (ב-`9558bc0f`; לאמת בריצה הבאה), `scripts/verify_run_state.py` (ריצה ששינתה מניות חייבת קובץ פנקס), `core/live/trade_ledger.py:202`, `core/live/snapshots.py:143`, `core/backtest/decision_logger.py:294` (רק stem `YYYY-MM-DD`). בדיקה: `core/live/tests/test_trade_ledger.py::test_icloud_collision_copies_are_ignored`; `scripts/tests/test_verify_run_state.py::test_share_change_without_ledger_file_fails`.

4. **לתקן שורות פנקס משוחזרות שמשקרות** — `data/trades/peter_lynch/2026-08-07.json` (THR → 63.89 / −21.60, tag `cash-merger`), ASGN/EFOR 08-07/08-10 → `rename`, 4 round-trips של marks ב-19.08 → `frozen-price`; לתייג 2026-08-07..10-30 `fractional-era`; `scripts/backfill_trade_ledger.py` לוקח מחיר יציאה מ-`data/decisions` כשקיים. בדיקה: `scripts/tests/test_backfill_trade_ledger.py::test_reconstructed_exit_uses_journal_price_when_available` (CPB 11-09 → 22.70).

5. **לרענן פונדמנטלס ולהוסיף freshness gate** — `scripts/prefetch_full_us_market.py`, `.github/workflows/cache-refresh.yml`, `core/live/runner.py` (`max(filed)` ישן מ-120 יום → `raise`/skip). בדיקה: `core/live/tests/test_runner.py::test_run_refuses_when_fundamentals_older_than_120_days`; `scripts/tests/test_prefetch.py::test_is_due_true_for_fiscal_year_filer_past_deadline`.

6. **רשימת filings: לא להחזיר cache ללא תנאי; למחוק שורות FMP** — `core/backtest/point_in_time.py:481-484`, ניקוי 100 שורות `FMP-%` ב-`edgar_filings.sqlite`. בדיקה: `core/backtest/tests/test_point_in_time.py::test_stale_sqlite_filing_list_is_refreshed_from_parquet` (MSFT → 10-Q 2026-01-28).

7. **חוב: להרחיב concepts ולא להפוך היעדר לאפס** — `core/data/fundamentals_fetcher.py` (`_compute_total_debt` + roll-ups), `core/scoring/leverage.py` (`has_complete_balance_sheet`: liab/eq > 1.5 ללא תג → `None`). בדיקה: `core/data/tests/test_fundamentals_fetcher.py::test_fisv_debt_is_read_from_rollup`; `core/scoring/tests/test_leverage.py::test_high_liab_eq_without_debt_tag_returns_none` (HRI → None).

8. **שווי שוק ביחידות אחידות** — 11 `_market_cap` → helper ב-`core/research/splits.py` / `agents/market_core/shares.py`; `fundamentals_fetcher.py:206` להוריד `shares_outstanding` מ-`_STOCK_CONCEPTS`. בדיקה: `core/research/tests/test_splits.py::test_nvda_2024_06_05_market_cap_is_split_adjusted`; `::test_bmnr_uses_newest_cover_count`.

9. **גלאי split: רצפת שפיות + אימות מחיר + fallback** — `core/research/splits.py` (dei < 1,000 → ignore; יחס חייב תנועה הפוכה במחיר; dei ישן → `WeightedAverageNumberOfSharesOutstandingBasic`). בדיקה: `test_splits.py::test_foxa_single_dei_row_of_one_yields_none`, `::test_aos_falls_back_to_weighted_average_when_dei_stale`, `::test_ryn_2026_02_23_issuance_is_not_a_split`.

10. **cache מחירים עקבי לטיקר** — `core/backtest/data_loader.py` (`get_history`: refetch עם גורם קבוע → החלפת כל ההיסטוריה + `dividends`; `get_price_on` לא לפי שנה); בדיקת גבול יומית. לבנות מחדש APH, RUSHA, BUR, CTVA, EFOR + 86 המסומנים. בדיקה: `core/backtest/tests/test_data_loader.py::test_refetch_with_constant_factor_restates_full_history`.

11. **מאזן: 10-Q מלא מנצח 10-K ישן; `source_filing` מהעובדות** — `fundamentals_fetcher.py:331` (`prefer_annual=False` ל-instant), `point_in_time.py:563`. בדיקה: `test_point_in_time.py::test_calm_balance_sheet_is_10q_2026_02_28_and_source_filing_matches`.

12. **טבלת דיבידנדים: backfill מלא ו-CTVA** — `data_loader.py:268-290` (`_write_dividends` מהיסטוריה מלאה); לאמת CTVA לפני 2026-12-01. בדיקה: `test_data_loader.py::test_quarterly_payer_has_no_gap_over_110_days_in_holding_window`.

13. **כללי מכירה של הסוכנים מורצים** — `core/live/agent_adapter.py` (לקרוא `strategy.last_exit_decisions`), `core/live/runner.py:1675` (`_log_sell` רושם סיבה אמיתית), ואז לכל סוכן `select(held=...)` מחזיר retention: `agents/graham/net_net.py`, `agents/schloss/deep_value.py`, `agents/buffett/wonderful_business.py`, `agents/klarman/margin_of_safety.py`, `agents/fisher/quality_growth.py`, `agents/neff/total_return.py`, `agents/dreman/contrarian.py`, `agents/marks/cycle_value.py`. בדיקה לכל סוכן: `agents/<x>/tests/test_exits.py::test_held_name_that_still_qualifies_is_retained_when_it_slips_in_rank` + `::test_exit_reason_in_journal_names_the_trigger`. עד אז: בכל `playbook.md` "exit section: not executed".

14. **cash posture מכובד** — `core/live/runner.py:231-303` (`cash = min(cash, nav×Σweights − invested)`), `:1534` (ADD לא חורג). בדיקה: `test_runner.py::test_klarman_92_percent_deployment_leaves_8_percent_cash`; `::test_marks_neutral_posture_holds_20_percent`.

15. **רצועת rebalance לפי דוקטרינה + per-position cap** — `core/live/agent_adapter.py` (`rebalance_band: float | None`; `max_position_pct` Graham 10%, Schloss 5%), `runner.py` (`reachable_targets` לא מעלה מעל cap). בדיקה: `test_runner.py::test_lynch_winner_is_not_trimmed_by_band`; `::test_graham_with_5_reachable_names_holds_50_percent_cash_not_20_percent_each`.

16. **רצפת 30 יום גם ב-trim** — `runner.py:1494-1531`. בדיקה: `test_runner.py::test_seven_day_old_overweight_position_is_not_trimmed`.

17. **Greenblatt: שם מוגן שלא ניקד נשאר ביעדים** — `agent_adapter.py:184` (`_collect_targets` מ-`weights`). בדיקה: `agents/greenblatt/tests/test_live_adapter.py::test_unscored_protected_holding_is_emitted_as_target`.

18. **Lynch: גלגול לוח לא מפיל שם** — `agents/lynch/exits.py`. בדיקה: `agents/lynch/tests/test_exits.py::test_crs_2026_08_19_window_roll_does_not_sell`.

19. **Council/Pabrai: E8 רק ב-rebalance; הרץ לא מקצץ; כוונות ביומן** — `agents/council/pipeline.py:179-210`, `agent_adapter.py` (`PabraiLive.rebalance_band=None`), `agents/council/strategy.py` (`decision_logger`). בדיקה: `agents/council/tests/test_runs.py::test_heartbeat_sells_only_forced_exits`; `::test_intent_rows_are_journaled`.

20. **Graham_2 רושם תחת השם שלו** — `agents/graham/net_net.py` / `runner.py:436`. בדיקה: `core/live/tests/test_runner.py::test_second_cohort_intent_rows_land_in_benjamin_graham_2`.

21. **Marks: posture hysteresis** — `agents/marks/temperature.py`. בדיקה: `agents/marks/tests/test_temperature.py::test_posture_does_not_flip_on_one_day_score_change`.

22. **split: התאמת מניות/entry_price בספר** — `data_loader.py` (לשמור `Stock Splits`), `runner.py` (`shares×r`, `entry_price/r`, רישום `corporate-action`). בדיקה: `test_runner.py::test_two_for_one_split_leaves_nav_unchanged`.

23. **`paid_dividends` ניקוי + פעולות ידניות דרך `sell()`** — `scripts/floor_fractional_lots.py`. בדיקה: `core/live/tests/test_portfolio.py::test_paid_dividends_keys_subset_of_positions_after_floor`.

24. **רישום: `logger.debug` → `warning` + ספירה; EXIT עם shares; snapshot 08-06; ימי חג** — `runner.py:1611,1658,1683`, `:1677`, backfill `data/snapshots/*/2026-08-06.json` מ-`8ac0c8bf`, לתייג/למחוק 55 snapshots + 42 שורות פנקס. בדיקה: `test_runner.py::test_journal_write_failure_is_counted_not_swallowed`; `scripts/tests/test_verify_run_state.py::test_no_snapshot_on_closed_day`.

25. **RK-09 / RK-12 / PD-08 (נמוך)** — logs תחת שם מעוקב; להסיר `|| true`; `data/memos` ל-`git add`; `fetched_at` + `session_settled` ב-`prices`; `--dry-run`. בדיקה: `scripts/tests/test_workflow_schedule.py::test_commit_step_stages_memos_and_logs`; `test_data_loader.py::test_close_run_marks_held_bars_settled`.

**קריטריון למעבר לשלב הסוכנים:** פריטים 1-12 ירוקים (כסף ונתונים), פריט 13 לפחות ב-shadow (כל סוכן מדווח מה *היה* מוכר), ו-ENG-3/ENG-4/CE-4/RK-07/RK-10/RK-13 עדיין ירוקים אחרי השינויים.
