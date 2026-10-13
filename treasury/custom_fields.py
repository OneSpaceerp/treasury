"""Custom fields and property setters Treasury adds to ERPNext doctypes.

They are created in code (on install and after every migrate) rather than
shipped as fixtures, so there is one definition, it is idempotent, and it
upgrades the fields left behind by the apps Treasury replaces in place: the
fieldnames below are the ones those apps used, so existing data keeps working.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from treasury.cheques import state

MODULE = "Treasury"

IS_CHEQUE = 'doc.mode_of_payment_type == "Cheque"'
CHEQUE_NOT_TRANSFER = f'eval:{IS_CHEQUE} && doc.payment_type != "Internal Transfer"'
CHEQUE_RECEIVED = f'eval:{IS_CHEQUE} && doc.payment_type == "Receive"'
CHEQUE_ISSUED = f'eval:{IS_CHEQUE} && doc.payment_type != "Receive"'

PURPOSE_IS_BANK = 'eval:in_list(["Bank Guarantee", "Cheque"], doc.bank_guarantee_purpose)'
PURPOSE_IS_GUARANTEE = 'eval:doc.bank_guarantee_purpose == "Bank Guarantee"'
WITH_FACILITIES = (
	'eval:doc.bank_guarantee_purpose == "Bank Guarantee" && doc.banking_facilities == "With Facilities"'
)


def options(*values: str) -> str:
	return "\n".join(values)


def chain(first_anchor: str, fields: list[dict]) -> list[dict]:
	"""Place each field after the previous one, starting after `first_anchor`.

	A field that sets its own `insert_after` starts a new run from there.
	"""
	anchor = first_anchor
	for field in fields:
		field.setdefault("insert_after", anchor)
		field.setdefault("module", MODULE)
		anchor = field["fieldname"]
	return fields


def get_custom_fields() -> dict[str, list[dict]]:
	return {
		"Company": chain(
			"default_scrap_warehouse",
			[
				dict(fieldname="treasury_tab", fieldtype="Tab Break", label="Treasury"),
				dict(fieldname="cheque_accounts", fieldtype="Section Break", label="Cheque Accounts"),
				dict(
					fieldname="default_incoming_cheque_wallet_account",
					fieldtype="Link",
					options="Account",
					label="Incoming Cheques Wallet Account",
					description="Holds received cheques when the Mode of Payment has no account of its own.",
				),
				dict(
					fieldname="default_payback_cheque_wallet_account",
					fieldtype="Link",
					options="Account",
					label="Rejected Cheques Account",
					description="Holds cheques rejected by the bank until they are redeposited, encashed or returned.",
				),
				dict(fieldname="column_break_treasury_1", fieldtype="Column Break"),
				dict(
					fieldname="default_bank_commissions_account",
					fieldtype="Link",
					options="Account",
					label="Bank Commissions Account",
					description="Expense account for bank commissions on cheque deposits and rejections.",
				),
				dict(
					fieldname="guarantee_cheque_accounts_section",
					fieldtype="Section Break",
					label="Guarantee Cheque Accounts",
				),
				dict(
					fieldname="acc1",
					fieldtype="Link",
					options="Account",
					label="Guarantee Cheques Issued Account",
					description="Credited when a cheque is provided to a third party as a guarantee.",
				),
				dict(fieldname="column_break_treasury_2", fieldtype="Column Break"),
				dict(
					fieldname="acc3",
					fieldtype="Link",
					options="Account",
					label="Guarantee Cheques Received Account",
					description="Debited when a cheque is received from a third party as a guarantee.",
				),
				dict(
					fieldname="acc2",
					fieldtype="Link",
					options="Account",
					label="Guarantee Cheques Received Contra Account",
					description="Credited against the guarantee cheques received account.",
				),
			],
		),
		"Bank": chain(
			"bank_name",
			[dict(fieldname="company_bank", fieldtype="Check", label="Company Bank")],
		),
		"Bank Account": chain(
			"account",
			[
				dict(
					fieldname="collection_fee_account",
					fieldtype="Link",
					options="Account",
					label="Cheques Under Collection Account",
					depends_on="is_company_account",
					description="Holds received cheques deposited with this bank until they are collected.",
				),
				dict(
					fieldname="payable_account",
					fieldtype="Link",
					options="Account",
					label="Cheques Payable Account",
					depends_on="is_company_account",
					description="Holds cheques issued on this bank account until the bank pays them.",
				),
				dict(
					fieldname="commission",
					fieldtype="Section Break",
					label="Cheque Commissions",
					depends_on="is_company_account",
					insert_after="mask",
				),
				dict(fieldname="co3_", fieldtype="Currency", label="Cheque Collection Commission"),
				dict(fieldname="column_break_treasury_1", fieldtype="Column Break"),
				dict(fieldname="co5_", fieldtype="Currency", label="Cheque Rejection Commission"),
			],
		),
		"Customer Group": chain(
			"is_group",
			[
				dict(
					fieldname="bg_insurance_account",
					fieldtype="Link",
					options="Account",
					label="Guarantee Deposit Account",
					description="Default deposit account for bank guarantees of customers in this group.",
				)
			],
		),
		"Journal Entry": chain(
			"party_not_required",
			[
				dict(
					fieldname="reference_section",
					fieldtype="Section Break",
					label="Treasury Reference",
					collapsible=1,
					depends_on="reference_link",
				),
				dict(
					fieldname="reference_doctype",
					fieldtype="Link",
					options="DocType",
					label="Treasury Document Type",
					read_only=1,
					no_copy=1,
				),
				dict(
					fieldname="reference_link",
					fieldtype="Dynamic Link",
					options="reference_doctype",
					label="Treasury Document",
					read_only=1,
					no_copy=1,
					search_index=1,
				),
				dict(fieldname="column_break_treasury_1", fieldtype="Column Break"),
				dict(
					fieldname="treasury_action",
					fieldtype="Data",
					label="Treasury Action",
					read_only=1,
					no_copy=1,
				),
				dict(
					fieldname="treasury_amount",
					fieldtype="Currency",
					label="Treasury Amount",
					read_only=1,
					no_copy=1,
					hidden=1,
				),
				dict(
					fieldname="treasury_snapshot",
					fieldtype="Long Text",
					label="Treasury Snapshot",
					read_only=1,
					no_copy=1,
					hidden=1,
				),
			],
		),
		"Payment Entry": get_payment_entry_fields(),
		"Bank Guarantee": get_bank_guarantee_fields(),
	}


def get_payment_entry_fields() -> list[dict]:
	return chain(
		"mode_of_payment",
		[
			dict(
				fieldname="mode_of_payment_type",
				fieldtype="Data",
				label="Mode of Payment Type",
				fetch_from="mode_of_payment.type",
				read_only=1,
				hidden=1,
			),
			# Cheque details, entered on the draft
			dict(
				fieldname="cheque_info",
				fieldtype="Section Break",
				label="Cheque Details",
				depends_on=f"eval:{IS_CHEQUE}",
				insert_after="clearance_date",
			),
			dict(
				fieldname="cheque_status",
				fieldtype="Select",
				label="Cheque Status",
				options=options("", *state.RECEIVE_STATUSES),
				depends_on=f"eval:{IS_CHEQUE} && doc.payment_type == 'Receive' && doc.docstatus == 1",
				read_only=1,
				no_copy=1,
				allow_on_submit=1,
				in_standard_filter=1,
				search_index=1,
			),
			dict(
				fieldname="cheque_status_pay",
				fieldtype="Select",
				label="Issued Cheque Status",
				options=options("", *state.PAY_STATUSES),
				depends_on=f"eval:{IS_CHEQUE} && doc.payment_type != 'Receive' && doc.docstatus == 1",
				read_only=1,
				no_copy=1,
				allow_on_submit=1,
				in_standard_filter=1,
				search_index=1,
			),
			dict(
				fieldname="cheque_type",
				fieldtype="Select",
				label="Cheque Type",
				options=options("", "Opened", "Crossed", "Endorsed", "Issued To First Beneficiary"),
				mandatory_depends_on=CHEQUE_NOT_TRANSFER,
			),
			dict(
				fieldname="drawn_bank",
				fieldtype="Link",
				options="Bank",
				label="Drawn Bank",
				depends_on=CHEQUE_RECEIVED,
				mandatory_depends_on=CHEQUE_RECEIVED,
			),
			dict(
				fieldname="first_beneficiary",
				fieldtype="Select",
				label="First Beneficiary",
				options=options("", "Personal", "Company"),
				mandatory_depends_on=CHEQUE_NOT_TRANSFER,
			),
			dict(fieldname="column_break_cheque_1", fieldtype="Column Break"),
			dict(
				fieldname="issuer_name",
				fieldtype="Data",
				label="Issuer Name",
				mandatory_depends_on=CHEQUE_NOT_TRANSFER,
			),
			dict(fieldname="person_name", fieldtype="Data", label="Beneficiary Name"),
			dict(fieldname="holder", fieldtype="Link", options="Employee", label="Holder", allow_on_submit=1),
			dict(
				fieldname="holder_name",
				fieldtype="Data",
				label="Holder Name",
				fetch_from="holder.employee_name",
				depends_on="holder",
				read_only=1,
				allow_on_submit=1,
			),
			dict(fieldname="picture_of_check", fieldtype="Attach", label="Cheque Image", allow_on_submit=1),
			# The company bank: chosen on the draft for an issued cheque, set by
			# the Deposit action for a received one
			dict(
				fieldname="bank_details",
				fieldtype="Section Break",
				label="Company Bank",
				depends_on=f"eval:{IS_CHEQUE} && (doc.payment_type != 'Receive' || doc.bank_acc)",
			),
			dict(
				fieldname="cheque_bank",
				fieldtype="Link",
				options="Bank",
				label="Bank",
				read_only_depends_on="eval:doc.payment_type == 'Receive'",
				no_copy=1,
			),
			dict(
				fieldname="bank_acc",
				fieldtype="Link",
				options="Bank Account",
				label="Bank Account",
				mandatory_depends_on=CHEQUE_ISSUED,
				read_only_depends_on="eval:doc.payment_type == 'Receive'",
				no_copy=1,
			),
			dict(fieldname="column_break_cheque_2", fieldtype="Column Break"),
			dict(
				fieldname="account",
				fieldtype="Link",
				options="Account",
				label="Bank GL Account",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="collection_fee_account",
				fieldtype="Link",
				options="Account",
				label="Cheques Under Collection Account",
				depends_on="eval:doc.payment_type == 'Receive'",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="payable_account",
				fieldtype="Link",
				options="Account",
				label="Cheques Payable Account",
				depends_on="eval:doc.payment_type != 'Receive'",
				read_only=1,
				no_copy=1,
			),
			# Maintained by the cheque actions after submission
			dict(
				fieldname="cheque_tracking_section",
				fieldtype="Section Break",
				label="Cheque Tracking",
				depends_on=f"eval:{IS_CHEQUE} && doc.docstatus == 1",
			),
			dict(
				fieldname="new_mode_of_payment",
				fieldtype="Link",
				options="Mode of Payment",
				label="Current Cheque Wallet",
				depends_on="eval:doc.payment_type == 'Receive'",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="cheque_wallet_account",
				fieldtype="Link",
				options="Account",
				label="Cheque Wallet Account",
				depends_on="eval:doc.payment_type == 'Receive'",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="reason_for_rejection",
				fieldtype="Small Text",
				label="Reason for Rejection",
				depends_on="reason_for_rejection",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="change_date",
				fieldtype="Check",
				label="Due Date Changed",
				depends_on="change_date",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="cheque_new_date",
				fieldtype="Date",
				label="New Due Date",
				depends_on="change_date",
				read_only=1,
				no_copy=1,
			),
			dict(fieldname="column_break_cheque_3", fieldtype="Column Break"),
			dict(
				fieldname="encashed_amount",
				fieldtype="Currency",
				label="Encashed Amount",
				options="paid_to_account_currency",
				depends_on="eval:doc.payment_type == 'Receive'",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="remaining_amount",
				fieldtype="Currency",
				label="Remaining Amount",
				options="paid_to_account_currency",
				depends_on="eval:doc.payment_type == 'Receive'",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="party_type_",
				fieldtype="Link",
				options="DocType",
				label="Endorsed To Party Type",
				depends_on="party_",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="party_",
				fieldtype="Dynamic Link",
				options="party_type_",
				label="Endorsed To",
				depends_on="party_",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="account_1",
				fieldtype="Link",
				options="Account",
				label="Endorsee Account",
				depends_on="party_",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="reference_doctype",
				fieldtype="Link",
				options="DocType",
				label="Created From Type",
				read_only=1,
				no_copy=1,
				hidden=1,
			),
			dict(
				fieldname="reference_link",
				fieldtype="Dynamic Link",
				options="reference_doctype",
				label="Created From",
				depends_on="reference_link",
				read_only=1,
				no_copy=1,
			),
			dict(
				fieldname="multiple_cheque_row",
				fieldtype="Data",
				label="Multiple Cheque Entry Row",
				read_only=1,
				no_copy=1,
				hidden=1,
			),
		],
	)


def get_bank_guarantee_fields() -> list[dict]:
	return chain(
		"bg_type",
		[
			dict(
				fieldname="bank_guarantee_purpose",
				fieldtype="Select",
				label="Guarantee Purpose",
				options=options("Bank Guarantee", "Cheque", "Cash", "Deduction"),
				default="Bank Guarantee",
				reqd=1,
				in_standard_filter=1,
			),
			dict(
				fieldname="company",
				fieldtype="Link",
				options="Company",
				label="Company",
				reqd=1,
			),
			dict(
				fieldname="status_of_letter_of_guarantee",
				fieldtype="Select",
				label="Guarantee Category",
				options=options("", "Initial", "Advanced Payment", "Final"),
				mandatory_depends_on=PURPOSE_IS_GUARANTEE,
			),
			dict(
				fieldname="party_type",
				fieldtype="Link",
				options="DocType",
				label="Party Type",
				reqd=1,
				insert_after="reference_docname",
			),
			dict(
				fieldname="party",
				fieldtype="Dynamic Link",
				options="party_type",
				label="Party",
				reqd=1,
				in_standard_filter=1,
			),
			dict(
				fieldname="customer_group",
				fieldtype="Link",
				options="Customer Group",
				label="Customer Group",
				depends_on="customer_group",
				read_only=1,
				insert_after="customer",
			),
			dict(
				fieldname="customer_address",
				fieldtype="Data",
				label="Customer Address",
				depends_on='eval:doc.bg_type == "Receiving"',
				mandatory_depends_on='eval:doc.bg_type == "Receiving"',
			),
			# Lifecycle, maintained by the server
			dict(
				fieldname="posting_date",
				fieldtype="Date",
				label="Posting Date",
				default="Today",
				insert_after="column_break_6",
			),
			dict(
				fieldname="bank_guarantee_status",
				fieldtype="Select",
				label="Status",
				options=options("New", "Issued", "Extended", "Expired", "Returned", "Cancelled"),
				default="New",
				read_only=1,
				no_copy=1,
				allow_on_submit=1,
				in_list_view=1,
				in_standard_filter=1,
				search_index=1,
			),
			dict(
				fieldname="issued",
				fieldtype="Check",
				label="Issued",
				read_only=1,
				no_copy=1,
				hidden=1,
				allow_on_submit=1,
			),
			dict(
				fieldname="returned",
				fieldtype="Check",
				label="Returned",
				read_only=1,
				no_copy=1,
				hidden=1,
				allow_on_submit=1,
			),
			dict(
				fieldname="return_date",
				fieldtype="Date",
				label="Return Date",
				depends_on="return_date",
				read_only=1,
				no_copy=1,
				allow_on_submit=1,
			),
			dict(
				fieldname="extend_validity",
				fieldtype="Check",
				label="Validity Extended",
				depends_on="extend_validity",
				read_only=1,
				no_copy=1,
				allow_on_submit=1,
				insert_after="end_date",
			),
			dict(
				fieldname="no_of_extended_days",
				fieldtype="Int",
				label="Extended Days",
				depends_on="extend_validity",
				read_only=1,
				no_copy=1,
				allow_on_submit=1,
			),
			dict(
				fieldname="new_date",
				fieldtype="Date",
				label="Extended End Date",
				depends_on="extend_validity",
				read_only=1,
				no_copy=1,
				allow_on_submit=1,
			),
			dict(
				fieldname="type_of_letter_of_guarantee",
				fieldtype="Select",
				label="Conditions",
				options=options("Without Condition", "With Condition"),
				default="Without Condition",
			),
			dict(
				fieldname="bg_condition",
				fieldtype="Small Text",
				label="Guarantee Condition",
				depends_on='eval:doc.type_of_letter_of_guarantee == "With Condition"',
				mandatory_depends_on='eval:doc.type_of_letter_of_guarantee == "With Condition"',
			),
			# Bank section
			dict(
				fieldname="bg_commission",
				fieldtype="Currency",
				label="Issue Commission",
				depends_on=PURPOSE_IS_BANK,
				insert_after="bank_account_no",
			),
			dict(
				fieldname="reference_no",
				fieldtype="Data",
				label="Cheque No",
				depends_on='eval:doc.bank_guarantee_purpose == "Cheque"',
				mandatory_depends_on='eval:doc.bank_guarantee_purpose == "Cheque"',
			),
			dict(
				fieldname="reference_date",
				fieldtype="Date",
				label="Cheque Date",
				depends_on='eval:doc.bank_guarantee_purpose == "Cheque"',
				mandatory_depends_on='eval:doc.bank_guarantee_purpose == "Cheque"',
			),
			# Accounts
			dict(
				fieldname="guarantee_accounts_section",
				fieldtype="Section Break",
				label="Guarantee Accounts",
				insert_after="swift_number",
			),
			dict(
				fieldname="dpaccount",
				fieldtype="Link",
				options="Account",
				label="Guarantee Deposit Account",
				fetch_from="customer_group.bg_insurance_account",
				fetch_if_empty=1,
				description="The asset or liability account that carries the guarantee while it is outstanding.",
			),
			dict(
				fieldname="dpaccountc",
				fieldtype="Link",
				options="Account",
				label="Fees Account",
				depends_on=PURPOSE_IS_BANK,
				description="Expense account for the bank's issue commission.",
			),
			dict(
				fieldname="cash_account",
				fieldtype="Link",
				options="Account",
				label="Cash Account",
				depends_on='eval:doc.bank_guarantee_purpose == "Cash"',
				mandatory_depends_on='eval:doc.bank_guarantee_purpose == "Cash"',
			),
			dict(
				fieldname="deduction_return",
				fieldtype="Link",
				options="Account",
				label="Return Account",
				depends_on='eval:doc.bank_guarantee_purpose == "Deduction"',
				allow_on_submit=1,
				description="Cash or bank account the deduction is settled through when the guarantee is returned.",
			),
			dict(fieldname="column_break_treasury_1", fieldtype="Column Break"),
			dict(
				fieldname="banking_facilities",
				fieldtype="Select",
				label="Banking Facilities",
				options=options("Without Facilities", "With Facilities"),
				default="Without Facilities",
				depends_on=PURPOSE_IS_GUARANTEE,
			),
			dict(
				fieldname="rate",
				fieldtype="Percent",
				label="Bank Rate %",
				depends_on=WITH_FACILITIES,
				mandatory_depends_on=WITH_FACILITIES,
				description="Share of the guarantee covered by cash held at the bank.",
			),
			dict(
				fieldname="bank_amount",
				fieldtype="Currency",
				label="Bank Amount",
				depends_on=PURPOSE_IS_GUARANTEE,
				read_only=1,
			),
			dict(
				fieldname="account_for_bank_facilities",
				fieldtype="Link",
				options="Bank Account",
				label="Bank Facilities Account",
				depends_on=WITH_FACILITIES,
				mandatory_depends_on=WITH_FACILITIES,
			),
			dict(
				fieldname="account_1",
				fieldtype="Link",
				options="Account",
				label="Bank Facilities GL Account",
				depends_on=WITH_FACILITIES,
				read_only=1,
			),
			dict(
				fieldname="rate2",
				fieldtype="Percent",
				label="Facility Rate %",
				depends_on=WITH_FACILITIES,
				read_only=1,
			),
			dict(
				fieldname="facility_amount",
				fieldtype="Currency",
				label="Facility Amount",
				depends_on=WITH_FACILITIES,
				read_only=1,
			),
			# Documents
			dict(
				fieldname="guarantee_documents_section",
				fieldtype="Section Break",
				label="Documents",
				insert_after="more_information",
			),
			dict(
				fieldname="bank_guarantee_copy_after_receiving",
				fieldtype="Attach",
				label="Guarantee Copy After Receiving",
				allow_on_submit=1,
			),
			dict(
				fieldname="conditions_brochure",
				fieldtype="Attach",
				label="Conditions Brochure",
				depends_on='eval:doc.status_of_letter_of_guarantee == "Initial"',
				allow_on_submit=1,
			),
			dict(
				fieldname="po",
				fieldtype="Attach",
				label="Purchase Order",
				depends_on='eval:doc.status_of_letter_of_guarantee && doc.status_of_letter_of_guarantee != "Initial"',
				allow_on_submit=1,
			),
		],
	)


# (doctype, fieldname or None for the doctype itself, property, value, property type)
PROPERTY_SETTERS = [
	# The counterparty is entered once, as the party; core's own fields follow it.
	("Bank Guarantee", "customer", "hidden", "1", "Check"),
	("Bank Guarantee", "supplier", "hidden", "1", "Check"),
	("Bank Guarantee", "customer", "depends_on", "", "Data"),
	("Bank Guarantee", "supplier", "depends_on", "", "Data"),
	("Bank Guarantee", "bank", "depends_on", PURPOSE_IS_BANK, "Data"),
	("Bank Guarantee", "bank_account", "depends_on", PURPOSE_IS_BANK, "Data"),
	("Bank Guarantee", "account", "depends_on", "eval:doc.bank_account", "Data"),
	("Bank Guarantee", "bank", "in_standard_filter", "1", "Check"),
	# Replaced by the guarantee accounts and the issue commission.
	("Bank Guarantee", "margin_money", "hidden", "1", "Check"),
	("Bank Guarantee", "charges", "hidden", "1", "Check"),
	("Bank Guarantee", "fixed_deposit_number", "hidden", "1", "Check"),
]

# Fields of the replaced apps whose job is now done by cheque/guarantee
# actions. Their metadata is removed so they stop showing on the forms; the
# database columns, and so the historical values, are left untouched.
OBSOLETE_CUSTOM_FIELDS = {
	"Payment Entry": [
		"section_break_0",
		"cheque_action",
		"column_break_2",
		"cheque_action_date",
		"reference_section",
		"column_break_28",
		"column_break_99",
		"column_break_19",
		"with_bank_commission",
		"co3_",
		"co4_",
		"co5_",
		"co6",
		"encashment_amount",
		"cheque_table_no",
		"cheque_table_no2",
		"logs",
		"mode_of_payment_2",
	],
	"Bank Guarantee": ["bg_commission_", "mode_of_payment", "facility_amount_2"],
	"Bank Account": ["co4_", "co6", "column_break_36"],
	"Company": ["default_rejected_cheque_account", "column_break_52", "column_break_60"],
	"Journal Entry": ["column_break_12"],
}


def setup_customizations() -> None:
	"""Create or update every Treasury customization. Safe to run repeatedly."""
	remove_obsolete_custom_fields()
	create_custom_fields(get_custom_fields(), ignore_validate=True)
	for doctype, fieldname, prop, value, property_type in PROPERTY_SETTERS:
		set_property(doctype, fieldname, prop, value, property_type)
	add_cheque_mode_of_payment_type()
	frappe.clear_cache()


def remove_obsolete_custom_fields() -> None:
	for doctype, fieldnames in OBSOLETE_CUSTOM_FIELDS.items():
		for name in frappe.get_all(
			"Custom Field", filters={"dt": doctype, "fieldname": ["in", fieldnames]}, pluck="name"
		):
			# Only the metadata row goes; the column and its values stay.
			frappe.db.delete("Custom Field", {"name": name})
		frappe.clear_cache(doctype=doctype)


def set_property(doctype: str, fieldname: str | None, prop: str, value: str, property_type: str) -> None:
	frappe.make_property_setter(
		{
			"doctype": doctype,
			"doctype_or_field": "DocField" if fieldname else "DocType",
			"fieldname": fieldname,
			"property": prop,
			"value": value,
			"property_type": property_type,
		},
		validate_fields_for_doctype=False,
		is_system_generated=False,
		module=MODULE,
	)


def add_cheque_mode_of_payment_type() -> None:
	"""Add "Cheque" to the Mode of Payment types, keeping whatever core defines."""
	current = frappe.get_meta("Mode of Payment").get_field("type").options or ""
	types = [option for option in current.split("\n") if option]
	if "Cheque" in types:
		return
	set_property("Mode of Payment", "type", "options", "\n".join([*types, "Cheque"]), "Text")
