# -*- coding: utf-8 -*-
# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from dateutil.relativedelta import relativedelta
from frappe.utils import cint, flt, nowdate, add_days, getdate, fmt_money, add_to_date, DATE_FORMAT
from frappe import _
from erpnext.accounts.utils import get_fiscal_year
from erpnext.hr.doctype.employee.employee import get_holiday_list_for_employee

from frappe.model.meta import get_field_precision
from erpnext.accounts.general_ledger import get_round_off_account_and_cost_center
from erpnext.controllers.accounts_controller import AccountsController
from erpnext.accounts.general_ledger import make_gl_entries

from erpnext.hr.doctype.payroll_entry.payroll_entry import PayrollEntry
import erpnext.hr.doctype.payroll_entry.payroll_entry


class PayrollVoucher(AccountsController, PayrollEntry):
	"""
		This doctype "Payroll Voucher" records a set of Salary Slips into to the general ledger. It allows
		authorized users to generate new Salary Slips for a given subset of employees at a company, 
		identified by their payroll frequency and period of work and optionally filtered by Branch, 
		Department, and Designation.

		When a pay period and frequency have been entered, the Payroll Voucher will find all employees 
		who are listed as active in matching Salary Structures, and it will match those employees to 
		Salary Slips that already exist for them. Users are then given the opportunity to create new Salary
		Slips for any employees who currently lack them for the relevant period. These new slips are created 
		with sensible defaults, and they can be edited further before the Payroll Voucher is submitted.

		The Payroll Voucher aggregates all credits and debits on each account to a single ledger entry. This
		allows HR to keep individual salary structures private even when accounts are public. If, however,
		the company's default payroll acount is marked as of type "Payable", each employee's net income
		will be alloted to an individual account.
		

		ADAPTED FROM Payroll Entry doctype, as of 17 May 2018
		TODO:
			- some loose ends might exist related (especially to multiple currencies)
			- the employee loan part needs to be significantly tested
			- make the "Create missing salary slips" button appear or disappear as needed
			- create a button or link that takes users to a view of salary slips relevantly filtered
			- FIX: "Payroll Frequency" still expected to match even if timesheets is checked
	"""

	####################
	### CRUD methods ###
	####################
	def on_submit(self):
		"""
			MODIFIED: submit unsubmitted salary slips on submission of payroll voucher
		"""
 		self.submit_salary_slips()

 	def on_cancel(self):
 		"""
 			NEW: remove ledger entries on cancellation
 		"""
 		self.register_payroll_in_gl(cancel=True)



 	################################
	### Doctype building methods ###
	################################
	def fill_salary_slips(self):
		"""
			NEW
			This function updates the Payroll Voucher's table of Payroll Salary Slip Detail documents, a custom
			child doctype. These documents map salary slips to specific employees for the given pay period.
		"""
		self.set('salary_slips', [])
		salary_slips = self.get_sal_slip_list(as_dict=True)
		for d in salary_slips:
			self.append('salary_slips', d)

		employees = self.get_emp_list()
		self.add_missing_slips(employees)

	def get_sal_slip_list(self, as_dict=False, ss_status=0):
		"""
			MODIFIED to select more fields and to get both draft and submitted slips for period
			Returns list of existing salary slips matching doctument-specified criteria
		"""
		cond = self.get_filter_condition()

		ss_list = frappe.db.sql("""
			select t1.name, t1.name as salary_slip, t1.employee, t1.employee_name, t1.start_date, t1.end_date from `tabSalary Slip` t1
			where t1.docstatus != 2 and t1.start_date >= %s and t1.end_date <= %s
			and (t1.journal_entry is null or t1.journal_entry = "") and ifnull(salary_slip_based_on_timesheet,0) = %s %s
		""" % ('%s', '%s','%s', cond), (self.start_date, self.end_date, self.salary_slip_based_on_timesheet), as_dict=as_dict)
		return ss_list

	def add_missing_slips(self, employees):
		"""
			NEW
			Add lines to the Payroll Salary Slip Detail table representing employees who don't currently
			have salary slips but who should because they match the specified criteria
		"""
		if employees: 
			all_employees = [o.employee for o in employees]
			employees_with_slips = [o.employee for o in self.get('salary_slips')]
			employees_without_slips = list(set(all_employees) - set(employees_with_slips))

			for e in employees_without_slips:
				slipless = frappe.new_doc("Payroll Salary Slip Detail")
				slipless.employee = e
				slipless.employee_name = [n.employee_name for n in employees if n.employee == e][0]
				slipless.salary_slip = None
				self.append('salary_slips', slipless)


	def create_salary_slips(self):
		"""
			MODIFIED: ???
			Creates salary slip for selected employees if already not created
		"""
		self.check_permission('write')
		self.created = 1;
		emp_list = self.get_emp_list()
		ss_list = []
		if emp_list:
			for emp in emp_list:
				if not frappe.db.sql("""select
						name from `tabSalary Slip`
					where
						docstatus!= 2 and
						employee = %s and
						start_date >= %s and
						end_date <= %s and
						company = %s
						""", (emp['employee'], self.start_date, self.end_date, self.company)):
					ss = frappe.get_doc({
						"doctype": "Salary Slip",
						"salary_slip_based_on_timesheet": self.salary_slip_based_on_timesheet,
						"payroll_frequency": self.payroll_frequency,
						"start_date": self.start_date,
						"end_date": self.end_date,
						"employee": emp['employee'],
						"employee_name": frappe.get_value("Employee", {"name":emp['employee']}, "employee_name"),
						"company": self.company,
						"posting_date": self.posting_date
					})
					ss.insert()
					ss_dict = {}
					ss_dict["Employee Name"] = ss.employee_name
					ss_dict["Total Pay"] = fmt_money(ss.rounded_total,currency = frappe.defaults.get_global_default("currency"))
					ss_dict["Salary Slip"] = format(ss.name)[0]
					ss_list.append(ss_dict)
			self.fill_salary_slips()
		return ss_list

	###############################
	### LEDGER BUILDING METHODS ###
	###############################

	def register_payroll_in_gl(self, cancel=False, adv_adj=False):
		"""
			NEW
			Apply net payroll transactions to the General Ledger. If default payroll account is of type "Payable",
			issue net salary each relevant employee's payable account, otherwise aggregate together and issue to
			the payroll account.
		"""
		self.check_permission('write')
		default_payroll_payable_account = self.get_default_payroll_payable_account()
 		payroll_account_is_type_payable = self.check_if_account_is_type_payable(default_payroll_payable_account)
 		slips = self.get_sal_slip_list(as_dict=True)
		
 		earnings = self.get_salary_components(component_type="earnings") or {}
 		deductions = self.get_salary_components(component_type="deductions") or {}
 		loan_details = self.get_loan_details()


 		gl_map = []
 		# manage earnings
 		for earning in earnings:
 			earning["account"] = self.get_salary_component_account(earning["salary_component"])
 			is_flexible_benefit, only_tax_impact = frappe.db.get_value("Salary Component", earning['salary_component'], ['is_flexible_benefit', 'only_tax_impact'])
 			# if the earning is not actually salary but only_tax_impact, do not add it to the GL
 			if not (is_flexible_benefit and only_tax_impact):
	 			gl_map.append(self.new_gl_line(
	 				account=self.get_salary_component_account(earning["salary_component"]),
	 				debit=earning["amount"],
	 				#against_voucher=earning["parent"],
	 				#against_voucher_type="Salary Slip"
	 			))

	 	# manage deductions
 		for deduction in deductions:
 			deduction["account"] = self.get_salary_component_account(deduction["salary_component"])
 			gl_map.append(self.new_gl_line(
 				account=self.get_salary_component_account(deduction["salary_component"]),
 				credit=deduction["amount"],
 				#against_voucher=deduction["parent"],
 				#against_voucher_type="Salary Slip"
 			))

 		# manage loans
		for loan in loan_details:
			gl_map.append(self.new_gl_line(
				account=loan.loan_account,
				against=loan.employee,
				credit=loan.principal_amount,
				party_type="Employee",
				party=loan.employee
			))

			if loan.interest_amount and not loan.interest_income_account:
					frappe.throw(_("Select interest income account in employee loan {0}").format(loan.loan))

			if loan.interest_income_account and loan.interest_amount:
				gl_map.append(self.new_gl_line(
					account=loan.interest_income_account,
					against=loan.employee,
					credit=loan.interest_amount,

				))

		# manage payable amounts
		for slip in slips:
			ss = frappe.get_doc("Salary Slip", slip["name"])
			if self.aggregate_salary_slips:
				gl_map.append(self.new_gl_line(
					account=default_payroll_payable_account,
					credit=ss.net_pay
				))
			else:
				gl_map.append(self.new_gl_line(
					account=default_payroll_payable_account,
					credit=ss.net_pay,
					party=ss.employee,
					party_type="Employee",
					against_voucher=ss.name,
					against_voucher_type="Salary Slip"
				))

		self.round_off_debit_credit(gl_map)
		
		## iterate through the gl_map to set "against" values for everything.
		credit_accts = ", ".join(list(set((item["account"] for item in gl_map if item["credit"] > 0))))
		debit_accts = ", ".join(list(set((item["account"] for item in gl_map if item["debit"] > 0))))
		for gle in gl_map:
			if gle["credit"] > 0:
				gle["against"] = debit_accts
			elif gle["debit"] > 0:
				gle["against"] = credit_accts
				
		make_gl_entries(gl_map, cancel=cancel, adv_adj=adv_adj, merge_entries=True)


	def new_gl_line(self, account=None, credit=None, debit=None, party=None, party_type=None, against_voucher=None, against_voucher_type=None):
		"""
			NEW: Utility function to help register_payroll_in_gl
		"""
		return self.get_gl_dict({
			"account": account,
			"credit": flt(credit, frappe.get_precision("Journal Entry Account", "credit_in_account_currency")),
			"debit": flt(debit, frappe.get_precision("Journal Entry Account", "debit_in_account_currency")),
			"party": party,
			"party_type": party_type,
			"against_voucher": against_voucher,
			"against_voucher_type": against_voucher_type,
	# 		"account_currency": d.account_currency,
	# 		"debit_in_account_currency": flt(d.debit_in_account_currency, d.precision("debit_in_account_currency")),
	# 		"credit_in_account_currency": flt(d.credit_in_account_currency, d.precision("credit_in_account_currency")),
			"remarks": _('Accrual for salaries from {0} to {1}').format(self.start_date, self.end_date),
			"cost_center": self.cost_center,
			"project": self.project,
			"company": self.company,
			"posting_date": self.posting_date,
		})

	def get_salary_components(self, component_type):
		"""
			MODIFIED: select also parent field to be able to distinguish by individual salary slip
		"""
		salary_slips = self.get_sal_slip_list(ss_status = 1, as_dict = True)
		if salary_slips:
			salary_components = frappe.db.sql("""select salary_component, amount, parentfield, parent
				from `tabSalary Detail` where parentfield = '%s' and parent in (%s)""" %
				(component_type, ', '.join(['%s']*len(salary_slips))), tuple([d.name for d in salary_slips]), as_dict=True)
			return salary_components

	def round_off_debit_credit(self, gl_map):
		"""
			NEW: add a rounding entry if necessary to balance credit/debit
		"""
		precision = get_field_precision(frappe.get_meta("GL Entry").get_field("debit"),
			currency=frappe.db.get_value("Company", gl_map[0].company, "default_currency", cache=True))

		debit_credit_diff = 0.0
		for entry in gl_map:
			entry.debit = flt(entry.debit, precision)
			entry.credit = flt(entry.credit, precision)
			debit_credit_diff += entry.debit - entry.credit

		debit_credit_diff = flt(debit_credit_diff, precision)
		round_off_account, round_off_cost_center = get_round_off_account_and_cost_center(gl_map[0].company)
		
		round_off_gle = frappe._dict()
		for k in ["voucher_type", "voucher_no", "company",
			"posting_date", "remarks", "is_opening"]:
				round_off_gle[k] = gl_map[0][k]

		round_off_gle.update({
			"account": round_off_account,
			"debit_in_account_currency": abs(debit_credit_diff) if debit_credit_diff < 0 else 0,
			"credit_in_account_currency": debit_credit_diff if debit_credit_diff > 0 else 0,
			"debit": abs(debit_credit_diff) if debit_credit_diff < 0 else 0,
			"credit": debit_credit_diff if debit_credit_diff > 0 else 0,
			"cost_center": round_off_cost_center,
			"party_type": None,
			"party": None,
			"against_voucher_type": None,
			"against_voucher": None
		})

		gl_map.append(round_off_gle)

	def check_if_account_is_type_payable(self, account):
		acct_type = frappe.db.get_value(doctype="Account", fieldname="account_type", filters={"name": account})
		is_payable = (acct_type == "Payable")
		return is_payable

	def submit_salary_slips(self):
		"""
			MODIFIED: single line changed to make this call the custom non-class method submit_salary_slips_for_employees_mod
			(If there were a way to override non-class methods somehow, this wouldn't be necessary)
		"""
		self.check_permission('write')
		ss_list = self.get_sal_slip_list(ss_status=0)
		if len(ss_list) > 30:
			frappe.enqueue(submit_salary_slips_for_employees_mod, timeout=600, payroll_entry=self, salary_slips=ss_list)
		else:
			submit_salary_slips_for_employees_mod(self, ss_list, publish_progress=False)


