# Copyright (c) 2025, SAW India and contributors
# For license information, please see license.txt



import datetime
import calendar
import frappe
from frappe import _
from frappe.utils import flt, formatdate

from erpnext.controllers.trends import get_period_date_ranges, get_period_month_ranges
from budget_control.override.budget import get_requested_amount, get_ordered_amount

def execute(filters=None):
	if not filters:
		filters = {}

	columns = get_columns(filters)
	if filters.get("budget_against_filter"):
		dimensions = filters.get("budget_against_filter")
	else:
		dimensions = get_cost_centers(filters)

	period_month_ranges = get_period_month_ranges(filters["period"], filters["from_fiscal_year"])
	cam_map = get_dimension_account_month_map(filters)

	data = []
	for dimension in dimensions:
		dimension_items = cam_map.get(dimension)
		if dimension_items:
			data = get_final_data(dimension, dimension_items, filters, period_month_ranges, data, 0)

	chart = get_chart_data(filters, columns, data)

	return columns, data, None, chart


def get_final_data(dimension, dimension_items, filters, period_month_ranges, data, DCC_allocation):
	result = []

	for account, monthwise_data in dimension_items.items():
		if len(result) == 0 and monthwise_data.get("total_amount_flag") == True:
			result.append([dimension, ''] + ([0,0,0] * ((len(period_month_ranges)) * len(get_fiscal_years(filters))) + [0,0,0] if filters['period'] != 'Yearly' else [0,0,0] * len(get_fiscal_years(filters))) + [0])

		row = [dimension, account]
		totals = [0, 0, 0]
		for index, year in enumerate(get_fiscal_years(filters)):
			index+=1
			last_total = 0
			for count, relevant_months in enumerate(period_month_ranges):
				period_data = [0, 0, 0]
				for month in relevant_months:
					if monthwise_data.get(year[0]):
						month_data = monthwise_data.get(year[0]).get(month, {})
						for i, fieldname in enumerate(["target", "actual", "variance"]):
							value = flt(month_data.get(fieldname))
							if monthwise_data.get("total_amount_flag") == True:
								if i == 2:
									if filters['period'] != 'Yearly':
										result[0][-2] = result[0][-4] - result[0][-3]
									result[0][((count*index)*3) + 2 + i] = result[0][((count*index)*3) + i] - result[0][((count*index)*3) + 1 + i]

								elif i == 1:
									period_data[i] += value
									if filters['period'] != 'Yearly':
										result[0][-3] += value
									totals[i] += value
								elif i == 0:
									value = (value/len(dimension_items))
									if filters['period'] != 'Yearly':
										result[0][-4] += value

								if i != 2:
									result[0][((count*index)*3) + 2 + i] += value
							else:
								period_data[i] += value
								totals[i] += value

				if not monthwise_data.get("total_amount_flag") == True:
					period_data[0] += last_total

				if DCC_allocation:
					period_data[0] = period_data[0] * (DCC_allocation / 100)
					period_data[1] = period_data[1] * (DCC_allocation / 100)

				if filters.get("show_cumulative"):
					last_total = period_data[0] - period_data[1]

				if not monthwise_data.get("total_amount_flag") == True:
					period_data[2] = period_data[0] - period_data[1]

				row += period_data


		if not monthwise_data.get("total_amount_flag") == True:
			totals[2] = totals[0] - totals[1]

		if filters["period"] != "Yearly":
			row += totals

		if monthwise_data.get("remove_account") == True:
			continue

		result.append(row + [1])

	if monthwise_data.get("total_amount_flag") == True and filters.get("show_cumulative") and len(result[0]) > 6:
		skip_count = 1
		for i in range(5, len(result[0])-4, 3):
			if skip_count == len(period_month_ranges):
				skip_count = 1
				continue
			skip_count += 1
			result[0][i] = result[0][i] + result[0][i - 1]
			result[0][i + 2] = result[0][i + 2] + result[0][i - 1]

	data += result

	return data


