"""Unit tests for database layer requests"""

import sys, os.path
scriptdir, scriptfile = os.path.split(sys.argv[0])
sys.path.append(os.path.join(scriptdir, ".."))

import unittest
from test import helpers
from test.db import delete, insert, modify, read, search, requests
from db.database import *

def suite():
	"""Generate test suite for this module"""
	res = helpers.NamedTestSuite()
	
	parameters = [
		('memory.sqlite3', Sqlite3Database, ':memory:'),
	]

	modules = [delete, insert, modify, read, search]

	for parameter in parameters:
		for module in modules:
			res.addTest(unittest.TestLoader().loadTestsFromTestCase(
				requests.getParameterized(module.get_class(), *parameter)))
	
	return res
