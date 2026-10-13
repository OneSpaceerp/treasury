// Copyright (c) 2026, nsdLabs and contributors
// For license information, please see license.txt

const RECEIVED_CHEQUE_STATUSES = [
	"In Wallet",
	"Under Collection",
	"Collected",
	"Cashed",
	"Endorsed",
	"Rejected",
	"Partially Encashed",
	"Returned",
];
const ISSUED_CHEQUE_STATUSES = ["Issued", "Paid", "Withdrawn"];

function cheque_status_options(payment_type) {
	const statuses = payment_type === "Receive" ? RECEIVED_CHEQUE_STATUSES : ISSUED_CHEQUE_STATUSES;
	return [{ value: "", label: "" }].concat(statuses.map((status) => ({ value: status, label: __(status) })));
}

frappe.query_reports["Cheques Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "payment_type",
			label: __("Payment Type"),
			fieldtype: "Select",
			options: ["Receive", "Pay", "Internal Transfer"],
			default: "Receive",
			reqd: 1,
			on_change() {
				const status = frappe.query_report.get_filter("status");
				status.df.options = cheque_status_options(frappe.query_report.get_filter_value("payment_type"));
				status.set_value("");
				status.refresh();
				frappe.query_report.refresh();
			},
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: cheque_status_options("Receive"),
		},
		{ fieldname: "from_date", label: __("Due From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Due To"), fieldtype: "Date" },
		{
			fieldname: "party_type",
			label: __("Party Type"),
			fieldtype: "Select",
			options: ["", "Customer", "Supplier", "Employee"],
			on_change() {
				frappe.query_report.set_filter_value("party", "");
			},
		},
		{
			fieldname: "party",
			label: __("Party"),
			fieldtype: "Dynamic Link",
			options: "party_type",
		},
		{
			fieldname: "mode_of_payment",
			label: __("Cheque Wallet"),
			fieldtype: "Link",
			options: "Mode of Payment",
			get_query: () => ({ filters: { type: "Cheque" } }),
		},
		{
			fieldname: "bank_account",
			label: __("Company Bank Account"),
			fieldtype: "Link",
			options: "Bank Account",
			get_query: () => ({ filters: { is_company_account: 1 } }),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (column.fieldname === "status" && data.status) {
			return __(data.status);
		}
		// Flag open cheques that fall due within the next 15 days, or are overdue.
		if (column.fieldname === "due_date" && data.is_open && data.due_date) {
			const days = frappe.datetime.get_diff(data.due_date, frappe.datetime.nowdate());
			if (days <= 15) {
				value = `<span class="text-danger bold">${value}</span>`;
			}
		}
		return value;
	},
};
