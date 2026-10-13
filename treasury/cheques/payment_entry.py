"""Document events that turn a cheque-mode Payment Entry into a tracked cheque."""

import frappe
from frappe import _
from frappe.utils import flt

from treasury.accounting import cancel_treasury_entries
from treasury.cheques import state
from treasury.cheques.actions import cheque_amount, is_cheque

# State a cheque accumulates after submission. None of it may survive into a
# draft, whether that draft is new, duplicated or amended.
RESET_ON_DRAFT = {
	"cheque_status": None,
	"cheque_status_pay": None,
	"cheque_wallet_account": None,
	"new_mode_of_payment": None,
	"encashed_amount": 0,
	"remaining_amount": 0,
	"reason_for_rejection": None,
	"party_type_": None,
	"party_": None,
	"account_1": None,
	"change_date": 0,
	"cheque_new_date": None,
}


def validate(doc, method=None):
	doc.mode_of_payment_type = (
		frappe.get_cached_value("Mode of Payment", doc.mode_of_payment, "type")
		if doc.mode_of_payment
		else None
	)
	if not is_cheque(doc):
		return

	# Saving an already-submitted cheque (an allow-on-submit edit) must not
	# re-derive anything: its state is owned by the cheque actions from here on.
	if doc.docstatus == 1 and doc.get("_action") != "submit":
		return

	if doc.docstatus == 0:
		doc.update(RESET_ON_DRAFT)

	if not doc.reference_no or not doc.reference_date:
		frappe.throw(_("Cheque No and Cheque Date are mandatory for a cheque payment."))

	validate_cheque_currency(doc)
	set_company_bank_details(doc)
	validate_duplicate_cheque(doc)


def validate_cheque_currency(doc):
	"""The account that holds the cheque must be in the company currency.

	Every later cheque action posts a plain Journal Entry against it, and
	those are single-currency.
	"""
	company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	if doc.payment_type == state.RECEIVE:
		account, currency = doc.paid_to, doc.paid_to_account_currency
	else:
		account, currency = doc.paid_from, doc.paid_from_account_currency
	if currency and currency != company_currency:
		frappe.throw(
			_(
				"Cheque account {0} is held in {1}. Cheques are supported in the company currency ({2}) only."
			).format(frappe.bold(account), currency, company_currency),
			title=_("Unsupported Currency"),
		)


def set_company_bank_details(doc):
	"""Keep the accounts fetched from the company Bank Account honest."""
	if not doc.get("bank_acc"):
		if doc.payment_type != state.RECEIVE:
			frappe.throw(_("Please select the company Bank Account the cheque is drawn on."))
		return

	bank = frappe.db.get_value(
		"Bank Account",
		doc.bank_acc,
		["bank", "account", "company", "is_company_account", "collection_fee_account", "payable_account"],
		as_dict=True,
	)
	if not bank or not bank.is_company_account or bank.company != doc.company:
		frappe.throw(
			_("Bank Account {0} is not a company account of {1}.").format(
				frappe.bold(doc.bank_acc), frappe.bold(doc.company)
			)
		)
	doc.cheque_bank = bank.bank
	doc.account = bank.account
	doc.collection_fee_account = bank.collection_fee_account
	doc.payable_account = bank.payable_account


def validate_duplicate_cheque(doc):
	"""The same cheque cannot be registered twice."""
	filters = {
		"docstatus": 1,
		"name": ["!=", doc.name],
		"mode_of_payment_type": "Cheque",
		"payment_type": doc.payment_type,
		"reference_no": doc.reference_no,
	}
	if doc.payment_type == state.RECEIVE:
		filters.update(
			{"party_type": doc.party_type, "party": doc.party, "drawn_bank": doc.get("drawn_bank")}
		)
	else:
		filters["bank_acc"] = doc.get("bank_acc")
	if doc.get("amended_from"):
		filters["name"] = ["not in", [doc.name, doc.amended_from]]

	duplicate = frappe.db.get_value("Payment Entry", filters, "name")
	if duplicate:
		frappe.throw(
			_("Cheque {0} is already registered in {1}.").format(
				frappe.bold(doc.reference_no), frappe.utils.get_link_to_form("Payment Entry", duplicate)
			),
			title=_("Duplicate Cheque"),
		)


def on_submit(doc, method=None):
	if not is_cheque(doc):
		return
	values = {
		state.status_field(doc.payment_type): state.initial_status(doc.payment_type),
		"encashed_amount": 0,
		"remaining_amount": flt(cheque_amount(doc)),
	}
	if doc.payment_type == state.RECEIVE:
		values["cheque_wallet_account"] = doc.paid_to
		values["new_mode_of_payment"] = doc.mode_of_payment
	doc.db_set(values)


def on_cancel(doc, method=None):
	"""Cancelling a cheque reverses every action taken on it, newest first."""
	if doc.get("mode_of_payment_type") != "Cheque":
		return
	cancel_treasury_entries("Payment Entry", doc.name)

	# A cheque registered through a Multiple Cheque Entry is linked from its
	# row. Release that link so one cheque of a batch can be cancelled on its
	# own; the batch then simply no longer includes it.
	for child_doctype in ("Cheque Table Receive", "Cheque Table Pay"):
		for row in frappe.get_all(child_doctype, filters={"payment_entry": doc.name}, pluck="name"):
			frappe.db.set_value(child_doctype, row, "payment_entry", None, update_modified=False)
