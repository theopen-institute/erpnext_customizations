from __future__ import unicode_literals

import frappe, erpnext
import frappe.defaults
from frappe.utils import nowdate, cstr, flt, cint, now, getdate
from frappe import throw, _, scrub
from frappe.utils import formatdate, get_number_format_info

from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry

def pingpong(self,doc):
	frappe.msgprint("hallO")

def custom_payment_entry_validation(doc,method):
	print(PaymentEntry.validate)
	print(validate)
	print(doc.validate)
	doc.validate = validate
	doc.validate(doc)
	#frappe.msgprint("zzzz")

# from payment_entry.py
def validate(self):
	self.setup_party_account_field()
	self.set_missing_values()
	self.validate_payment_type()
	self.validate_party_details()
	self.validate_bank_accounts()
	self.set_exchange_rate()
	self.validate_mandatory()
	validate_reference_documents(self)
	self.set_amounts()
	self.clear_unallocated_reference_document_rows()
	self.validate_payment_against_negative_invoice()
	self.validate_transaction_reference()
	self.set_title()
	self.set_remarks()
	self.validate_duplicate_entry()
	self.validate_allocated_amount()
	self.ensure_supplier_is_not_blocked()
	
def validate_reference_documents(self):
	if self.party_type == "Student":
		valid_reference_doctypes = ("Fees")
	elif self.party_type == "Customer":
		valid_reference_doctypes = ("Sales Order", "Sales Invoice", "Journal Entry")
	elif self.party_type == "Supplier":
		valid_reference_doctypes = ("Purchase Order", "Purchase Invoice", "Journal Entry")
	elif self.party_type == "Employee":
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