def get_columns(filters):
	columns = [
		{
			"label": _(filters.get("budget_against")),
			"fieldtype": "Link",
			"fieldname": "budget_against",
			"options": filters.get("budget_against"),
			"width": 150,
		},
		{
			"label": _("Account"),
			"fieldname": "Account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 150,
		},
	]

	group_months = False if filters["period"] == "Monthly" else True

	fiscal_year = get_fiscal_years(filters)

	for year in fiscal_year:
		for from_date, to_date in get_period_date_ranges(filters["period"], year[0]):
			if filters["period"] == "Yearly":
				labels = [
					_("Budget") + " " + str(year[0]),
					_("Actual") + " " + str(year[0]),
					_("Variance") + " " + str(year[0]),
				]
				for label in labels:
					columns.append(
						{"label": label, "fieldtype": "Float", "fieldname": frappe.scrub(label), "width": 150}
					)
			else:
				for label in [
					_("Budget") + " (%s)" + " " + str(year[0]),
					_("Actual") + " (%s)" + " " + str(year[0]),
					_("Variance") + " (%s)" + " " + str(year[0]),
				]:
					if group_months:
						label = label % (
							formatdate(from_date, format_string="MMM")
							+ "-"
							+ formatdate(to_date, format_string="MMM")
						)
					else:
						label = label % formatdate(from_date, format_string="MMM")

					columns.append(
						{"label": label, "fieldtype": "Float", "fieldname": frappe.scrub(label), "width": 150}
					)

	if filters["period"] != "Yearly":
		for label in [_("Total Budget"), _("Total Actual"), _("Total Variance")]:
			columns.append(
				{"label": label, "fieldtype": "Float", "fieldname": frappe.scrub(label), "width": 150}
			)
		columns.append(
			{
				"fieldname": "indent",
				"fieldtype": "Data",
				"width": 10,
				"hidden": 1,
			}
		)

		return columns
	else:
		columns.append(
			{
				"fieldname": "indent",
				"fieldtype": "Data",
				"width": 10,
				"hidden": 1,
			}
		)
		return columns


def get_cost_centers(filters):
	order_by = ""
	if filters.get("budget_against") == "Cost Center":
		order_by = "order by lft"

	if filters.get("budget_against") in ["Cost Center", "Project"]:
		return frappe.db.sql_list(
			"""
				select
					name
				from
					`tab{tab}`
				where
					company = %s
				{order_by}
			""".format(tab=filters.get("budget_against"), order_by=order_by),
			filters.get("company"),
		)
	else:
		return frappe.db.sql_list(
			"""
				select
					name
				from
					`tab{tab}`
			""".format(tab=filters.get("budget_against"))
		)  # nosec


# Get dimension & target details
def get_dimension_target_details(filters):
	budget_against = frappe.scrub(filters.get("budget_against"))

	budget_filters = {
		"docstatus": 1,
		"fiscal_year": filters.from_fiscal_year,
		"budget_against": budget_against,
		"company": filters.company,
	}

	if filters.get("budget_against_filter"):
		budget_filters[filters.get("budget_against")] = ['in', filters.get("budget_against_filter")]

	budget_list = frappe.db.get_list("Budget", budget_filters, pluck="name")

	result = []

	for budget in budget_list:
		data = frappe._dict()
		budget_doc = frappe.get_doc("Budget", budget)
		data["budget_against"] = budget_doc.get(budget_against)
		data["monthly_distribution"] = budget_doc.monthly_distribution
		data["fiscal_year"] = budget_doc.fiscal_year
		if budget_doc.custom_total_amount > 0:
			data["budget_amount"] = budget_doc.custom_total_amount
			data['total_amount_flag'] = True
		
		if budget_doc.custom_apply_all_expense_account:
			account_list = frappe.db.get_all("Account", 
				{
					"company": filters.company, 
					"report_type": "Profit and Loss", 
					"is_group": 0, 
					"root_type": "Expense",
					"account_type": ("not in", ("Cost of Goods Sold", "Stock Adjustment", "Expense Included In Stock Valuation", "Expense Included In Asset Valuation"))
				}, pluck="name")
		else:
			account_list = frappe.db.get_all("Budget Account", {"parent": budget}, pluck="account")

		for account in account_list:
			data["account"] = account
			if not budget_doc.custom_total_amount > 0:
				data["budget_amount"] = frappe.db.get_value("Budget Account", {"parent": budget, "account": account}, "budget_amount")
				data['total_amount_flag'] = False
			result.append(data.copy())

	return result


