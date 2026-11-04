"""Protections against attacks: password guessing, open sign-up, open redirects, injected scripts,
formulas in Excel files, oversized uploads."""

import datetime
import re
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import openpyxl
import pytest
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from finance_saber.cashbook.models import Company
from finance_saber.production.models import Product, ProductShipment, ProductShipmentLine
from finance_saber.purchases.models import Bill
from finance_saber.users import telegram, throttle
from finance_saber.users.models import PasswordResetCode

LOGIN = reverse("users:login")
TEMPLATES = Path(settings.APPS_DIR)


def sign_in(client, username, password="wrong-password", ip="10.0.0.1"):
    return client.post(LOGIN, {"username": username, "password": password}, REMOTE_ADDR=ip)


def locked(response):
    return response.status_code == 200 and "Слишком много" in response.content.decode()


# Password guessing ----------------------------------------------------------------------------


def test_a_username_is_locked_after_five_failed_sign_ins(client, user):
    for _attempt in range(throttle.USER_FAILURES):
        assert not locked(sign_in(client, "alice"))
    response = sign_in(client, "alice", password="s3cret-pass")  # even the right password
    assert locked(response) and "_auth_user_id" not in client.session


def test_failures_before_a_successful_sign_in_do_not_count(client, user):
    for _attempt in range(throttle.USER_FAILURES - 1):
        sign_in(client, "alice")
    assert sign_in(client, "alice", password="s3cret-pass").status_code == 302
    client.logout()
    sign_in(client, "alice")
    assert sign_in(client, "alice", password="s3cret-pass").status_code == 302


def test_the_lock_ends_after_the_window(client, user, monkeypatch):
    for _attempt in range(throttle.USER_FAILURES):
        sign_in(client, "alice")
    monkeypatch.setattr(throttle, "WINDOW", datetime.timedelta(seconds=0))
    assert sign_in(client, "alice", password="s3cret-pass").status_code == 302


def test_an_ip_is_locked_after_many_failures_whatever_the_usernames(client, user):
    for attempt in range(throttle.IP_FAILURES):
        sign_in(client, f"guess{attempt}")
    assert locked(sign_in(client, "alice", password="s3cret-pass"))
    assert sign_in(client, "alice", password="s3cret-pass", ip="10.0.0.2").status_code == 302  # another address


def test_reset_codes_are_capped_per_account(client, user, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat_id, text: sent.append(text) or True)
    user.telegram_chat_id = 4242
    user.save()
    for _request in range(throttle.CODES_PER_HOUR + 2):
        response = client.post(reverse("users:password_reset"), {"identifier": "alice"})
        assert response.status_code == 302  # the same answer every time
    assert len(sent) == throttle.CODES_PER_HOUR == PasswordResetCode.objects.count()


def test_passwords_need_ten_characters():
    with pytest.raises(ValidationError):
        validate_password("Kx7#mPq2z")
    validate_password("Kx7#mPq2zR")


def test_a_session_lasts_a_working_day():
    assert settings.SESSION_COOKIE_AGE == 12 * 60 * 60


# Accounts ---------------------------------------------------------------------------------------


def test_public_sign_up_is_off_by_default(client, db):
    assert settings.ALLOW_SIGNUP is False
    assert client.get(reverse("users:register")).status_code == 404
    data = {"username": "eve", "email": "eve@example.com", "password1": "a-long-Pass-42", "password2": "a-long-Pass-42"}
    assert client.post(reverse("users:register"), data).status_code == 404


# Redirects --------------------------------------------------------------------------------------


def test_cancelling_a_bill_never_redirects_off_the_site(client, finance):
    supplier = Company.objects.create(name="Поставщик", inn="300000001")
    bill = Bill.objects.create(supplier=supplier, date=datetime.date(2026, 10, 1), number="Т-1", amount=Decimal(1))
    client.force_login(finance)
    response = client.post(reverse("purchases:bill_cancel", args=[bill.pk]), {"next": "https://evil.example/"})
    assert response["Location"] == reverse("purchases:bills")
    response = client.post(reverse("purchases:bill_cancel", args=[bill.pk]), {"next": "/purchases/bills/?page=2"})
    assert response["Location"] == "/purchases/bills/?page=2"


# Scripts ----------------------------------------------------------------------------------------


def test_pages_send_a_content_security_policy_with_a_nonce(client, user):
    client.force_login(user)
    response = client.get(reverse("core:home"))
    policy = response["Content-Security-Policy"]
    nonce = re.search(r"'nonce-([^']+)'", policy).group(1)
    assert "script-src 'self' 'nonce-" in policy and "unsafe-inline" not in policy.split(";")[1]
    assert "frame-ancestors 'none'" in policy and "object-src 'none'" in policy
    # Inline scripts, not data blocks (json_script: type="application/json", never run).
    inline = re.findall(r"<script(?![^>]*\b(?:src=|type=\"application/json))([^>]*)>", response.content.decode())
    assert inline and all(f'nonce="{nonce}"' in attributes for attributes in inline)


def test_the_sign_in_page_scripts_carry_the_nonce(client, db):
    response = client.get(LOGIN)
    nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"]).group(1)
    assert response.content.decode().count(f'nonce="{nonce}"') == 2  # the theme settings and the theme switch


def test_templates_have_no_inline_handlers_or_javascript_links():
    """The policy blocks them, so they would silently stop working."""
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        if "static" in path.parts:
            continue  # the theme's own demo pages, never served as the app's pages
        text = path.read_text(encoding="utf-8")
        if re.search(r"\son[a-z]+=\"", text) or "javascript:" in text:
            offenders.append(path.name)
        script = r"<script(?![^>]*\b(?:src=|nonce=|type=\"application/json))[^>]*>"
        offenders += [path.name for _ in re.finditer(script, text)]
    assert offenders == []


# Excel files ------------------------------------------------------------------------------------


def test_a_partner_name_in_the_akt_stays_text_not_a_formula(client, finance):
    partner = Company.objects.create(name='=HYPERLINK("https://evil.example","Открыть")', inn="300000002")
    product = Product.objects.create(name="Чай", volume=Decimal("0.45"))
    shipment = ProductShipment.objects.create(date=datetime.date(2026, 10, 2), customer=partner)
    shipment.assign_number()
    ProductShipmentLine.objects.create(shipment=shipment, product=product, quantity=10, amount=Decimal(1000))
    shipment.rebuild_moves()
    client.force_login(finance)
    response = client.get(reverse("settlements:statement", args=[partner.pk]), {"export": "xlsx"})
    sheet = openpyxl.load_workbook(BytesIO(response.content)).active
    cells = [cell for row in sheet.iter_rows() for cell in row if isinstance(cell.value, str)]
    assert any("HYPERLINK" in cell.value for cell in cells)  # shown as typed
    assert all(cell.data_type != "f" for cell in cells)  # never as a formula


# Uploads ----------------------------------------------------------------------------------------


def test_an_oversized_sales_report_is_refused_before_it_is_read(client, finance, monkeypatch):
    from finance_saber.production import shipment_import, views

    monkeypatch.setattr(views, "MAX_WORKBOOK_SIZE", 10)
    monkeypatch.setattr(shipment_import, "read_report", lambda upload: pytest.fail("the file was read"))
    client.force_login(finance)
    upload = SimpleUploadedFile("report.xlsx", b"x" * 11)
    response = client.post(reverse("production:shipments_import"), {"file": upload}, follow=True)
    assert "слишком большой" in response.content.decode()
