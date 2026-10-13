"""Bank Guarantee, extended with purposes, accounting and a managed lifecycle.

A guarantee is issued on submit (one Journal Entry), may be extended, expires
on its own once past its end date, and is closed by returning it (a second
Journal Entry). Cancelling the guarantee cancels both entries.
"""

import frappe
from erpnext.accounts.doctype.bank_guarantee.bank_guarantee import BankGuarantee
from erpnext.accounts.party import get_party_account
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, nowdate

from treasury.accounting import (
	cancel_treasury_entries,
	get_company_account,
	make_journal_entry,
	row,
)

BANK_GUARANTEE = "Bank Guarantee"
CHEQUE = "Cheque"
CASH = "Cash"
DEDUCTION = "Deduction"
PURPOSES = (BANK_GUARANTEE, CHEQUE, CASH, DEDUCTION)

PROVIDING = "Providing"
RECEIVING = "Receiving"

WITH_FACILITIES = "With Facilities"
WITHOUT_FACILITIES = "Without Facilities"

NEW = "New"
ISSUED = "Issued"
EXTENDED = "Extended"
EXPIRED = "Expired"
RETURNED = "Returned"
CANCELLED = "Cancelled"

PARTY_TYPES = ("Customer", "Supplier")

# Lifecycle state. None of it may survive into a draft, whether that draft is
# new, duplicated or amended.
RESET_ON_DRAFT = {
	"bank_guarantee_status": NEW,
	"issued": 0,
	"returned": 0,
	"return_date": None,
	"extend_validity": 0,
	"no_of_extended_days": 0,
	"new_date": None,
}


