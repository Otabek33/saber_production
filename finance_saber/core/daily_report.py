"""The daily Telegram report: the main parts of today's dashboard, as a message.

Built from ``dashboard.build`` for the person it goes to, so the figures are the ones
on the home page and each role sees only its own blocks. People switch it on and pick
the time in their profile; ``send_due`` sends whoever's time has come and is run every
minute by ``telegram_poll`` (local) or every few minutes by ``send_daily_reports`` from
cron (server). Each person gets it at most once a day, however often those run.
"""

import datetime
import html
import logging
from decimal import Decimal

from django.conf import settings
from django.utils import formats, timezone, translation
from django.utils.translation import gettext as _

from . import dashboard

logger = logging.getLogger(__name__)
MAX_MESSAGE = 4096  # Telegram's limit per message
LIST_LIMIT = 8  # rows per list (regions, debtors, products) so the message stays readable


def esc(value) -> str:
    """Text from the database, safe inside Telegram HTML."""
    return html.escape(str(value), quote=False)


def number(value, decimals=0) -> str:
    return formats.number_format(value, decimals, use_l10n=True, force_grouping=True)


def money(value) -> str:
    """Short amount for a phone screen: 1,25 млрд · 57,5 млн · 950 000."""
    value = Decimal(value or 0)
    if abs(value) >= 1_000_000_000:
        text = f"{number(value / 1_000_000_000, 2)} {_('bn')}"
    elif abs(value) >= 1_000_000:
        text = f"{number(value / 1_000_000, 1)} {_('mln')}"
    else:
        text = number(value)
    return text.replace("-", "−", 1)  # a real minus sign reads better than a hyphen


def signed(value) -> str:
    return f"+{money(value)}" if value > 0 else money(value)


def quantity(value) -> str:
    value = Decimal(value or 0)
    return number(value, 0 if value == value.to_integral_value() else 2)


def share(value) -> str:
    return f" ({value}%)" if value is not None else ""


def channel_line(channels, skip_zero=False) -> str:
    parts = []
    for channel in channels:
        if skip_zero and not channel["amount"]:
            continue
        part = f"{channel['label']} {money(channel['amount'])}"
        if channel["usd"]:
            part += " " + _("(incl. %(usd)s USD)") % {"usd": number(channel["usd"])}
        parts.append(part)
    return "   " + " · ".join(parts) if parts else ""


