"""Material receipts: a document from a supplier (number, date) with many material lines, like 1C's «Поступление»."""

import datetime
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest
from django.urls import reverse
from django.utils import timezone

from finance_saber.cashbook.models import Company
from finance_saber.production.models import Material, MaterialMove, MaterialReceipt, material_stock

D = Decimal
DAY = datetime.date(2026, 10, 22)
NEW = reverse("production:material_receipt")


@pytest.fixture
def supplier(db):
    return Company.objects.create(name="ООО Ромашка", inn="770000001")


@pytest.fixture
def sugar(db):
    return Material.objects.create(name="Сахар", unit="кг")


@pytest.fixture
def cans(db):
    return Material.objects.create(name="Банка", unit="шт", kind=Material.Kind.PACKAGING)


def receipt_data(supplier, lines, number="НК-101", date=DAY, **extra):
    """The new-receipt form's POST: the document and its lines (material, quantity)."""
    data = {
        "supplier": supplier.pk if supplier else "",
        "number": number,
        "date": date.isoformat(),
        "note": "",
        "lines-TOTAL_FORMS": str(len(lines)),
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "1",
        "lines-MAX_NUM_FORMS": "1000",
        **extra,
    }
    for index, (material, quantity) in enumerate(lines):
        data[f"lines-{index}-material"] = material.pk if material else ""
        data[f"lines-{index}-quantity"] = quantity
    return data


def post_receipt(client, *args, **kwargs):
    return client.post(NEW, receipt_data(*args, **kwargs))


# --- Entering a receipt ---------------------------------------------------------------


def test_one_document_adds_every_material_to_stock(client, technologist, supplier, sugar, cans):
    client.force_login(technologist)
    response = post_receipt(client, supplier, [(sugar, "1 250,5"), (cans, "4000")])
    receipt = MaterialReceipt.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("production:receipt", args=[receipt.pk])
    assert (receipt.supplier, receipt.number, receipt.date, receipt.created_by) == (
        supplier, "НК-101", DAY, technologist
    )  # fmt: skip
    assert [(line.material, line.quantity) for line in receipt.lines.all()] == [(sugar, D("1250.5")), (cans, D("4000"))]
    stock = material_stock()
    assert (stock[sugar.pk], stock[cans.pk]) == (D("1250.5"), D("4000"))
    moves = MaterialMove.objects.order_by("id")
    assert {move.kind for move in moves} == {MaterialMove.Kind.RECEIPT}
    assert all(move.made_by_document for move in moves)  # they change only with the document
    assert moves[0].note == "ООО Ромашка, № НК-101"


def test_supplier_and_number_are_required(client, technologist, sugar):
    client.force_login(technologist)
    response = post_receipt(client, None, [(sugar, "5")], number="")
    assert response.status_code == 200
    assert set(response.context["form"].errors) == {"supplier", "number"}
    assert not MaterialReceipt.objects.exists()


def test_a_receipt_needs_at_least_one_material(client, technologist, supplier):
    client.force_login(technologist)
    response = post_receipt(client, supplier, [(None, "")])
    assert response.status_code == 200
    assert response.context["lines"].non_form_errors()
    assert not MaterialReceipt.objects.exists()


def test_empty_extra_lines_are_ignored(client, technologist, supplier, sugar):
    client.force_login(technologist)
    post_receipt(client, supplier, [(sugar, "5"), (None, ""), (None, "")])
    assert MaterialReceipt.objects.get().lines.count() == 1


def test_the_same_material_twice_is_refused(client, technologist, supplier, sugar):
    client.force_login(technologist)
    response = post_receipt(client, supplier, [(sugar, "5"), (sugar, "7")])
    assert response.status_code == 200
    assert response.context["lines"].forms[1].errors["material"]
    assert not MaterialReceipt.objects.exists()


@pytest.mark.parametrize("quantity", ["0", "-3", "abc"])
def test_quantity_must_be_positive(client, technologist, supplier, sugar, quantity):
    client.force_login(technologist)
    assert post_receipt(client, supplier, [(sugar, quantity)]).status_code == 200
    assert not MaterialReceipt.objects.exists()


def test_the_same_invoice_of_a_supplier_is_entered_once(client, technologist, supplier, sugar):
    client.force_login(technologist)
    post_receipt(client, supplier, [(sugar, "5")], number="НК-101")
    response = post_receipt(client, supplier, [(sugar, "5")], number=" нк-101 ")  # spaces and case don't matter
    assert response.status_code == 200
    assert response.context["form"].errors["number"]
    other = Company.objects.create(name="ООО Другая", inn="770000002")
    assert post_receipt(client, other, [(sugar, "5")], number="НК-101").status_code == 302  # another supplier's
    assert MaterialReceipt.objects.count() == 2


