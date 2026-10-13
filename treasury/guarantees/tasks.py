import frappe
from frappe.utils import getdate, nowdate


def expire_guarantees() -> None:
	"""Daily: mark live guarantees whose end date has passed as Expired.

	Idempotent, so a double-fired scheduler tick changes nothing.
	"""
	today = getdate(nowdate())
	guarantees = frappe.get_all(
		"Bank Guarantee",
		filters={"docstatus": 1, "returned": 0, "bank_guarantee_status": ["in", ["Issued", "Extended"]]},
		fields=["name", "end_date", "new_date", "extend_validity"],
	)
	for guarantee in guarantees:
		end_date = (
			guarantee.new_date if guarantee.extend_validity and guarantee.new_date else guarantee.end_date
		)
		if end_date and getdate(end_date) < today:
			frappe.db.set_value("Bank Guarantee", guarantee.name, "bank_guarantee_status", "Expired")
