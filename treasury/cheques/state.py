"""The cheque state machine, as data.

This module deliberately imports nothing from Frappe: the rules for which
action is allowed from which status are the heart of the cheque workflow, and
keeping them pure lets them be unit-tested without a site.
"""

from dataclasses import dataclass

# Statuses of a received cheque (`Payment Entry.cheque_status`)
IN_WALLET = "In Wallet"
UNDER_COLLECTION = "Under Collection"
COLLECTED = "Collected"
CASHED = "Cashed"
ENDORSED = "Endorsed"
REJECTED = "Rejected"
PARTIALLY_ENCASHED = "Partially Encashed"
RETURNED = "Returned"

RECEIVE_STATUSES = (
	IN_WALLET,
	UNDER_COLLECTION,
	COLLECTED,
	CASHED,
	ENDORSED,
	REJECTED,
	PARTIALLY_ENCASHED,
	RETURNED,
)

# Statuses of an issued cheque (`Payment Entry.cheque_status_pay`)
ISSUED = "Issued"
PAID = "Paid"
WITHDRAWN = "Withdrawn"

PAY_STATUSES = (ISSUED, PAID, WITHDRAWN)

# A cheque in one of these statuses is still an open exposure for the company.
OPEN_RECEIVE_STATUSES = (IN_WALLET, UNDER_COLLECTION, REJECTED, PARTIALLY_ENCASHED)
OPEN_PAY_STATUSES = (ISSUED,)

RECEIVE = "Receive"
OUTGOING = ("Pay", "Internal Transfer")


@dataclass(frozen=True)
class Action:
	name: str
	label: str
	payment_types: tuple
	sources: tuple
	# Only valid while no part of the cheque has been encashed.
	requires_unencashed: bool = False
	# Only valid while some of the cheque value is still outstanding.
	requires_remaining: bool = False
	# Posts no Journal Entry and therefore has nothing to cancel.
	posts_entry: bool = True


ACTIONS = {
	action.name: action
	for action in (
		Action(
			"deposit", "Deposit Under Collection", (RECEIVE,), (IN_WALLET, REJECTED), requires_unencashed=True
		),
		Action("collect", "Collect Cheque", (RECEIVE,), (UNDER_COLLECTION,)),
		Action("reject", "Reject Cheque", (RECEIVE,), (UNDER_COLLECTION,)),
		Action("cash", "Cash Cheque", (RECEIVE,), (IN_WALLET,)),
		Action("endorse", "Endorse Cheque", (RECEIVE,), (IN_WALLET,)),
		Action("transfer", "Transfer to Another Wallet", (RECEIVE,), (IN_WALLET,)),
		Action(
			"return_to_party",
			"Return to Party",
			(RECEIVE,),
			(IN_WALLET, REJECTED, PARTIALLY_ENCASHED),
			requires_remaining=True,
		),
		Action("return_to_wallet", "Return to Wallet", (RECEIVE,), (REJECTED,), requires_unencashed=True),
		Action(
			"encash", "Encash Cheque", (RECEIVE,), (REJECTED, PARTIALLY_ENCASHED), requires_remaining=True
		),
		Action("pay", "Pay Cheque", OUTGOING, (ISSUED,)),
		Action("withdraw", "Withdraw Cheque", OUTGOING, (ISSUED,)),
		Action("reschedule", "Change Due Date", (RECEIVE, *OUTGOING), (IN_WALLET, ISSUED), posts_entry=False),
	)
}


def status_field(payment_type: str) -> str:
	"""The Payment Entry field that holds the cheque status for this payment type."""
	return "cheque_status" if payment_type == RECEIVE else "cheque_status_pay"


def initial_status(payment_type: str) -> str:
	return IN_WALLET if payment_type == RECEIVE else ISSUED


def is_allowed(action: str, payment_type: str, status: str, amount: float = 0, encashed: float = 0) -> bool:
	"""Whether `action` may be applied to a cheque in `status`."""
	rule = ACTIONS.get(action)
	if not rule or payment_type not in rule.payment_types or status not in rule.sources:
		return False
	if rule.requires_unencashed and encashed > 0:
		return False
	if rule.requires_remaining and amount - encashed <= 0:
		return False
	return True


def allowed_actions(payment_type: str, status: str, amount: float = 0, encashed: float = 0) -> list[str]:
	"""Names of the actions available for a cheque, in display order."""
	return [name for name in ACTIONS if is_allowed(name, payment_type, status, amount, encashed)]


# Statuses stored by the retired ecs_cheques app, mapped to the ones above.
LEGACY_RECEIVE_STATUSES = {
	"حافظة شيكات واردة": IN_WALLET,
	"تحت التحصيل": UNDER_COLLECTION,
	"محصل": COLLECTED,
	"محصل فوري": CASHED,
	"مظهر": ENDORSED,
	"مرفوض بالبنك": REJECTED,
	"حافظة شيكات مرجعة": PARTIALLY_ENCASHED,
	"مردود": RETURNED,
}

LEGACY_PAY_STATUSES = {
	"حافظة شيكات برسم الدفع": ISSUED,
	"مدفوع": PAID,
	"مسحوب": WITHDRAWN,
}

# What cancelling a journal entry of the retired app restored the cheque to,
# keyed by the `pe_status` it stamped on the entry: (status field, value).
LEGACY_CANCEL_RESTORE = {
	"محصل فوري": ("cheque_status", IN_WALLET),
	"مظهر": ("cheque_status", IN_WALLET),
	"تحت التحصيل": ("cheque_status", IN_WALLET),
	"تحت التحصيل 2": ("cheque_status", REJECTED),
	"مردود 1": ("cheque_status", IN_WALLET),
	"مردود 2": ("cheque_status", REJECTED),
	"محصل": ("cheque_status", UNDER_COLLECTION),
	"مرفوض بالبنك": ("cheque_status", UNDER_COLLECTION),
	"حافظة شيكات مرجعة": ("cheque_status", REJECTED),
	"مدفوع": ("cheque_status_pay", ISSUED),
	"مسحوب": ("cheque_status_pay", ISSUED),
}
