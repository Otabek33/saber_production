"""Companies with filials from SmartUp's «Юридическое лицо» export: heads by INN, filials by owner ID, bank accounts."""

from io import BytesIO

import openpyxl
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.urls import reverse

from finance_saber.cashbook import company_files
from finance_saber.cashbook.models import Company, CompanyBankAccount, Counterparty

# SmartUp's header row as it writes it: "(*)" on required columns, two spaces in "ИД  владельца".
HEADER = [
    "ИД", "Код", "ИД  владельца", "Код владельца", "Владелец", "Штрих-код", "ИНН(*)", "Название(*)",
    "Альтернативное название", "ОКЭД", "НДС", "МФО", "Расчетный счет(основной)", "Банк(основной)",
    "Код валюты (пустое поле означает базовую валюту)", "Страна", "Регион", "Город/поселок", "Улица/район", "Адрес",
    "Почтовый адрес", "Ориентир", "Почтовый индекс", "Группа", "Категория", "Тип", "Телефон", "Email",
    "GPS координаты", "Рабочая зона ",
]  # fmt: skip


def row(smartup_id, name, inn="", owner="", filial="N", **extra):
    values = {"ИД": smartup_id, "Название(*)": name, "ИНН(*)": inn, "ИД  владельца": owner, "Владелец": filial}
    values |= {key.replace("_", " "): value for key, value in extra.items()}
    return values


def workbook(rows):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Импорт"
    sheet.append(HEADER)
    for values in rows:
        sheet.append([values.get(header, values.get(header.strip())) for header in HEADER])
    buffer = BytesIO()
    book.save(buffer)
    buffer.seek(0)
    return buffer


def load(rows, **kwargs):
    report = company_files.load_companies(workbook(rows), **kwargs)
    assert report.errors == []
    return report


RETAIL = [
    row("100", 'OOO "NEW RETAIL"', inn="308254105", Адрес="Ташкент, Чиланзар 1", Телефон="+998901112233"),
    row("101", '"NEW RETAIL" 001 Optom', inn="308254105", owner="100", filial="Y", Тип="КАМ Supermarket"),
    row("102", "Korzinka Чирчик", owner="100", filial="Y", GPS_координаты="41.469,69.582,12"),
    row("103", "Korzinka Чирчик 2", owner="102", filial="Y"),  # a filial of a filial
]


@pytest.fixture
def retail_from_statements(db):
    """The company as bank statements created it, with its statement account."""
    company = Company.objects.create(name="NEW RETAIL MCHJ (выписка)", inn="308254105")
    Counterparty.objects.create(reference="2020/308254105/NEW RETAIL", name="NEW RETAIL", company=company)
    return company


def test_a_head_company_found_by_inn_keeps_its_name_and_gets_smartups_details(retail_from_statements):
    report = load(RETAIL)
    company = Company.objects.get(smartup_id="100")
    assert company == retail_from_statements
    assert company.name == "NEW RETAIL MCHJ (выписка)"
    assert (company.address, company.phone, company.parent) == ("Ташкент, Чиланзар 1", "+998901112233", None)
    assert (report.created, report.updated) == (3, 1)


def test_filials_go_under_their_owner_and_may_share_its_inn(retail_from_statements):
    load(RETAIL)
    optom, shop, second_shop = (Company.objects.get(smartup_id=id) for id in ("101", "102", "103"))
    assert optom.parent == shop.parent == retail_from_statements
    assert (optom.inn, shop.inn) == ("308254105", "")
    assert second_shop.parent == shop and second_shop.head == retail_from_statements
    assert optom.kind == "КАМ Supermarket"
    assert shop.map_url == "https://maps.google.com/?q=41.469,69.582"