##########################################
### non-class methods to be overridden ###
##########################################

def submit_salary_slips_for_employees_mod(payroll_entry, salary_slips, publish_progress=True):
	"""
		MODIFIED: Single line changed
		payroll_entry.make_accrual_jv_entry() -> payroll_entry.register_payroll_in_gl(cancel=False)
	"""
	submitted_ss = []
	not_submitted_ss = []
	frappe.flags.via_payroll_entry = True

	count = 0
	for ss in salary_slips:
		ss_obj = frappe.get_doc("Salary Slip",ss[0])
		if ss_obj.net_pay<0:
			not_submitted_ss.append(ss[0])
		else:
			try:
				ss_obj.submit()
				submitted_ss.append(ss_obj)
			except frappe.ValidationError:
				not_submitted_ss.append(ss[0])
		
		count += 1
		if publish_progress:
			frappe.publish_progress(count*100/len(salary_slips), title = _("Submitting Salary Slips..."))

	if submitted_ss:
		#payroll_entry.make_accrual_jv_entry()
		payroll_entry.register_payroll_in_gl(cancel=False)
		frappe.msgprint(_("Salary Slip submitted for period from {0} to {1}")
			.format(ss_obj.start_date, ss_obj.end_date))

		payroll_entry.email_salary_slip(submitted_ss)
	
	payroll_entry.db_set("salary_slips_submitted", 1)
	payroll_entry.notify_update()

	if not submitted_ss and not not_submitted_ss:
		frappe.msgprint(_("No salary slip found to submit for the above selected criteria OR salary slip already submitted"))

	if not_submitted_ss:
		frappe.msgprint(_("Could not submit some Salary Slips"))	

