# رادار العروض – JobRadar Online

موقع ثابت على GitHub Pages يعرض عروض عمل مؤقت وجزئي في الجزائر العاصمة، تجمعها مهمة GitHub Actions **كل ساعة** من مصادر عامة فقط، مع أولوية لعروض الفعاليات والتنشيط (مضيفات، ستاند، صالونات SAFEX…).

**الموقع:** https://mirouamar97.github.io/jobradar-online/

## كيف يعمل
1. `.github/workflows/collect.yml` يشغّل `tools/collect.py` كل ساعة.
2. الجامع يقرأ المصادر في `tools/sources.json` (مواقع توظيف، قنوات تيليغرام عامة)، ويقيّم كل عرض بالقواعد في `tools/rules.json`، ويكتب:
   - `data/offers.json`: العروض المقبولة (آخر 14 يوماً، 300 كحد أقصى).
   - `data/status.json`: حالة كل مصدر بصدق: هل قُرئ، كم بطاقة، كم قُبلت، ولماذا رُفض الباقي (`rejected`).
3. `index.html` يعرض البيانات. تطبيق أندرويد «رادار العروض» يستورد `data/offers.json` كمصدر.

## ما لا يفعله
- لا حسابات، لا كلمات سر، لا إرسال رسائل، لا بيانات شخصية.
- لا يقرأ فيسبوك ولا إنستغرام ولا تيك توك.
- بحث الويب **معطّل هنا** (`"enabled": false`): محركات البحث تحجب خوادم GitHub Actions، فكانت تعطي 0 نتيجة في كل تشغيل.
- AlgerieJob يرفض الطلبات الآلية (403)، وقد يتعطل أي مصدر إذا غيّر تصميمه. الحالة تظهر في أسفل الموقع.

## تشخيص مصدر
```
pip install -r tools/requirements.txt
python tools/collect.py --only sfnemploi -v      # يطبع كل بطاقة وسبب قبولها أو رفضها، ولا يكتب شيئاً
python -m pytest -q tools/tests
```

## التصميم
يتبع نظام تصميم التطبيق (Material 3، أخضر الرادار `#0e6e53`، خط Cairo، عربي من اليمين لليسار، وضع داكن تلقائي).

## الترخيص
MIT

## Identifiants d'offres et lien profond vers l'appli (sens unique)
- **Identifiant** : `uid` de chaque entree de `data/offers.json` = SHA-1 hexadecimal (40 caracteres `[0-9a-f]`) calcule par
  `tools/collect.py:uid_of` : sur l'URL de l'annonce, ou, si l'URL est vide / racine de canal (`t.me`, `emploitic.com`),
  sur le texte normalise (300 premiers caracteres). Stable d'un passage a l'autre ; il change si l'URL ou le texte change.
  Les salons utilisent leur `id` (slug) dans `data/salons.json` (pas de lien profond pour l'instant).
- **Lien** : chaque carte propose « Ouvrir dans l'appli » = `jobradar://offer/<uid>` (valide par `^[0-9a-f]{40}$` avant d'etre ecrit).
  Si l'appli n'est pas installee, apres 1,5 s un lien « annonce d'origine » apparait (repli web normal).
- **Sens unique** : le site ne recoit rien de l'appli ; aucun cookie, compte ni donnee personnelle.