def render(user, day: datetime.date | None = None) -> str:
    """The report for ``user`` on ``day`` (today by default), as Telegram HTML."""
    from finance_saber.cashbook.models import Kind

    day = day or timezone.localdate()
    data = dashboard.build(user, dashboard.Period(day, day, "today"))
    lines = [f"📊 <b>{esc(settings.SITE_NAME)} · {_('Daily report')} · {day:%d.%m.%Y}</b>"]
    if data["rate"]:
        lines.append(_("Rate: %(rate)s UZS per USD") % {"rate": number(data["rate"])})
    shown = len(lines)

    if "money" in data:
        lines += ["", f"💰 <b>{_('Money at day end')}: {money(data['money']['total_uzs'])} UZS</b>"]
        lines.append(channel_line(data["money"]["channels"]))
    if "cash_flow" in data:
        flow = data["cash_flow"]
        income, expense = flow["totals"][Kind.INCOME], flow["totals"][Kind.EXPENSE]
        lines.append("")
        for icon, label, total in (("🟢", _("Money in"), income), ("🔴", _("Money out"), expense)):
            plan = _(", %(percent)s%% of plan") % {"percent": total["percent"]} if total["percent"] is not None else ""
            lines.append(f"{icon} {label}: <b>{money(total['total'])}</b>{plan}")
            if total["total"]:
                lines.append(channel_line(total["channels"], skip_zero=True))
        lines.append(f"⚖️ {_('Net cash flow')}: <b>{signed(flow['net'])}</b>")
    if "sales" in data:
        sales = data["sales"]
        lines += ["", f"🚚 {_('Shipped')}: <b>{money(sales['amount'])}</b>"]
        if sales["amount"]:
            lines[-1] += " · " + _("%(quantity)s units to %(clients)s clients") % {
                "quantity": quantity(sales["quantity"]),
                "clients": sales["clients"],
            }
    if "debts" in data:
        debts = data["debts"]
        lines.append(
            f"🧾 {_('Owed to us')}: <b>{money(debts['receivable'])}</b> "
            + _("(%(count)s debtors)") % {"count": debts["debtor_count"]}
        )
        if debts["payable"]:
            lines.append("   " + _("Paid in advance: %(amount)s") % {"amount": money(debts["payable"])})
    products = data.get("products")
    if products and "sales" in products["totals"]:
        label = _("Sales plan for %(month)s") % {"month": formats.date_format(products["month"], "F").lower()}
        lines.append(
            f"🎯 {label}: <b>{products['totals']['sales']['percent'] or 0}%</b> "
            + _("(%(progress)s%% of the month gone)") % {"progress": products["month_progress"]}
        )

    if "week" in data:
        week = data["week"]
        start, end = week["days"][0]["date"], week["days"][-1]["date"]
        title = _("Week %(start)s–%(end)s: plan and fact") % {"start": f"{start:%d.%m}", "end": f"{end:%d.%m}"}
        lines += ["", f"📅 <b>{title}</b>"]
        for label, part in ((_("Money in"), week["income"]), (_("Money out"), week["expense"])):
            done = share(dashboard.percent(part["fact"], part["plan"]))
            amounts = _("fact %(fact)s / plan %(plan)s") % {"fact": money(part["fact"]), "plan": money(part["plan"])}
            lines.append(f"   {label}: {amounts}{done}")
    if data.get("collections") and data["collections"]["rows"]:
        collections = data["collections"]
        lines += ["", f"🗺 <b>{_('Collections by region')}</b> ({_('fact / plan')})"]
        rows = sorted(collections["rows"], key=lambda row: -row["fact"])
        for row in rows[:LIST_LIMIT]:
            lines.append(
                f"   {esc(row['region'].name)}: {money(row['fact'])} / {money(row['plan'])}{share(row['percent'])}"
            )
        if len(rows) > LIST_LIMIT:
            lines.append("   " + _("…and %(count)s more") % {"count": len(rows) - LIST_LIMIT})
        lines.append(
            f"   <b>{_('Total')}: {money(collections['fact'])} / {money(collections['plan'])}"
            f"{share(collections['percent'])}</b>"
        )
    if products and products["rows"]:
        title = _("%(month)s: plan and fact") % {"month": formats.date_format(products["month"], "F").capitalize()}
        lines += ["", f"🏭 <b>{title}</b>"]
        for row in products["rows"][: LIST_LIMIT + 4]:
            parts = []
            if "production" in row and (row["production"]["plan"] or row["production"]["fact"]):
                part = row["production"]
                parts.append(f"{_('made')} {quantity(part['fact'])}/{quantity(part['plan'])}{share(part['percent'])}")
            if "sales" in row and (row["sales"]["plan"] or row["sales"]["fact"]):
                part = row["sales"]
                parts.append(f"{_('sold')} {quantity(part['fact'])}/{quantity(part['plan'])}{share(part['percent'])}")
            lines.append(f"   {esc(row['product'].name)}: {', '.join(parts)}")
    if "debts" in data and data["debts"]["top"]:
        lines += ["", f"👥 <b>{_('Largest debtors')}</b>"]
        for i, row in enumerate(data["debts"]["top"], 1):
            lines.append(f"   {i}. {esc(row.name)} — {money(row.balance)}")
    if "stock" in data:
        stock = data["stock"]
        lines += ["", f"📦 <b>{_('Warehouse')}</b>"]
        if stock["short"]:
            lines.append("   ⚠️ " + _("Materials short: %(count)s") % {"count": stock["short_count"]})
            for row in stock["short"]:
                material = row["material"]
                lines.append(f"   • {esc(material.name)}: {quantity(row['stock'])} {esc(material.unit)}")
        else:
            lines.append("   " + _("No materials are short."))
        lines.append("   " + _("Finished goods: %(units)s units") % {"units": quantity(stock["goods_units"])})

    if len(lines) == shown:
        lines += ["", _("Your role has no dashboard data to show.")]
    if settings.SITE_URL:
        lines += ["", f'<a href="{html.escape(settings.SITE_URL)}/">{_("Open the dashboard")}</a>']
    return "\n".join(lines).strip()