#########################################################################
### NOTHING TO SEE BELOW HERE; OLD STUFF KEPT FOR REVIEW IF NECESSARY ###
#########################################################################

	# def something_else(self):

 	# 	payable_amounts = {}
 	# 	total_payable = 0
 	# 	for slip in slips:
 	# 		name = slip['salary_slip']
 	# 		emp = slip['employee']
 	# 		net_amount = frappe.db.get_value(doctype="Salary Slip", fieldname="rounded_total", filters={"name": name})
 	# 		payable_amounts[emp] = net_amount
 	# 		total_payable += net_amount

 	# 	self.set('base_grand_total', total_payable)
 		
 	# 	# register accounts that will be set against earnings and deductions
 	# 	against_earnings = []
 	# 	for acct in deductions:
 	# 		against_earnings.append(acct)
 	# 	if payroll_account_is_type_payable:
 	# 		for emp in payable_amounts:
 	# 			against_earnings.append(emp)
 	# 	else:
 	# 		against_earnings.append(default_payroll_payable_account)

 	# 	against_deductions = []
		# for acct in earnings:
		# 	against_deductions.append(acct)

 		

 	# 	### Now we'll build up the general ledger map
 	# 	gl_map = []
 	# 	if self.aggregate_salary_slips:
 	# 		if earnings or deductions:
		# 		for acc, amount in earnings.items():
		# 			print(acc, amount)
		# 			gl_map.append(self.new_gl_line(
		# 				account=acc,
		# 				against=", ".join(list(set(against_earnings))),
		# 				debit=amount
		# 			))

		# 		# deductions
		# 		for acc, amount in deductions.items():
		# 			gl_map.append(self.new_gl_line(
		# 				account=acc,
		# 				against=", ".join(list(set(against_deductions))),
		# 				credit=amount,
		# 			))

		# 		# Loan
		# 		for loan in loan_details:
		# 			gl_map.append(self.new_gl_line(
		# 				account=loan.loan_account,
		# 				against=loan.employee,
		# 				credit=loan.principal_amount,
		# 				party_type="Employee",
		# 				party=loan.employee
		# 			))

		# 			if loan.interest_amount and not loan.interest_income_account:
	 # 					frappe.throw(_("Select interest income account in employee loan {0}").format(loan.loan))

	 # 				if loan.interest_income_account and loan.interest_amount:
	 # 					gl_map.append(self.new_gl_line(
	 # 						account=loan.interest_income_account,
	 # 						against=loan.employee,
	 # 						credit=loan.interest_amount,

	 # 					))

 	# 		gl_map.append(self.new_gl_line(
		# 			account=default_payroll_payable_account,
		# 			against=",".join(list(set(against_deductions))),
		# 			credit=total_payable
		# 		))

 	# 	else: 	
 	# 		# if payroll_account_is_type_payable:
 	# 		# 	#for emp, amt in payable_amounts.items():
		# 		# #	gl_map.append(self.new_gl_line(
		# 		# #		account=default_payroll_payable_account,
		# 		# #		against=", ".join(list(set(against_deductions))),
		# 		# #		credit=amt,
		# 		# #		party_type="Employee",
		# 		# #		party=emp
		# 		# #	))
		# 	for slip in slips:
	 # 			name = slip['salary_slip']
	 # 			#slip_doc = frappe.get_doc("Salary Slip", name)
	 # 			emp = slip['employee']
	 # 			net_amount = frappe.db.get_value(doctype="Salary Slip", fieldname="rounded_total", filters={"name": name})
	 # 			payable_amounts[emp] = net_amount
	 # 			total_payable += net_amount
	 # 			print(slip)
	 # 			gl_map.append(self.new_gl_line(
		# 			account=default_payroll_payable_account,
		# 			against=", ".join(list(set(against_deductions))),
		# 			credit=net_amount,
		# 			party_type="Employee",
		# 			party=emp,
		# 			against_voucher=name,
		# 			against_voucher_type="Salary Slip"
		# 		))



 	# 	# earnings and deductions
		# if earnings or deductions:
		# 	# earnings
		# 	for acc, amount in earnings.items():
		# 		print(acc, amount)
		# 		gl_map.append(self.new_gl_line(
		# 			account=acc,
		# 			against=", ".join(list(set(against_earnings))),
		# 			debit=amount
		# 		))

		# 	# deductions
		# 	for acc, amount in deductions.items():
		# 		gl_map.append(self.new_gl_line(
		# 			account=acc,
		# 			against=", ".join(list(set(against_deductions))),
		# 			credit=amount,
		# 		))

		# 	# Loan
			

 	# 		###
 	# 		##Get rid of this distinction; this entire section will now be for aggregated
 	# 		###

 	# 		# payable
		# 	if payroll_account_is_type_payable:
		# 		for slip in slips:
		#  			name = slip['salary_slip']
		#  			#slip_doc = frappe.get_doc("Salary Slip", name)
		#  			emp = slip['employee']
		#  			net_amount = frappe.db.get_value(doctype="Salary Slip", fieldname="rounded_total", filters={"name": name})
		#  			payable_amounts[emp] = net_amount
		#  			total_payable += net_amount
		#  			print(slip)
		#  			gl_map.append(self.new_gl_line(
		# 				account=default_payroll_payable_account,
		# 				against=", ".join(list(set(against_deductions))),
		# 				credit=net_amount,
		# 				party_type="Employee",
		# 				party=emp,
		# 				against_voucher=name,
		# 				against_voucher_type="Salary Slip"
		# 			))

		# 		#for emp, amt in payable_amounts.items():
		# 		#	gl_map.append(self.new_gl_line(
		# 		#		account=default_payroll_payable_account,
		# 		#		against=", ".join(list(set(against_deductions))),
		# 		#		credit=amt,
		# 		#		party_type="Employee",
		# 		#		party=emp
		# 		#	))

		# 	else:
		# 		gl_map.append(self.new_gl_line(
		# 			account=default_payroll_payable_account,
		# 			against=",".join(list(set(against_deductions))),
		# 			credit=total_payable
		# 		))


	"""
		what follows are all functions used to fetch and aggregate various parts of Salary Slips, called
		by the register_payroll_in_gl function.
	"""
	# def get_salary_component_total(self, component_type = None):
	# 	salary_components = self.get_salary_components(component_type)
	# 	if salary_components:
	# 		component_dict = {}
	# 		for item in salary_components:
	# 			component_dict[item['salary_component']] = component_dict.get(item['salary_component'], 0) + item['amount']
	# 		account_details = self.get_account(component_dict = component_dict)
	# 		return account_details

	# def get_salary_components(self, component_type):
	# 	salary_slips = self.get_sal_slip_list(as_dict = True)
	# 	if salary_slips:
	# 		salary_components = frappe.db.sql("""select salary_component, amount, parentfield
	# 			from `tabSalary Detail` where parentfield = '%s' and parent in (%s)""" %
	# 			(component_type, ', '.join(['%s']*len(salary_slips))), tuple([d.salary_slip for d in salary_slips]), as_dict=True)
	# 		return salary_components

	# def get_account(self, component_dict = None):
	# 	account_dict = {}
	# 	for s, a in component_dict.items():
	# 		account = self.get_salary_component_account(s)
	# 		account_dict[account] = account_dict.get(account, 0) + a
	# 	return account_dict

	# def get_salary_component_account(self, salary_component):
	# 	account = frappe.db.get_value("Salary Component Account",
	# 		{"parent": salary_component, "company": self.company}, "default_account")
	# 	if not account:
	# 		frappe.throw(_("Please set default account in Salary Component {0}")
	# 			.format(salary_component))
	# 	return account

	# def get_default_payroll_payable_account(self):
	# 	payroll_payable_account = frappe.db.get_value("Company",
	# 		{"company_name": self.company}, "default_payroll_payable_account")
	# 	if not payroll_payable_account:
	# 		frappe.throw(_("Please set Default Payroll Payable Account in Company {0}")
	# 			.format(self.company))
	# 	return payroll_payable_account

	# def get_loan_details(self):
	# 	"""
	# 		Get loan details from submitted salary slip based on selected criteria
	# 	"""
	# 	cond = self.get_filter_condition()
	# 	return frappe.db.sql(""" select t1.employee, eld.loan_account, eld.loan,
	# 			eld.interest_income_account, eld.principal_amount, eld.interest_amount, eld.total_payment
	# 		from
	# 			`tabSalary Slip` t1, `tabSalary Slip Loan` eld
	# 		where
	# 			t1.docstatus = 1 and t1.name = eld.parent and start_date >= %s and end_date <= %s %s
	# 		""" % ('%s', '%s', cond), (self.start_date, self.end_date), as_dict=True) or []

	# def get_total_salary_amount(self):
	# 	"""
	# 		Get total salary amount from submitted salary slip based on selected criteria
	# 	"""
	# 	cond = self.get_filter_condition()
	# 	totals = frappe.db.sql(""" select sum(rounded_total) as rounded_total from `tabSalary Slip` t1
	# 		where t1.docstatus = 1 and start_date >= %s and end_date <= %s %s
	# 		""" % ('%s', '%s', cond), (self.start_date, self.end_date), as_dict=True)
	# 	return totals and totals[0] or None