def test_loading_the_file_again_changes_nothing_new(retail_from_statements):
    load(RETAIL)
    report = load([*RETAIL[:1], row("101", '"NEW RETAIL" 001 Optom (новое имя)', owner="100", filial="Y")])
    assert (report.created, report.updated, report.filials) == (0, 2, 0)
    assert Company.objects.count() == 4
    assert Company.objects.get(smartup_id="101").name == '"NEW RETAIL" 001 Optom (новое имя)'  # SmartUp's name


def test_a_filial_waits_for_its_owner_until_a_file_brings_it(db):
    report = load([row("201", "Korzinka K039", owner="999", filial="Y")])
    shop = Company.objects.get(smartup_id="201")
    assert report.waiting == {"999"}
    assert shop.parent is None and shop.smartup_owner_id == "999"
    report = load([row("999", "ANGLESEY FOOD", inn="202099756")])
    shop.refresh_from_db()
    assert shop.parent.name == "ANGLESEY FOOD"
    assert report.filials == 1 and report.waiting == set()


def test_a_second_head_row_with_a_taken_inn_goes_under_the_first(db):
    load([row("301", '"XALQ RETAIL" MCHJ', inn="306955509"), row("302", '"XALQ RETAIL" Olma', inn="306955509")])
    first, second = Company.objects.get(smartup_id="301"), Company.objects.get(smartup_id="302")
    assert second.parent == first
    assert list(Company.objects.heads().filter(inn="306955509")) == [first]


def test_owner_given_to_a_row_without_its_own_inn_makes_it_a_filial_but_not_a_company_with_its_inn(db):
    load([
        row("401", "(РЦ) HAVAS FOOD", inn="305645847", owner="400"),  # N with its INN: stays a head company
        row("402", "H317 Massiv Feruza", owner="401"),  # N, a shop without INN: a filial
    ])  # fmt: skip
    head, shop = Company.objects.get(smartup_id="401"), Company.objects.get(smartup_id="402")
    assert head.parent is None and head.smartup_owner_id == ""
    assert shop.parent == head


def test_a_filial_is_never_taken_for_the_company_with_its_inn(retail_from_statements):
    load([row("501", "M140 ВУЗГОРОДОК", inn="308254105", owner="777", filial="Y")])
    shop = Company.objects.get(smartup_id="501")
    assert shop != retail_from_statements
    assert retail_from_statements.parent is None and retail_from_statements.smartup_id == ""


def test_bank_account_is_kept_and_its_statement_counterparty_connected(db):
    statement = Counterparty.objects.create(
        reference="20208000307008679001/311100748/7S BEVERAGES", name="7S", account_number="20208000307008679001"
    )
    report = load([
        row("601", '"7S BEVERAGES" MCHJ', inn="311100748", МФО="01095", **{
            "Расчетный счет(основной)": "20208000307008679001", "Банк(основной)": "Saber water",
            "Код валюты (пустое поле означает базовую валюту)": "860",
        }),
    ])  # fmt: skip
    company = Company.objects.get(smartup_id="601")
    [account] = company.bank_accounts.all()
    assert (account.mfo, account.account_number, account.bank, account.currency) == (
        "01095", "20208000307008679001", "Saber water", "860",
    )  # fmt: skip
    statement.refresh_from_db()
    assert statement.company == company
    assert (report.requisites, report.linked) == (1, 1)


def test_dry_run_and_errors_save_nothing(db):
    load(RETAIL, dry_run=True)
    assert not Company.objects.exists()
    report = company_files.load_companies(workbook([*RETAIL, row("700", "", inn="1")]))
    assert report.errors and not Company.objects.exists()


def test_download_loads_back_into_the_same_companies(retail_from_statements, tmp_path):
    load(RETAIL)
    CompanyBankAccount.objects.create(company=retail_from_statements, mfo="00001", account_number="2020800001")
    path = company_files.write_seed_file(tmp_path / "companies.xlsx")
    tree = {company.name: (company.parent and company.parent.name, company.inn) for company in Company.objects.all()}
    Counterparty.objects.update(company=None)
    CompanyBankAccount.objects.all().delete()
    for company in Company.objects.order_by("-parent_id"):  # filials first
        company.delete()
    report = company_files.load_companies(path)
    assert report.errors == []
    assert {
        company.name: (company.parent and company.parent.name, company.inn) for company in Company.objects.all()
    } == tree
    head = Company.objects.get(smartup_id="100")
    assert head.bank_accounts.get().account_number == "2020800001"
    assert head.accounts.get().reference == "2020/308254105/NEW RETAIL"