# Each cash desk's day (like its page, e.g. /cash/bon/) ------------------------------------

ACCOUNT_EMOJI = {"cash_uzs": "💵", "cash_usd": "💲", "card": "💳", "bank": "🏦"}
OPERATIONS_LIMIT = 12  # per section (money in / out / transfers); the rest is summed up


def in_currency(value, currency) -> str:
    return f"{money(value)} USD" if currency == "USD" else money(value)


def totals_text(operations, sign="") -> str:
    """ "+5,2 млн и 120 USD": the operations' sum, per currency."""
    from finance_saber.cashbook.models import ACCOUNT_CURRENCY

    by_currency = {}
    for operation in operations:
        currency = ACCOUNT_CURRENCY[operation.account]
        by_currency[currency] = by_currency.get(currency, dashboard.ZERO) + operation.amount
    return f" {_('and')} ".join(
        f"{sign}{in_currency(amount, currency)}" for currency, amount in sorted(by_currency.items(), reverse=True)
    )


def operation_line(operation, sign: str, several_accounts: bool) -> str:
    """• 💳 +1,2 млн — Продажи › Оплата клиентов · ООО «Клиент» · note"""
    from finance_saber.cashbook.models import ACCOUNT_CURRENCY, Account, Kind

    currency = ACCOUNT_CURRENCY[operation.account]
    parts = [f"<b>{sign}{in_currency(operation.amount, currency)}</b>"]
    if operation.kind == Kind.TRANSFER:
        route = f"{Account(operation.account).label} → {Account(operation.to_account).label}"
        if operation.to_amount and ACCOUNT_CURRENCY[operation.to_account] != currency:
            route += f" ({in_currency(operation.to_amount, ACCOUNT_CURRENCY[operation.to_account])})"
        parts.append(esc(route))
    else:
        category = operation.category
        same = category.name.strip().lower() == category.article.name.strip().lower()
        parts.append(esc(category.name if same else category))  # "Дивиденд", not "Дивиденд › Дивиденд"
    if operation.counterparty:
        parts.append(esc(operation.counterparty.name))
    if operation.note:
        note = operation.note if len(operation.note) <= 60 else operation.note[:59] + "…"
        parts.append(f"<i>{esc(note)}</i>")
    icon = f"{ACCOUNT_EMOJI.get(operation.account, '')} " if several_accounts else ""
    return f"• {icon}{parts[0]} — {' · '.join(parts[1:])}"


