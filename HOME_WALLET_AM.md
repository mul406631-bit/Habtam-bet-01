# Home ገጽ ላይ Deposit / Withdraw (ፈጣን ቦርሳ ካርድ)

## የተቀየሩ ፋይሎች (3 + ይህ ማስታወሻ)
- `index.html` — 2 መስመር ብቻ ተጨመረ (home_wallet.css እና home_wallet.js)
- `home_wallet.js` — አዲስ
- `home_wallet.css` — አዲስ

## ምን ይሰራል
Home ላይ ከላይ ባነሩ ስር "ቦርሳዬ" ካርድ ይታያል፦
- ጠቅላላ ቀሪ ሂሳብ + ማውጣት የሚችሉት መጠን (ቦነስ ሳይጨምር) + History አዝራር (pending ካለ ቀይ ነጥብ)
- **Deposit**: አካውንቶች (Telebirr/CBE/BOA) ከ"ቅዳ" ጋር → መጠን (50/100/200/500/1000) → Screenshot → ላክ
- **Withdraw**: ዘዴ → መጠን (100/500/1000/ሁሉንም) → ስም + ቁጥር → ላክ። ከዚህ በፊት "አስታውስ" ከተደረገ በራሱ ይሞላል።

## ደህንነት
ካርዱ ራሱ /deposit ወይም /withdraw አይጠራም። የWallet ገጹን ትክክለኛ ፎርሞች ሞልቶ ያስገባል፣ ስለዚህ ሁሉም ነባር ቼኮች (ዝቅተኛ መጠን፣ ቀሪ ሂሳብ፣ wager ህግ፣ የማረጋገጫ ጥያቄ፣ admin approval) እንዳሉ ናቸው። Backend አልተነካም።

## ተጨማሪ ማስተካከያ
Wallet ገጽ ላይ "ማውጣት የሚችሉት" ጠቅላላ ቀሪውን (ቦነስ ጨምሮ) ያሳይ ነበር፤ አሁን withdrawable ያሳያል። "ሁሉንም" ቁልፍም በዚሁ መጠን ይሞላል።

## መመለስ
በindex.html ውስጥ home_wallet ያሉትን 2 መስመሮች ይሰርዙ።
