# ኦዲት እና ማስተካከያ (2026-11-12)

## የተስተካከሉ
1. `habtam_theme.css` በ index.html ይጫናል ግን በ `MINI_PUBLIC_ASSETS` (kdm_mini_app.py) አልነበረም → production ላይ 404 (ያልተቀባ ገጽ)። ተጨምሯል።
2. Chapa ሙሉ በሙሉ ተወግዷል (manual Telebirr/CBE/BOA ብቻ): webapp_api.py (routes + webhook + verify), app.js (Pay Online ሳጥን)፣ config.py, kdm_mini_app.py (guards)፣ .env.example, render.yaml, style.css/kdm_design.css። የ`payments` ዓምዶች (provider, provider_ref, credited_at) ሌላ ኮድ ስለሚጠቀማቸው ቀርተዋል።
3. ያልተጫኑ/የተተኩ ፋይሎች ወደ `_unused/` ተወስደዋል: kdm_aviator_live.js, kdm_crash_live.js, kdm_jet_live.js, kdm_rocket_live.js (kdm_live_crash.js ተክቷቸዋል)፣ kdm_fastkeno_live.js (ሰርቨር route የለውም)፣ `kdm_icons_premium (1).css` (የተሳሳተ ስም ያለው ቅጂ)።
4. ሙከራዎች ከChapa መወገድ ጋር ተስማምተዋል (launch_blocker, platform_controls_e2e, risk_controls, release_self_check, smoke tests)።

## ውጤት
42 regression tests + static release check = ሁሉም PASS።
