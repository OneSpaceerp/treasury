// Copyright (c) 2026, nsdLabs and contributors
// For license information, please see license.txt

frappe.query_reports["Bank Guarantee Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{ fieldname: "from_date", label: __("Start From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Start To"), fieldtype: "Date" },
		{
			fieldname: "bank_guarantee_purpose",
			label: __("Purpose"),
			fieldtype: "Select",
			options: ["", "Bank Guarantee", "Cheque", "Cash", "Deduction"],
		},
		{
			fieldname: "bg_type",
			label: __("Type"),
			fieldtype: "Select",
			options: ["", "Providing", "Receiving"],
		},
		{
			fieldname: "status_of_letter_of_guarantee",
			label: __("Category"),
			fieldtype: "Select",
			options: ["", "Initial", "Advanced Payment", "Final"],
		},
		{
			fieldname: "bank_guarantee_status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["", "Issued", "Extended", "Expired", "Returned"],
		},
		{ fieldname: "bank", label: __("Bank"), fieldtype: "Link", options: "Bank" },
		{
			fieldname: "party_type",
			label: __("Party Type"),
			fieldtype: "Select",
			options: ["", "Customer", "Supplier"],
			on_change() {
				frappe.query_report.set_filter_value("party", "");
			},
		},
		{ fieldname: "party", label: __("Party"), fieldtype: "Dynamic Link", options: "party_type" },
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		// Flag live guarantees that expire within the next 15 days.
		if (column.fieldname === "valid_until" && data.is_live && data.valid_until) {
			const days = frappe.datetime.get_diff(data.valid_until, frappe.datetime.nowdate());
			if (days <= 15) {
				value = `<span class="text-danger bold">${value}</span>`;
			}
		}
		return value;
	},
};
