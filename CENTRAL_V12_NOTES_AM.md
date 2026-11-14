# V12 — ማዕከላዊ የ40% ትርፍ ተቆጣጣሪ + Leaderboard + ንዝረት + የአድሚን ሪፖርት

## 1. ማዕከላዊ ተቆጣጣሪ (`margin_governor.py`)
- ሁሉም ጨዋታዎች (cabinet፣ live፣ realtime፣ pool፣ sports) house edge የሚያገኙት ከአንድ ቦታ ነው (`platform_controls.effective_controls`)። ተቆጣጣሪው በዚያ ቁጥር ላይ ተቀምጧል።
- **ጅምር 15% edge (RTP 85%)** — `governor_base_edge`። ተቆጣጣሪው ሲያስተካክል ተጫዋቾች ወዲያው ያያሉ: ከላይ በስተቀኝ `RTP xx%` ምልክት + ሲቀየር ማሳወቂያ (`rtp_badge.js`፣ `GET /api/house/rtp`)። ⚠️ Margin ወደ 40% ለመድረስ edge ከ15% እስከ 60% ይጨምራል፤ ማለትም RTP ከ85% ወደ ~40–55% ሊወርድ ይችላል።
- **Dynamic Odds Adjustment**: ከ`transactions` ledger እውነተኛውን Margin ይለካል (ውርርድ − refund = Inflow፤ ክፍያ = Payout)። ተጫዋቾች እያሸነፉ Margin ከ40% በታች ቢወርድ፣ ለሚቀጥሉት ውርርዶች edge ይጨምራል (እስከ 60%) — ሲያገግም ወደ 40% ይመለሳል።
- ተጫዋቹ የሚያየው RTP = በእውነት የሚጫወትበት RTP። ዙሩ ከተጫወተ በኋላ ውጤት አይቀየርም።
- Admin ቅንብሮች (`/api/admin/platform-controls`): `governor_enabled`, `target_margin` (0.40), `governor_min_volume`, `governor_window_days`, `governor_gain`, `governor_max_edge`.
- `MAX_HOUSE_EDGE` default 0.30 → 0.60 ተቀይሯል (40% ማስገባት እንዲቻል)።
- ⚠️ ዙሮች random ስለሆኑ 40% በአጭር ጊዜ "ዋስትና" የለውም፤ በውርርድ መጠን ሲጨምር ይገጥማል። በትንሽ Inflow (< `governor_min_volume`) ወለሉ ብቻ ይሠራል።

## 2. Leaderboard
- `GET /api/leaderboard/weekly` — የአሁኑ ሳምንት (ሰኞ 00:00 – እሁድ 24:00 አዲስ አበባ) ከፍተኛ 10 አሸናፊዎች፣ ስም + ያሸነፉት መጠን (ክፍያ − ውርርድ፣ refund ተቀንሶ)፣ የራስ ደረጃ፣ የቀረ ሰዓት።
- የታገዱ ተጫዋቾች አይታዩም። UI: Sidebar → 🏆 Leaderboard እና Profile → 🏆 የሳምንቱ አሸናፊዎች (`leaderboard.js/css`)።

## 3. ንዝረት (`haptics.js`)
- ቁልፍ/ካርድ/nav ሲነኩ፣ ስክሪኑን ሲነኩ (soft)፣ ውርርድ ሲያስገቡ (medium)፣ ሲያሸንፉ (success + ትልቅ ድል ≥5× ተጨማሪ ንዝረት)፣ ሲሸነፉ (warning)።
- Telegram HapticFeedback; በሌላ browser `navigator.vibrate`። Profile ውስጥ 📳 ማብሪያ/ማጥፊያ።

## 4. የአድሚን ሪፖርት (`central_v12.py`)
- በየቀኑ ከጠዋቱ `ADMIN_REPORT_HOUR` (default 8) ሰዓት ለ`ADMIN_ID` Telegram ይላካል፤ Margin ከግቡ በ2 ነጥብ በላይ ሲወርድ ወዲያው ማስጠንቀቂያ (በሰዓት አንድ ጊዜ)።
- `GET /api/admin/margin-report?days=7` (JSON) / `POST {"days":7,"send":true}` (ወደ Telegram ይልካል)። Inflow፣ Payout፣ RTP፣ Margin፣ ቦነስ ወጪ፣ ከቦነስ በኋላ ትርፍ፣ ጨዋታ በጨዋታ፣ Deposit/Withdraw፣ ከፍተኛ አሸናፊዎች።
- ሙከራ ለማጥፋት: `DISABLE_ADMIN_REPORT_WORKER=1`. ሙከራዎች: `python central_v12_e2e_test.py` → `CENTRAL_V12_E2E=PASS`.
- የቆዩ ሙከራዎች `MARGIN_GOVERNOR_DEFAULT=0` (ወይም governor_enabled=0) ይጠቀማሉ፤ ምክንያቱም 15% pay-table ይፈትናሉ።
