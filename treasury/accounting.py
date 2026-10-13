"""Journal Entry posting shared by cheque actions and bank guarantees.

Every Treasury posting goes through `make_journal_entry`, so the checks that
keep the ledger consistent (accounts present, same company, company currency,
balanced) live in exactly one place.
"""

import frappe
from frappe import _
from frappe.utils import flt


def get_company_account(company: str, fieldname: str, label: str | None = None) -> str:
	"""Return a Treasury account configured on the Company, or explain where to set it."""
	account = frappe.get_cached_value("Company", company, fieldname)
	if not account:
		label = label or frappe.get_meta("Company").get_label(fieldname)
		frappe.throw(
			_("Please set {0} in the Treasury tab of Company {1}.").format(
				frappe.bold(_(label)), frappe.bold(company)
			),
			title=_("Missing Treasury Account"),
		)
	return account


def row(account: str | None, debit: float = 0, credit: float = 0, *, label: str, party_type=None, party=None):
	"""One side of a posting. `label` names the account in error messages."""
	return frappe._dict(
		account=account, debit=flt(debit), credit=flt(credit), label=label, party_type=party_type, party=party
	)


def make_journal_entry(
	*,
	company: str,
	posting_date,
	rows: list,
	reference_doctype: str,
	reference_name: str,
	action: str,
	remark: str,
	cheque_no: str | None = None,
	cheque_date=None,
	voucher_type: str = "Journal Entry",
	snapshot: dict | None = None,
	amount: float = 0,
):
	"""Insert and submit a balanced Journal Entry linked to a Treasury document.

	Zero-value rows are dropped (ERPNext refuses them), every remaining row
	must name an account of `company` held in the company currency, and the
	entry must balance. The caller has already checked the user's permission
	on the source document, so the entry itself is posted without a second
	permission check on Journal Entry.
	"""
	if not posting_date:
		frappe.throw(_("Posting Date is required."))

	precision = frappe.get_precision("Journal Entry Account", "debit_in_account_currency") or 2
	company_currency, cost_center = frappe.get_cached_value(
		"Company", company, ["default_currency", "cost_center"]
	)

	accounts, total_debit, total_credit = [], 0.0, 0.0
	for line in rows:
		debit, credit = flt(line.debit, precision), flt(line.credit, precision)
		if not debit and not credit:
			continue
		if debit < 0 or credit < 0:
			frappe.throw(_("{0}: amounts cannot be negative.").format(_(line.label)))
		if not line.account:
			frappe.throw(_("{0} is not set.").format(frappe.bold(_(line.label))), title=_("Missing Account"))

		account = frappe.get_cached_value(
			"Account", line.account, ["company", "account_currency", "is_group", "disabled"], as_dict=True
		)
		if not account:
			frappe.throw(_("Account {0} does not exist.").format(frappe.bold(line.account)))
		if account.company != company:
			frappe.throw(
				_("{0}: account {1} does not belong to company {2}.").format(
					_(line.label), frappe.bold(line.account), frappe.bold(company)
				)
			)
		if account.is_group or account.disabled:
			frappe.throw(
				_("{0}: account {1} is a group or is disabled.").format(
					_(line.label), frappe.bold(line.account)
				)
			)
		if (account.account_currency or company_currency) != company_currency:
			frappe.throw(
				_(
					"{0}: account {1} is held in {2}. Treasury postings support accounts in the company currency ({3}) only."
				).format(
					_(line.label), frappe.bold(line.account), account.account_currency, company_currency
				),
				title=_("Unsupported Currency"),
			)

		accounts.append(
			{
				"account": line.account,
				"party_type": line.party_type if line.party else None,
				"party": line.party,
				"debit_in_account_currency": debit,
				"credit_in_account_currency": credit,
				"cost_center": cost_center,
				"user_remark": remark,
			}
		)
		total_debit += debit
		total_credit += credit

	if not accounts:
		return None
	if flt(total_debit - total_credit, precision):
		frappe.throw(
			_("Treasury posting is not balanced: debit {0}, credit {1}.").format(total_debit, total_credit)
		)

	journal_entry = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": voucher_type,
			"company": company,
			"posting_date": posting_date,
			"cheque_no": cheque_no,
			"cheque_date": cheque_date or posting_date,
			"user_remark": remark,
			"accounts": accounts,
			"reference_doctype": reference_doctype,
			"reference_link": reference_name,
			"treasury_action": action,
			"treasury_amount": flt(amount, precision),
			"treasury_snapshot": frappe.as_json(snapshot or {}),
		}
	)
	journal_entry.flags.ignore_permissions = True
	journal_entry.insert()
	journal_entry.submit()
	return journal_entry


def get_treasury_entries(reference_doctype: str, reference_name: str, docstatus: int = 1) -> list[str]:
	"""Names of the Treasury journal entries of a document, newest first."""
	return frappe.get_all(
		"Journal Entry",
		filters={
			"reference_doctype": reference_doctype,
			"reference_link": reference_name,
			"docstatus": docstatus,
		},
		order_by="creation desc, name desc",
		pluck="name",
	)


def cancel_treasury_entries(reference_doctype: str, reference_name: str) -> None:
	"""Cancel every Treasury journal entry of a document, newest first.

	Used when the source document itself is cancelled. The flag tells the
	Journal Entry hook that the source is handling its own state, so it only
	checks nothing and restores nothing.
	"""
	frappe.flags.treasury_cascade_cancel = True
	try:
		for name in get_treasury_entries(reference_doctype, reference_name):
			journal_entry = frappe.get_doc("Journal Entry", name)
			journal_entry.flags.ignore_permissions = True
			journal_entry.cancel()
	finally:
		frappe.flags.treasury_cascade_cancel = False