# 	def update_salary_slip_status(self, jv_name = None):
# 		ss_list = self.get_sal_slip_list(ss_status=1)
# 		for ss in ss_list:
# 			ss_obj = frappe.get_doc("Salary Slip",ss[0])
# 			frappe.db.set_value("Salary Slip", ss_obj.name, "status", "Paid")
# 			frappe.db.set_value("Salary Slip", ss_obj.name, "journal_entry", jv_name)

# 	def set_start_end_dates(self):
# 		self.update(get_start_end_dates(self.payroll_frequency,
# 			self.start_date or self.posting_date, self.company))


# @frappe.whitelist()
# def get_start_end_dates(payroll_frequency, start_date=None, company=None):
# 	'''Returns dict of start and end dates for given payroll frequency based on start_date'''

# 	if payroll_frequency == "Monthly" or payroll_frequency == "Bimonthly" or payroll_frequency == "":
# 		fiscal_year = get_fiscal_year(start_date, company=company)[0]
# 		month = "%02d" % getdate(start_date).month
# 		m = get_month_details(fiscal_year, month)
# 		if payroll_frequency == "Bimonthly":
# 			if getdate(start_date).day <= 15:
# 				start_date = m['month_start_date']
# 				end_date = m['month_mid_end_date']
# 			else:
# 				start_date = m['month_mid_start_date']
# 				end_date = m['month_end_date']
# 		else:
# 			start_date = m['month_start_date']
# 			end_date = m['month_end_date']

