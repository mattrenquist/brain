"""Module with classes, describing DDB wrapper over SQL"""

import sqlite3
import re
import copy
import functools

from . import interface
from .interface import Field
from . import op

class _StructureLayer:
	"""Class which is connected to DB engine and incapsulates all SQL queries"""

	_ID_COLUMN = 'id' # name of column with object id in all tables

	# column names for specification table
	_FIELD_COLUMN = 'field' # field names
	_TYPE_COLUMN = 'type' # field types
	_REFCOUNT_COLUMN = 'refcount' # number of records with this type
	_VALUE_COLUMN = 'value' # name of column with field values


	def __init__(self, engine):
		self._engine = engine

		# memorize strings with support table names
		self._ID_TABLE = self._engine.getNameString(["id"])

		# types for support tables
		self._ID_TYPE = self._engine.getIdType()
		self._TEXT_TYPE = self._engine.getColumnType(str())
		self._INT_TYPE = self._engine.getColumnType(int())

		# create support tables
		self._engine.begin()
		self._createSupportTables()
		self._engine.commit()


	def _createSupportTables(self):
		"""Create database support tables (sort of caching)"""

		# create specification table, which holds field names, their types
		# and number of records of each type for all database objects
		id_table_spec = ("({id_column} {id_type}, {field_column} {text_type}, " +
			"{type_column} {text_type}, " +
			"{refcount_column} {refcount_type})").format(
			field_column=self._FIELD_COLUMN,
			id_column=self._ID_COLUMN,
			id_type=self._ID_TYPE,
			refcount_column=self._REFCOUNT_COLUMN,
			refcount_type=self._INT_TYPE,
			text_type=self._TEXT_TYPE,
			type_column=self._TYPE_COLUMN)

		if not self._engine.tableExists(self._ID_TABLE):
			self._engine.execute("CREATE table {} " + id_table_spec, [self._ID_TABLE])

	def _deleteSupportTables(self):
		self._engine.deleteTable(self._ID_TABLE)

	def repairSupportTables(self):
		"""Recreate support tables according to other information in DB"""

		# if the repair request was created, we cannot rely on existing tables
		self._deleteSupportTables()
		self._createSupportTables()

		# search for all field tables in database
		tables = self._engine.getTablesList()

		# we need to filter out non-field tables
		tables.remove(self._ID_TABLE)
		tables = [x for x in tables if Field.isFieldTableName(self._engine, x)]

		# collect info from field tables and create refcounters table in memory
		refcounters = {}
		for table in tables:
			res = self._engine.execute("SELECT id FROM {}", [table])
			ids = [x[0] for x in res]
			field = Field.fromNameStr(self._engine, table)
			name_str_no_type = field.name_str_no_type
			type_str = field.type_str

			for obj_id in ids:
				if obj_id not in refcounters:
					refcounters[obj_id] = {}

				if name_str_no_type not in refcounters[obj_id]:
					refcounters[obj_id][name_str_no_type] = {}

				if type_str not in refcounters[obj_id][name_str_no_type]:
					refcounters[obj_id][name_str_no_type][type_str] = 1
				else:
					refcounters[obj_id][name_str_no_type][type_str] += 1

		# flatten refcounters hierarchy
		values = []
		for obj_id in refcounters:
			for name_str_no_type in refcounters[obj_id]:
				for type_str in refcounters[obj_id][name_str_no_type]:
					values.append([obj_id, name_str_no_type, type_str,
						refcounters[obj_id][name_str_no_type][type_str]])

		# fill refcounters table
		for values_list in values:
			self._engine.execute("INSERT INTO {} VALUES (?, ?, ?, ?)",
				[self._ID_TABLE], values_list)

	def deleteSpecification(self, id):
		"""Delete all information about object from specification table"""
		self._engine.execute("DELETE FROM {} WHERE " + self._ID_COLUMN + "=?",
			[self._ID_TABLE], [id])

	def increaseRefcount(self, id, field):
		"""
		Increase reference counter of given field and type (or create it)

		field should have definite type
		"""
		new_type = field.type_str not in self.getValueTypes(id, field)

		if new_type:
		# if adding a value of new type to existing field,
		# add a reference counter for this field and this type
			self._engine.execute("INSERT INTO {} VALUES (?, ?, ?, 1)",
				[self._ID_TABLE], [id, field.name_str_no_type,
				field.type_str])
		else:
		# otherwise increase the existing reference counter
			ref_col = self._REFCOUNT_COLUMN
			self._engine.execute("UPDATE {} " +
				"SET " + ref_col + "="+ ref_col + "+1 " +
				"WHERE " + self._ID_COLUMN + "=? AND " + self._FIELD_COLUMN + "=? " +
				"AND " + self._TYPE_COLUMN + "=?",
				[self._ID_TABLE], [id, field.name_str_no_type, field.type_str])

	def decreaseRefcount(self, id, field, num=1):
		"""
		Decrease reference count for given field and type
		one can specify a decrement if deleting values by mask

		field should have definite type
		"""
		type_cond = '=?'
		type_cond_val = [field.type_str]

		# get current value of reference counter
		l = self._engine.execute("SELECT " + self._REFCOUNT_COLUMN + " FROM {} " +
			"WHERE " + self._ID_COLUMN + "=? AND " + self._FIELD_COLUMN + "=? " +
			"AND " + self._TYPE_COLUMN + type_cond,
			[self._ID_TABLE], [id, field.name_str_no_type] + type_cond_val)

		if l[0][0] == num:
		# if these references are the last ones, delete this counter
			self._engine.execute("DELETE FROM {} " +
				"WHERE " + self._ID_COLUMN + "=? AND " + self._FIELD_COLUMN + "=? " +
				"AND " + self._TYPE_COLUMN + type_cond,
				[self._ID_TABLE], [id, field.name_str_no_type] + type_cond_val)
		else:
		# otherwise just decrease the counter by given value
			ref_col = self._REFCOUNT_COLUMN
			self._engine.execute("UPDATE {} SET " + ref_col + "=" + ref_col + "-? " +
				"WHERE " + self._ID_COLUMN + "=? AND " + self._FIELD_COLUMN + "=? " +
				"AND " + self._TYPE_COLUMN + type_cond,
				[self._ID_TABLE], [num, id, field.name_str_no_type] + type_cond_val)

	def getValueTypes(self, id, field):
		"""Returns list of value types already stored in given field"""

		# just query specification table for all types for given object and field
		l = self._engine.execute("SELECT " + self._TYPE_COLUMN + " FROM {} " +
			"WHERE " + self._ID_COLUMN + "=? AND " + self._FIELD_COLUMN + "=?",
			[self._ID_TABLE], [id, field.name_str_no_type])

		return [x[0] for x in l]

	def getFieldsList(self, id, field=None, exclude_self=False):
		"""
		Get list of fields of all possible types for given object.
		If field is given, return only those whose names start from its name
		If exclude_self is true, exclude 'field' itself from results
		"""

		if field is not None:
		# If field is given, return only fields, which contain its name in the beginning
			regexp_cond = " AND " + self._FIELD_COLUMN + \
				" " + self._engine.getRegexpOp() + " ?"
			regexp_val = ["^" + re.escape(field.name_str_no_type) + "\.\."]
		else:
			regexp_cond = ""
			regexp_val = []

		# Get list of fields
		l = self._engine.execute("SELECT DISTINCT " + self._FIELD_COLUMN + " FROM {} " +
			"WHERE " + self._ID_COLUMN + "=?" + regexp_cond,
			[self._ID_TABLE], [id] + regexp_val)

		# fill the beginnings of found field names with the name of
		# given field (if any) or just construct result list
		res = []
		for elem in l:
			fld = Field.fromNameStrNoType(self._engine, elem[0])
			if field is not None:
				fld.name[:len(field.name)] = field.name
			res.append(fld)

		if field is not None and not exclude_self:
			res += [Field(self._engine, field.name)]

		return res

	def getRawFieldsInfo(self, id, masks=None, include_refcounts=False):

		regexp_vals = []
		if masks is not None:
		# If fields list is given, return only fields, which contain its name in the beginning
			regexp_cond_list = []
			for mask in masks:
				regexp_cond_list.append(self._FIELD_COLUMN +
					" " + self._engine.getRegexpOp() + " ?")
				regexp_vals.append("^" + re.escape(mask.name_str_no_type) + "(\.\.|$)")
			regexp_cond = " AND (" + " OR ".join(regexp_cond_list) + ")"
		else:
			regexp_cond = ""

		# Get information
		l = self._engine.execute("SELECT " + self._FIELD_COLUMN + ", " + self._TYPE_COLUMN +
			((", " + self._REFCOUNT_COLUMN) if include_refcounts else "") +
			" FROM {} WHERE " + self._ID_COLUMN + "=?" + regexp_cond,
			[self._ID_TABLE], [id] + regexp_vals)

		# fill 'raw' information map - name strings to existing types correspondence
		raw_fields_info = {}
		for elem in l:
			name_str = elem[0]
			type_str = elem[1]
			if name_str not in raw_fields_info:
				raw_fields_info[name_str] = []

			if include_refcounts:
				to_append = (type_str, elem[2])
			else:
				to_append = type_str

			raw_fields_info[name_str].append(to_append)

		return raw_fields_info

	def getFieldsInfo(self, id, masks=None):
		"""
		Get types of given fields from support table
		if fields is equal to None, info for all object's fields are returned
		"""

		raw_fields_info = self.getRawFieldsInfo(id, masks)
		res = []

		# construct resulting list of partially defined fields
		for name_str in raw_fields_info:
			if masks is None:
			# just construct all possible fields from name strings
				for type_str in raw_fields_info[name_str]:
					f = Field.fromNameStrNoType(self._engine, name_str)
					f.type_str = type_str
					res.append(f)
			else:
			# since we do not know, which name string was found using which mask,
			# we should skip unmatched combinations of masks and name strings
				temp = Field.fromNameStrNoType(self._engine, name_str)
				for mask in masks:
					if temp.matches(mask):
						for type_str in raw_fields_info[name_str]:
							f = Field.fromNameStrNoType(self._engine, name_str)
							f.name[:len(mask.name)] = mask.name
							f.type_str = type_str
							res.append(f)

		return res

	def objectExists(self, id):
		"""Check if object exists in database"""

		# We need just check if there is at least one row with its id
		# in specification table
		l = self._engine.execute("SELECT COUNT(*) FROM {} WHERE " + self._ID_COLUMN + "=?",
			[self._ID_TABLE], [id])

		return l[0][0] > 0


	def getFieldValue(self, id, field):
		"""
		Read value of given field(s)

		field should have definite type
		"""

		# if there is no such field - nothing to do
		if not self._engine.tableExists(field.name_str):
			return []

		# Get field values
		# If field is a mask (i.e., contains Nones), there will be more than one result
		columns_query = field.columns_query

		val_col = self._VALUE_COLUMN
		l = self._engine.execute("SELECT " + val_col + columns_query + " FROM {} " +
			"WHERE " + self._ID_COLUMN + "=?" + field.columns_condition,
			[field.name_str], [id])

		# Convert results to list of Fields
		res = []
		l = [tuple(x) for x in l]
		for elem in l:
			list_indexes = elem[1:]
			value = elem[0]

			new_field = Field(self._engine, field.getDeterminedName(list_indexes))
			new_field.type_str = field.type_str
			new_field.db_value = value
			res.append(new_field)

		return res

	def assureFieldTableExists(self, field):
		"""
		Create table for storing values of this field if it does not exist yet

		field should have definite type
		"""

		if not self._engine.tableExists(field.name_str):
			table_spec = field.getCreationStr(self._ID_COLUMN,
				self._VALUE_COLUMN, self._ID_TYPE, self._INT_TYPE)
			self._engine.execute("CREATE TABLE {} (" + table_spec + ")",
				[field.name_str])

	def buildSqlQuery(self, condition):
		"""Recursive function to transform condition into SQL query"""

		if condition is None:
			return "SELECT DISTINCT " + self._ID_COLUMN + " FROM {}", [self._ID_TABLE], []

		if not condition.leaf:
			# child conditions
			cond1, tables1, values1 = self.buildSqlQuery(condition.operand1)
			cond2, tables2, values2 = self.buildSqlQuery(condition.operand2)

			# mapping to SQL operations
			operations = {
				op.AND: 'INTERSECT',
				op.OR: 'UNION'
			}

			if cond1 is None and cond2 is None:
				return None, None, None
			elif cond1 is None:
				if condition.operator == op.AND:
					return None, None, None
				elif condition.operator == op.OR:
					return cond2, tables2, values2
			elif cond2 is None:
				if condition.operator == op.AND:
					return None, None, None
				elif condition.operator == op.OR:
					return cond1, tables1, values1

			return "SELECT * FROM (" + cond1 + ") as temp " + \
				operations[condition.operator] + " SELECT * FROM (" + \
				cond2 + ") as temp", tables1 + tables2, values1 + values2

		# Leaf condition
		op1 = condition.operand1 # it must be Field without value
		op2 = condition.operand2 # it must be Field with value

		# If table with given field does not exist, just return empty query
		if op1 is None:
			if condition.invert:
				return "SELECT DISTINCT " + self._ID_COLUMN + " FROM {}", \
					[self._ID_TABLE], []
			else:
				return None, None, None

		not_str = " NOT " if condition.invert else " "

		# mapping to SQL comparisons
		comparisons = {
			op.EQ: '=',
			op.REGEXP: self._engine.getRegexpOp(),
			op.LT: '<',
			op.GT: '>',
			op.LTE: '<=',
			op.GTE: '>='
		}

		# build query
		tables = []
		values = []

		# construct comparing condition
		comp_str = "WHERE " + not_str + " " + self._VALUE_COLUMN + " " + \
			comparisons[condition.operator] + " ?"
		values = [op2.db_value]

		# construct query
		result = "SELECT DISTINCT " + self._ID_COLUMN + " FROM {} " + \
			comp_str + " " + op1.columns_condition
		tables = [condition.operand1.name_str]

		if condition.invert:
		# we will add objects that do not even have such field
			result += " UNION "

		# if we need to invert results, we have to add all objects that do
		# not have this field explicitly, because they won't be caught by previous query
		if condition.invert:
			result += "SELECT " + self._ID_COLUMN + " FROM " + \
				"(SELECT DISTINCT " + self._ID_COLUMN + " FROM {} " + \
				"EXCEPT SELECT DISTINCT " + self._ID_COLUMN + " FROM {}) as temp"
			tables += [self._ID_TABLE, condition.operand1.name_str]

		return result, tables, values

	def renumberList(self, id, target_field, field, shift):
		"""
		Shift indexes in given list
		target_field - points to list which is being processed
		field - child field for one of the elements of this list
		"""

		# Get the name and the value of last numerical column
		col_name, col_val = target_field.getLastListColumn()
		cond = target_field.renumber_condition

		# renumber list indexes for all types
		types = self.getValueTypes(id, field)
		for type in types:
			field.type_str = type
			self._engine.execute("UPDATE {} " +
				"SET " + col_name + "=" + col_name + "+? " +
				"WHERE " + self._ID_COLUMN + "=?" + cond +
				" AND " + col_name + ">=?",
				[field.name_str], [shift, id, col_val])

	def deleteValues(self, id, field, condition=None):
		"""Delete value of given field(s)"""

		field_copy = Field(self._engine, field.name)

		# if there is no special condition, take the mask of given field
		if condition is None:
			condition = field.columns_condition

		types = self.getValueTypes(id, field_copy)
		for type in types:

			# prepare query for deletion
			field_copy.type_str = type
			query_str = "FROM {} WHERE " + self._ID_COLUMN + "=?" + condition
			tables = [field_copy.name_str]
			values = [id]

			# get number of records to be deleted
			res = self._engine.execute("SELECT COUNT(*) " + query_str, tables,values)
			del_num = res[0][0]

			# if there are any, delete them
			if del_num > 0:
				self.decreaseRefcount(id, field_copy, num=del_num)
				self._engine.execute("DELETE " + query_str, tables, values)

				# check if the table is empty and if it is - delete it too
				if self._engine.tableIsEmpty(field_copy.name_str):
					self._engine.deleteTable(field_copy.name_str)

	def addValueRecord(self, id, field):
		"""
		Add value to the corresponding table

		field should have definite type
		"""

		new_value = ", ?"
		self._engine.execute("INSERT INTO {} " +
			"VALUES (?" + new_value + field.columns_values + ")",
			[field.name_str], [id, field.db_value])

	def getMaxListIndex(self, id, field):
		"""Get maximum index in list, specified by given field"""

		col_name, col_val = field.getLastListColumn()
		cond = field.renumber_condition

		max = -1
		for fld in self.getFieldsInfo(id, [field]):
			res = self._engine.execute("SELECT MAX(" + col_name + ") FROM {} WHERE " +
				self._ID_COLUMN + "=?" + cond, [fld.name_str], [id])

			if res[0][0] is not None and res[0][0] > max:
				max = res[0][0]

		return max if max != -1 else None

	def objectHasField(self, id, field):
		"""Returns True if object has given field (with any type of value)"""

		# if field points to root, object definitely has it
		if len(field.name) == 0:
			return True

		types = self.getValueTypes(id, field)
		if len(types) == 0:
			return False

		field_copy = Field(self._engine, field.name)
		queries = []
		tables = []
		values = []
		columns_condition = field_copy.columns_condition
		for type in types:
			field_copy.type_str = type
			queries.append("SELECT " + self._ID_COLUMN +
				" FROM {} WHERE " + self._ID_COLUMN + "=?" +
				columns_condition)
			tables.append(field_copy.name_str)
			values.append(id)
		query = "SELECT COUNT(*) FROM (" + " UNION ".join(queries) + ")"

		res = self._engine.execute(query, tables, values)

		return res[0][0] > 0


