"""Unit tests for database layer modify request"""

import sys, os.path
scriptdir, scriptfile = os.path.split(sys.argv[0])
sys.path.append(os.path.join(scriptdir, ".."))

import unittest
from test import helpers
from test.internal.requests import TestRequest
from brain.database import *
from brain.interface import *

class Modify(TestRequest):
	"""Test different uses of ModifyRequest"""

	def testBlankObjectAddition(self):
		"""Check that object without fields can be created"""
		self.addObject()

	def testAdditionNoCheck(self):
		"""Check simple object addition"""
		self.prepareStandNoList()

	def testAddition(self):
		"""Simple object addition with result checking"""
		self.prepareStandNoList()

		res = self.db.processRequest(SearchRequest(SearchRequest.Condition(
			Field('phone'), SearchRequest.EQ, '1111')))

		self.checkRequestResult(res, [self.id1, self.id5])

	def testAdditionSameFields(self):
		"""Check that several equal fields are handled correctly"""
		self.id1 = self.db.processRequest(ModifyRequest(None, [
			Field(['tracks'], value='Track 1'),
			Field(['tracks'], value='Track 2'),
			Field(['tracks'], value='Track 3')]
			))

		res = self.db.processRequest(ReadRequest(self.id1, [Field(['tracks'])]))

		# only last field value should be saved
		self.checkRequestResult(res, [
			Field(['tracks'], 'Track 3')
			])

	def testModificationNoCheck(self):
		"""Check object modification"""
		self.prepareStandNoList()

		self.db.processRequest(ModifyRequest(self.id1, [
			Field('name', 'Zack')
		]))

	def testModification(self):
		"""Object modification with results checking"""
		self.prepareStandNoList()

		self.db.processRequest(ModifyRequest(self.id1, [
			Field('name', 'Zack')
		]))

		res = self.db.processRequest(SearchRequest(SearchRequest.Condition(
			Field('name'), SearchRequest.EQ, 'Zack')))

		self.checkRequestResult(res, [self.id1])

	def testModificationAddsField(self):
		"""Check that object modification can add a new field"""
		self.prepareStandNoList()

		self.db.processRequest(ModifyRequest(self.id1, [
			Field('age', '66')
		]))

		res = self.db.processRequest(SearchRequest(SearchRequest.Condition(
			Field('age'), SearchRequest.EQ, '66')))

		self.checkRequestResult(res, [self.id1])

	def testModificationAddsFieldTwice(self):
		"""Regression test for non-updating specification"""
		self.prepareStandNoList()

		# Add new field to object
		self.db.processRequest(ModifyRequest(self.id1, [
			Field('age', '66')
		]))

		# Delete object. If specification was not updated,
		# new field is still in database
		self.db.processRequest(DeleteRequest(self.id1))

		# Add object again
		self.db.processRequest(ModifyRequest(self.id1, [
			Field(['name'], 'Alex'),
			Field(['phone'], '1111')
		]))

		# Check that field from old object is not there
		res = self.db.processRequest(SearchRequest(SearchRequest.Condition(
			Field('age'), SearchRequest.EQ, '66')))

		self.checkRequestResult(res, [])

	def testModificationPreservesFields(self):
		"""Check that modification preserves existing fields"""
		self.prepareStandNoList()

		self.db.processRequest(ModifyRequest(self.id2, [
			Field('name', 'Zack')
		]))

		res = self.db.processRequest(SearchRequest(SearchRequest.Condition(
			Field('phone'), SearchRequest.EQ, '2222')))

		self.checkRequestResult(res, [self.id2])

	def testListAdditions(self):
		"""Regression test for erroneous modify results for lists"""
		self.prepareStandSimpleList()

		res = self.db.processRequest(ModifyRequest(self.id1,
			[
				Field(['tracks', 3], 'Track 4'),
				Field(['tracks', 4], 'Track 5')
			]
		))

		res = self.db.processRequest(ReadRequest(self.id1, [Field(['tracks', None])]))
		self.checkRequestResult(res, [
			Field(['tracks', 0], 'Track 1'),
			Field(['tracks', 1], 'Track 2'),
			Field(['tracks', 2], 'Track 3'),
			Field(['tracks', 3], 'Track 4'),
			Field(['tracks', 4], 'Track 5'),
		])

	def testModificationAddsList(self):
		"""Check that modification request creates necessary hierarchy"""
		self.prepareStandNestedList()

		self.db.processRequest(ModifyRequest(self.id1, [
			Field(['tracks', 2, 'Lyrics', 0], 'Blablabla')
		]))

	def testListOnTopOfMap(self):
		"""Check that list cannot be created if map exists on the same level"""
		self.prepareStandNestedList()

		self.failUnlessRaises(DatabaseError, self.db.processRequest,
			ModifyRequest(self.id1, [Field(['tracks', 2, 0], 'Blablabla')])
		)

	def testMapOnTopOfList(self):
		"""Check that map cannot be created if list exists on the same level"""
		self.prepareStandNestedList()

		self.failUnlessRaises(DatabaseError, self.db.processRequest,
			ModifyRequest(self.id1, [Field(['tracks', 'some_name'], 'Blablabla')])
		)

	def testModificationAddsNewField(self):
		"""Check that modification can add totally new field to object"""
		self.prepareStandNoList()

		self.db.processRequest(ModifyRequest(self.id1, [
			Field('title', 'Mr')
		]))

		res = self.db.processRequest(SearchRequest(SearchRequest.Condition(
			Field('title'), SearchRequest.EQ, 'Mr')))

		self.checkRequestResult(res, [self.id1])

	def testAdditionDifferentTypes(self):
		"""Test that values of different types can be added"""
		values = ['text value', 123, 45.56, b'\x00\x01']
		reference_fields = []

		# create fields with values of different types
		for value in values:
			fld = Field('fld' + str(values.index(value)), value)
			reference_fields.append(fld)

		# check that all of them can be added and read
		self.id1 = self.db.processRequest(ModifyRequest(None, reference_fields))
		res = self.db.processRequest(ReadRequest(self.id1))
		self.checkRequestResult(res, reference_fields)

	def testModificationChangesFieldType(self):
		"""Test that you can change type of field value"""
		values = ['text value', 123, 45.56, b'\x00\x01']
		reference_fields = []

		# create fields with values of different types
		self.id1 = None
		for value in values:
			fld = Field('fld', value)
			if self.id1 is None:
				self.id1 = self.db.processRequest(ModifyRequest(None, [fld]))
			else:
				self.db.processRequest(ModifyRequest(self.id1, [fld]))
			res = self.db.processRequest(ReadRequest(self.id1))
			self.checkRequestResult(res, [fld])

	def testSeveralTypesInOneField(self):
		"""
		Check that different objects can store values
		of different types in the same field
		"""
		objects = [
			[Field('fld', 1)],
			[Field('fld', 'text')],
			[Field('fld', 1.234)],
			[Field('fld', b'\x00\x01')]
		]

		# create objects
		ids_and_objects = [(self.db.processRequest(ModifyRequest(None, fields)), fields)
			for fields in objects]

		# check that objects can be read
		for id, fields in ids_and_objects:
			res = self.db.processRequest(ReadRequest(id))
			self.checkRequestResult(res, fields)

	def testSeveralTypesInList(self):
		"""Check that list can store values of different types"""
		fields = [
			Field(['vals', 0], 'Zack'),
			Field(['vals', 1], 1),
			Field(['vals', 2], 1.234),
			Field(['vals', 3], b'Zack')
		]

		self.id1 = self.db.processRequest(ModifyRequest(None, fields))

		res = self.db.processRequest(ReadRequest(self.id1))

		self.checkRequestResult(res, fields)

	def testMapOnTopOfMapValue(self):
		"""Check that map can be written on top of existing value"""
		self.id1 = self.db.processRequest(ModifyRequest(None, [
			Field(['fld1'], value='val1'),
			Field(['fld1', 'fld2', 'fld3'], value=2),
			Field(['fld1', 'fld2', 'fld4'], value='a')
			]))

		res = self.db.processRequest(ReadRequest(self.id1))

		self.checkRequestResult(res, [
			Field(['fld1', 'fld2', 'fld3'], 2),
			Field(['fld1', 'fld2', 'fld4'], value='a')
			])

	def testMapOnTopOfListElement(self):
		"""Check that map can be written on top of existing list element"""
		self.id1 = self.db.processRequest(ModifyRequest(None, [
			Field(['fld1', 0], value='val1'),
			Field(['fld1', 1], value='val2'),
			Field(['fld1', 1, 'fld3'], value=2)
			]))

		res = self.db.processRequest(ReadRequest(self.id1))

		self.checkRequestResult(res, [
			Field(['fld1', 0], value='val1'),
			Field(['fld1', 1, 'fld3'], value=2)
			])

	def testListOnTopOfListElement(self):
		"""Check that list can be written on top of existing list element"""
		self.id1 = self.db.processRequest(ModifyRequest(None, [
			Field(['fld1', 0], value='val1'),
			Field(['fld1', 1], value='val2'),
			Field(['fld1', 1, 0], value=2)
			]))

		res = self.db.processRequest(ReadRequest(self.id1))

		self.checkRequestResult(res, [
			Field(['fld1', 0], value='val1'),
			Field(['fld1', 1, 0], value=2)
			])

	def testNoneValue(self):
		"""Check basic support of Null values"""
		self.id1 = self.db.processRequest(ModifyRequest(None, [
			Field(['fld1', 0], value=None),
			Field(['fld1', 1], value=1)
		]))

		res = self.db.processRequest(ReadRequest(self.id1))

		self.checkRequestResult(res, [
			Field(['fld1', 0], value=None),
			Field(['fld1', 1], value=1)
			])

	def testChangeListElementType(self):
		"""
		Regression test, showing that it is necessary to check all possible
		value types when modifying value in list
		"""
		self.id1 = self.db.processRequest(ModifyRequest(None, [
			Field(['fld1', 0], value=1),
			Field(['fld1', 1], value='a')
		]))

		self.db.processRequest(ModifyRequest(self.id1, [
			Field(['fld1', 1], value=2)
		]))

		res = self.db.processRequest(ReadRequest(self.id1))

		self.checkRequestResult(res, [
			Field(['fld1', 0], value=1),
			Field(['fld1', 1], value=2)
			])

	def testObjectCreation(self):
		"""Check that passing blank ID to ModifyRequest creates new element"""
		self.id1 = self.db.processRequest(ModifyRequest(None, [
			Field(['fld1', 0], value=1)
		]))

		res = self.db.processRequest(ReadRequest(self.id1))

		self.checkRequestResult(res, [
			Field(['fld1', 0], value=1)
			])


def get_class():
	return Modify