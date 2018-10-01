from __future__ import unicode_literals

import frappe, erpnext
import frappe.defaults
from frappe.utils import nowdate, cstr, flt, cint, now, getdate
from frappe import throw, _, scrub
from frappe.utils import formatdate, get_number_format_info

from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry


def onloadping(doc,method):
	print("############# hook method2")
	print(doc.validate_reference_documents)
	PaymentEntry.validate_reference_documents = custom_validate_reference_documents
	print(doc.validate_reference_documents)

	#PaymentEntry.get_orders_to_be_billed = custom_get_orders_to_be_billed
	#print(PaymentEntry.validate_reference_documents)
	#print(doc.validate_reference_documents)
	#doc.validate_reference_documents(doc)

	
	#PaymentEntry.validate_reference_documents = custom_validate_reference_documents
	#Pay.validate(doc)
	#frappe.msgprint("zzzz")

def customize_before_validate(doc,method):
	print("&&&&&&&&&&&&&& before validate")
	PaymentEntry.validate_reference_documents = custom_validate_reference_documents

def customize_payment_entry(doc,method):
	print("############# hook method")
	PaymentEntry.validate_reference_documents = custom_validate_reference_documents
	
	doc.setup_party_account_field()
	doc.set_missing_values()
	doc.validate_payment_type()
	doc.validate_party_details()
	doc.validate_bank_accounts()
	doc.set_exchange_rate()
	doc.validate_mandatory()
	doc.validate_reference_documents()
	doc.set_amounts()
	doc.clear_unallocated_reference_document_rows()
	doc.validate_payment_against_negative_invoice()
	doc.validate_transaction_reference()
	doc.set_title()
	doc.set_remarks()
	doc.validate_duplicate_entry()
	doc.validate_allocated_amount()
	doc.ensure_supplier_is_not_blocked()

	return {'override': True}


# methods to override from payment_entry.py
def custom_validate_reference_documents(self):
	print("############# SUCCESSFUL OVERRIDE AAAAAAA")
	#raise Exception('New method called intentionally!')
	if self.party_type == "Student":
		valid_reference_doctypes = ("Fees")
	elif self.party_type == "Customer":
		valid_reference_doctypes = ("Sales Order", "Sales Invoice", "Journal Entry")
	elif self.party_type == "Supplier":
		valid_reference_doctypes = ("Purchase Order", "Purchase Invoice", "Journal Entry")
	elif self.party_type == "Employee":
		# erpnext_tinkererization: Payroll Voucher added to list of valid reference doctypes
		valid_reference_doctypes = ("Expense Claim", "Journal Entry", "Employee Advance", "Payroll Voucher")

	for d in self.get("references"):
		if not d.allocated_amount:
			continue
		if d.reference_doctype not in valid_reference_doctypes:
			frappe.throw(_("Reference Doctype must be one of {0}")
				.format(comma_or(valid_reference_doctypes)))

		elif d.reference_name:
			if not frappe.db.exists(d.reference_doctype, d.reference_name):
				frappe.throw(_("{0} {1} does not exist").format(d.reference_doctype, d.reference_name))
			else:
				ref_doc = frappe.get_doc(d.reference_doctype, d.reference_name)

				if d.reference_doctype != "Journal Entry":
					# erpnext_tinkererization: Payroll Voucher logic added (very simple as yet)
					if d.reference_doctype != "Payroll Voucher" and self.party != ref_doc.get(scrub(self.party_type)):
						frappe.throw(_("{0} {1} is not associated with {2} {3}")
							.format(d.reference_doctype, d.reference_name, self.party_type, self.party))
				else:
					self.validate_journal_entry()

				if d.reference_doctype in ("Sales Invoice", "Purchase Invoice", "Expense Claim", "Fees"):
					if self.party_type == "Customer":
						ref_party_account = ref_doc.debit_to
					elif self.party_type == "Student":
						ref_party_account = ref_doc.receivable_account
					elif self.party_type=="Supplier":
						ref_party_account = ref_doc.credit_to
					elif self.party_type=="Employee":
						ref_party_account = ref_doc.payable_account

					if ref_party_account != self.party_account:
							frappe.throw(_("{0} {1} is associated with {2}, but Party Account is {3}")
								.format(d.reference_doctype, d.reference_name, ref_party_account, self.party_account))

				if ref_doc.docstatus != 1:
					frappe.throw(_("{0} {1} must be submitted")
						.format(d.reference_doctype, d.reference_name))


def custom_get_orders_to_be_billed(posting_date, party_type, party, party_account_currency, company_currency, cost_center=None):
	print("###########SUCCESSFUL OVERRIDE BBBBBBB")
	if party_type == "Customer":
		voucher_type = 'Sales Order'
	elif party_type == "Supplier":
		voucher_type = 'Purchase Order'
	elif party_type == "Employee":
		voucher_type = None

	# Add cost center condition
	# erpnext_tinkererization: added a check here to see if voucher_type exists, otherwise get_doc is liable to crash
	if voucher_type:
		doc = frappe.get_doc({"doctype": voucher_type})
		condition = ""
		if doc and hasattr(doc, 'cost_center'):
			condition = " and cost_center='%s'" % cost_center

	orders = []
	if voucher_type:
		ref_field = "base_grand_total" if party_account_currency == company_currency else "grand_total"

		orders = frappe.db.sql("""
			select
				name as voucher_no,
				{ref_field} as invoice_amount,
				({ref_field} - advance_paid) as outstanding_amount,
				transaction_date as posting_date
			from
				`tab{voucher_type}`
			where
				{party_type} = %s
				and docstatus = 1
				and ifnull(status, "") != "Closed"
				and {ref_field} > advance_paid
				and abs(100 - per_billed) > 0.01
				{condition}
			order by
				transaction_date, name
		""".format(**{
			"ref_field": ref_field,
			"voucher_type": voucher_type,
			"party_type": scrub(party_type),
			"condition": condition
		}), party, as_dict=True)

	order_list = []
	for d in orders:
		d["voucher_type"] = voucher_type
		# This assumes that the exchange rate required is the one in the SO
		d["exchange_rate"] = get_exchange_rate(party_account_currency, company_currency, posting_date)
		order_list.append(d)

	return order_list