# 	if payroll_frequency == "Weekly":
# 		end_date = add_days(start_date, 6)

# 	if payroll_frequency == "Fortnightly":
# 		end_date = add_days(start_date, 13)

# 	if payroll_frequency == "Daily":
# 		end_date = start_date

# 	return frappe._dict({
# 		'start_date': start_date, 'end_date': end_date
# 	})

# def validate_employee_attendance(self):
# 	employees_to_mark_attendance = []
# 	days_in_payroll, days_holiday, days_attendance_marked = 0, 0, 0
# 	for employee_detail in self.employees:
# 		days_holiday = self.get_count_holidays_of_employee(employee_detail.employee)
# 		days_attendance_marked = self.get_count_employee_attendance(employee_detail.employee)
# 		days_in_payroll = date_diff(self.end_date, self.start_date) + 1
# 		if days_in_payroll > days_holiday + days_attendance_marked:
# 			employees_to_mark_attendance.append({
# 				"employee": employee_detail.employee,
# 				"employee_name": employee_detail.employee_name
# 				})
# 	return employees_to_mark_attendance

# def get_count_holidays_of_employee(self, employee):
# 	holiday_list = get_holiday_list_for_employee(employee)
# 	holidays = 0
# 	if holiday_list:
# 		days = frappe.db.sql("""select count(*) from tabHoliday where
# 			parent=%s and holiday_date between %s and %s""", (holiday_list,
# 			self.start_date, self.end_date))
# 		if days and days[0][0]:
# 			holidays = days[0][0]
# 	return holidays

