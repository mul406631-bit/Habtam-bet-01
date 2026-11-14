# V12 ውህደት (Habtam-bet-main + changed-files-only)

መሠረት: `Habtam-_bet-main` (Habtam brand፣ manual deposit፣ አዲሱ admin edge/wheel/deposit ቅንብር ሁሉ እንዳለ)።
ከ`changed-files-only` zip የገባው V12 ብቻ:
- `margin_governor.py` + `platform_controls.effective_controls` (ሁሉም ጨዋታ የሚያነበው)
- Leaderboard (`leaderboard.js/css`, `/api/leaderboard/weekly`), ንዝረት (`haptics.js`), RTP ምልክት (`rtp_badge.js`)
- የአድሚን ሪፖርት (`central_v12.py`, `/api/admin/margin-report`)
- `game_fx.js` (live cash-out ድል ውጤት), የአድሚን ticket Read/Close
- አዲሶቹ ፋይሎች በ`kdm_mini_app.py` MINI_PUBLIC_ASSETS ውስጥ ተመዝግበዋል (ያለዚያ 404)

ያልገባ (ሆን ተብሎ): King Bet ስም/theme፣ Chapa payment፣ "ሁሉም ጨዋታ ወደ 15%" የአንድ ጊዜ DB reset፣ MAX_HOUSE_EDGE 0.60።

⚠️ ተቆጣጣሪው በነባሪ ON ነው፤ base edge 15% (RTP 85%) እና እስከ 60% ሊጨምር ይችላል። ለማጥፋት: Admin → `governor_enabled=false`።
የቆዩ ፈተናዎች ቋሚ edge ስለሚጠብቁ `MARGIN_GOVERNOR_DEFAULT=0` ይጠቀማሉ።

## Mystery Box ዲዛይን
- አዲሱ ባነር: `mystery_box_banner.webp` (በ`MINI_PUBLIC_ASSETS` ተመዝግቧል)። `rewards.js` ውስጥ በHome እና በProfile ያለው Mystery Box ካርድ ይህን ይጠቀማል።
- የH:M:S ቁጥሮች ከምስሉ ተደምስሰው በቀጥታ ቆጣሪ ተተክተዋል (የሰዓቱ ወይም የአድሚን ልቀት ቀሪ ጊዜ)። ሳጥኑ ዝግጁ ሲሆን ባነሩ ያበራል፤ ሲከፈት ይንቀጠቀጣል።
- የ«ክፈት» ቁልፍ እና የሽልማት ጽሑፍ ከባነሩ በታች እንዳሉ ናቸው።
