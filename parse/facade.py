import sys, os.path
scriptdir, scriptfile = os.path.split(sys.argv[0])
sys.path.append(os.path.join(scriptdir, ".."))

from db import interface, database, engine
import yaml
import functools

SIMPLE_TYPES = [
	int,
	str,
	float,
	bytes
]


def flattenHierarchy(data):
	def flattenNode(node, prefix=[]):
		if isinstance(node, dict):
			results = [flattenNode(node[x], list(prefix) + [x]) for x in node.keys()]
			return functools.reduce(list.__add__, results, [])
		elif isinstance(node, list):
			results = [flattenNode(x, list(prefix) + [i]) for i, x in enumerate(node)]
			return functools.reduce(list.__add__, results, [])
		elif node is None or node.__class__ in SIMPLE_TYPES:
			return [(prefix, node)]
		else:
			raise Exception("Unsupported type: " + node.__type__)

	return [(path, value) for path, value in flattenNode(data)]

def fieldsToTree(fields):

	res = []

	def saveTo(obj, ptr, path, value):

		if isinstance(obj, list) and len(obj) < ptr + 1:
			obj.extend([None] * (ptr + 1 - len(obj)))
		elif isinstance(obj, dict) and ptr not in obj:
			obj[ptr] = None

		if len(path) == 0:
			obj[ptr] = value
		else:
			if obj[ptr] is None:
				if isinstance(path[0], str):
					obj[ptr] = {}
				else:
					obj[ptr] = []

			saveTo(obj[ptr], path[0], path[1:], value)

	for field in fields:
		saveTo(res, 0, field.name, field.value)

	return res[0]

class Facade:

	def connect(self, path, open_existing=None):

		return Connection(database.SimpleDatabase(
			engine.Sqlite3Engine, path, open_existing))


def transacted(func):
	def handler(obj, *args, **kwds):
		create_transaction = not obj.transaction

		if create_transaction: obj.begin()
		func(obj, *args, **kwds)
		if create_transaction:
			return obj.commit()[0]

	return handler


class Connection:

	def __init__(self, db):
		self.db = db
		self.transaction = False
		self.requests = []

	def disconnect(self):
		self.db.disconnect()

	def begin(self):
		if not self.transaction:
			self.transaction = True
		else:
			raise Exception("Transaction is already in progress")

	def commit(self):
		try:
			res = self.db.processRequests(self.requests)
			return self.transformResults(self.requests, res)
		finally:
			self.transaction = False
			self.requests = []

	def rollback(self):
		if self.transaction:
			self.transaction = False
			self.requests = []
		else:
			raise Exception("Transaction is not in progress")

	def transformResults(self, requests, results):
		res = []
		for result, request in zip(results, requests):
			if isinstance(request, interface.ReadRequest):
				res.append(fieldsToTree(result))
			elif isinstance(request, interface.ModifyRequest):
				res.append(None)
			elif isinstance(request, interface.InsertRequest):
				res.append(None)
			elif isinstance(request, interface.DeleteRequest):
				res.append(None)

		return res

	@transacted
	def modify(self, id, value, path=None):
		if path is None: path = []
		parsed = flattenHierarchy(value)
		fields = [interface.Field(path + relative_path, val)
			for relative_path, val in parsed]
		self.requests.append(interface.ModifyRequest(id, fields))

	@transacted
	def read(self, id, path=None):
		if path is not None:
			path = interface.Field(path)
		self.requests.append(interface.ReadRequest(id, path))

	@transacted
	def insert(self, id, path, value=None):
		parsed = flattenHierarchy(value)
		fields = [interface.Field(relative_path, val) for relative_path, val in parsed]
		self.requests.append(interface.InsertRequest(
			id, interface.Field(path), fields))

	@transacted
	def delete(self, id, path):
		self.requests.append(interface.DeleteRequest(id, [interface.Field(path)]))


class YamlFacade:

	def __init__(self, facade):
		self.facade = facade
		self.sessions = {}
		self.session_counter = 0

	def parseRequest(self, request):
		request = yaml.load(request)

		handlers = {
			'connect': self.processConnectRequest,
			'disconnect': self.processDisonnectRequest
		}

		if not 'type' in request:
			raise Exception("Request type is missing")

		if not request['type'] in handlers:
			raise Exception("Unknown request type: " + str(request['type']))

		return handlers[request['type']](request)

	def processConnectRequest(self, request):
		if not 'path' in request:
			raise Exception('Database path is missing')
		path = request['path']

		open_existing = request['connect'] if 'connect' in request else None

		self.session_counter += 1
		self.sessions[self.session_counter] = self.facade.connect(path, open_existing)

		return self.session_counter

	def processDisonnectRequest(self, request):
		if not 'session' in request:
			raise Exception('Session ID is missing')
		session = request['session']

		self.sessions[session].disconnect()
		del self.sessions[session]


if __name__ == '__main__':
	f = Facade()
	c = f.connect('c:\\gitrepos\\brain\\parse\\test.dat')

	c.modify('1', 'RRR', ['name'])
	#c.insert('1', ['names', None], 66)
	c.delete('1', ['name'])
	print(c.read('1'))

	c.disconnect()