# Get target distribution details of accounts of cost center
def get_target_distribution_details(filters):
	target_details = {}
	for d in frappe.db.sql(
		"""
			select
				md.name,
				mdp.month,
				mdp.percentage_allocation
			from
				`tabMonthly Distribution Percentage` mdp,
				`tabMonthly Distribution` md
			where
				mdp.parent = md.name
				and md.fiscal_year between %s and %s
			order by
				md.fiscal_year
		""",
		(filters.from_fiscal_year, filters.to_fiscal_year),
		as_dict=1,
	):
		target_details.setdefault(d.name, {}).setdefault(d.month, flt(d.percentage_allocation))

	return target_details


# Get actual details from gl entry
def get_actual_details(name, filters):
	budget_against = frappe.scrub(filters.get("budget_against"))
	cond = ""

	if filters.get("budget_against") == "Cost Center":
		cc_lft, cc_rgt = frappe.db.get_value("Cost Center", name, ["lft", "rgt"])
		cond = f"""
				and lft >= "{cc_lft}"
				and rgt <= "{cc_rgt}"
			"""

	ac_details = frappe.db.sql(
		f"""
			select
				gl.account,
				gl.debit,
				gl.credit,
				gl.fiscal_year,
				MONTHNAME(gl.posting_date) as month_name,
				b.{budget_against} as budget_against
			from
				`tabGL Entry` gl,
				`tabBudget Account` ba,
				`tabBudget` b
			where
				b.name = ba.parent
				and b.docstatus = 1
				and ba.account=gl.account
				and b.{budget_against} = gl.{budget_against}
				and gl.fiscal_year between %s and %s
				and gl.is_cancelled = 0
				and b.{budget_against} = %s
				and exists(
					select
						name
					from
						`tab{filters.budget_against}`
					where
						name = gl.{budget_against}
						{cond}
				)
				group by
					gl.name
				order by gl.fiscal_year
		""",
		(filters.from_fiscal_year, filters.to_fiscal_year, name),
		as_dict=1,
	)

	cc_actual_details = {}
	for d in ac_details:
		cc_actual_details.setdefault(d.account, []).append(d)

	return cc_actual_details


