from __future__ import annotations

from enum import Enum


class ReqType(str, Enum):
    DOCUMENT = "document"
    NUMERIC = "numeric"
    EXPERIENCE = "experience"
    TURNOVER = "turnover"
    SECURITY = "security"
    DEADLINE = "deadline"
    RISK = "risk"


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    MANUAL = "manual"


class DocKind(str, Enum):
    REGISTRATION_CERTIFICATE = "registration_certificate"
    TIN_CERTIFICATE = "tin_certificate"
    TAX_CLEARANCE = "tax_clearance"
    BANKRUPTCY_STATEMENT = "bankruptcy_statement"
    POWER_OF_ATTORNEY = "power_of_attorney"
    LICENSE = "license"
    CERTIFICATE = "certificate"
    FINANCIAL_STATEMENT = "financial_statement"
    MANUFACTURER_AUTHORIZATION = "manufacturer_authorization"
    EXPERIENCE_REFERENCE = "experience_reference"
    # Tender-specific: prepared per tender, never stored in the passport.
    BID_SECURITY = "bid_security"
    GUARANTEE_LETTER = "guarantee_letter"
    APPLICATION_FORM = "application_form"
    TECHNICAL_PROPOSAL = "technical_proposal"
    PRICE_PROPOSAL = "price_proposal"
    ANTI_CORRUPTION = "anti_corruption"


TENDER_SPECIFIC: frozenset[DocKind] = frozenset(
    {
        DocKind.BID_SECURITY,
        DocKind.GUARANTEE_LETTER,
        DocKind.APPLICATION_FORM,
        DocKind.TECHNICAL_PROPOSAL,
        DocKind.PRICE_PROPOSAL,
        DocKind.ANTI_CORRUPTION,
    }
)

# Documents that buyers commonly require to be recent. Heuristic: produces WARN, never FAIL.
STALE_AFTER_DAYS: dict[DocKind, int] = {
    DocKind.TAX_CLEARANCE: 30,
    DocKind.BANKRUPTCY_STATEMENT: 30,
}

DOC_LABELS_UZ: dict[DocKind, str] = {
    DocKind.REGISTRATION_CERTIFICATE: "Davlat ro'yxatidan o'tganlik guvohnomasi",
    DocKind.TIN_CERTIFICATE: "STIR (soliq to'lovchi) guvohnomasi",
    DocKind.TAX_CLEARANCE: "Soliq qarzdorligi yo'qligi ma'lumotnomasi",
    DocKind.BANKRUPTCY_STATEMENT: "Bankrotlik/tugatish jarayoni yo'qligi ma'lumotnomasi",
    DocKind.POWER_OF_ATTORNEY: "Ishonchnoma / vakolat hujjati",
    DocKind.LICENSE: "Litsenziya",
    DocKind.CERTIFICATE: "Sertifikat",
    DocKind.FINANCIAL_STATEMENT: "Moliyaviy hisobot",
    DocKind.MANUFACTURER_AUTHORIZATION: "Ishlab chiqaruvchi avtorizatsiyasi",
    DocKind.EXPERIENCE_REFERENCE: "Tajriba ma'lumotnomasi",
    DocKind.BID_SECURITY: "Taklif ta'minoti (bank kafolati / garov)",
    DocKind.GUARANTEE_LETTER: "Kafolat xati",
    DocKind.APPLICATION_FORM: "Qatnashish arizasi / anketa",
    DocKind.TECHNICAL_PROPOSAL: "Texnik taklif",
    DocKind.PRICE_PROPOSAL: "Narx taklifi",
    DocKind.ANTI_CORRUPTION: "Korrupsiyaga qarshi deklaratsiya",
}
