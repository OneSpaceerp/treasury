"""Server-side cheque workflow.

A cheque is a submitted Payment Entry whose Mode of Payment is of type
"Cheque". Its life after submission is a sequence of actions, each validated
against the state machine in `state.py`, each posting one Journal Entry that
remembers the values it replaced so that cancelling the entry puts the cheque
back exactly where it was.
"""

import frappe
from erpnext.accounts.party import get_party_account
from frappe import _
from frappe.utils import flt, getdate, nowdate

from treasury.accounting import get_company_account, make_journal_entry, row
from treasury.cheques import state

ENDORSEE_PARTY_TYPES = ("Customer", "Supplier", "Employee")

# The only Payment Entry fields a cheque action may change, and therefore the
# only ones a cancelled journal entry may restore.
TRACKED_FIELDS = (
	"cheque_status",
	"cheque_status_pay",
	"clearance_date",
	"cheque_bank",
	"bank_acc",
	"account",
	"collection_fee_account",
	"reason_for_rejection",
	"encashed_amount",
	"remaining_amount",
	"new_mode_of_payment",
	"cheque_wallet_account",
	"party_type_",
	"party_",
	"account_1",
	"change_date",
	"cheque_new_date",
)


def is_cheque(doc) -> bool:
	"""Whether a Payment Entry is paid by a cheque-type Mode of Payment."""
	mode_of_payment = doc.get("mode_of_payment")
	if not mode_of_payment:
		return False
	return frappe.get_cached_value("Mode of Payment", mode_of_payment, "type") == "Cheque"


def cheque_amount(doc) -> float:
	"""The cheque value in the currency of the account that holds it."""
	return flt(doc.received_amount if doc.payment_type == state.RECEIVE else doc.paid_amount)


def mode_of_payment_account(mode_of_payment: str, company: str) -> str | None:
	return frappe.db.get_value(
		"Mode of Payment Account", {"parent": mode_of_payment, "company": company}, "default_account"
	)


def wallet_account(doc) -> str:
	"""The GL account currently holding a received cheque.

	It starts as `paid_to` and moves when the cheque is transferred to another
	wallet. Cheques created by the retired app carry no tracked account, so it
	is derived from the wallet they were last moved to.
	"""
	if doc.get("cheque_wallet_account"):
		return doc.cheque_wallet_account
	current = doc.get("new_mode_of_payment")
	if current and current != doc.mode_of_payment:
		return mode_of_payment_account(current, doc.company) or doc.paid_to
	return doc.paid_to


def rejected_account(doc) -> str:
	return get_company_account(doc.company, "default_payback_cheque_wallet_account")


def get_company_bank_account(bank_account: str | None, company: str):
	if not bank_account:
		frappe.throw(_("Please select the company Bank Account."))
	details = frappe.db.get_value(
		"Bank Account",
		bank_account,
		[
			"name",
			"bank",
			"account",
			"company",
			"is_company_account",
			"collection_fee_account",
			"payable_account",
		],
		as_dict=True,
	)
	if not details:
		frappe.throw(_("Bank Account {0} does not exist.").format(frappe.bold(bank_account)))
	if not details.is_company_account or details.company != company:
		frappe.throw(
			_("Bank Account {0} is not a company account of {1}.").format(
				frappe.bold(bank_account), frappe.bold(company)
			)
		)
	return details


def commission_rows(company: str, bank_gl_account: str | None, commission) -> list:
	commission = flt(commission)
	if commission < 0:
		frappe.throw(_("Bank commission cannot be negative."))
	if not commission:
		return []
	return [
		row(
			get_company_account(company, "default_bank_commissions_account"),
			debit=commission,
			label="Bank Commissions Account",
		),
		row(bank_gl_account, credit=commission, label="Bank GL Account of the Bank Account"),
	]


# Action handlers
# ---------------
# Each returns (rows, updates): the journal entry lines to post and the
# Payment Entry fields to change. They validate their own inputs and never
# write anything themselves.


def _deposit(doc, args):
	bank = get_company_bank_account(args.bank_account, doc.company)
	amount = cheque_amount(doc)
	if doc.cheque_status == state.REJECTED:
		source = row(rejected_account(doc), credit=amount, label="Rejected Cheques Account")
	else:
		source = row(wallet_account(doc), credit=amount, label="Cheque Wallet Account")
	rows = [
		row(
			bank.collection_fee_account,
			debit=amount,
			label="Cheques Under Collection Account of the Bank Account",
		),
		source,
		*commission_rows(doc.company, bank.account, args.commission),
	]
	updates = {
		"cheque_status": state.UNDER_COLLECTION,
		"cheque_bank": bank.bank,
		"bank_acc": bank.name,
		"account": bank.account,
		"collection_fee_account": bank.collection_fee_account,
		"reason_for_rejection": None,
	}
	return rows, updates