# def get_count_employee_attendance(self, employee):
# 	marked_days = 0
# 	attendances = frappe.db.sql("""select count(*) from tabAttendance where
# 		employee=%s and docstatus=1 and attendance_date between %s and %s""",
# 		(employee, self.start_date, self.end_date))
# 	if attendances and attendances[0][0]:
# 		marked_days = attendances[0][0]
# 	return marked_days


# def get_frequency_kwargs(frequency_name):
# 	frequency_dict = {
# 		'monthly': {'months': 1},
# 		'fortnightly': {'days': 14},
# 		'weekly': {'days': 7},
# 		'daily': {'days': 1}
# 	}
# 	return frequency_dict.get(frequency_name)




# @frappe.whitelist()
# def get_end_date(start_date, frequency):
# 	start_date = getdate(start_date)
# 	frequency = frequency.lower() if frequency else 'monthly'
# 	kwargs = get_frequency_kwargs(frequency) if frequency != 'bimonthly' else get_frequency_kwargs('monthly')

# 	# weekly, fortnightly and daily intervals have fixed days so no problems
# 	end_date = add_to_date(start_date, **kwargs) - relativedelta(days=1)
# 	if frequency != 'bimonthly':
# 		return dict(end_date=end_date.strftime(DATE_FORMAT))

