# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from . import __version__ as app_version

app_name = "oi_custom"
app_title = "OI Custom"
app_publisher = "the Open Institute for Social Science"
app_description = "Customizations by/for the Open Institute"
app_icon = "octicon octicon-squirrel"
app_color = "grey"
app_email = "admin@theopen.institute"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/oi_custom/css/oi_custom.css"
# app_include_js = "/assets/oi_custom/js/oi_custom.js"

app_include_css = [
    "/assets/oi_custom/css/custom.css"
]

# include js, css files in header of web template
# web_include_css = "/assets/oi_custom/css/oi_custom.css"
# web_include_js = "/assets/oi_custom/js/oi_custom.js"

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
#	"Role": "home_page"
# }

# Website user home page (by function)
# get_website_user_home_page = "oi_custom.utils.get_home_page"

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Installation
# ------------

# before_install = "oi_custom.install.before_install"
# after_install = "oi_custom.install.after_install"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "oi_custom.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
#	}
# }

doc_events = {
	"Payment Entry": {
		"before_validate":"oi_custom.customizations.overrides.custom_payment_entry.customize_before_validate",
	}
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"oi_custom.tasks.all"
# 	],
# 	"daily": [
# 		"oi_custom.tasks.daily"
# 	],
# 	"hourly": [
# 		"oi_custom.tasks.hourly"
# 	],
# 	"weekly": [
# 		"oi_custom.tasks.weekly"
# 	]
# 	"monthly": [
# 		"oi_custom.tasks.monthly"
# 	]
# }

# Testing
# -------

# before_tests = "oi_custom.install.before_tests"

# Overriding Whitelisted Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "oi_custom.event.get_events"
# }