def _collect(doc, args):
	amount = cheque_amount(doc)
	rows = [
		row(doc.account, debit=amount, label="Bank GL Account of the Bank Account"),
		row(
			doc.collection_fee_account,
			credit=amount,
			label="Cheques Under Collection Account of the Bank Account",
		),
	]
	return rows, {"cheque_status": state.COLLECTED, "clearance_date": args.posting_date}


def _reject(doc, args):
	if not (args.reason or "").strip():
		frappe.throw(_("Please enter the reason for rejection."))
	amount = cheque_amount(doc)
	rows = [
		row(rejected_account(doc), debit=amount, label="Rejected Cheques Account"),
		row(
			doc.collection_fee_account,
			credit=amount,
			label="Cheques Under Collection Account of the Bank Account",
		),
		*commission_rows(doc.company, doc.account, args.commission),
	]
	return rows, {"cheque_status": state.REJECTED, "reason_for_rejection": args.reason.strip()}


def _cash_account(doc, args) -> str:
	return args.cash_account or get_company_account(doc.company, "default_cash_account")


def _cash(doc, args):
	amount = cheque_amount(doc)
	rows = [
		row(_cash_account(doc, args), debit=amount, label="Cash Account"),
		row(wallet_account(doc), credit=amount, label="Cheque Wallet Account"),
	]
	return rows, {"cheque_status": state.CASHED, "clearance_date": args.posting_date}


def _endorse(doc, args):
	if args.party_type not in ENDORSEE_PARTY_TYPES:
		frappe.throw(_("A cheque can be endorsed to a Customer, Supplier or Employee."))
	if not args.party or not frappe.db.exists(args.party_type, args.party):
		frappe.throw(_("Please select the party the cheque is endorsed to."))
	party_account = get_party_account(args.party_type, args.party, doc.company)
	amount = cheque_amount(doc)
	rows = [
		row(
			party_account,
			debit=amount,
			label="Endorsee Party Account",
			party_type=args.party_type,
			party=args.party,
		),
		row(wallet_account(doc), credit=amount, label="Cheque Wallet Account"),
	]
	updates = {
		"cheque_status": state.ENDORSED,
		"party_type_": args.party_type,
		"party_": args.party,
		"account_1": party_account,
	}
	return rows, updates


def _return_to_party(doc, args):
	amount = cheque_amount(doc)
	if doc.cheque_status == state.IN_WALLET:
		source = row(wallet_account(doc), credit=amount, label="Cheque Wallet Account")
	else:
		# Whatever was encashed has already left the rejected cheques account.
		amount = flt(amount - flt(doc.encashed_amount))
		source = row(rejected_account(doc), credit=amount, label="Rejected Cheques Account")
	rows = [
		row(doc.paid_from, debit=amount, label="Party Account", party_type=doc.party_type, party=doc.party),
		source,
	]
	return rows, {"cheque_status": state.RETURNED}


def _return_to_wallet(doc, args):
	amount = cheque_amount(doc)
	rows = [
		row(wallet_account(doc), debit=amount, label="Cheque Wallet Account"),
		row(rejected_account(doc), credit=amount, label="Rejected Cheques Account"),
	]
	return rows, {"cheque_status": state.IN_WALLET, "reason_for_rejection": None}


def _encash(doc, args):
	total = cheque_amount(doc)
	encashed = flt(doc.encashed_amount)
	remaining = flt(total - encashed)
	amount = flt(args.amount)
	if amount <= 0:
		frappe.throw(_("Please enter the amount to encash."))
	if amount > remaining:
		frappe.throw(
			_("Encashment amount {0} exceeds the remaining cheque amount {1}.").format(amount, remaining)
		)
	rows = [
		row(_cash_account(doc, args), debit=amount, label="Cash Account"),
		row(rejected_account(doc), credit=amount, label="Rejected Cheques Account"),
	]
	left = flt(remaining - amount, 9)
	updates = {
		"encashed_amount": flt(encashed + amount),
		"remaining_amount": left,
		"cheque_status": state.PARTIALLY_ENCASHED if left > 0 else state.CASHED,
	}
	if left <= 0:
		updates["clearance_date"] = args.posting_date
	return rows, updates