class TreasuryBankGuarantee(BankGuarantee):
	# Validation
	# ----------

	def is_saved_after_submit(self) -> bool:
		"""A save of an already-submitted guarantee, whose state the lifecycle functions own."""
		return self.docstatus == 1 and self.get("_action") != "submit"

	def before_validate(self):
		if self.is_saved_after_submit():
			return
		if not self.get("company"):
			self.company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
				"Global Defaults", "default_company"
			)
		if not self.get("bank_guarantee_purpose"):
			self.bank_guarantee_purpose = BANK_GUARANTEE
		if self.docstatus == 0:
			self.update(RESET_ON_DRAFT)
			if not self.get("posting_date"):
				self.posting_date = nowdate()
		self.set_party()

	def set_party(self):
		"""Core tracks the counterparty as customer/supplier; keep them in step with the party."""
		if self.get("party_type") not in PARTY_TYPES:
			frappe.throw(_("Party Type must be Customer or Supplier."))
		if not self.get("party"):
			frappe.throw(_("Please select the Party."))
		if self.party_type == "Customer":
			self.customer, self.supplier = self.party, None
			self.customer_group = frappe.db.get_value("Customer", self.party, "customer_group")
		else:
			self.customer, self.supplier = None, self.party
			self.customer_group = None
		if not self.name_of_beneficiary:
			name_field = "customer_name" if self.party_type == "Customer" else "supplier_name"
			self.name_of_beneficiary = (
				frappe.db.get_value(self.party_type, self.party, name_field) or self.party
			)

	def validate(self):
		super().validate()
		if self.is_saved_after_submit():
			return
		if not self.company:
			frappe.throw(_("Please select the Company."))
		if self.bank_guarantee_purpose not in PURPOSES:
			frappe.throw(_("Bank Guarantee Purpose must be one of {0}.").format(", ".join(PURPOSES)))
		if flt(self.amount) <= 0:
			frappe.throw(_("Amount must be greater than zero."))
		if flt(self.bg_commission) < 0:
			frappe.throw(_("Issue Commission cannot be negative."))
		self.validate_dates()
		self.set_bank_accounts()
		self.set_facility_split()

	def validate_dates(self):
		if not self.end_date and self.start_date and cint(self.validity):
			self.end_date = add_days(self.start_date, cint(self.validity))
		if not self.end_date:
			frappe.throw(_("Please enter the End Date or the Validity in days."))
		if getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("End Date cannot be before Start Date."))

	def set_bank_accounts(self):
		"""Derive the GL accounts from the selected Bank Accounts on the server."""
		if self.bank_guarantee_purpose not in (BANK_GUARANTEE, CHEQUE):
			return
		if self.bank_account:
			bank, account = frappe.db.get_value("Bank Account", self.bank_account, ["bank", "account"])
			self.account = account
			self.bank = self.bank or bank
		if self.bank_guarantee_purpose == BANK_GUARANTEE and self.get("account_for_bank_facilities"):
			self.account_1 = frappe.db.get_value("Bank Account", self.account_for_bank_facilities, "account")

	def set_facility_split(self):
		"""Split a bank guarantee between cash cover held by the bank and a credit facility."""
		precision = self.precision("amount")
		amount = flt(self.amount, precision)
		if self.bank_guarantee_purpose != BANK_GUARANTEE:
			self.banking_facilities = None
			self.rate = self.rate2 = self.bank_amount = self.facility_amount = 0
			return

		if self.banking_facilities == WITH_FACILITIES:
			rate = flt(self.rate)
			if rate <= 0 or rate >= 100:
				frappe.throw(_("With Facilities, the Bank Rate % must be more than 0 and less than 100."))
			self.rate2 = flt(100 - rate, 9)
			self.bank_amount = flt(amount * rate / 100, precision)
			self.facility_amount = flt(amount - self.bank_amount, precision)
		else:
			self.banking_facilities = WITHOUT_FACILITIES
			self.rate, self.rate2 = 100, 0
			self.bank_amount, self.facility_amount = amount, 0
			self.account_for_bank_facilities = self.account_1 = None

	# Issue
	# -----

	def before_submit(self):
		self.validate_for_issue()

	def validate_for_issue(self):
		def require(fieldname, label=None):
			if not self.get(fieldname):
				label = label or self.meta.get_label(fieldname)
				frappe.throw(_("{0} is required to issue this guarantee.").format(frappe.bold(_(label))))

		require("posting_date")
		require("name_of_beneficiary")
		purpose = self.bank_guarantee_purpose

		if purpose == BANK_GUARANTEE:
			for fieldname in ("bank", "bank_account", "account", "bank_guarantee_number", "dpaccount"):
				require(fieldname)
			if self.banking_facilities == WITH_FACILITIES:
				require("account_for_bank_facilities")
				require("account_1", "Bank Facilities GL Account")
		elif purpose == CHEQUE:
			require("bank")
			require("reference_no")
			require("reference_date")
			if self.bg_type == PROVIDING:
				require("dpaccount")
		elif purpose == CASH:
			require("dpaccount")
			require("cash_account")
		elif purpose == DEDUCTION:
			require("dpaccount")

		if flt(self.bg_commission) and purpose in (BANK_GUARANTEE, CHEQUE):
			require("dpaccountc")
			require("account", "Bank GL Account of the Bank Account")

	def on_submit(self):
		# Deliberately not calling the core on_submit: it requires a bank and a
		# guarantee number for every guarantee, which cash and deduction
		# guarantees do not have. `validate_for_issue` covers what applies.
		make_journal_entry(
			company=self.company,
			posting_date=self.posting_date,
			rows=self.get_issue_rows(),
			reference_doctype=self.doctype,
			reference_name=self.name,
			action="issue",
			remark=_("Issue {0} guarantee {1}").format(_(self.bank_guarantee_purpose), self.name),
			cheque_no=self.get_reference_no(),
			cheque_date=self.get("reference_date") if self.bank_guarantee_purpose == CHEQUE else None,
			amount=flt(self.amount),
		)
		self.db_set({"issued": 1, "bank_guarantee_status": ISSUED})

	def get_reference_no(self) -> str:
		if self.bank_guarantee_purpose == CHEQUE and self.get("reference_no"):
			return self.reference_no
		return self.bank_guarantee_number or self.name

	def get_party_account(self) -> str:
		return get_party_account(self.party_type, self.party, self.company)

	def get_issue_rows(self) -> list:
		amount, commission = flt(self.amount), flt(self.bg_commission)
		purpose, providing = self.bank_guarantee_purpose, self.bg_type == PROVIDING
		deposit = "Guarantee Deposit Account"

		if purpose == CASH:
			if providing:
				return [
					row(self.dpaccount, debit=amount, label=deposit),
					row(self.cash_account, credit=amount, label="Cash Account"),
				]
			return [
				row(self.cash_account, debit=amount, label="Cash Account"),
				row(self.dpaccount, credit=amount, label=deposit),
			]

		if purpose == BANK_GUARANTEE:
			return [
				row(self.dpaccount, debit=amount, label=deposit),
				row(self.dpaccountc, debit=commission, label="Fees Account"),
				row(self.account, credit=flt(self.bank_amount) + commission, label="Bank GL Account"),
				row(self.account_1, credit=flt(self.facility_amount), label="Bank Facilities GL Account"),
			]

		if purpose == DEDUCTION:
			party = dict(party_type=self.party_type, party=self.party)
			if providing:
				return [
					row(self.dpaccount, debit=amount, label=deposit),
					row(self.get_party_account(), credit=amount, label="Party Account", **party),
				]
			return [
				row(self.get_party_account(), debit=amount, label="Party Account", **party),
				row(self.dpaccount, credit=amount, label=deposit),
			]

		# Cheque
		if providing:
			return [
				row(self.dpaccount, debit=amount, label=deposit),
				row(self.dpaccountc, debit=commission, label="Fees Account"),
				row(
					get_company_account(self.company, "acc1"),
					credit=amount,
					label="Guarantee Cheques Issued Account",
				),
				row(self.account, credit=commission, label="Bank GL Account"),
			]
		return [
			row(
				get_company_account(self.company, "acc3"),
				debit=amount,
				label="Guarantee Cheques Received Account",
			),
			row(
				get_company_account(self.company, "acc2"),
				credit=amount,
				label="Guarantee Cheques Received Contra Account",
			),
		]

	# Return
	# ------

	def get_return_rows(self, deduction_return: str | None = None) -> list:
		"""The mirror of the issue entry, without the commission, which is spent."""
		amount = flt(self.amount)
		purpose, providing = self.bank_guarantee_purpose, self.bg_type == PROVIDING
		deposit = "Guarantee Deposit Account"

		if purpose == CASH:
			if providing:
				return [
					row(self.cash_account, debit=amount, label="Cash Account"),
					row(self.dpaccount, credit=amount, label=deposit),
				]
			return [
				row(self.dpaccount, debit=amount, label=deposit),
				row(self.cash_account, credit=amount, label="Cash Account"),
			]

		if purpose == BANK_GUARANTEE:
			return [
				row(self.account, debit=flt(self.bank_amount), label="Bank GL Account"),
				row(self.account_1, debit=flt(self.facility_amount), label="Bank Facilities GL Account"),
				row(self.dpaccount, credit=amount, label=deposit),
			]

		if purpose == DEDUCTION:
			if providing:
				return [
					row(deduction_return, debit=amount, label="Return Account"),
					row(self.dpaccount, credit=amount, label=deposit),
				]
			return [
				row(self.dpaccount, debit=amount, label=deposit),
				row(deduction_return, credit=amount, label="Return Account"),
			]

		# Cheque
		if providing:
			return [
				row(
					get_company_account(self.company, "acc1"),
					debit=amount,
					label="Guarantee Cheques Issued Account",
				),
				row(self.dpaccount, credit=amount, label=deposit),
			]
		return [
			row(
				get_company_account(self.company, "acc2"),
				debit=amount,
				label="Guarantee Cheques Received Contra Account",
			),
			row(
				get_company_account(self.company, "acc3"),
				credit=amount,
				label="Guarantee Cheques Received Account",
			),
		]

	def get_effective_end_date(self):
		return self.new_date if self.extend_validity and self.new_date else self.end_date

	def get_live_status(self) -> str:
		"""The status of a submitted guarantee that has not been returned."""
		end_date = self.get_effective_end_date()
		if end_date and getdate(end_date) < getdate(nowdate()):
			return EXPIRED
		return EXTENDED if self.extend_validity and self.new_date else ISSUED

	# Cancel
	# ------

	def on_cancel(self):
		cancel_treasury_entries(self.doctype, self.name)
		self.db_set("bank_guarantee_status", CANCELLED)


