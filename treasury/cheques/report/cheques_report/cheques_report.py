# Copyright (c) 2026, nsdLabs and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from treasury.cheques import state


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.payment_type:
		frappe.throw(_("Please select the Payment Type."))
	if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
		frappe.throw(_("From Date must be before To Date."))
	received = filters.payment_type == state.RECEIVE
	return get_columns(received), get_data(filters, received)


def get_data(filters, received: bool) -> list[dict]:
	conditions = {
		"docstatus": 1,
		"mode_of_payment_type": "Cheque",
		"payment_type": filters.payment_type,
	}
	for fieldname in ("company", "party_type", "party"):
		if filters.get(fieldname):
			conditions[fieldname] = filters[fieldname]
	if filters.status:
		conditions[state.status_field(filters.payment_type)] = filters.status
	if filters.bank_account:
		conditions["bank_acc"] = filters.bank_account
	if filters.mode_of_payment:
		conditions["new_mode_of_payment" if received else "mode_of_payment"] = filters.mode_of_payment
	if filters.from_date and filters.to_date:
		conditions["reference_date"] = ["between", [filters.from_date, filters.to_date]]
	elif filters.from_date:
		conditions["reference_date"] = [">=", filters.from_date]
	elif filters.to_date:
		conditions["reference_date"] = ["<=", filters.to_date]

	# `get_list` applies the user's permissions on Payment Entry.
	rows = frappe.get_list(
		"Payment Entry",
		filters=conditions,
		fields=[
			"name as payment_entry",
			"reference_no",
			"reference_date",
			"cheque_new_date",
			"change_date",
			"posting_date",
			"clearance_date",
			"party_type",
			"party",
			"party_name",
			"drawn_bank",
			"bank_acc",
			"cheque_type",
			"mode_of_payment",
			"new_mode_of_payment",
			"cheque_status",
			"cheque_status_pay",
			"paid_amount",
			"received_amount",
			"encashed_amount",
			"remaining_amount",
			"party_",
			"first_beneficiary",
			"person_name",
			"paid_from",
			"paid_to",
		],
		order_by="reference_date asc, name asc",
		limit_page_length=0,
	)
	for row in rows:
		row.due_date = row.cheque_new_date if row.change_date and row.cheque_new_date else row.reference_date
		row.status = row.cheque_status if received else row.cheque_status_pay
		row.amount = row.received_amount if received else row.paid_amount
		# Drives the due-soon highlight: only cheques still awaiting settlement.
		row.is_open = row.status in (state.OPEN_RECEIVE_STATUSES if received else state.OPEN_PAY_STATUSES)
	return rows


def get_columns(received: bool) -> list[dict]:
	columns = [
		{
			"label": _("Payment Entry"),
			"fieldname": "payment_entry",
			"fieldtype": "Link",
			"options": "Payment Entry",
			"width": 170,
		},
		{"label": _("Cheque No"), "fieldname": "reference_no", "fieldtype": "Data", "width": 120},
		{"label": _("Due Date"), "fieldname": "due_date", "fieldtype": "Date", "width": 110},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 150},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 100},
		{
			"label": _("Party"),
			"fieldname": "party",
			"fieldtype": "Dynamic Link",
			"options": "party_type",
			"width": 160,
		},
		{"label": _("Party Name"), "fieldname": "party_name", "fieldtype": "Data", "width": 180},
	]
	if received:
		columns += [
			{
				"label": _("Drawn Bank"),
				"fieldname": "drawn_bank",
				"fieldtype": "Link",
				"options": "Bank",
				"width": 140,
			},
			{
				"label": _("Current Wallet"),
				"fieldname": "new_mode_of_payment",
				"fieldtype": "Link",
				"options": "Mode of Payment",
				"width": 150,
			},
			{
				"label": _("Encashed Amount"),
				"fieldname": "encashed_amount",
				"fieldtype": "Currency",
				"width": 130,
			},
			{
				"label": _("Remaining Amount"),
				"fieldname": "remaining_amount",
				"fieldtype": "Currency",
				"width": 130,
			},
			{"label": _("Endorsed To"), "fieldname": "party_", "fieldtype": "Data", "width": 140},
		]
	else:
		columns += [
			{
				"label": _("Mode of Payment"),
				"fieldname": "mode_of_payment",
				"fieldtype": "Link",
				"options": "Mode of Payment",
				"width": 150,
			},
			{
				"label": _("Paid From"),
				"fieldname": "paid_from",
				"fieldtype": "Link",
				"options": "Account",
				"width": 180,
			},
			{
				"label": _("Paid To"),
				"fieldname": "paid_to",
				"fieldtype": "Link",
				"options": "Account",
				"width": 180,
			},
		]
	columns += [
		{
			"label": _("Company Bank Account"),
			"fieldname": "bank_acc",
			"fieldtype": "Link",
			"options": "Bank Account",
			"width": 170,
		},
		{"label": _("Cheque Type"), "fieldname": "cheque_type", "fieldtype": "Data", "width": 130},
		{
			"label": _("First Beneficiary"),
			"fieldname": "first_beneficiary",
			"fieldtype": "Data",
			"width": 130,
		},
		{"label": _("Beneficiary Name"), "fieldname": "person_name", "fieldtype": "Data", "width": 150},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 110},
		{"label": _("Clearance Date"), "fieldname": "clearance_date", "fieldtype": "Date", "width": 110},
	]
	return columns
