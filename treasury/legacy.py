"""Bring data of the replaced apps forward.

Treasury supersedes `ecs_cheques`, `ecs_bguarantee` and `nsd_bguarantee`. It
keeps their fieldnames, so existing documents keep working; what changes is
how cheque statuses are stored (English values, translated for display) and a
few pieces of desk furniture that collide with Treasury's own.

Everything here is idempotent and does nothing on a site that never had those
apps. Re-run it at any time with:

    bench --site <site> execute treasury.legacy.migrate_legacy_data
"""

import frappe
from frappe.query_builder.functions import IfNull

from treasury.cheques import state

LEGACY_APPS = ("ecs_cheques", "ecs_bguarantee", "nsd_bguarantee")
LEGACY_MODULES = ("ECS Cheques", "Ecs Bguarantee", "Bank Guarantee")

# Workspaces of the replaced apps. "Bank Guarantee" also shadowed the route of
# the Bank Guarantee list view.
LEGACY_WORKSPACES = ("Bank Guarantee", "Bank Guarantees")
LEGACY_DASHBOARDS = ("Cheques Dashborad",)


def migrate_legacy_data() -> None:
	translate_cheque_statuses()
	backfill_cheque_tracking()
	backfill_guarantee_company()
	remove_legacy_desk_records()
	frappe.clear_cache()


def translate_cheque_statuses() -> None:
	"""Replace the Arabic cheque statuses stored by ecs_cheques with Treasury's."""
	targets = (
		("Payment Entry", "cheque_status", state.LEGACY_RECEIVE_STATUSES),
		("Payment Entry", "cheque_status_pay", state.LEGACY_PAY_STATUSES),
		("Multiple Cheque Entry", "cheque_status", state.LEGACY_RECEIVE_STATUSES),
		("Multiple Cheque Entry", "cheque_status_pay", state.LEGACY_PAY_STATUSES),
	)
	for doctype, fieldname, mapping in targets:
		if not frappe.db.has_column(doctype, fieldname):
			continue
		table = frappe.qb.DocType(doctype)
		for old, new in mapping.items():
			frappe.qb.update(table).set(table[fieldname], new).where(table[fieldname] == old).run()

	# A draft or non-cheque entry carried the default status of the old app.
	payment_entry = frappe.qb.DocType("Payment Entry")
	for fieldname in ("cheque_status", "cheque_status_pay"):
		if frappe.db.has_column("Payment Entry", fieldname):
			(
				frappe.qb.update(payment_entry)
				.set(payment_entry[fieldname], None)
				.where(payment_entry.docstatus == 0)
				.run()
			)


def backfill_cheque_tracking() -> None:
	"""Give cheques of the old app the remaining amount Treasury tracks."""
	if not frappe.db.has_column("Payment Entry", "remaining_amount"):
		return
	payment_entry = frappe.qb.DocType("Payment Entry")
	(
		frappe.qb.update(payment_entry)
		.set(
			payment_entry.remaining_amount,
			payment_entry.received_amount - IfNull(payment_entry.encashed_amount, 0),
		)
		.where(payment_entry.docstatus == 1)
		.where(payment_entry.payment_type == state.RECEIVE)
		.where(payment_entry.mode_of_payment_type == "Cheque")
		.where(IfNull(payment_entry.remaining_amount, 0) == 0)
		.where(payment_entry.cheque_status.isin(state.OPEN_RECEIVE_STATUSES))
		.run()
	)


def backfill_guarantee_company() -> None:
	"""The old apps posted every guarantee to the default company without recording it."""
	if not frappe.db.has_column("Bank Guarantee", "company"):
		return
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if not company:
		return
	guarantee = frappe.qb.DocType("Bank Guarantee")
	(
		frappe.qb.update(guarantee)
		.set(guarantee.company, company)
		.where((guarantee.company.isnull()) | (guarantee.company == ""))
		.run()
	)


def remove_legacy_desk_records() -> None:
	for name in LEGACY_WORKSPACES:
		if frappe.db.get_value("Workspace", name, "module") in LEGACY_MODULES:
			frappe.delete_doc("Workspace", name, ignore_permissions=True, force=True)
	for name in LEGACY_DASHBOARDS:
		if frappe.db.exists("Dashboard", name):
			frappe.delete_doc("Dashboard", name, ignore_permissions=True, force=True)
