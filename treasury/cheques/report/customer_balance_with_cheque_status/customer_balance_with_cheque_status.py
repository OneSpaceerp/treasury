# Copyright (c) 2026, nsdLabs and contributors
# For license information, please see license.txt

import frappe
from frappe import _, scrub
from frappe.query_builder.functions import Count, Sum

from treasury.cheques import state

OPEN_STATUSES = state.OPEN_RECEIVE_STATUSES


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Please select the Company."))
	if not filters.from_date or not filters.to_date:
		frappe.throw(_("Please select From Date and To Date."))
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date must be before To Date."))
	# The figures are read straight from the ledger, so the report is limited
	# to users who may read the ledger.
	frappe.has_permission("GL Entry", "read", throw=True)

	cheques = get_open_cheques(filters)
	customers = filters.get("customers") or sorted(cheques)
	balances = get_customer_balances(filters, customers)
	names = get_customer_names(customers)

	data = []
	for customer in customers:
		row = {
			"party": customer,
			"party_name": names.get(customer),
			"customer_balance": balances.get(customer, 0.0),
			"no_of_cheques": 0,
		}
		total_cheques = 0.0
		for status in OPEN_STATUSES:
			amount, count = cheques.get(customer, {}).get(status, (0.0, 0))
			row[scrub(status)] = amount
			row["no_of_cheques"] += count
			total_cheques += amount
		row["total_cheques"] = total_cheques
		row["balance"] = row["customer_balance"] + total_cheques
		data.append(row)

	return get_columns(), data


def get_open_cheques(filters) -> dict:
	"""Open cheque amount and count per customer and status, in one query."""
	payment_entry = frappe.qb.DocType("Payment Entry")
	query = (
		frappe.qb.from_(payment_entry)
		.select(
			payment_entry.party,
			payment_entry.cheque_status,
			Sum(payment_entry.base_received_amount).as_("amount"),
			Count(payment_entry.name).as_("count"),
		)
		.where(payment_entry.docstatus == 1)
		.where(payment_entry.company == filters.company)
		.where(payment_entry.payment_type == state.RECEIVE)
		.where(payment_entry.party_type == "Customer")
		.where(payment_entry.mode_of_payment_type == "Cheque")
		.where(payment_entry.cheque_status.isin(OPEN_STATUSES))
		.where(payment_entry.posting_date[filters.from_date : filters.to_date])
		.groupby(payment_entry.party, payment_entry.cheque_status)
	)
	if filters.get("customers"):
		query = query.where(payment_entry.party.isin(filters.customers))

	cheques = {}
	for row in query.run(as_dict=True):
		# What remains of a partly encashed cheque is the exposure, not its face value.
		cheques.setdefault(row.party, {})[row.cheque_status] = (row.amount or 0.0, row.count or 0)
	deduct_encashed(filters, cheques)
	return cheques


def deduct_encashed(filters, cheques: dict) -> None:
	payment_entry = frappe.qb.DocType("Payment Entry")
	query = (
		frappe.qb.from_(payment_entry)
		.select(payment_entry.party, Sum(payment_entry.encashed_amount).as_("encashed"))
		.where(payment_entry.docstatus == 1)
		.where(payment_entry.company == filters.company)
		.where(payment_entry.payment_type == state.RECEIVE)
		.where(payment_entry.party_type == "Customer")
		.where(payment_entry.mode_of_payment_type == "Cheque")
		.where(payment_entry.cheque_status == state.PARTIALLY_ENCASHED)
		.where(payment_entry.posting_date[filters.from_date : filters.to_date])
		.groupby(payment_entry.party)
	)
	for row in query.run(as_dict=True):
		amount, count = cheques.get(row.party, {}).get(state.PARTIALLY_ENCASHED, (0.0, 0))
		if count:
			cheques[row.party][state.PARTIALLY_ENCASHED] = (amount - (row.encashed or 0.0), count)


def get_customer_balances(filters, customers: list[str]) -> dict:
	"""Receivable balance per customer as of the To Date, in one query."""
	if not customers:
		return {}
	gl_entry = frappe.qb.DocType("GL Entry")
	rows = (
		frappe.qb.from_(gl_entry)
		.select(gl_entry.party, (Sum(gl_entry.debit) - Sum(gl_entry.credit)).as_("balance"))
		.where(gl_entry.is_cancelled == 0)
		.where(gl_entry.company == filters.company)
		.where(gl_entry.party_type == "Customer")
		.where(gl_entry.party.isin(customers))
		.where(gl_entry.posting_date <= filters.to_date)
		.groupby(gl_entry.party)
	).run(as_dict=True)
	return {row.party: row.balance or 0.0 for row in rows}


def get_customer_names(customers: list[str]) -> dict:
	if not customers:
		return {}
	return dict(
		frappe.get_all(
			"Customer", filters={"name": ["in", customers]}, fields=["name", "customer_name"], as_list=True
		)
	)


def get_columns() -> list[dict]:
	columns = [
		{
			"label": _("Customer"),
			"fieldname": "party",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 160,
		},
		{"label": _("Customer Name"), "fieldname": "party_name", "fieldtype": "Data", "width": 200},
		{
			"label": _("Customer Balance"),
			"fieldname": "customer_balance",
			"fieldtype": "Currency",
			"width": 150,
		},
	]
	columns += [
		{"label": _(status), "fieldname": scrub(status), "fieldtype": "Currency", "width": 150}
		for status in OPEN_STATUSES
	]
	columns += [
		{"label": _("Number of Cheques"), "fieldname": "no_of_cheques", "fieldtype": "Int", "width": 140},
		{"label": _("Open Cheques"), "fieldname": "total_cheques", "fieldtype": "Currency", "width": 150},
		{
			"label": _("Balance Including Open Cheques"),
			"fieldname": "balance",
			"fieldtype": "Currency",
			"width": 220,
		},
	]
	return columns
