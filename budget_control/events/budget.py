import frappe
from frappe import _
from frappe.utils import fmt_money
from budget_control.override.budget import get_actual_expense, get_ordered_amount, get_requested_amount


def validate(doc, method):
    if doc.custom_total_amount < 0:
        frappe.throw(_("The total amount cannot be negative"))

    check_budget_amount(doc)


def on_update_after_submit(doc, method):
    check_budget_amount(doc)


def check_budget_amount(doc):
    if doc.custom_total_amount > 0:
        if doc.custom_apply_all_expense_account:
            account_list = frappe.get_all("Account", {"company": doc.company, "report_type": "Profit and Loss", "is_group": 0}, pluck="name")
        else:
            account_list = frappe.db.get_all("Budget Account", {"parent": doc.name}, pluck="account")

        args = frappe._dict({
            "account_list": account_list,
            "budget_against_field": doc.budget_against.lower().replace(" ", "_"),
            doc.budget_against.lower().replace(" ", "_"): doc.get(doc.budget_against.lower().replace(" ", "_")),
            "budget_against_doctype": doc.budget_against,
            "company": doc.company,
            "fiscal_year": doc.fiscal_year,
            "is_tree": True if frappe.get_cached_value("DocType", doc.budget_against, "is_tree") else False,
        })

        args.actual_expense, args.requested_amount, args.ordered_amount = 0, 0, 0

        if doc.applicable_on_material_request:
            args.requested_amount = get_requested_amount(args)

        if doc.applicable_on_purchase_order:
            args.ordered_amount = get_ordered_amount(args)

        if doc.applicable_on_booking_actual_expenses:
            args.actual_expense = get_actual_expense(args)
        
        if doc.custom_outstanding_from_other_documents == "Yes":
            amount = args.actual_expense + args.requested_amount + args.ordered_amount
        else:
            amount = args.actual_expense

        if amount > doc.custom_total_amount:
            currency = frappe.db.get_value("Company", doc.company, "default_currency")
            msg = _(
                "The Annual Budget for {0} {1} is {2}. "
                "This budget has already been utilized, so the total amount cannot be less than or equal to this."
            ).format(
                frappe.unscrub(args.budget_against_field),
                frappe.bold(doc.get(doc.budget_against.lower().replace(" ", "_"))),
                frappe.bold(fmt_money(amount, currency=currency)),
            )
            frappe.throw(msg)
