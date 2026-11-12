from __future__ import annotations

import io
from datetime import date, timedelta

from docx import Document


def _seed(client):
    r = client.post("/api/demo/seed")
    assert r.status_code == 200
    return r.json()


def test_demo_flow_end_to_end(client):
    ids = _seed(client)
    t = client.get(f"/api/tenders/{ids['tender_id']}").json()
    assert t["deadline"] == (date.today() + timedelta(days=12)).isoformat()
    assert t["extraction_mode"] == "rules" and len(t["requirements"]) >= 15
    assert all(r["quote"] and r["page"] >= 1 for r in t["requirements"])

    rep = client.get(f"/api/tenders/{ids['tender_id']}/check").json()
    by = {(x["type"], x["label"]): x for x in rep["results"]}
    numeric = next(x for x in rep["results"] if x["type"] == "numeric")
    assert numeric["status"] == "fail" and "450" in numeric["message"] and "500" in numeric["message"]
    tax = by[("document", "Soliq qarzdorligi yo'qligi ma'lumotnomasi")]
    assert tax["status"] == "fail" and "tugagan" in tax["message"]  # expires before the tender deadline
    assert next(x for x in rep["results"] if x["type"] == "turnover")["status"] == "ok"
    assert next(x for x in rep["results"] if x["type"] == "experience" and "yil" in x["message"])["status"] == "ok"
    guarantee = by[("document", "Kafolat xati")]
    assert guarantee["status"] == "manual" and guarantee["generate"] == "guarantee_letter"
    assert {c["label"] for c in rep["consistency"]} == {"STIR mos emas", "Ko'rsatkichlar ziddiyati"}
    assert rep["readiness"]["verdict"].startswith("TAYYOR EMAS") and 0 < rep["readiness"]["score"] < 100

    al = client.get(f"/api/companies/{ids['company_id']}/alerts").json()
    assert any(a["kind"] == "document_expiry" for a in al) and any(a["kind"] == "tender_deadline" for a in al)

    made = client.post(f"/api/tenders/{ids['tender_id']}/tasks/auto").json()
    assert made and {m["role"] for m in made} <= {"lawyer", "accountant", "technical", "other"}
    assert client.post(f"/api/tenders/{ids['tender_id']}/tasks/auto").json() == []  # idempotent
    done = client.patch(f"/api/tasks/{made[0]['id']}", json={"done": True}).json()
    assert done["done"] is True


def test_fixing_the_passport_flips_the_result(client):
    ids = _seed(client)
    cid, tid = ids["company_id"], ids["tender_id"]
    pas = client.get(f"/api/companies/{cid}").json()
    for a in pas["attributes"]:
        client.delete(f"/api/attributes/{a['id']}")
    client.post(f"/api/companies/{cid}/attributes", json={"label": "Oylik ishlab chiqarish quvvati", "value": 520, "unit": "dona"})
    rep = client.get(f"/api/tenders/{tid}/check").json()
    assert next(x for x in rep["results"] if x["type"] == "numeric")["status"] == "ok"
    assert "Ko'rsatkichlar ziddiyati" not in {c["label"] for c in rep["consistency"]}


def test_tender_upload_text_docx_and_errors(client):
    cid = client.post("/api/companies", json={"legal_name": "Test MChJ", "stir": "300000001"}).json()["id"]
    r = client.post(f"/api/companies/{cid}/tenders", data={"title": "T1", "text": "Taklif ta'minoti 5 000 000 so'm bo'lishi kerak."})
    assert r.status_code == 201 and r.json()["requirements"][0]["type"] == "security"

    d = Document()
    d.add_paragraph("Ishtirokchi kamida 4 yil faoliyat yuritgan bo'lishi kerak.")
    buf = io.BytesIO()
    d.save(buf)
    r = client.post(f"/api/companies/{cid}/tenders", data={"title": "T2"},
                    files={"file": ("t.docx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 201 and r.json()["requirements"][0]["value"] == 4

    assert client.post(f"/api/companies/{cid}/tenders", data={"title": "T3"}).status_code == 422
    r = client.post(f"/api/companies/{cid}/tenders", data={"title": "T4"}, files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415
    r = client.post(f"/api/companies/{cid}/tenders", data={"title": "T5"}, files={"file": ("bad.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 422
    assert client.post("/api/companies/999/tenders", data={"title": "x", "text": "y"}).status_code == 404


def test_document_upload_is_validated_and_path_safe(client):
    cid = client.post("/api/companies", json={"legal_name": "Test MChJ"}).json()["id"]
    ok = client.post(f"/api/companies/{cid}/documents",
                     data={"kind": "license", "title": "Lit", "expires_date": "2030-01-01", "fields": '{"stir": "1"}'},
                     files={"file": ("../../etc/passwd.pdf", b"%PDF-1.4", "application/pdf")})
    assert ok.status_code == 201 and ok.json()["filename"] == "passwd.pdf"
    dl = client.get(f"/api/documents/{ok.json()['id']}/download")
    assert dl.content == b"%PDF-1.4" and dl.headers["x-content-type-options"] == "nosniff"
    assert client.post(f"/api/companies/{cid}/documents", data={"kind": "license", "title": "x"}, files={"file": ("a.exe", b"MZ")}).status_code == 415
    assert client.post(f"/api/companies/{cid}/documents", data={"kind": "bogus", "title": "x"}).status_code == 422
    assert client.post(f"/api/companies/{cid}/documents", data={"kind": "license", "title": "x", "fields": "[1]"}).status_code == 422
    assert client.post(f"/api/companies/{cid}/documents", data={"kind": "bid_security", "title": "x"}).status_code == 201


def test_generate_docx_and_404s(client):
    ids = _seed(client)
    r = client.get(f"/api/tenders/{ids['tender_id']}/generate/guarantee_letter")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert client.get(f"/api/tenders/{ids['tender_id']}/generate/nope").status_code == 404
    assert client.get("/api/tenders/999").status_code == 404
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200
