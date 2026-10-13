// Copyright (c) 2026, nsdLabs and contributors
// For license information, please see license.txt

// Cheque handling on Payment Entry. Every action is validated and posted on
// the server (treasury.cheques.actions); this script only collects input.

frappe.provide("treasury.cheques");

treasury.cheques.STATUS_COLORS = {
	"In Wallet": "blue",
	"Under Collection": "orange",
	Collected: "green",
	Cashed: "green",
	Endorsed: "purple",
	Rejected: "red",
	"Partially Encashed": "yellow",
	Returned: "gray",
	Issued: "blue",
	Paid: "green",
	Withdrawn: "gray",
};

treasury.cheques.is_cheque = (frm) => frm.doc.mode_of_payment_type === "Cheque";

// Extra dialog fields each action needs besides its date.
treasury.cheques.action_fields = {
	deposit: (frm) => [
		{
			fieldname: "bank_account",
			fieldtype: "Link",
			options: "Bank Account",
			label: __("Deposit To Bank Account"),
			reqd: 1,
			default: frm.doc.bank_acc,
			get_query: () => ({ filters: { is_company_account: 1, company: frm.doc.company } }),
		},
		{
			fieldname: "commission",
			fieldtype: "Currency",
			label: __("Bank Commission"),
			description: __("Leave at zero when the bank charges nothing for the deposit."),
		},
	],
	reject: (frm, context) => [
		{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason for Rejection"), reqd: 1 },
		{
			fieldname: "commission",
			fieldtype: "Currency",
			label: __("Bank Commission"),
			default: context.rejection_commission,
		},
	],
	cash: (frm, context) => [treasury.cheques.cash_account_field(frm, context)],
	endorse: () => [
		{
			fieldname: "party_type",
			fieldtype: "Select",
			label: __("Endorse To Party Type"),
			options: ["Supplier", "Customer", "Employee"],
			default: "Supplier",
			reqd: 1,
		},
		{
			fieldname: "party",
			fieldtype: "Dynamic Link",
			options: "party_type",
			label: __("Endorse To"),
			reqd: 1,
		},
	],
	encash: (frm, context) => [
		{
			fieldname: "amount",
			fieldtype: "Currency",
			label: __("Amount to Encash"),
			default: context.remaining,
			reqd: 1,
			description: __("Remaining: {0}", [format_currency(context.remaining, frm.doc.paid_to_account_currency)]),
		},
		treasury.cheques.cash_account_field(frm, context),
	],
	transfer: (frm) => [
		{
			fieldname: "mode_of_payment",
			fieldtype: "Link",
			options: "Mode of Payment",
			label: __("Transfer To Wallet"),
			reqd: 1,
			get_query: () => ({
				filters: {
					type: "Cheque",
					name: ["!=", frm.doc.new_mode_of_payment || frm.doc.mode_of_payment],
				},
			}),
		},
	],
};

treasury.cheques.cash_account_field = (frm, context) => ({
	fieldname: "cash_account",
	fieldtype: "Link",
	options: "Account",
	label: __("Cash Account"),
	reqd: 1,
	default: context.cash_account,
	get_query: () => ({
		filters: { company: frm.doc.company, is_group: 0, account_type: ["in", ["Cash", "Bank"]] },
	}),
});

treasury.cheques.run_action = (frm, action, context) => {
	const reschedule = action.action === "reschedule";
	const fields = [
		reschedule
			? {
					fieldname: "new_date",
					fieldtype: "Date",
					label: __("New Due Date"),
					reqd: 1,
					default: frm.doc.cheque_new_date || frm.doc.reference_date,
			  }
			: {
					fieldname: "posting_date",
					fieldtype: "Date",
					label: __("Action Date"),
					reqd: 1,
					default: frappe.datetime.get_today(),
			  },
		...(treasury.cheques.action_fields[action.action] || (() => []))(frm, context),
	];

	const dialog = new frappe.ui.Dialog({
		title: action.label,
		fields,
		primary_action_label: action.label,
		primary_action(values) {
			dialog.hide();
			frappe.call({
				method: "treasury.cheques.actions.apply_cheque_action",
				args: { payment_entry: frm.doc.name, action: action.action, ...values },
				freeze: true,
				freeze_message: __("Posting cheque action..."),
				callback(r) {
					if (r.message && r.message.journal_entry) {
						frappe.show_alert({
							message: __("{0} posted as {1}", [action.label, r.message.journal_entry]),
							indicator: "green",
						});
					}
					frm.reload_doc();
				},
			});
		},
	});
	if (action.action === "deposit") {
		// Offer the bank's standard collection commission as the default.
		dialog.fields_dict.bank_account.df.onchange = () => {
			const account = dialog.get_value("bank_account");
			if (!account) return;
			frappe.db.get_value("Bank Account", account, "co3_").then((r) => {
				dialog.set_value("commission", (r.message && r.message.co3_) || 0);
			});
		};
	}
	dialog.show();
};

treasury.cheques.setup_actions = (frm) => {
	if (frm.doc.docstatus !== 1 || !treasury.cheques.is_cheque(frm)) return;

	const status = frm.doc.payment_type === "Receive" ? frm.doc.cheque_status : frm.doc.cheque_status_pay;
	if (status) {
		frm.dashboard.add_indicator(
			__("Cheque: {0}", [__(status)]),
			treasury.cheques.STATUS_COLORS[status] || "gray"
		);
	}

	frappe.call({
		method: "treasury.cheques.actions.get_available_actions",
		args: { payment_entry: frm.doc.name },
		callback(r) {
			const context = r.message || {};
			(context.actions || []).forEach((action) => {
				frm.add_custom_button(
					action.label,
					() => treasury.cheques.run_action(frm, action, context),
					__("Cheque Actions")
				);
			});
		},
	});

	frm.add_custom_button(
		__("Cheque Journal Entries"),
		() => frappe.set_route("List", "Journal Entry", { reference_link: frm.doc.name }),
		__("View")
	);
};

treasury.cheques.set_names = (frm) => {
	// Who a cheque is made out to, and by whom, follows from the direction of
	// the payment when the first beneficiary is the company itself.
	if (!treasury.cheques.is_cheque(frm) || !frm.doc.first_beneficiary) return;
	const party = frm.doc.party_name || frm.doc.party;
	const receiving = frm.doc.payment_type === "Receive";
	if (frm.doc.first_beneficiary === "Company") {
		frm.set_value("person_name", receiving ? frm.doc.company : party);
		frm.set_value("issuer_name", receiving ? party : frm.doc.company);
	} else {
		frm.set_value("person_name", "");
		frm.set_value("issuer_name", receiving ? "" : frm.doc.company);
	}
};

frappe.ui.form.on("Payment Entry", {
	setup(frm) {
		frm.set_query("cheque_bank", () => ({ filters: { company_bank: 1 } }));
		frm.set_query("bank_acc", () => {
			const filters = { is_company_account: 1, company: frm.doc.company };
			if (frm.doc.cheque_bank) filters.bank = frm.doc.cheque_bank;
			return { filters };
		});
	},

	refresh(frm) {
		treasury.cheques.setup_actions(frm);
	},

	cheque_bank(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.bank_acc) {
			frm.set_value("bank_acc", "");
		}
	},

	bank_acc(frm) {
		if (frm.doc.docstatus !== 0) return;
		if (!frm.doc.bank_acc) {
			["account", "collection_fee_account", "payable_account"].forEach((f) => frm.set_value(f, ""));
			return;
		}
		frappe.db
			.get_value("Bank Account", frm.doc.bank_acc, [
				"bank",
				"account",
				"collection_fee_account",
				"payable_account",
			])
			.then((r) => {
				const bank = r.message || {};
				frm.set_value("account", bank.account);
				frm.set_value("collection_fee_account", bank.collection_fee_account);
				frm.set_value("payable_account", bank.payable_account);
				if (!frm.doc.cheque_bank && bank.bank) {
					frm.doc.cheque_bank = bank.bank;
					frm.refresh_field("cheque_bank");
				}
				// An issued cheque sits in the bank's cheques payable account
				// until the bank pays it.
				if (treasury.cheques.is_cheque(frm) && frm.doc.payment_type === "Pay" && bank.payable_account) {
					frm.set_value("paid_from", bank.payable_account);
				}
			});
	},

	first_beneficiary(frm) {
		treasury.cheques.set_names(frm);
	},
});
