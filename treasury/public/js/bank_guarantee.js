// Copyright (c) 2026, nsdLabs and contributors
// For license information, please see license.txt

// Treasury additions to the Bank Guarantee form. Amounts, accounts and the
// lifecycle are all decided on the server
// (treasury.guarantees.overrides.bank_guarantee); the calculations here only
// preview what the server will store.

frappe.provide("treasury.guarantees");

treasury.guarantees.STATUS_COLORS = {
	New: "gray",
	Issued: "blue",
	Extended: "purple",
	Expired: "red",
	Returned: "green",
	Cancelled: "gray",
};

treasury.guarantees.leaf_accounts = (frm, account_types) => {
	const filters = { company: frm.doc.company, is_group: 0 };
	if (account_types) filters.account_type = ["in", account_types];
	return { filters };
};

treasury.guarantees.set_split = (frm) => {
	if (frm.doc.docstatus !== 0 || frm.doc.bank_guarantee_purpose !== "Bank Guarantee") return;
	const amount = flt(frm.doc.amount);
	if (frm.doc.banking_facilities === "With Facilities") {
		const rate = flt(frm.doc.rate);
		const bank_amount = flt((amount * rate) / 100, precision("amount"));
		frm.set_value("bank_amount", bank_amount);
		frm.set_value("rate2", 100 - rate);
		frm.set_value("facility_amount", flt(amount - bank_amount, precision("amount")));
	} else {
		frm.set_value("rate", 100);
		frm.set_value("rate2", 0);
		frm.set_value("bank_amount", amount);
		frm.set_value("facility_amount", 0);
	}
};

treasury.guarantees.return_guarantee = (frm) => {
	const deduction = frm.doc.bank_guarantee_purpose === "Deduction";
	const dialog = new frappe.ui.Dialog({
		title: __("Return Guarantee"),
		fields: [
			{
				fieldname: "posting_date",
				fieldtype: "Date",
				label: __("Return Date"),
				reqd: 1,
				default: frappe.datetime.get_today(),
			},
			{
				fieldname: "deduction_return",
				fieldtype: "Link",
				options: "Account",
				label: __("Return Account"),
				reqd: deduction,
				hidden: !deduction,
				default: frm.doc.deduction_return,
				get_query: () => treasury.guarantees.leaf_accounts(frm, ["Cash", "Bank"]),
			},
		],
		primary_action_label: __("Return"),
		primary_action(values) {
			dialog.hide();
			frappe.call({
				method: "treasury.guarantees.overrides.bank_guarantee.return_guarantee",
				args: { name: frm.doc.name, ...values },
				freeze: true,
				freeze_message: __("Posting return entry..."),
				callback(r) {
					if (r.message) {
						frappe.show_alert({
							message: __("Guarantee returned. Journal Entry {0}", [r.message.journal_entry]),
							indicator: "green",
						});
					}
					frm.reload_doc();
				},
			});
		},
	});
	dialog.show();
};

treasury.guarantees.extend_guarantee = (frm) => {
	const current = frm.doc.extend_validity && frm.doc.new_date ? frm.doc.new_date : frm.doc.end_date;
	frappe.prompt(
		[
			{
				fieldname: "new_end_date",
				fieldtype: "Date",
				label: __("New End Date"),
				reqd: 1,
				description: __("Current end date: {0}", [frappe.datetime.str_to_user(current)]),
			},
		],
		(values) => {
			frappe.call({
				method: "treasury.guarantees.overrides.bank_guarantee.extend_guarantee",
				args: { name: frm.doc.name, new_end_date: values.new_end_date },
				freeze: true,
				callback: () => frm.reload_doc(),
			});
		},
		__("Extend Validity"),
		__("Extend")
	);
};

frappe.ui.form.on("Bank Guarantee", {
	setup(frm) {
		frm.set_query("party_type", () => ({ filters: { name: ["in", ["Customer", "Supplier"]] } }));
		frm.set_query("dpaccount", () => treasury.guarantees.leaf_accounts(frm));
		frm.set_query("dpaccountc", () => treasury.guarantees.leaf_accounts(frm));
		frm.set_query("cash_account", () => treasury.guarantees.leaf_accounts(frm, ["Cash"]));
		frm.set_query("deduction_return", () => treasury.guarantees.leaf_accounts(frm, ["Cash", "Bank"]));
		frm.set_query("bank_account", () => {
			const filters = { is_company_account: 1, company: frm.doc.company };
			if (frm.doc.bank) filters.bank = frm.doc.bank;
			return { filters };
		});
		frm.set_query("account_for_bank_facilities", () => {
			const filters = { is_company_account: 1, company: frm.doc.company };
			if (frm.doc.bank) filters.bank = frm.doc.bank;
			return { filters };
		});
	},

	onload(frm) {
		if (frm.is_new() && !frm.doc.company) {
			frm.set_value("company", frappe.defaults.get_user_default("Company"));
		}
	},

	refresh(frm) {
		const status = frm.doc.bank_guarantee_status;
		if (frm.doc.docstatus === 1 && status) {
			frm.dashboard.add_indicator(__(status), treasury.guarantees.STATUS_COLORS[status] || "gray");
		}
		if (frm.doc.docstatus !== 1) return;

		if (!frm.doc.returned && frm.perm[0] && frm.perm[0].submit) {
			frm.add_custom_button(__("Return Guarantee"), () => treasury.guarantees.return_guarantee(frm), __("Actions"));
			frm.add_custom_button(__("Extend Validity"), () => treasury.guarantees.extend_guarantee(frm), __("Actions"));
		}
		frm.add_custom_button(
			__("Journal Entries"),
			() => frappe.set_route("List", "Journal Entry", { reference_link: frm.doc.name }),
			__("View")
		);
	},

	party_type(frm) {
		if (frm.doc.docstatus === 0) frm.set_value("party", "");
	},

	party(frm) {
		if (frm.doc.docstatus !== 0 || !frm.doc.party) return;
		const customer = frm.doc.party_type === "Customer";
		// Core keeps the counterparty in these two fields; the server keeps
		// them in step, this just mirrors it on the form.
		frm.set_value("customer", customer ? frm.doc.party : "");
		frm.set_value("supplier", customer ? "" : frm.doc.party);
		frappe.db
			.get_value(frm.doc.party_type, frm.doc.party, customer ? "customer_name" : "supplier_name")
			.then((r) => {
				const name = r.message && Object.values(r.message)[0];
				frm.set_value("name_of_beneficiary", name || frm.doc.party);
			});
	},

	bank_guarantee_purpose(frm) {
		treasury.guarantees.set_split(frm);
	},

	banking_facilities(frm) {
		treasury.guarantees.set_split(frm);
	},

	amount(frm) {
		treasury.guarantees.set_split(frm);
	},

	rate(frm) {
		treasury.guarantees.set_split(frm);
	},

	account_for_bank_facilities(frm) {
		if (!frm.doc.account_for_bank_facilities) {
			frm.set_value("account_1", "");
			return;
		}
		frappe.db.get_value("Bank Account", frm.doc.account_for_bank_facilities, "account").then((r) => {
			frm.set_value("account_1", r.message && r.message.account);
		});
	},
});