def _transfer(doc, args):
	current = doc.get("new_mode_of_payment") or doc.mode_of_payment
	target = args.mode_of_payment
	if not target or frappe.db.get_value("Mode of Payment", target, "type") != "Cheque":
		frappe.throw(_("Please select a Mode of Payment of type Cheque to transfer the cheque to."))
	if target == current:
		frappe.throw(_("The cheque is already in wallet {0}.").format(frappe.bold(target)))
	target_account = mode_of_payment_account(target, doc.company)
	if not target_account:
		frappe.throw(
			_("Mode of Payment {0} has no default account for company {1}.").format(
				frappe.bold(target), frappe.bold(doc.company)
			)
		)
	source_account = wallet_account(doc)
	rows = []
	if target_account != source_account:
		amount = cheque_amount(doc)
		rows = [
			row(target_account, debit=amount, label="Target Cheque Wallet Account"),
			row(source_account, credit=amount, label="Cheque Wallet Account"),
		]
	return rows, {"new_mode_of_payment": target, "cheque_wallet_account": target_account}


def _pay(doc, args):
	if not doc.get("bank_acc") or not doc.get("account"):
		frappe.throw(_("The cheque has no company Bank Account to be paid from."))
	amount = cheque_amount(doc)
	rows = []
	# A cheque drawn straight on the bank GL account has nothing left to clear.
	if doc.paid_from != doc.account:
		rows = [
			row(doc.paid_from, debit=amount, label="Cheques Payable Account"),
			row(doc.account, credit=amount, label="Bank GL Account of the Bank Account"),
		]
	return rows, {"cheque_status_pay": state.PAID, "clearance_date": args.posting_date}


def _withdraw(doc, args):
	amount = cheque_amount(doc)
	rows = [
		row(doc.paid_from, debit=amount, label="Cheques Payable Account"),
		row(doc.paid_to, credit=amount, label="Party Account", party_type=doc.party_type, party=doc.party),
	]
	return rows, {"cheque_status_pay": state.WITHDRAWN}


def _reschedule(doc, args):
	if not args.new_date:
		frappe.throw(_("Please enter the new due date."))
	return [], {"change_date": 1, "cheque_new_date": getdate(args.new_date)}


HANDLERS = {
	"deposit": _deposit,
	"collect": _collect,
	"reject": _reject,
	"cash": _cash,
	"endorse": _endorse,
	"transfer": _transfer,
	"return_to_party": _return_to_party,
	"return_to_wallet": _return_to_wallet,
	"encash": _encash,
	"pay": _pay,
	"withdraw": _withdraw,
	"reschedule": _reschedule,
}


# Whitelisted API
# ---------------


@frappe.whitelist()
def get_available_actions(payment_entry: str) -> dict:
	"""Actions the session user may apply to a cheque, with dialog defaults."""
	doc = frappe.get_doc("Payment Entry", payment_entry)
	doc.check_permission("read")
	if doc.docstatus != 1 or not is_cheque(doc) or not doc.has_permission("submit"):
		return {"actions": []}

	status = doc.get(state.status_field(doc.payment_type))
	amount = cheque_amount(doc)
	names = state.allowed_actions(doc.payment_type, status, amount, flt(doc.encashed_amount))
	bank = (
		frappe.db.get_value("Bank Account", doc.bank_acc, ["co3_", "co5_"], as_dict=True)
		if doc.get("bank_acc")
		else None
	)
	return {
		"actions": [{"action": name, "label": _(state.ACTIONS[name].label)} for name in names],
		"status": status,
		"remaining": flt(amount - flt(doc.encashed_amount)),
		"cash_account": frappe.get_cached_value("Company", doc.company, "default_cash_account"),
		"rejection_commission": flt(bank.co5_) if bank else 0,
	}