def test_head_inn_is_unique_but_filials_may_repeat_it(retail_from_statements):
    Company.objects.create(name="Shop", inn=retail_from_statements.inn, parent=retail_from_statements)
    with pytest.raises(IntegrityError):
        Company.objects.create(name="Another head", inn=retail_from_statements.inn)


# --- Pages --------------------------------------------------------------------------------


def test_list_shows_head_companies_and_search_finds_filials(client, finance, retail_from_statements):
    load(RETAIL)
    client.force_login(finance)
    url = reverse("cashbook:companies")
    heads = [company.name for company in client.get(url).context["companies"]]
    assert heads == ["NEW RETAIL MCHJ (выписка)"]
    found = [company.name for company in client.get(url, {"q": "korzinka"}).context["companies"]]
    assert found == ["Korzinka Чирчик", "Korzinka Чирчик 2"]
    assert len(client.get(url, {"show": "filials"}).context["companies"]) == 3


def test_company_page_shows_its_filials_and_head(client, finance, retail_from_statements):
    load(RETAIL)
    client.force_login(finance)
    page = client.get(retail_from_statements.get_absolute_url())
    assert [filial.name for filial in page.context["filials"]] == ['"NEW RETAIL" 001 Optom', "Korzinka Чирчик"]
    second_shop = Company.objects.get(smartup_id="103")
    chain = client.get(second_shop.get_absolute_url()).context["chain"]
    assert [company.name for company in chain] == ["NEW RETAIL MCHJ (выписка)", "Korzinka Чирчик"]


def test_form_adds_a_filial_with_its_bank_account_and_refuses_a_loop(client, finance, retail_from_statements):
    load(RETAIL)
    client.force_login(finance)
    add = reverse("cashbook:company_add")
    assert client.get(add, {"parent": retail_from_statements.pk}).context["form"].initial["parent"] == str(
        retail_from_statements.pk
    )
    response = client.post(add, {
        "name": "Новый магазин", "parent": retail_from_statements.pk, "requisites-TOTAL_FORMS": "1",
        "requisites-INITIAL_FORMS": "0", "requisites-0-mfo": "00444", "requisites-0-account_number": "2020",
    })  # fmt: skip
    shop = Company.objects.get(name="Новый магазин")
    assert response["Location"] == shop.get_absolute_url()
    assert shop.parent == retail_from_statements and shop.bank_accounts.get().mfo == "00444"
    # The head company can't go under its own filial: the filials aren't offered.
    form = client.get(reverse("cashbook:company_edit", args=[retail_from_statements.pk])).context["form"]
    assert shop not in form.fields["parent"].queryset
    response = client.post(reverse("cashbook:company_edit", args=[retail_from_statements.pk]), {
        "name": retail_from_statements.name, "inn": retail_from_statements.inn, "parent": shop.pk,
        "requisites-TOTAL_FORMS": "0", "requisites-INITIAL_FORMS": "0",
    })  # fmt: skip
    assert response.status_code == 200
    retail_from_statements.refresh_from_db()
    assert retail_from_statements.parent is None


def test_upload_page_reports_the_counts(client, finance):
    client.force_login(finance)
    file = SimpleUploadedFile("Юридическое лицо.xlsx", workbook(RETAIL).getvalue())
    response = client.post(reverse("cashbook:companies_import"), {"file": file}, follow=True)
    text = " ".join(str(message) for message in response.context["messages"])
    assert "4" in text and Company.objects.count() == 4
