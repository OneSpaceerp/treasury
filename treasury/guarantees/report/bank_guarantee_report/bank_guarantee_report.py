# Copyright (c) 2026, nsdLabs and contributors
# For license information, please see license.txt

import frappe
from frappe import _

LIVE_STATUSES = ("Issued", "Extended")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
		frappe.throw(_("From Date must be before To Date."))
	return get_columns(), get_data(filters)


def get_data(filters) -> list[dict]:
	conditions = {"docstatus": 1}
	for fieldname in (
		"company",
		"bank_guarantee_purpose",
		"bg_type",
		"status_of_letter_of_guarantee",
		"bank_guarantee_status",
		"bank",
		"party_type",
		"party",
	):
		if filters.get(fieldname):
			conditions[fieldname] = filters[fieldname]
	if filters.from_date and filters.to_date:
		conditions["start_date"] = ["between", [filters.from_date, filters.to_date]]
	elif filters.from_date:
		conditions["start_date"] = [">=", filters.from_date]
	elif filters.to_date:
		conditions["start_date"] = ["<=", filters.to_date]

	# `get_list` applies the user's permissions on Bank Guarantee.
	rows = frappe.get_list(
		"Bank Guarantee",
		filters=conditions,
		fields=[
			"name",
			"bank_guarantee_number",
			"bank_guarantee_purpose",
			"bg_type",
			"status_of_letter_of_guarantee",
			"bank_guarantee_status",
			"party_type",
			"party",
			"name_of_beneficiary",
			"bank",
			"start_date",
			"end_date",
			"extend_validity",
			"new_date",
			"return_date",
			"amount",
			"bg_commission",
			"rate",
			"bank_amount",
			"rate2",
			"facility_amount",
		],
		order_by="start_date desc, name desc",
		limit_page_length=0,
	)
	for row in rows:
		row.valid_until = row.new_date if row.extend_validity and row.new_date else row.end_date
		# Drives the expiring-soon highlight: only guarantees still outstanding.
		row.is_live = row.bank_guarantee_status in LIVE_STATUSES
	return rows


def get_columns() -> list[dict]:
	return [
		{
			"label": _("Bank Guarantee"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Bank Guarantee",
			"width": 170,
		},
		{"label": _("Guarantee No"), "fieldname": "bank_guarantee_number", "fieldtype": "Data", "width": 130},
		{"label": _("Purpose"), "fieldname": "bank_guarantee_purpose", "fieldtype": "Data", "width": 130},
		{"label": _("Type"), "fieldname": "bg_type", "fieldtype": "Data", "width": 100},
		{
			"label": _("Category"),
			"fieldname": "status_of_letter_of_guarantee",
			"fieldtype": "Data",
			"width": 130,
		},
		{"label": _("Status"), "fieldname": "bank_guarantee_status", "fieldtype": "Data", "width": 100},
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 100},
		{
			"label": _("Party"),
			"fieldname": "party",
			"fieldtype": "Dynamic Link",
			"options": "party_type",
			"width": 160,
		},
		{"label": _("Beneficiary"), "fieldname": "name_of_beneficiary", "fieldtype": "Data", "width": 180},
		{"label": _("Bank"), "fieldname": "bank", "fieldtype": "Link", "options": "Bank", "width": 140},
		{"label": _("Start Date"), "fieldname": "start_date", "fieldtype": "Date", "width": 110},
		{"label": _("End Date"), "fieldname": "end_date", "fieldtype": "Date", "width": 110},
		{"label": _("Valid Until"), "fieldname": "valid_until", "fieldtype": "Date", "width": 110},
		{"label": _("Return Date"), "fieldname": "return_date", "fieldtype": "Date", "width": 110},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Issue Commission"), "fieldname": "bg_commission", "fieldtype": "Currency", "width": 130},
		{"label": _("Bank %"), "fieldname": "rate", "fieldtype": "Percent", "width": 90},
		{"label": _("Bank Amount"), "fieldname": "bank_amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Facility %"), "fieldname": "rate2", "fieldtype": "Percent", "width": 90},
		{
			"label": _("Facility Amount"),
			"fieldname": "facility_amount",
			"fieldtype": "Currency",
			"width": 130,
		},
	]
