import frappe
from frappe import _
from frappe.utils import flt, get_last_day, fmt_money
from erpnext.accounts.utils import get_fiscal_year
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_accounting_dimensions,
)
from erpnext.accounts.doctype.budget.budget import (
	get_item_details,
	get_actions,
	get_accumulated_monthly_budget,
	BudgetError
)

"""
	Budget Control Validate Expense Against Budget Function is Override For Apply Total Amount Apply For 
	All Expense Account of Child Table or Company Expense All Account

	File Override Path : erpnext/accounts/doctype/budget/budget.py
"""

def validate_expense_against_budget(args, expense_amount=0):
	args = frappe._dict(args)
	if not frappe.get_all("Budget", limit=1):
		return

	if args.get("company") and not args.fiscal_year:
		args.fiscal_year = get_fiscal_year(args.get("posting_date"), company=args.get("company"))[0]
		frappe.flags.exception_approver_role = frappe.get_cached_value(
			"Company", args.get("company"), "exception_budget_approver_role"
		)

	if not frappe.get_cached_value("Budget", {"fiscal_year": args.fiscal_year, "company": args.company}):  # nosec
		return

	if not args.account:
		args.account = args.get("expense_account")

	if not (args.get("account") and args.get("cost_center")) and args.item_code:
		args.cost_center, args.account = get_item_details(args)

	if not args.account:
		return

	default_dimensions = [
		{
			"fieldname": "project",
			"document_type": "Project",
		},
		{
			"fieldname": "cost_center",
			"document_type": "Cost Center",
		},
	]

	for dimension in default_dimensions + get_accounting_dimensions(as_list=False):
		budget_against = dimension.get("fieldname")

		if (
			args.get(budget_against)
			and args.account
			and (frappe.get_cached_value("Account", args.account, "root_type") == "Expense")
		):
			doctype = dimension.get("document_type")

			if frappe.get_cached_value("DocType", doctype, "is_tree"):
				lft, rgt = frappe.get_cached_value(doctype, args.get(budget_against), ["lft", "rgt"])
				condition = f"""and exists(select name from `tab{doctype}`
					where lft<={lft} and rgt>={rgt} and name=b.{budget_against})"""  # nosec
				args.is_tree = True
			else:
				condition = f"and b.{budget_against}={frappe.db.escape(args.get(budget_against))}"
				args.is_tree = False

			args.budget_against_field = budget_against
			args.budget_against_doctype = doctype


			# -------- Customization Part ---------
			# Change the query to add total_amount and check for apply_all_expense_account or not
			budget_records = frappe.db.sql(
				f"""
				select
					b.name, b.custom_total_amount, b.custom_apply_all_expense_account, b.custom_outstanding_from_other_documents,
					b.{budget_against} as budget_against, ba.budget_amount, b.monthly_distribution,
					ifnull(b.applicable_on_material_request, 0) as for_material_request,
					ifnull(applicable_on_purchase_order, 0) as for_purchase_order,
					ifnull(applicable_on_booking_actual_expenses,0) as for_actual_expenses,
					b.action_if_annual_budget_exceeded, b.action_if_accumulated_monthly_budget_exceeded,
					b.action_if_annual_budget_exceeded_on_mr, b.action_if_accumulated_monthly_budget_exceeded_on_mr,
					b.action_if_annual_budget_exceeded_on_po, b.action_if_accumulated_monthly_budget_exceeded_on_po
				from
					`tabBudget` b, `tabBudget Account` ba
				where
					b.name=ba.parent and b.fiscal_year=%s
					and ba.account=%s and b.docstatus=1
					{condition}
			""",
				(args.fiscal_year, args.account),
				as_dict=True,
			)  # nosec

			# If no record found against for any expense account then, check for apply_all_expense_account checked then apply particular budget
			if not budget_records:
				budget_records = frappe.db.sql(
					f"""
					select
						b.name, b.custom_total_amount, b.custom_apply_all_expense_account, b.custom_outstanding_from_other_documents,
						b.{budget_against} as budget_against, 0 as budget_amount, b.monthly_distribution,
						ifnull(b.applicable_on_material_request, 0) as for_material_request,
						ifnull(b.applicable_on_purchase_order, 0) as for_purchase_order,
						ifnull(b.applicable_on_booking_actual_expenses, 0) as for_actual_expenses,
						b.action_if_annual_budget_exceeded, b.action_if_accumulated_monthly_budget_exceeded,
						b.action_if_annual_budget_exceeded_on_mr, b.action_if_accumulated_monthly_budget_exceeded_on_mr,
						b.action_if_annual_budget_exceeded_on_po, b.action_if_accumulated_monthly_budget_exceeded_on_po
					from
						`tabBudget` b
					where
						b.fiscal_year = %s
						and b.custom_apply_all_expense_account = 1
						and b.docstatus = 1
						{condition}
					""",
					(args.fiscal_year,),
					as_dict=True,
				)
			# -------- Customization Part END ---------

			if budget_records:
				validate_budget_records(args, budget_records, expense_amount)