# 	else:
# 		return dict(end_date='')


# def get_month_details(year, month):
# 	ysd = frappe.db.get_value("Fiscal Year", year, "year_start_date")
# 	if ysd:
# 		import calendar, datetime
# 		diff_mnt = cint(month)-cint(ysd.month)
# 		if diff_mnt<0:
# 			diff_mnt = 12-int(ysd.month)+cint(month)
# 		msd = ysd + relativedelta(months=diff_mnt) # month start date
# 		month_days = cint(calendar.monthrange(cint(msd.year) ,cint(month))[1]) # days in month
# 		mid_start = datetime.date(msd.year, cint(month), 16) # month mid start date
# 		mid_end = datetime.date(msd.year, cint(month), 15) # month mid end date
# 		med = datetime.date(msd.year, cint(month), month_days) # month end date
# 		return frappe._dict({
# 			'year': msd.year,
# 			'month_start_date': msd,
# 			'month_end_date': med,
# 			'month_mid_start_date': mid_start,
# 			'month_mid_end_date': mid_end,
# 			'month_days': month_days
# 		})
# 	else:
# 		frappe.throw(_("Fiscal Year {0} not found").format(year))


# @frappe.whitelist()
# def create_log(ss_list):
# 	if not ss_list:
# 		frappe.throw(
# 			_("There are no employees for the listed criteria currently missing salary slips."),
# 			title='Note'
# 		)
# 	return ss_list

# def create_submit_log(submitted_ss, unsubmitted_ss):
# 	if not submitted_ss and not unsubmitted_ss:
# 		frappe.msgprint(_("No salary slips found for the above criteria"))

# 	if unsubmitted_ss:
# 		frappe.msgprint(_("Could not submit a Salary Slip <br>\
# 			Possible reasons: <br>\
# 			1. Net pay is less than 0. <br>\
# 			2. Company Email Address specified in employee master is not valid. <br>"))

# def format_as_links(salary_slip):
# 	return ['<a href="#Form/Salary Slip/{0}">{0}</a>'.format(salary_slip)]


# def get_salary_slip_list(name, docstatus, as_dict=0):
# 	payroll_entry = frappe.get_doc('Payroll Entry', name)

# 	salary_slip_list = frappe.db.sql(
# 		"select t1.name, t1.salary_structure from `tabSalary Slip` t1 "
# 		"where t1.docstatus = %s "
# 		"and t1.start_date >= %s "
# 		"and t1.end_date <= %s",
# 		(docstatus, payroll_entry.start_date, payroll_entry.end_date),
# 		as_dict=as_dict
# 	)

# 	return salary_slip_list


# @frappe.whitelist()
# def payroll_entry_has_created_slips(name):
# 	response = {}

# 	draft_salary_slips = get_salary_slip_list(name, docstatus=0)
# 	submitted_salary_slips = get_salary_slip_list(name, docstatus=1)

# 	response['draft'] = 1 if draft_salary_slips else 0
# 	response['submitted'] = 1 if submitted_salary_slips else 0

# 	return response


# def get_payroll_entry_bank_entries(payroll_entry_name):
# 	journal_entries = frappe.db.sql(
# 		'select name from `tabJournal Entry Account` '
# 		'where reference_type="Payroll Entry" '
# 		'and reference_name=%s and docstatus=1',
# 		payroll_entry_name,
# 		as_dict=1
# 	)

# 	return journal_entries


# @frappe.whitelist()
# def payroll_entry_has_bank_entries(name):
# 	response = {}

# 	bank_entries = get_payroll_entry_bank_entries(name)
# 	response['submitted'] = 1 if bank_entries else 0

# 	return response