def test_our_own_company_is_not_a_supplier(client, technologist, supplier):
    own = Company.objects.create(name="ООО Наша", inn="305900839", is_own=True)
    client.force_login(technologist)
    page = client.get(NEW).content.decode()
    assert f'<option value="{supplier.pk}">ООО Ромашка</option>' in page
    assert "ООО Наша" not in page
    assert post_receipt(client, own, [(Material.objects.create(name="Соль", unit="кг"), "1")]).status_code == 200


def test_the_date_is_today_by_default(client, technologist):
    client.force_login(technologist)
    page = client.get(NEW).content.decode()
    assert f'value="{timezone.localdate().isoformat()}"' in page


def test_form_page_shows_the_lines_once_a_supplier_is_chosen(client, technologist, supplier, sugar):
    client.force_login(technologist)
    page = client.get(NEW).content.decode()
    assert "data-supplier-first" in page
    assert "data-after-supplier hidden" in page
    assert 'id="material-units"' in page  # each line shows its material's unit
    # After an error, with a supplier already chosen, the lines stay visible.
    page = post_receipt(client, supplier, [(sugar, "0")]).content.decode()
    assert "data-after-supplier hidden" not in page


# --- Journal, document, cancelling ------------------------------------------------------


@pytest.fixture
def receipts(supplier, sugar, cans, technologist):
    other = Company.objects.create(name="ООО Другая", inn="770000002")
    made = []
    for company, number, date, lines in [
        (supplier, "A-1", DAY, [(sugar, D("10")), (cans, D("100"))]),
        (other, "B-1", DAY + datetime.timedelta(days=3), [(cans, D("50"))]),
    ]:
        receipt = MaterialReceipt.objects.create(supplier=company, number=number, date=date, created_by=technologist)
        for material, quantity in lines:
            receipt.lines.create(material=material, quantity=quantity)
        receipt.rebuild_moves()
        made.append(receipt)
    return made


def journal(client, **params):
    response = client.get(reverse("production:receipts"), params)
    assert response.status_code == 200
    return [receipt.number for receipt in response.context["page_obj"]]


def test_journal_filters_by_supplier_material_dates_and_number(client, finance, supplier, cans, sugar, receipts):
    client.force_login(finance)  # finance sees the journal
    assert journal(client) == ["B-1", "A-1"]  # newest first
    assert journal(client, supplier=supplier.pk) == ["A-1"]
    assert journal(client, material=sugar.pk) == ["A-1"]
    assert journal(client, material=cans.pk) == ["B-1", "A-1"]
    assert journal(client, **{"from": (DAY + datetime.timedelta(days=1)).isoformat()}) == ["B-1"]
    assert journal(client, to=DAY.isoformat()) == ["A-1"]
    assert journal(client, q="b-") == ["B-1"]
    receipts[0].number = "НК-7"
    receipts[0].save()
    assert journal(client, q="нк") == ["НК-7"]  # lower-case Cyrillic finds it too


def test_journal_lists_each_documents_materials(client, finance, receipts):
    client.force_login(finance)
    page = client.get(reverse("production:receipts")).content.decode()
    assert "Сахар" in page and "Банка" in page and "№ A-1" in page