@frappe.whitelist(methods=["POST"])
def apply_cheque_action(
	payment_entry: str,
	action: str,
	posting_date: str | None = None,
	bank_account: str | None = None,
	commission: str | float | None = None,
	cash_account: str | None = None,
	amount: str | float | None = None,
	reason: str | None = None,
	party_type: str | None = None,
	party: str | None = None,
	mode_of_payment: str | None = None,
	new_date: str | None = None,
) -> dict:
	"""Apply one cheque action to a submitted cheque Payment Entry."""
	# Serialise concurrent actions on the same cheque: the second caller waits
	# here and then sees the status the first one left behind.
	if not frappe.db.get_value("Payment Entry", payment_entry, "name", for_update=True):
		frappe.throw(_("Payment Entry {0} does not exist.").format(payment_entry), frappe.DoesNotExistError)

	doc = frappe.get_doc("Payment Entry", payment_entry)
	doc.check_permission("submit")
	if doc.docstatus != 1:
		frappe.throw(_("Cheque actions are available on submitted Payment Entries only."))
	if not is_cheque(doc):
		frappe.throw(_("Payment Entry {0} is not a cheque.").format(frappe.bold(doc.name)))

	rule = state.ACTIONS.get(action)
	field = state.status_field(doc.payment_type)
	status = doc.get(field)
	if not rule or not state.is_allowed(
		action, doc.payment_type, status, cheque_amount(doc), flt(doc.encashed_amount)
	):
		frappe.throw(
			_("Action {0} is not allowed for a cheque with status {1}.").format(
				frappe.bold(_(rule.label) if rule else action), frappe.bold(_(status or "Not Set"))
			),
			title=_("Invalid Cheque Action"),
		)

	posting_date = getdate(posting_date or nowdate())
	if posting_date < getdate(doc.posting_date):
		frappe.throw(
			_("Action date cannot be before the Payment Entry posting date {0}.").format(doc.posting_date)
		)

	args = frappe._dict(
		posting_date=posting_date,
		bank_account=bank_account,
		commission=commission,
		cash_account=cash_account,
		amount=amount,
		reason=reason,
		party_type=party_type,
		party=party,
		mode_of_payment=mode_of_payment,
		new_date=new_date,
	)
	rows, updates = HANDLERS[action](doc, args)
	updates = {key: value for key, value in updates.items() if key in TRACKED_FIELDS}
	snapshot = {key: doc.get(key) for key in updates}

	label = _(rule.label)
	journal_entry = make_journal_entry(
		company=doc.company,
		posting_date=posting_date,
		rows=rows,
		reference_doctype="Payment Entry",
		reference_name=doc.name,
		action=action,
		remark=_("{0}: cheque {1} ({2})").format(label, doc.reference_no, doc.name),
		cheque_no=doc.reference_no,
		cheque_date=doc.reference_date,
		voucher_type="Bank Entry",
		snapshot=snapshot,
		amount=flt(amount) if action == "encash" else cheque_amount(doc),
	)

	frappe.db.set_value("Payment Entry", doc.name, updates)
	note = _("Cheque action: {0} on {1}").format(label, frappe.utils.formatdate(posting_date))
	if journal_entry:
		note += " — " + frappe.utils.get_link_to_form("Journal Entry", journal_entry.name)
	doc.add_comment("Info", note)
	doc.notify_update()

	return {
		"journal_entry": journal_entry.name if journal_entry else None,
		"status": updates.get(field, status),
	}


# Journal Entry cancellation
# --------------------------


def validate_journal_entry_cancel(journal_entry) -> None:
	"""Cheque actions are undone newest first, so each restores a state that is still current."""
	payment_entry = journal_entry.reference_link
	if frappe.db.get_value("Payment Entry", payment_entry, "docstatus") != 1:
		# The Payment Entry itself is being (or has been) cancelled.
		return

	later = frappe.get_all(
		"Journal Entry",
		filters={
			"reference_doctype": "Payment Entry",
			"reference_link": payment_entry,
			"docstatus": 1,
			"name": ["!=", journal_entry.name],
			"creation": [">", journal_entry.creation],
		},
		order_by="creation desc",
		pluck="name",
	)
	if later:
		frappe.throw(
			_("Cheque actions must be cancelled newest first. Cancel {0} before this entry.").format(
				frappe.utils.get_link_to_form("Journal Entry", later[0])
			),
			title=_("Later Cheque Action Exists"),
		)


def on_journal_entry_cancel(journal_entry) -> None:
	"""Put a cheque back to the state it had before the cancelled entry."""
	payment_entry = journal_entry.reference_link
	if frappe.db.get_value("Payment Entry", payment_entry, "docstatus") != 1:
		return

	if journal_entry.get("treasury_action"):
		snapshot = frappe.parse_json(journal_entry.get("treasury_snapshot") or "{}")
		restore = {key: value for key, value in snapshot.items() if key in TRACKED_FIELDS}
	else:
		# An entry posted by the retired ecs_cheques app.
		legacy = state.LEGACY_CANCEL_RESTORE.get(journal_entry.get("pe_status"))
		restore = {legacy[0]: legacy[1], "clearance_date": None} if legacy else {}

	if not restore:
		return
	frappe.db.set_value("Payment Entry", payment_entry, restore)
	frappe.get_doc("Payment Entry", payment_entry).add_comment(
		"Info",
		_("Cheque action reverted by cancelling {0}").format(
			frappe.utils.get_link_to_form("Journal Entry", journal_entry.name)
		),
	)