def validate_budget_records(args, budget_records, expense_amount):
	for budget in budget_records:
		# -------- Customization Part ---------
		# If custom_total_amount is set then check for apply_all_expense_account then fetch all expense account
		if budget.custom_total_amount:
			if budget.custom_apply_all_expense_account:
				account_list = frappe.get_all("Account", {"company": args.company, "report_type": "Profit and Loss", "is_group": 0}, pluck="name")
			else:
				account_list = frappe.db.get_all("Budget Account", {"parent": budget.name}, pluck="account")

			args.update({
				"account_list": account_list,
			})
			budget.budget_amount = budget.custom_total_amount
		else:
			args.account_list = [args.account]
		# -------- Customization Part END ---------

		if flt(budget.budget_amount):
			yearly_action, monthly_action = get_actions(args, budget)
			args["for_material_request"] = budget.for_material_request
			args["for_purchase_order"] = budget.for_purchase_order

			if yearly_action in ("Stop", "Warn"):
				compare_expense_with_budget(
					args,
					flt(budget.budget_amount),
					_("Annual"),
					yearly_action,
					budget.budget_against,
					expense_amount,
					budget.custom_outstanding_from_other_documents
				)

			if monthly_action in ["Stop", "Warn"]:
				budget_amount = get_accumulated_monthly_budget(
					budget.monthly_distribution, args.posting_date, args.fiscal_year, budget.budget_amount
				)

				args["month_end_date"] = get_last_day(args.posting_date)

				compare_expense_with_budget(
					args,
					budget_amount,
					_("Accumulated Monthly"),
					monthly_action,
					budget.budget_against,
					expense_amount,
					budget.custom_outstanding_from_other_documents
				)
				

def compare_expense_with_budget(args, budget_amount, action_for, action, budget_against, amount=0, consolidated_amount="No"):
	args.actual_expense, args.requested_amount, args.ordered_amount = get_actual_expense(args), 0, 0
	# -------- Customization Part ---------
	"""
		If Outstanding Also Include in Expense Then Apply consolidated_amount also else only actual_expense will calculate
	"""
	if not amount:
		args.requested_amount, args.ordered_amount = get_requested_amount(args), get_ordered_amount(args)

		if args.get("doctype") == "Material Request" and args.for_material_request:
			amount = args.requested_amount + args.ordered_amount

		elif args.get("doctype") == "Purchase Order" and args.for_purchase_order:
			if consolidated_amount == "Yes":
				amount = args.requested_amount + args.ordered_amount
			else:
				amount = args.ordered_amount

	elif amount and consolidated_amount == "Yes":
		args.requested_amount, args.ordered_amount = get_requested_amount(args), get_ordered_amount(args)
		amount += args.requested_amount + args.ordered_amount

	# -------- Customization Part END ---------

	total_expense = args.actual_expense + amount

	if total_expense > budget_amount:
		if args.actual_expense > budget_amount:
			error_tense = _("is already")
			diff = args.actual_expense - budget_amount
		else:
			error_tense = _("will be")
			diff = total_expense - budget_amount

		currency = frappe.get_cached_value("Company", args.company, "default_currency")

		msg = _("{0} Budget for Account {1} against {2} {3} is {4}. It {5} exceed by {6}").format(
			_(action_for),
			frappe.bold(args.account),
			frappe.unscrub(args.budget_against_field),
			frappe.bold(budget_against),
			frappe.bold(fmt_money(budget_amount, currency=currency)),
			error_tense,
			frappe.bold(fmt_money(diff, currency=currency)),
		)

		msg += get_expense_breakup(args, currency, budget_against)

		if frappe.flags.exception_approver_role and frappe.flags.exception_approver_role in frappe.get_roles(
			frappe.session.user
		):
			action = "Warn"

		if action == "Stop":
			frappe.throw(msg, BudgetError, title=_("Budget Exceeded"))
		else:
			frappe.msgprint(msg, indicator="orange", title=_("Budget Exceeded"))


def get_requested_amount(args):
	# -------- Customization Part ---------
	# item_code = args.get("item_code")
	condition = get_other_condition(args, "Material Request")

	data = frappe.db.sql(
		""" select ifnull(sum((child.stock_qty - child.ordered_qty) * rate), 0) as amount
		from `tabMaterial Request Item` child, `tabMaterial Request` parent where parent.name = child.parent and
		parent.docstatus = 1 and child.stock_qty > child.ordered_qty and {} and
		parent.material_request_type = 'Purchase' and parent.status != 'Stopped'""".format(condition),
		# item_code,
		as_list=1,
	)
	# -------- Customization Part END ---------

	return data[0][0] if data else 0