def test_excel_has_one_row_per_material_with_the_filters(client, finance, supplier, receipts):
    client.force_login(finance)
    response = client.get(reverse("production:receipts_export"), {"supplier": supplier.pk})
    sheet = openpyxl.load_workbook(BytesIO(response.content)).active
    rows = [row[1:5] + row[6:7] for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert rows == [("A-1", "ООО Ромашка", "770000001", "Сахар", 10), ("A-1", "ООО Ромашка", "770000001", "Банка", 100)]


def test_cancelling_takes_its_materials_out_of_stock_and_restoring_brings_them_back(
    client, technologist, sugar, receipts
):
    first = receipts[0]
    client.force_login(technologist)
    client.post(reverse("production:receipt_cancel", args=[first.pk]))
    first.refresh_from_db()
    assert first.is_cancelled
    assert material_stock().get(sugar.pk, 0) == 0
    assert not first.moves().exists()
    client.post(reverse("production:receipt_cancel", args=[first.pk]))
    first.refresh_from_db()
    assert not first.is_cancelled
    assert material_stock()[sugar.pk] == D("10")


def test_a_cancelled_receipt_can_be_entered_again_but_then_not_restored(
    client, technologist, supplier, sugar, receipts
):
    first = receipts[0]
    client.force_login(technologist)
    client.post(reverse("production:receipt_cancel", args=[first.pk]))
    assert post_receipt(client, supplier, [(sugar, "12")], number="A-1").status_code == 302  # the corrected one
    client.post(reverse("production:receipt_cancel", args=[first.pk]))  # restoring would duplicate it
    first.refresh_from_db()
    assert first.is_cancelled
    assert material_stock()[sugar.pk] == D("12")


def test_receipt_movements_cant_be_cancelled_one_by_one(client, technologist, receipts):
    move = receipts[0].moves().first()
    client.force_login(technologist)
    assert client.post(reverse("production:move_cancel", args=["materials", move.pk])).status_code == 404


def test_document_page(client, finance, receipts):
    client.force_login(finance)
    page = client.get(reverse("production:receipt", args=[receipts[0].pk])).content.decode()
    assert "ООО Ромашка" in page and "770000001" in page and "Сахар" in page
    assert reverse("production:receipt_cancel", args=[receipts[0].pk]) not in page  # finance only views


# --- Who may do what ---------------------------------------------------------------------


def test_finance_sees_receipts_but_does_not_enter_or_cancel_them(client, finance, receipts):
    client.force_login(finance)
    assert client.get(NEW).status_code == 403
    assert client.post(reverse("production:receipt_cancel", args=[receipts[0].pk])).status_code == 403


def test_operator_has_no_access(client, operator, receipts):
    client.force_login(operator)
    assert client.get(reverse("production:receipts")).status_code == 403
    assert client.get(reverse("production:receipt", args=[receipts[0].pk])).status_code == 403


# --- Removing lines, typing numbers --------------------------------------------------------


def test_a_removed_line_is_not_saved_wherever_it_was(client, technologist, supplier, sugar, cans):
    salt = Material.objects.create(name="Соль", unit="кг")
    client.force_login(technologist)
    data = receipt_data(supplier, [(sugar, "5"), (cans, "abc"), (salt, "2")], **{"lines-1-DELETE": "on"})
    assert client.post(NEW, data).status_code == 302  # the removed line's bad quantity doesn't matter
    assert [line.material for line in MaterialReceipt.objects.get().lines.all()] == [sugar, salt]


def test_a_removed_line_doesnt_count_as_a_repeated_material(client, technologist, supplier, sugar):
    client.force_login(technologist)
    data = receipt_data(supplier, [(sugar, "5"), (sugar, "7")], **{"lines-0-DELETE": "on"})
    assert client.post(NEW, data).status_code == 302
    assert MaterialReceipt.objects.get().lines.get().quantity == D("7")


def test_removing_every_line_is_refused(client, technologist, supplier, sugar):
    client.force_login(technologist)
    data = receipt_data(supplier, [(sugar, "5")], **{"lines-0-DELETE": "on"})
    response = client.post(NEW, data)
    assert response.status_code == 200
    assert response.context["lines"].non_form_errors()


def test_a_removed_line_stays_removed_after_an_error(client, technologist, supplier, sugar, cans):
    client.force_login(technologist)
    data = receipt_data(supplier, [(sugar, "5"), (cans, "9")], number="", **{"lines-1-DELETE": "on"})
    response = client.post(NEW, data)
    assert response.status_code == 200  # the number is missing
    rows = response.content.decode().split('data-lines="lines"')[1].split("</tbody>")[0].split("<tr")[1:]
    assert [row.startswith(" hidden") for row in rows] == [False, True]
    assert 'name="lines-1-DELETE" value="on"' in response.content.decode()


def test_form_has_number_inputs_and_remove_buttons(client, technologist):
    client.force_login(technologist)
    page = client.get(NEW).content.decode()
    assert 'data-number="4"' in page
    assert page.count("data-remove-line") == 4  # three lines and the empty one for "Add a line"
    assert 'type="hidden" name="lines-0-DELETE"' in page


@pytest.mark.parametrize(
    ("typed", "saved"),
    [("0,125", "0.125"), ("12,3456", "12.3456"), ("1 250,5", "1250.5"), ("1250.5", "1250.5"), ("1 000 000", "1000000")],
)
def test_quantities_are_read_as_typed(client, technologist, supplier, sugar, typed, saved):
    client.force_login(technologist)
    post_receipt(client, supplier, [(sugar, typed)])
    assert MaterialReceipt.objects.get().lines.get().quantity == D(saved)
