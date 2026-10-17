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

	# The old app left a cheque in its "returned wallet" status even once it
	# was encashed in full; Treasury calls that Cashed.
	if frappe.db.has_column("Payment Entry", "encashed_amount"):
		pe = frappe.qb.DocType("Payment Entry")
		(
			frappe.qb.update(pe)
			.set(pe.cheque_status, state.CASHED)
			.where(pe.docstatus == 1)
			.where(pe.cheque_status == state.PARTIALLY_ENCASHED)
			.where(IfNull(pe.encashed_amount, 0) >= pe.received_amount)
			.run()
		)

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


# Pre-flight check
# ----------------


def preflight() -> None:
	"""Read-only report to run before replacing the old apps on a site.

	It changes nothing, and works whether or not Treasury is installed yet:

	    bench --site <site> execute treasury.legacy.preflight
	"""
	report = []

	def say(line=""):
		report.append(line)

	installed = [app for app in LEGACY_APPS if app in frappe.get_installed_apps()]
	say(f"Installed apps to replace: {', '.join(installed) or 'none'}")
	say(f"Treasury installed: {'yes' if 'treasury' in frappe.get_installed_apps() else 'no'}")

	open_receive = list(state.OPEN_RECEIVE_STATUSES) + [
		old for old, new in state.LEGACY_RECEIVE_STATUSES.items() if new in state.OPEN_RECEIVE_STATUSES
	]
	open_pay = list(state.OPEN_PAY_STATUSES) + [
		old for old, new in state.LEGACY_PAY_STATUSES.items() if new in state.OPEN_PAY_STATUSES
	]

	if frappe.db.has_column("Payment Entry", "cheque_status"):
		say()
		say("== Submitted cheques by status")
		for payment_type, field in (("Receive", "cheque_status"), ("Pay", "cheque_status_pay")):
			rows = frappe.get_all(
				"Payment Entry",
				filters={"docstatus": 1, "mode_of_payment_type": "Cheque", "payment_type": payment_type},
				fields=[field + " as status", "count(name) as cheques", "sum(base_paid_amount) as amount"],
				group_by=field,
			)
			for row in rows:
				label = state.LEGACY_RECEIVE_STATUSES.get(row.status) or state.LEGACY_PAY_STATUSES.get(
					row.status
				)
				status = f"{row.status} -> {label}" if label else (row.status or "(none)")
				say(f"  {payment_type:8} {status:45} {row.cheques:6} cheques  {row.amount or 0:,.2f}")

		say()
		say("== Open cheques held in a foreign-currency account (Treasury cannot act on these)")
		foreign = []
		for payment_type, field, account_field, currency_field, statuses in (
			("Receive", "cheque_status", "paid_to", "paid_to_account_currency", open_receive),
			("Pay", "cheque_status_pay", "paid_from", "paid_from_account_currency", open_pay),
		):
			for row in frappe.get_all(
				"Payment Entry",
				filters={
					"docstatus": 1,
					"mode_of_payment_type": "Cheque",
					"payment_type": payment_type,
					field: ["in", statuses],
				},
				fields=["name", "company", account_field + " as account", currency_field + " as currency"],
			):
				if row.currency and row.currency != frappe.get_cached_value(
					"Company", row.company, "default_currency"
				):
					foreign.append(row)
		say(
			f"  {len(foreign)} found"
			+ (": " + ", ".join(f"{r.name} ({r.currency})" for r in foreign[:30]) if foreign else "")
		)
		if len(foreign) > 30:
			say(f"  ... and {len(foreign) - 30} more")

		say()
		say("== Draft cheque payments without a company bank account (must be filled before submit)")
		drafts = frappe.get_all(
			"Payment Entry",
			filters={
				"docstatus": 0,
				"mode_of_payment_type": "Cheque",
				"payment_type": ["!=", "Receive"],
				"bank_acc": ["is", "not set"],
			},
			pluck="name",
		)
		say(f"  {len(drafts)} found" + (": " + ", ".join(drafts[:30]) if drafts else ""))

	say()
	say("== Company cheque accounts")
	company_fields = [
		f
		for f in (
			"default_incoming_cheque_wallet_account",
			"default_payback_cheque_wallet_account",
			"default_rejected_cheque_account",
			"default_bank_commissions_account",
		)
		if frappe.db.has_column("Company", f)
	]
	for company in frappe.get_all("Company", fields=["name", *company_fields]):
		say(f"  {company.name}")
		for f in company_fields:
			say(f"    {f:42} {company.get(f) or '(not set)'}")
		payback, incoming = (
			company.get("default_payback_cheque_wallet_account"),
			company.get("default_incoming_cheque_wallet_account"),
		)
		if payback and payback == incoming:
			say("    ! The payback (rejected cheques) account is the incoming wallet account.")
			say(
				"      Treasury posts rejected cheques to the payback account. Read the README before changing it."
			)

	say()
	say("== Company bank accounts missing cheque accounts")
	if frappe.db.has_column("Bank Account", "collection_fee_account"):
		wallets = set(
			frappe.get_all(
				"Mode of Payment Account",
				filters={
					"parent": ["in", frappe.get_all("Mode of Payment", {"type": "Cheque"}, pluck="name")]
				},
				pluck="default_account",
			)
		)
		for bank in frappe.get_all(
			"Bank Account",
			filters={"is_company_account": 1, "disabled": 0},
			fields=["name", "account", "collection_fee_account", "payable_account"],
		):
			issues = []
			if not bank.collection_fee_account:
				issues.append("no under-collection account")
			elif bank.collection_fee_account in wallets:
				issues.append(
					f"under-collection account {bank.collection_fee_account} is also a cheque wallet"
				)
			if not bank.payable_account:
				issues.append("no cheques payable account")
			if issues:
				say(f"  {bank.name}: {'; '.join(issues)}")

	say()
	say("== Bank guarantees")
	if frappe.db.has_column("Bank Guarantee", "bank_guarantee_status"):
		for row in frappe.get_all(
			"Bank Guarantee",
			filters={"docstatus": 1},
			fields=["bank_guarantee_status as status", "count(name) as guarantees", "sum(amount) as amount"],
			group_by="bank_guarantee_status",
		):
			say(f"  {row.status or '(none)':12} {row.guarantees:6} guarantees  {row.amount or 0:,.2f}")
		if frappe.db.has_column("Bank Guarantee", "party_type"):
			missing = frappe.get_all(
				"Bank Guarantee", filters={"docstatus": 1, "party_type": ["is", "not set"]}, pluck="name"
			)
			say(
				f"  submitted without a party: {len(missing)}"
				+ (": " + ", ".join(missing[:30]) if missing else "")
			)
	if not frappe.db.has_column("Bank Guarantee", "company"):
		default = frappe.db.get_single_value("Global Defaults", "default_company")
		say(
			f"  no company field yet; existing guarantees will be assigned to {default or '(no default company!)'}"
		)

	print(chr(10).join(report))
