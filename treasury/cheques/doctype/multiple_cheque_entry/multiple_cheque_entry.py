# Copyright (c) 2026, nsdLabs and contributors
# For license information, please see license.txt

import frappe
from erpnext.accounts.party import get_party_account
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from treasury.accounting import get_treasury_entries
from treasury.cheques import state
from treasury.cheques.actions import mode_of_payment_account

PARTY_TYPES = ("Customer", "Supplier")


class MultipleChequeEntry(Document):
	"""Registers a batch of cheques for one party as individual Payment Entries.

	The Payment Entries are created and submitted on the server inside the
	submit transaction, so a batch is either registered completely or not at
	all.
	"""

	@property
	def rows(self):
		return self.cheque_table if self.payment_type == state.RECEIVE else self.cheque_table_2

	def validate(self):
		self.validate_header()
		self.set_accounts()
		self.validate_rows()
		self.total_amount = sum(flt(row.paid_amount) for row in self.rows)

	def validate_header(self):
		if self.payment_type not in (state.RECEIVE, "Pay"):
			frappe.throw(_("Payment Type must be Receive or Pay."))
		if self.party_type not in PARTY_TYPES:
			frappe.throw(_("Party Type must be Customer or Supplier."))
		if frappe.db.get_value("Mode of Payment", self.mode_of_payment, "type") != "Cheque":
			frappe.throw(
				_("Mode of Payment {0} is not of type Cheque.").format(frappe.bold(self.mode_of_payment))
			)
		self.mode_of_payment_type = "Cheque"
		self.party_name = frappe.db.get_value(
			self.party_type, self.party, "customer_name" if self.party_type == "Customer" else "supplier_name"
		)
		# Only the table of the chosen payment type is kept.
		if self.payment_type == state.RECEIVE:
			self.set("cheque_table_2", [])
			self.bank_acc = None
		else:
			self.set("cheque_table", [])
			if not self.bank_acc:
				frappe.throw(_("Please select the company Bank Account the cheques are drawn on."))

	def set_accounts(self):
		accounts = get_cheque_accounts(
			company=self.company,
			payment_type=self.payment_type,
			party_type=self.party_type,
			party=self.party,
			mode_of_payment=self.mode_of_payment,
			bank_acc=self.bank_acc,
		)
		for fieldname in (
			"paid_from",
			"paid_to",
			"cheque_bank",
			"account",
			"collection_fee_account",
			"payable_account",
		):
			self.set(fieldname, accounts.get(fieldname))

		if not self.paid_from or not self.paid_to:
			frappe.throw(
				_(
					"Could not determine the accounts for this entry. Check the party account, the Mode of Payment account and the Bank Account setup."
				)
			)

	def validate_rows(self):
		if not self.rows:
			frappe.throw(_("Please add at least one cheque."))
		seen = set()
		for row in self.rows:
			if flt(row.paid_amount) <= 0:
				frappe.throw(_("Row {0}: Amount must be greater than zero.").format(row.idx))
			key = (row.reference_no, row.get("bank"))
			if key in seen:
				frappe.throw(
					_("Row {0}: Cheque {1} is entered more than once.").format(row.idx, row.reference_no)
				)
			seen.add(key)

	def on_submit(self):
		for row in self.rows:
			payment_entry = self.make_payment_entry(row)
			row.db_set("payment_entry", payment_entry.name)

	def make_payment_entry(self, row):
		currency = frappe.get_cached_value("Company", self.company, "default_currency")
		payment_entry = frappe.new_doc("Payment Entry")
		payment_entry.update(
			{
				"payment_type": self.payment_type,
				"company": self.company,
				"posting_date": self.posting_date,
				"mode_of_payment": self.mode_of_payment,
				"party_type": self.party_type,
				"party": self.party,
				"paid_from": self.paid_from,
				"paid_to": self.paid_to,
				"paid_from_account_currency": currency,
				"paid_to_account_currency": currency,
				"source_exchange_rate": 1,
				"target_exchange_rate": 1,
				"paid_amount": flt(row.paid_amount),
				"received_amount": flt(row.paid_amount),
				"reference_no": row.reference_no,
				"reference_date": row.reference_date,
				"cheque_type": row.cheque_type,
				"first_beneficiary": row.first_beneficiary,
				"person_name": row.person_name,
				"issuer_name": row.issuer_name,
				"picture_of_check": row.picture_of_check,
				"reference_doctype": self.doctype,
				"reference_link": self.name,
				"multiple_cheque_row": row.name,
			}
		)
		if self.payment_type == state.RECEIVE:
			payment_entry.drawn_bank = row.bank
		else:
			payment_entry.update({"cheque_bank": self.cheque_bank, "bank_acc": self.bank_acc})
		payment_entry.insert()
		payment_entry.submit()
		return payment_entry

	def on_cancel(self):
		"""Cancel the cheques of the batch, unless any of them has moved on."""
		names = [row.payment_entry for row in self.rows if row.payment_entry]
		for name in names:
			if frappe.db.get_value("Payment Entry", name, "docstatus") != 1:
				continue
			if get_treasury_entries("Payment Entry", name):
				frappe.throw(
					_(
						"Cheque {0} already has cheque actions. Cancel that Payment Entry first, then cancel this entry."
					).format(frappe.utils.get_link_to_form("Payment Entry", name)),
					title=_("Cheque In Progress"),
				)
		for name in reversed(names):
			if frappe.db.get_value("Payment Entry", name, "docstatus") == 1:
				frappe.get_doc("Payment Entry", name).cancel()


@frappe.whitelist()
def get_cheque_accounts(
	company: str,
	payment_type: str,
	party_type: str | None = None,
	party: str | None = None,
	mode_of_payment: str | None = None,
	bank_acc: str | None = None,
) -> dict:
	"""Accounts a batch of cheques is booked against.

	Receive: from the party account into the cheque wallet (the Mode of
	Payment account, falling back to the company's incoming cheques wallet).
	Pay: from the bank's cheques payable account to the party account.
	"""
	frappe.has_permission("Multiple Cheque Entry", "read", throw=True)
	accounts = frappe._dict()
	if not company:
		return accounts

	party_account = None
	if party_type in PARTY_TYPES and party and frappe.db.exists(party_type, party):
		party_account = get_party_account(party_type, party, company)

	if payment_type == state.RECEIVE:
		accounts.paid_from = party_account
		accounts.paid_to = (
			mode_of_payment_account(mode_of_payment, company) if mode_of_payment else None
		) or frappe.get_cached_value("Company", company, "default_incoming_cheque_wallet_account")
	else:
		accounts.paid_to = party_account
		if bank_acc:
			bank = frappe.db.get_value(
				"Bank Account",
				bank_acc,
				[
					"bank",
					"account",
					"company",
					"is_company_account",
					"collection_fee_account",
					"payable_account",
				],
				as_dict=True,
			)
			if not bank or not bank.is_company_account or bank.company != company:
				frappe.throw(
					_("Bank Account {0} is not a company account of {1}.").format(
						frappe.bold(bank_acc), frappe.bold(company)
					)
				)
			accounts.update(
				paid_from=bank.payable_account,
				cheque_bank=bank.bank,
				account=bank.account,
				collection_fee_account=bank.collection_fee_account,
				payable_account=bank.payable_account,
			)
	return accounts