class LogicLayer:
	"""Class, representing DDB logic"""

	def __init__(self, engine):
		self._engine = engine
		self._structure = _StructureLayer(engine)

	def _deleteField(self, id, field):
		"""Delete given field(s)"""

		if len(field.name) > 0 and field.pointsToListElement():
			# deletion of list element requires renumbering of other elements
			self._renumber(id, field, -1)
		else:
			# otherwise just delete values using given field mask
			for fld in self._structure.getFieldsList(id, field, exclude_self=False):
				self._structure.deleteValues(id, fld)

	def _checkForConflicts(self, id, field, remove_conflicts):
		"""
		Check that adding this field does not break the database structure, namely:
		given field can either contain value, or list, or map, not several at once
		"""
		name_copy = list(reversed(field.name))
		tmp_field = Field(self._engine, [])
		while len(name_copy) > 0:
			next = name_copy.pop()
			types = self._structure.getValueTypes(id, tmp_field)
			values = []
			for type in types:
				tmp_field.type_str = type
				values += self._structure.getFieldValue(id, tmp_field)
			if len(values) == 0:
				return
			values = [value.py_value for value in values]

			next_is_str = isinstance(next, str)
			if not (next_is_str and dict() in values) and not (not next_is_str and list() in values):
				if remove_conflicts:

					# remove all conflicting values
					for fld in self._structure.getFieldsList(id, tmp_field, exclude_self=False):
						self._structure.deleteValues(id, fld)

					# create necessary hierarchy
					name_copy.append(next)
					while len(name_copy) > 0:
						tmp_field.py_value = (dict() if isinstance(name_copy[-1], str) else list())
						if len(tmp_field.name) > 0 and isinstance(tmp_field.name[-1], int):
							self._fillWithNones(id, tmp_field)
						self._setFieldValue(id, tmp_field)
						tmp_field.name.append(name_copy.pop())

					return
				else:
					raise interface.StructureError("Path " + repr(tmp_field.name + [next]) +
						" conflicts with existing structure")
			tmp_field.name.append(next)

	def _setFieldValue(self, id, field):
		"""Set value of given field"""

		# Create field table if it does not exist yet
		self._structure.assureFieldTableExists(field)
		self._structure.increaseRefcount(id, field)
		self._structure.addValueRecord(id, field) # Insert new value

	def _deleteObject(self, id):
		"""Delete object with given ID"""

		fields = self._structure.getFieldsList(id)

		# for each field, remove it from tables
		for field in fields:
			self._deleteField(id, field)

		self._structure.deleteSpecification(id)

	def _renumber(self, id, target_field, shift):
		"""Renumber list elements before insertion or deletion"""

		# Get all child field names
		fields_to_reenum = self._structure.getFieldsList(id, target_field)
		for fld in fields_to_reenum:

			# if shift is negative, we should delete elements first
			if shift < 0:
				self._structure.deleteValues(id, fld, target_field.columns_condition)

			# shift numbers of all elements in list
			self._structure.renumberList(id, target_field, fld, shift)

	def _fillWithNones(self, id, path):
		max = self._structure.getMaxListIndex(id, path)
		if max is None:
			start = 0
		else:
			start = max + 1
		end = path.name[-1]

		path_copy = Field(self._engine, path.name)
		for i in range(start, end):
			path_copy.name[-1] = i
			path_copy.py_value = None
			self._setFieldValue(id, path_copy)

	def _modifyFields(self, id, path, fields, remove_conflicts):
		"""Store values of given fields"""

		if self._structure.objectHasField(id, path):
		# path already exists, delete it and all its children
			for field in self._structure.getFieldsList(id, path, exclude_self=False):
				self._structure.deleteValues(id, field, path.columns_condition)
		else:
		# path does not exist and is not root

			# check for list/map conflicts
			self._checkForConflicts(id, path, remove_conflicts)

			# fill autocreated list elements (if any) with Nones
			field_copy = Field(self._engine, path.name)
			while len(field_copy.name) > 0:
				if isinstance(field_copy.name[-1], int):
					self._fillWithNones(id, field_copy)
				field_copy.name.pop()

		# store field values
		for field in fields:
			field.name = path.name + field.name
			self._setFieldValue(id, field)

	def processCreateRequest(self, request):
		new_id = self._engine.getNewId()
		self._modifyFields(new_id, interface.Field(self._engine, []), request.fields, True)
		return new_id

	def processModifyRequest(self, request):
		self._modifyFields(request.id, request.path,
			request.fields, request.remove_conflicts)

	def processDeleteRequest(self, request):

		if request.fields is not None:
			# remove specified fields
			for field in request.fields:
				self._deleteField(request.id, field)
			return
		else:
			# delete whole object
			self._deleteObject(request.id)

	def processReadRequest(self, request):

		path = None if (request.path is None or len(request.path.name) == 0) else request.path
		masks = None if (request.masks is None or len(request.masks) == 0) else request.masks

		# construct list of masks for reading
		if masks is None:
			if path is None:
				fields = None
			else:
				fields = [request.path]
		else:
			fields = []
			for mask in masks:
				if path is None or mask.matches(path):
					fields.append(mask)

		# get list of typed fields to read (whose fields are guaranteed to exist)
		fields_list = self._structure.getFieldsInfo(request.id, fields)

		# read values
		result_list = []
		for field in fields_list:
			result_list += self._structure.getFieldValue(request.id, field)

		# if no fields were read - throw error (so that user could distinguish
		# this case from the case when None was read, for example)
		if len(result_list) == 0:

			if masks is None:
				if path is None:
					path_str = "exist"
				else:
					path_str = "have field " + str(request.path.name)

				raise interface.LogicError("Object " + str(request.id) +
					" does not " + path_str)
			else:
				raise interface.LogicError("Object " + str(request.id) +
					" does not have fields matching given masks")

		# remove root path from values
		if request.path is not None:
			for field in result_list:
				del field.name[:len(request.path.name)]

		return result_list

	def processSearchRequest(self, request):
		"""Search for all objects using given search condition"""

		def getMentionedFields(condition):
			if isinstance(condition.operand1, interface.SearchRequest.Condition):
				fields = getMentionedFields(condition.operand1)
				return fields.union(getMentionedFields(condition.operand2))
			else:
				return {condition.operand1.name_str}

		def updateCondition(condition, existing_tables):
			if isinstance(condition.operand1, interface.SearchRequest.Condition):
				updateCondition(condition.operand1, existing_tables)
				updateCondition(condition.operand2, existing_tables)
			else:
				if condition.operand1.name_str not in existing_tables:
					condition.operand1 = None

		if request.condition is not None:
			table_names = getMentionedFields(request.condition)
			table_names = self._engine.selectExistingTables(table_names)
			updateCondition(request.condition, table_names)

		request, tables, values = self._structure.buildSqlQuery(request.condition)
		if request is None:
			return []

		result = self._engine.execute(request, tables, values)
		list_res = [x[0] for x in result]

		return list_res

	def processInsertRequest(self, request):

		def enumerate(field_groups, col_num, starting_num):
			"""Enumerate given column in list of fields"""
			counter = starting_num
			for field_group in field_groups:
				for field in field_group:
					field.name[col_num] = counter
				counter += 1

		# check that dictionary does not already exists at the place
		# where request.path is pointing to
		parent_field = Field(self._engine, request.path.name[:-1])
		parent = self._structure.getFieldValue(request.id, parent_field)

		if len(parent) == 0 or parent[0].py_value != list():
			if len(parent) == 0 or request.remove_conflicts:
			# try to autocreate list
				new_val = Field(self._engine, [], list())
				self._modifyFields(request.id, parent_field,
					[new_val], remove_conflicts=request.remove_conflicts)
			else:
			# in this case we can raise more meaningful error
				raise interface.StructureError("Cannot insert to non-list")

		# if path does not point to beginning of the list, fill
		# missing elements with Nones
		if request.path.name[-1] is not None and request.path.name[-1] > 0:
			self._fillWithNones(request.id, request.path)

		target_col = len(request.path.name) - 1 # last column in name of target field

		max = self._structure.getMaxListIndex(request.id, request.path)
		if max is None:
		# list does not exist yet
			enumerate(request.field_groups, target_col, 0)
		elif request.path.name[target_col] is None:
		# list exists and we are inserting elements to the end
			starting_num = max + 1
			enumerate(request.field_groups, target_col, starting_num)
		else:
		# list exists and we are inserting elements to the beginning or to the middle
			self._renumber(request.id, request.path, len(request.field_groups))
			enumerate(request.field_groups, target_col, request.path.name[target_col])

		fields = functools.reduce(list.__add__, request.field_groups, [])
		for field in fields:
			self._setFieldValue(request.id, field)

	def processObjectExistsRequest(self, request):
		return self._structure.objectExists(request.id)

	def processDumpRequest(self, request):
		ids = self.processSearchRequest(interface.SearchRequest())

		result = []
		for obj_id in ids:
			result += [obj_id, self.processReadRequest(interface.ReadRequest(obj_id))]

		return result

	def processRepairRequest(self, request):
		self._structure.repairSupportTables()