def get_ordered_amount(args):
	# -------- Customization Part ---------
	# item_code = args.get("item_code")
	condition = get_other_condition(args, "Purchase Order")

	data = frappe.db.sql(
		f""" select ifnull(sum(child.amount - child.billed_amt), 0) as amount
		from `tabPurchase Order Item` child, `tabPurchase Order` parent where
		parent.name = child.parent and parent.docstatus = 1 and child.amount > child.billed_amt
		and parent.status != 'Closed' and {condition}""",
		# item_code,
		as_list=1,
	)
	# -------- Customization Part END ---------

	return data[0][0] if data else 0


def get_other_condition(args, for_doc):
	# -------- Customization Part ---------
	# condition = "expense_account = '%s'" % (args.expense_account)
	condition = "expense_account in (%s)" % (", ".join(["'%s'" % account for account in args.account_list]))
	# -------- Customization Part END ---------

	budget_against_field = args.get("budget_against_field")

	if budget_against_field and args.get(budget_against_field):
		condition += f" and child.{budget_against_field} = '{args.get(budget_against_field)}'"

	if args.get("fiscal_year"):
		date_field = "schedule_date" if for_doc == "Material Request" else "transaction_date"
		start_date, end_date = frappe.get_cached_value(
			"Fiscal Year", args.get("fiscal_year"), ["year_start_date", "year_end_date"]
		)

		condition += f""" and parent.{date_field}
			between '{start_date}' and '{end_date}' """

	return condition


def get_actual_expense(args):
	if not args.budget_against_doctype:
		args.budget_against_doctype = frappe.unscrub(args.budget_against_field)

	budget_against_field = args.get("budget_against_field")
	condition1 = " and gle.posting_date <= %(month_end_date)s" if args.get("month_end_date") else ""

	if args.is_tree:
		lft_rgt = frappe.db.get_value(
			args.budget_against_doctype, args.get(budget_against_field), ["lft", "rgt"], as_dict=1
		)

		args.update(lft_rgt)

		condition2 = f"""and exists(select name from `tab{args.budget_against_doctype}`
			where lft>=%(lft)s and rgt<=%(rgt)s
			and name=gle.{budget_against_field})"""
	else:
		condition2 = f"""and exists(select name from `tab{args.budget_against_doctype}`
		where name=gle.{budget_against_field} and
		gle.{budget_against_field} = %({budget_against_field})s)"""

	# -------- Customization Part ---------
	amount = flt(
		frappe.db.sql(
			f"""
		select sum(gle.debit) - sum(gle.credit)
		from `tabGL Entry` gle
		where
			is_cancelled = 0
			and gle.account in %(account_list)s
			{condition1}
			and gle.fiscal_year=%(fiscal_year)s
			and gle.company=%(company)s
			and gle.docstatus=1
			{condition2}
	""",
			(args),
		)[0][0]
	)  # nosec
	# -------- Customization Part END ---------

	return amount


def get_expense_breakup(args, currency, budget_against):
	msg = "<hr>Total Expenses booked through - <ul>"

	common_filters = frappe._dict(
		{
			args.budget_against_field: budget_against,
			"account": args.account,
			"company": args.company,
		}
	)

	msg += (
		"<li>"
		+ frappe.utils.get_link_to_report(
			"General Ledger",
			label="Actual Expenses",
			filters=common_filters.copy().update(
				{
					"from_date": frappe.get_cached_value("Fiscal Year", args.fiscal_year, "year_start_date"),
					"to_date": frappe.get_cached_value("Fiscal Year", args.fiscal_year, "year_end_date"),
					"is_cancelled": 0,
				}
			),
		)
		+ " - "
		+ frappe.bold(fmt_money(args.actual_expense, currency=currency))
		+ "</li>"
	)

	# -------- Customization Part ---------
	msg += (
		"<li>"
		+ frappe.utils.get_link_to_report(
			"Material Request",
			label="Material Requests",
			report_type="Report Builder",
			doctype="Material Request",
			filters=common_filters.copy().update(
				{
					"status": [["!=", "Stopped"]],
					"docstatus": 1,
					"material_request_type": "Purchase",
					"schedule_date": [["fiscal year", "2023-2024"]],
					# "item_code": args.item_code,
					"per_ordered": [["<", 100]],
				}
			),
		)
		+ " - "
		+ frappe.bold(fmt_money(args.requested_amount, currency=currency))
		+ "</li>"
	)

	msg += (
		"<li>"
		+ frappe.utils.get_link_to_report(
			"Purchase Order",
			label="Unbilled Orders",
			report_type="Report Builder",
			doctype="Purchase Order",
			filters=common_filters.copy().update(
				{
					"status": [["!=", "Closed"]],
					"docstatus": 1,
					"transaction_date": [["fiscal year", "2023-2024"]],
					# "item_code": args.item_code,
					"per_billed": [["<", 100]],
				}
			),
		)
		+ " - "
		+ frappe.bold(fmt_money(args.ordered_amount, currency=currency))
		+ "</li></ul>"
	)
	# -------- Customization Part END ---------

	return msg