def get_guarantee_for_update(name: str) -> TreasuryBankGuarantee:
	# Serialise concurrent lifecycle changes on the same guarantee.
	if not frappe.db.get_value("Bank Guarantee", name, "name", for_update=True):
		frappe.throw(_("Bank Guarantee {0} does not exist.").format(name), frappe.DoesNotExistError)
	doc = frappe.get_doc("Bank Guarantee", name)
	doc.check_permission("submit")
	if doc.docstatus != 1:
		frappe.throw(_("Bank Guarantee {0} is not submitted.").format(frappe.bold(name)))
	if doc.returned:
		frappe.throw(_("Bank Guarantee {0} has already been returned.").format(frappe.bold(name)))
	return doc


@frappe.whitelist(methods=["POST"])
def return_guarantee(name: str, posting_date: str, deduction_return: str | None = None) -> dict:
	"""Close a guarantee by returning it, posting the return Journal Entry."""
	doc = get_guarantee_for_update(name)
	posting_date = getdate(posting_date)
	issued_on = getdate(doc.get("posting_date") or doc.start_date)
	if posting_date < issued_on:
		frappe.throw(_("Return Date cannot be before the issue date {0}.").format(issued_on))

	deduction_return = deduction_return or doc.get("deduction_return")
	if doc.bank_guarantee_purpose == DEDUCTION and not deduction_return:
		frappe.throw(_("Please select the Return Account."))

	updates = {"returned": 1, "bank_guarantee_status": RETURNED, "return_date": posting_date}
	if doc.bank_guarantee_purpose == DEDUCTION:
		updates["deduction_return"] = deduction_return

	journal_entry = make_journal_entry(
		company=doc.company,
		posting_date=posting_date,
		rows=doc.get_return_rows(deduction_return),
		reference_doctype=doc.doctype,
		reference_name=doc.name,
		action="return",
		remark=_("Return {0} guarantee {1}").format(_(doc.bank_guarantee_purpose), doc.name),
		cheque_no=doc.get_reference_no(),
		cheque_date=doc.get("reference_date") if doc.bank_guarantee_purpose == CHEQUE else None,
		amount=flt(doc.amount),
	)
	doc.db_set(updates)
	doc.add_comment(
		"Info",
		_("Guarantee returned on {0} — {1}").format(
			frappe.utils.formatdate(posting_date),
			frappe.utils.get_link_to_form("Journal Entry", journal_entry.name),
		),
	)
	doc.notify_update()
	return {"journal_entry": journal_entry.name}


