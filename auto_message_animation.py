"""أداة المراسلة التلقائية لفرص الأنيماتور والمعارض — JobRadar DZ.

يقوم هذا السكريبت بـ:
1. فحص جميع العروض في data/offers.json واستخراج العروض المتعلقة بالأنيماسيون والمعارض.
2. تجهيز رسالة تقديم احترافية ومخصصة باسم عمار حيمور.
3. فتح محادثة واتساب تلقائياً مع كل صاحب عمل بنص الرسالة جاهز للإرسال.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OFFERS_FILE = BASE_DIR / "data" / "offers.json"
HISTORY_FILE = BASE_DIR / "data" / "messaged_history.json"

ANIM_KEYWORDS = [
    "animat", "hote", "safex", "salon", "stand", "foire", "evenement", "accueil", "promoteur", "promotrice",
    "منشط", "منشطة", "مضيف", "مضيفة", "معرض", "معارض", "صالون", "سافكس", "ستاند", "استقبال", "تنشيط"
]


def load_offers() -> list[dict]:
    if not OFFERS_FILE.exists():
        print(f"لم يتم العثور على {OFFERS_FILE}")
        return []
    with open(OFFERS_FILE, encoding="utf-8") as f:
        return json.load(f) or []


def load_history() -> set[str]:
    if not HISTORY_FILE.exists():
        return set()
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return set(json.load(f) or [])
    except Exception:
        return set()


def save_history(history: set[str]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(history), f, ensure_ascii=False, indent=2)


def is_animation(offer: dict) -> bool:
    corpus = (offer.get("text", "") + " " + offer.get("source", "") + " " + offer.get("gig_badge", "")).lower()
    return any(k in corpus for k in ANIM_KEYWORDS)


def generate_animation_pitch(offer: dict, candidate_name: str = "عمار حيمور", candidate_phone: str = "0550 12 34 56") -> str:
    source = offer.get("source", "SAFEX / Alger")
    return (
        f"Bonjour,\n\n"
        f"Je me permets de vous contacter suite à votre annonce d'animation / accueil stand ({source}).\n"
        f"Je suis basé à Alger, sérieux, dynamique, avec une excellente présentation et une bonne expérience dans l'animation d'événements et stands à la SAFEX.\n"
        f"Je suis immédiatement disponible pour toute la durée de l'événement.\n\n"
        f"Puis-je vous transmettre mon CV ?\n\n"
        f"Bien cordialement,\n"
        f"{candidate_name}\n"
        f"📞 {candidate_phone}"
    )


def clean_phone_number(raw_phone: str) -> str:
    digits = re.sub(r"\D", "", raw_phone or "")
    if digits.startswith("0") and len(digits) == 10:
        return "213" + digits[1:]
    if digits.startswith("213"):
        return digits
    if len(digits) == 9:
        return "213" + digits
    return digits


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("==================================================================")
    print("  🎪 JobRadar DZ — مركز المراسلة التلقائية لفرص الأنيماتور والمعارض")
    print("==================================================================")

    offers = load_offers()
    anim_offers = [o for o in offers if is_animation(o)]
    history = load_history()

    print(f"🔍 تم فحص {len(offers)} عرض، وتم العثور على {len(anim_offers)} فرصة أنيماسيون ومعارض!\n")

    eligible = []
    for o in anim_offers:
        phone = clean_phone_number(o.get("phones", ""))
        wa_url = o.get("whatsapp_url", "")
        if phone or wa_url:
            eligible.append((o, phone, wa_url))

    print(f"📱 عروض الأنيماسيون التي تملك رقم اتصال متاح: {len(eligible)} عرض.")
    print(f"📊 عروض تمت مراسلتها سابقاً: {len([e for e in eligible if e[0].get('uid') in history])} عرض.\n")

    if not eligible:
        print("لا توجد أرقام هواتف مسجلة حالياً.")
        return 0

    print("الخيارات المتاحة:")
    print("1. ⚡ إرسال تسلسلي آلي لجميع عروض الأنيماسيون غير المراسلة")
    print("2. 📋 استعراض واختيار عرض محدد لمراسلته يدوياً")
    print("3. 🔄 إعادة تعيين سجل المراسلة")
    print("4. ❌ خروج")

    choice = input("\nاختر رقماً (1-4) واضغط Enter [افتراضي: 1]: ").strip() or "1"

    if choice == "1":
        pending = [item for item in eligible if item[0].get("uid") not in history]
        if not pending:
            print("✨ لقد تمت مراسلة جميع عروض الأنيماسيون المؤهلة بالفعل!")
            reset = input("هل ترغب في إعادة مراسلتها من جديد؟ (y/n): ").strip().lower()
            if reset == "y":
                pending = eligible
            else:
                return 0

        print(f"\n🚀 بدء المراسلة التسلسلية لـ {len(pending)} عرض...")
        for i, (off, phone, wa_url) in enumerate(pending, 1):
            msg = generate_animation_pitch(off)
            if phone:
                url = f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}"
            else:
                url = wa_url

            print(f"\n[{i}/{len(pending)}] فتح واتساب: {off.get('source')} (هاتف: {phone or 'واتساب مباشر'})")
            webbrowser.open(url)
            history.add(off.get("uid", ""))
            save_history(history)

            if i < len(pending):
                print("⏳ مهلة 3 ثوانٍ قبل فتح العرض التالي...")
                time.sleep(3)

        print("\n🎉 اكتملت المراسلة لجميع عروض الأنيماسيون بنجاح!")
        return 0

    elif choice == "2":
        for i, (off, phone, _) in enumerate(eligible, 1):
            messaged = "✔ تمت مراسلته" if off.get("uid") in history else "جديد"
            print(f"[{i}] {off.get('source')} — هاتف: {phone} — {messaged}")
            print(f"    النص: {off.get('text', '')[:90]}...\n")

        idx_str = input(f"اختر رقم العرض (1-{len(eligible)}): ").strip()
        try:
            idx = int(idx_str) - 1
            if 0 <= idx < len(eligible):
                off, phone, wa_url = eligible[idx]
                msg = generate_animation_pitch(off)
                url = f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}" if phone else wa_url
                print(f"🚀 فتح محادثة واتساب...")
                webbrowser.open(url)
                history.add(off.get("uid", ""))
                save_history(history)
                print("✅ تم الإرسال وتسجيل العملية.")
        except ValueError:
            print("رقم غير صالح.")
        return 0

    elif choice == "3":
        save_history(set())
        print("✅ تم تفريغ سجل المراسلة.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
