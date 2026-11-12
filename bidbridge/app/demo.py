"""Demo data: one company with realistic problems and one tender text that triggers them."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import Company, CompanyAttribute, CompanyDocument, CompanyMember, Contract, FinancialYear, User


def demo_tender_text(deadline: date) -> str:
    return f"""Maktab mebellari yetkazib berish bo'yicha tender hujjatlari

1. Umumiy ma'lumot
Buyurtmachi: Qashqadaryo viloyati Xalq ta'limi boshqarmasi.
Taklif topshirishning oxirgi muddati: {deadline:%d.%m.%Y} soat 15:00.
Taklif ta'minoti sifatida 25 000 000 so'm miqdorida bank kafolati taqdim etilishi kerak.

2. Ishtirokchiga qo'yiladigan talablar
- Ishtirokchining oylik ishlab chiqarish quvvati kamida 500 dona bo'lishi lozim.
- Ishtirokchi kamida 3 yil faoliyat yuritgan bo'lishi kerak.
- Oxirgi 3 yil ichida kamida 2 ta o'xshash shartnomani bajargan bo'lishi talab etiladi.
- Ishtirokchining oxirgi yillik aylanmasi kamida 1 500 000 000 so'm bo'lishi shart.

3. Taqdim etiladigan hujjatlar
- Davlat ro'yxatidan o'tganlik guvohnomasi nusxasi;
- STIR (soliq to'lovchi) guvohnomasi;
- Soliq va boshqa majburiy to'lovlar bo'yicha qarzdorlik yo'qligi to'g'risidagi ma'lumotnoma;
- Bankrotlik yoki tugatish jarayoni yo'qligi haqida kafolat xati;
- Tajriba ma'lumotnomasi;
- Ishlab chiqarish quvvati va sifat tizimini tasdiqlovchi ISO 9001 sertifikati;
- Ishonchnoma (vakil imzolagan taqdirda);
- Texnik taklif va narx taklifi.

4. Rad etish asoslari
Talab etilgan hujjatlarning biri taqdim etilmasa, taklif rad etiladi.
Muddatdan keyin kelgan takliflar qabul qilinmaydi.
"""


def seed_demo(session: Session, user: User, today: date | None = None) -> tuple[int, date]:
    """Create a demo company owned by ``user``. Returns (company_id, tender_deadline)."""
    today = today or date.today()
    deadline = today + timedelta(days=12)
    c = Company(
        legal_name='"Qarshi Mebel Servis" MChJ', stir="301234567", registration_date=date(2019, 5, 20),
        address="Qashqadaryo viloyati, Qarshi shahri", director="Karimov A.B.", phone="+998 90 000 00 00",
    )
    c.documents = [
        CompanyDocument(kind="registration_certificate", title="Davlat ro'yxati guvohnomasi",
                        issued_date=date(2019, 5, 20), fields={"stir": "301234568", "legal_name": "Qarshi Mebel Servis MChJ"}),
        CompanyDocument(kind="tin_certificate", title="STIR guvohnomasi", issued_date=date(2019, 5, 22),
                        fields={"stir": "301234567"}),
        CompanyDocument(kind="tax_clearance", title="Soliq ma'lumotnomasi", issued_date=today - timedelta(days=40),
                        expires_date=deadline - timedelta(days=5)),
        CompanyDocument(kind="power_of_attorney", title="Direktor ishonchnomasi", issued_date=today - timedelta(days=100),
                        expires_date=today + timedelta(days=300)),
        CompanyDocument(kind="certificate", title="ISO 9001 sertifikati", issued_date=today - timedelta(days=500),
                        expires_date=today + timedelta(days=20)),
    ]
    c.attributes = [
        CompanyAttribute(label="Oylik ishlab chiqarish quvvati", value=450, unit="dona", source="ISO 9001 sertifikati"),
        CompanyAttribute(label="Ishlab chiqarish quvvati (oylik)", value=500, unit="dona", source="Texnik pasport"),
    ]
    c.contracts = [
        Contract(customer="Qarshi shahar 12-maktab", subject="Maktab mebellari yetkazib berish", amount_uzs=420_000_000,
                 completed_date=date(today.year - 1, 9, 1)),
        Contract(customer="Shahrisabz tuman XTB", subject="Parta va stul komplektlari", amount_uzs=610_000_000,
                 completed_date=date(today.year - 1, 11, 15)),
        Contract(customer="Koson tuman maktablari", subject="Sinf mebellari", amount_uzs=380_000_000,
                 completed_date=date(today.year - 2, 8, 20)),
    ]
    c.financials = [
        FinancialYear(year=today.year - 2, revenue_uzs=1_100_000_000),
        FinancialYear(year=today.year - 1, revenue_uzs=1_800_000_000),
    ]
    c.members = [CompanyMember(user_id=user.id, role="owner")]
    session.add(c)
    session.commit()
    return c.id, deadline