def desk_text(desk, rows: list[dict], day: datetime.date) -> str:
    """One cash desk's day: each account's balance and movement, then its operations."""
    from finance_saber.cashbook.models import Kind

    lines = [f"🗂 <b>{esc(desk.name)}</b> · {day:%d.%m.%Y}"]
    for row in rows:
        currency = row["currency"]
        lines.append(
            f"{ACCOUNT_EMOJI.get(row['account'], '')} {esc(row['account'].label)}: "
            f"<b>{in_currency(row['closing'], currency)}</b>"
        )
        if row["in"] or row["out"]:
            lines.append(
                "   "
                + _("at start %(opening)s · in +%(in)s · out −%(out)s")
                % {"opening": money(row["opening"]), "in": money(row["in"]), "out": money(row["out"])}
            )
    operations = list(
        desk.operations.posted()
        .filter(date=day)
        .select_related("category__article", "counterparty")
        .order_by("-amount")
    )
    if not operations:
        lines += ["", _("No operations this day.")]
    several_accounts = len(desk.accounts) > 1
    sections = (
        (Kind.INCOME, "🟢", _("Money in"), "+"),
        (Kind.EXPENSE, "🔴", _("Money out"), "−"),
        (Kind.TRANSFER, "🔁", _("Transfers between accounts"), ""),
    )
    for kind, icon, title, sign in sections:
        chosen = [operation for operation in operations if operation.kind == kind]
        if not chosen:
            continue
        count = _("%(count)s op.") % {"count": len(chosen)}
        lines += ["", f"{icon} <b>{title}: {totals_text(chosen, sign)}</b> ({count})"]
        lines += [operation_line(operation, sign, several_accounts) for operation in chosen[:OPERATIONS_LIMIT]]
        rest = chosen[OPERATIONS_LIMIT:]
        if rest:
            lines.append(
                _("…and %(count)s more for %(amount)s") % {"count": len(rest), "amount": totals_text(rest, sign)}
            )
    if settings.SITE_URL:
        url = f"{settings.SITE_URL}{desk.get_absolute_url()}?date_from={day.isoformat()}&date_to={day.isoformat()}"
        lines += ["", f'<a href="{html.escape(url)}">{_("Open the cash desk")}</a>']
    return "\n".join(lines)


def desk_texts(user, day: datetime.date) -> list[str]:
    """A message per active cash desk (Касса БОН, Инкасса, Банк…), for people who may see the cash book."""
    from finance_saber.cashbook.models import CashDesk

    if not user.has_perm("cashbook.view_operation"):
        return []
    rows = dashboard.accounts(dashboard.Period(day, day, "today"), dashboard.exchange_rate(day))
    return [
        desk_text(desk, [row for row in rows if row["desk"] == desk], day)
        for desk in CashDesk.objects.filter(is_active=True)
    ]


def messages(user, day: datetime.date | None = None) -> list[str]:
    """The whole report: the summary first, then each cash desk's day."""
    day = day or timezone.localdate()
    return [render(user, day), *desk_texts(user, day)]


def split(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Cut at line ends into messages Telegram accepts; blocks never break mid-line."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + 1 + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    return [*chunks, current] if current else chunks


def send(user, day: datetime.date | None = None) -> bool:
    """Send the report to the user's linked chat. False if not linked or Telegram refused it."""
    from finance_saber.users import telegram

    if not user.telegram_chat_id:
        return False
    with translation.override(settings.LANGUAGE_CODE):
        texts = messages(user, day)
    chunks = [chunk for text in texts for chunk in split(text)]
    return all(telegram.send_message(user.telegram_chat_id, chunk, parse_mode="HTML") for chunk in chunks)


def send_due(now: datetime.datetime | None = None) -> int:
    """Send today's report to everyone whose time has come and who hasn't had it today.

    Safe to run as often as wanted, from several processes: each person is claimed in
    the database before sending, and released again if Telegram refuses the message.
    """
    from finance_saber.users.models import User

    local = timezone.localtime(now)
    today = local.date()
    due = (
        User.objects.filter(
            is_active=True, daily_report=True, telegram_chat_id__isnull=False, daily_report_time__lte=local.time()
        )
        .exclude(daily_report_sent_on=today)
        .order_by("pk")
    )
    sent = 0
    for user in due:
        previous = user.daily_report_sent_on
        claimed = User.objects.filter(pk=user.pk).exclude(daily_report_sent_on=today)
        if not claimed.update(daily_report_sent_on=today):
            continue  # another process got there first
        try:
            ok = send(user, today)
        except Exception:
            logger.exception("Daily report for %s failed", user)
            ok = False
        if ok:
            sent += 1
        else:
            User.objects.filter(pk=user.pk).update(daily_report_sent_on=previous)  # try again next run
    return sent