@frappe.whitelist(methods=["POST"])
def extend_guarantee(name: str, new_end_date: str) -> dict:
	"""Extend the validity of a guarantee to a later end date."""
	doc = get_guarantee_for_update(name)
	new_end_date = getdate(new_end_date)
	current = getdate(doc.get_effective_end_date())
	if new_end_date <= current:
		frappe.throw(_("The new end date must be after the current end date {0}.").format(current))

	doc.extend_validity, doc.new_date = 1, new_end_date
	doc.db_set(
		{
			"extend_validity": 1,
			"new_date": new_end_date,
			"no_of_extended_days": date_diff(new_end_date, doc.end_date),
			"bank_guarantee_status": doc.get_live_status(),
		}
	)
	doc.add_comment("Info", _("Validity extended to {0}").format(frappe.utils.formatdate(new_end_date)))
	doc.notify_update()
	return {"status": doc.bank_guarantee_status}


def validate_journal_entry_cancel(journal_entry) -> None:
	"""The issue entry belongs to the guarantee and is only cancelled with it."""
	if frappe.flags.treasury_cascade_cancel:
		return
	name = journal_entry.reference_link
	if frappe.db.get_value("Bank Guarantee", name, "docstatus") != 1:
		return
	if journal_entry.get("treasury_action") == "issue":
		frappe.throw(
			_("This entry issues {0}. Cancel the Bank Guarantee instead.").format(
				frappe.utils.get_link_to_form("Bank Guarantee", name)
			),
			title=_("Cancel the Guarantee"),
		)


def on_journal_entry_cancel(journal_entry) -> None:
	"""Cancelling the return entry re-opens the guarantee."""
	if frappe.flags.treasury_cascade_cancel:
		return
	name = journal_entry.reference_link
	if frappe.db.get_value("Bank Guarantee", name, "docstatus") != 1:
		return
	if journal_entry.get("treasury_action") != "return":
		# The issue entry is refused in `validate_journal_entry_cancel`; anything
		# else was posted by a retired app and leaves the guarantee as it is.
		return

	doc = frappe.get_doc("Bank Guarantee", name)
	doc.db_set({"returned": 0, "return_date": None, "bank_guarantee_status": doc.get_live_status()})
	doc.add_comment(
		"Info",
		_("Return reverted by cancelling {0}").format(
			frappe.utils.get_link_to_form("Journal Entry", journal_entry.name)
		),
	)