def get_dimension_account_month_map(filters):
	dimension_target_details = get_dimension_target_details(filters)
	tdd = get_target_distribution_details(filters)

	cam_map = {}

	for ccd in dimension_target_details:
		actual_details = get_actual_details(ccd.budget_against, filters)
		amount_available_flag = False

		budget_doc = frappe.get_doc("Budget", {
			filters.budget_against.lower().replace(" ", "_"): ccd.budget_against
		})

		for month_id in range(1, 13):
			month = datetime.date(2013, month_id, 1).strftime("%B")
			cam_map.setdefault(ccd.budget_against, {}).setdefault(ccd.account, {}).setdefault(
				ccd.fiscal_year, {}
			).setdefault(month, frappe._dict({"target": 0.0, "actual": 0.0}))

			cam_map.setdefault(ccd.budget_against, {}).setdefault(ccd.account, {}).setdefault(
				"total_amount_flag", ccd.total_amount_flag 
			)

			tav_dict = cam_map[ccd.budget_against][ccd.account][ccd.fiscal_year][month]
			month_percentage = (
				tdd.get(ccd.monthly_distribution, {}).get(month, 0)
				if ccd.monthly_distribution
				else 100.0 / 12
			)

			tav_dict.target = flt(ccd.budget_amount) * month_percentage / 100

			for ad in actual_details.get(ccd.account, []):
				if ad.month_name == month and ad.fiscal_year == ccd.fiscal_year:
					tav_dict.actual += flt(ad.debit) - flt(ad.credit)
					if tav_dict.actual > 0:
						amount_available_flag = True

			start, end = get_month_date_range(ccd.fiscal_year, month)

			budget_doc_args = frappe._dict({
				"account_list": [ccd.account],
				"budget_against_field": filters.budget_against.lower().replace(" ", "_"),
				filters.budget_against.lower().replace(" ", "_"): ccd.budget_against,
				"from_date": start,
				"to_date": end
			})

			if budget_doc.applicable_on_purchase_order:
				tav_dict.actual += get_ordered_amount(budget_doc_args)

			if budget_doc.applicable_on_material_request:
				tav_dict.actual += get_requested_amount(budget_doc_args)

			if tav_dict.actual > 0 and budget_doc.custom_apply_all_expense_account:
				amount_available_flag = True

		if not amount_available_flag and cam_map[ccd.budget_against][ccd.account]['total_amount_flag'] == True:
			cam_map[ccd.budget_against][ccd.account]["remove_account"] = True
		else:
			cam_map[ccd.budget_against][ccd.account]["remove_account"] = False

	return cam_map


def get_month_date_range(fiscal_year, month_name):
    # Get fiscal year start & end
    fy_start, fy_end = frappe.get_cached_value(
        "Fiscal Year",
        fiscal_year,
        ["year_start_date", "year_end_date"]
    )

    # Convert month name → month number
    month_num = datetime.datetime.strptime(month_name, "%B").month

    # Determine the correct year for the month
    # Example: Fiscal Year 2025–2026 → April 2025 to March 2026
    if month_num >= fy_start.month:
        year = fy_start.year
    else:
        year = fy_end.year

    # First day of month
    month_start = datetime.datetime(year, month_num, 1)

    # Last day of month
    last_day = calendar.monthrange(year, month_num)[1]
    month_end = datetime.datetime(year, month_num, last_day)

    return month_start.date(), month_end.date()


def get_fiscal_years(filters):
	fiscal_year = frappe.db.sql(
		"""
			select
				name
			from
				`tabFiscal Year`
			where
				name between %(from_fiscal_year)s and %(to_fiscal_year)s
		""",
		{"from_fiscal_year": filters["from_fiscal_year"], "to_fiscal_year": filters["to_fiscal_year"]},
	)

	return fiscal_year


def get_chart_data(filters, columns, data):
	if not data:
		return None

	labels = []

	fiscal_year = get_fiscal_years(filters)
	group_months = False if filters["period"] == "Monthly" else True

	for year in fiscal_year:
		for from_date, to_date in get_period_date_ranges(filters["period"], year[0]):
			if filters["period"] == "Yearly":
				labels.append(year[0])
			else:
				if group_months:
					label = (
						formatdate(from_date, format_string="MMM")
						+ "-"
						+ formatdate(to_date, format_string="MMM")
					)
					labels.append(label)
				else:
					label = formatdate(from_date, format_string="MMM")
					labels.append(label)

	no_of_columns = len(labels)

	budget_values, actual_values = [0] * no_of_columns, [0] * no_of_columns
	for d in data:
		values = d[2:]
		index = 0

		for i in range(no_of_columns):
			budget_values[i] += values[index]
			actual_values[i] += values[index + 1] if d[1] else 0
			index += 3

	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Budget"), "chartType": "bar", "values": budget_values},
				{"name": _("Actual Expense"), "chartType": "bar", "values": actual_values},
			],
		},
		"type": "bar",
	}