###### CURRENTLY UNUSED METHODS FROM THE CORE CLASS
	# def on_submit(self):
	# 	# identical to payroll_entry
 # 		self.submit_salary_slips()

 # 	def before_submit(self):
 # 		# identical to payroll entry
 # 		if self.validate_attendance:
	# 		if self.validate_employee_attendance():
	# 			frappe.throw(_("Cannot Submit, Employees left to mark attendance"))


	# def get_emp_list(self):
	# 	# identical to payroll_entry method
	# 	"""
	# 		Returns list of active employees based on selected criteria
	# 		and for which salary structure exists
	# 	"""
	# 	cond = self.get_filter_condition()
	# 	cond += self.get_joining_releiving_condition()

	# 	condition = ''
	# 	if self.payroll_frequency:
	# 		condition = """and payroll_frequency = '%(payroll_frequency)s'"""% {"payroll_frequency": self.payroll_frequency}

	# 	sal_struct = frappe.db.sql_list("""
	# 			select
	# 				name from `tabSalary Structure`
	# 			where
	# 				docstatus = 1 and
	# 				is_active = 'Yes'
	# 				and company = %(company)s and
	# 				ifnull(salary_slip_based_on_timesheet,0) = %(salary_slip_based_on_timesheet)s
	# 				{condition}""".format(condition=condition),
	# 			{"company": self.company, "salary_slip_based_on_timesheet":self.salary_slip_based_on_timesheet})
	# 	if sal_struct:
	# 		cond += "and t2.salary_structure IN %(sal_struct)s "
	# 		cond += "and %(from_date)s >= t2.from_date"
	# 		emp_list = frappe.db.sql("""
	# 			select
	# 				distinct t1.name as employee, t1.employee_name, t1.department, t1.designation
	# 			from
	# 				`tabEmployee` t1, `tabSalary Structure Assignment` t2
	# 			where
	# 				t1.name = t2.employee
	# 				and t2.docstatus = 1
	# 		%s order by t2.from_date desc
	# 		""" % cond, {"sal_struct": tuple(sal_struct), "from_date": self.end_date}, as_dict=True)
	# 		return emp_list

	# def fill_employee_details(self):
	# # this method now unused and replaced with a fill_salary_slips
	# 	self.set('employees', [])
	# 	employees = self.get_emp_list()
	# 	if not employees:
	# 		frappe.throw(_("No employees for the mentioned criteria"))

	# 	for d in employees:
	# 		self.append('employees', d)

	# 	self.number_of_employees = len(employees)
	# 	if self.validate_attendance:
	# 		return self.validate_employee_attendance()

  # 	def get_joining_releiving_condition(self):
  # 		# identical to payroll_entry method
		# cond = """
		# 	and ifnull(t1.date_of_joining, '0000-00-00') <= '%(end_date)s'
		# 	and ifnull(t1.relieving_date, '2199-12-31') >= '%(start_date)s'
		# """ % {"start_date": self.start_date, "end_date": self.end_date}
		# return cond

	# def get_filter_condition(self):
	# 	"""
	# 		Assemble sql clause matching filters specified in the document
	# 	"""
	# 	for fieldname in ['company', 'start_date', 'end_date']:
	# 		if not self.get(fieldname):
	# 			frappe.throw(_("Please set {0}").format(self.meta.get_label(fieldname)))

	# 	cond = ''
	# 	for f in ['company', 'branch', 'department', 'designation']:
	# 		if self.get(f):
	# 			cond += " and t1." + f + " = '" + self.get(f).replace("'", "\'") + "'"
	# 	return cond

	# def get_filter_condition(self):
	# 	# identical to payment_entry
	# 	self.check_mandatory()

	# 	cond = ''
	# 	for f in ['company', 'branch', 'department', 'designation']:
	# 		if self.get(f):
	# 			cond += " and t1." + f + " = '" + self.get(f).replace("'", "\'") + "'"

	# 	return cond

	# def submit_salary_slips(self):
	# 	"""
	# 		Submit all salary slips listed in the Payroll Salary Slip Details table. This is typically done when the
	# 		Payroll Voucher document is submitted.

	# 		TODO: this could probably be simplified to simply iterate through the Payroll Salary Slip Details table
	# 	"""

	# 	self.check_permission('write')

	# 	ss_list = self.get_sal_slip_list()
	# 	submitted_ss = []
	# 	unsubmitted_ss = []
	# 	for ss in ss_list:
	# 		ss_obj = frappe.get_doc("Salary Slip",ss[0])
	# 		ss_dict = {}
	# 		ss_dict["Employee Name"] = ss_obj.employee_name
	# 		ss_dict["Total Pay"] = fmt_money(ss_obj.net_pay,
	# 			currency = frappe.defaults.get_global_default("currency"))
	# 		ss_dict["Salary Slip"] = format_as_links(ss_obj.name)[0]

	# 		if ss_obj.net_pay<0:
	# 			unsubmitted_ss.append(ss_dict)
	# 		else:
	# 			try:
	# 				ss_obj.submit()
	# 				submitted_ss.append(ss_obj)
	# 			except frappe.ValidationError:
	# 				unsubmitted_ss.append(ss_dict)

	# 	if submitted_ss:
	# 		self.register_payroll_in_gl()
	# 		frappe.msgprint(_("Salary Slips submitted for period from {0} to {1}").format(ss_obj.start_date, ss_obj.end_date))
	# 		self.email_salary_slip(submitted_ss)

	# 	return create_submit_log(submitted_ss, unsubmitted_ss)

	# def email_salary_slip(self, submitted_ss):
	# 	"""
	# 		send emails to employees if indicated by HR Settings
	# 	"""
	# 	if frappe.db.get_single_value("HR Settings", "email_salary_slip_to_employee"):
	# 		for ss in submitted_ss:
	# 			ss.email_salary_slip()