
from cheshire3.exceptions import *
from cheshire3.configParser import C3Object
from cheshire3.baseStore import SimpleStore
from cheshire3.recordStore import SimpleRecordStore
from cheshire3.documentStore import SimpleDocumentStore
from cheshire3.resultSet import SimpleResultSetItem
from cheshire3.documentFactory import BaseDocumentStream, SimpleDocumentFactory
from cheshire3.resultSetStore import SimpleResultSetStore
from cheshire3.resultSet import SimpleResultSet
from cheshire3.baseObjects import IndexStore
from cheshire3.objectStore import SimpleObjectStore
from cheshire3 import dynamic
from cheshire3.utils import elementType, getFirstData, nonTextToken, flattenTexts
from cheshire3.index import SimpleIndex
import cheshire3.cqlParser as cql


# Consider psycopg
import pg
import time

from lxml import etree

# Iterator for postgres stores

class PostgresIter(object):
    store = None
    cxn = None
    idList = None
    cursor = None

    def __init__(self, session, store):
        self.session = session
        self.store = store
        self.cxn = pg.connect(self.store.database)
        query = "SELECT identifier FROM %s ORDER BY identifier ASC" % (self.store.table)
        query = query.encode('utf-8')
        res = self.cxn.query(query)
        all = res.dictresult()
        self.idList = [item['identifier'] for item in all]
        self.cursor = 0

    def __iter__(self):
        return self

    def next(self):
        try:
            query = "SELECT * FROM %s WHERE identifier='%s' LIMIT 1" % (self.store.table, self.idList[self.cursor])
            query = query.encode('utf-8')
            res = self.cxn.query(query)
            self.cursor +=1
            d = res.getresult()[0]
            while d and (d[0][:2] == "__"):
                query = "SELECT * FROM %s WHERE identifier='%s' LIMIT 1" % (self.store.table, self.idList[self.cursor])
                query = query.encode('utf-8')
                res = self.cxn.query(query)
                self.cursor +=1
                d = res.getresult()[0]
            
            if not d:
                raise StopIteration()
            return d
        except IndexError:
            raise StopIteration()


class PostgresRecordIter(PostgresIter):
    # Get data from bdbIter and turn into record

    def next(self):
        d = PostgresIter.next(self)
        data = d[1]
        data = data.replace('\\000\\001', nonTextToken)
        data = data.replace('\\012', '\n')
        rec = self.store._process_data(self.session, d[0], data)
        return rec
    
class PostgresDocumentIter(PostgresIter):
    # Get data from bdbIter and turn into document

    def next(self):
        d = PostgresIter.next(self)
        data = d[1]
        doc = self.store._process_data(self.session, d[0], data)
        return doc
    
    
class PostgresObjectIter(PostgresRecordIter):
    # Get data from bdbIter and turn into record, then process reocrd into object

    def next(self):
        rec = PostgresRecordIter.next(self)
        obj = self.store._processRecord(None, rec.id, rec)
        return obj

# Idea is to take the results of an SQL search and XMLify them into documents.
# FIXME:  Implement PostgresDocumentStream
class PostgresDocumentStream(BaseDocumentStream):
    def __init__(self, stream=None, format='', tag='', codec=''):
        raise NotImplementedError

    def find_documents(self, cache=0):
        pass
    
class PostgresDocumentFactory(SimpleDocumentFactory):
    database = ''
    host = ''
    port = 0

    _possibleSettings = {'databaseName' : {'docs' : 'Name of the database in which to find the data'}
                         , 'host' : {'docs' : 'Host for where the SQL database is'}
                         , 'port'  : {'docs' : 'Port for where the SQL database is'}}

    def __init__(self, session, config, parent):        
        SimpleDocumentFactory.__init__(self, session, config, parent)
        self.database = self.get_setting(session, 'databaseName', '')
        self.host = self.get_setting(session, 'host', 'localhost')
        self.port = int(self.get_setting(session, 'port', '5432'))
        # query info to come in .load()

        
class PostgresStore(SimpleStore):
    cxn = None
    relations = {}

    _possiblePaths = {'databaseName' : {'docs' : "Database in which to store the data"}
                      , 'tableName' : {'docs' : "Table in the database in which to store the data"}
                      }
    # , 'idNormalizer'  : {'docs' : ""}}

    def __init__(self, session, config, parent):
        SimpleStore.__init__(self, session, config, parent)
        self.database = self.get_path(session, 'databaseName', 'cheshire3')
        self.table = self.get_path(session, 'tableName', parent.id + '_' + self.id)
        self.idNormalizer = self.get_path(session, 'idNormalizer', None)
        self._verifyDatabases(session)
        self.session = session
        
    def __iter__(self):
        # Return an iterator object to iter through
        return PostgresIter(self.session, self)

    def _handleConfigNode(self, session, node):
        if (node.nodeType == elementType and node.localName == 'relations'):
            self.relations = {}
            for rel in node.childNodes:
                if (rel.nodeType == elementType and rel.localName == 'relation'):
                    relName = rel.getAttributeNS(None, 'name')
                    fields = []
                    for fld in rel.childNodes:
                        if fld.nodeType == elementType:
                            if fld.localName == 'object':
                                oid = getFirstData(fld)
                                fields.append([oid, 'VARCHAR', oid])
                            elif fld.localName == 'field':
                                fname = fld.getAttributeNS(None, 'name')
                                ftype = getFirstData(fld)
                                fields.append([fname, ftype, ''])
                    self.relations[relName] = fields
        #- end _handleConfigNode ------------------------------------------

    def _handleLxmlConfigNode(self, session, node):
        if node.tag == 'relations':
            self.relations = {}
            for rel in node.iterchildren(tag=etree.Element):
                if rel.tag == 'relation':
                    relName = rel.attrib.get('name', None)
                    if relName is None:
                        raise ConfigFileException('Name not supplied for relation')
                    fields = []
                    for fld in rel.iterchildren(tag=etree.Element):
                        if fld.tag == 'object':
                            oid = flattenTexts(fld)
                            fields.append([oid, 'VARCHAR', oid])
                        elif fld.tag == 'field':
                            fname = fld.attrib.get('name', None)
                            if fname is None:
                                raise ConfigFileException('Name not supplied for field')
                            ftype = flattenTexts(fld)
                            fields.append([fname, ftype, ''])
                            
                    self.relations[relName] = fields
        #- end _handleLxmlConfigNode ------------------------------------------

    def _verifyDatabases(self, session):
        try:
#            self.cxn = pg.connect(self.database)
            self._openContainer(session)
        except pg.InternalError as e:
            raise ConfigFileException("Cannot connect to Postgres: %r" % e.args)

        try:
            query = "SELECT identifier FROM %s LIMIT 1" % self.table
            res = self._query(query)
        except pg.ProgrammingError as e:
            # no table for self, initialise
            query = """
            CREATE TABLE %s (identifier VARCHAR PRIMARY KEY,
            data BYTEA,
            digest VARCHAR(41),
            byteCount INT,
            wordCount INT,
            expires TIMESTAMP,
            tagName VARCHAR,
            parentStore VARCHAR,
            parentIdentifier VARCHAR,
            timeCreated TIMESTAMP,
            timeModified TIMESTAMP);
            """ % self.table
            self._query(query)


        # And check additional relations
        for (name, fields) in self.relations.iteritems():
            try:
                query = "SELECT identifier FROM %s_%s LIMIT 1" % (self.id,name)
                res = self._query(query)
            except pg.ProgrammingError as e:
                # No table for relation, initialise
                query = "CREATE TABLE %s_%s (identifier SERIAL PRIMARY KEY, " % (self.id, name)
                for f in fields:
                    query += ("%s %s" % (f[0], f[1]))
                    if f[2]:
                        # Foreign Key
                        query += (" REFERENCES %s (identifier) ON DELETE cascade" % f[2])
                    query += ", "
                query = query[:-2] + ") ;"                        
                res = self._query(query)

    def _openContainer(self, session):
        if self.cxn == None:
            self.cxn = pg.connect(self.database)

    def _closeContainer(self, session):
        if self.cxn is not None:
            self.cxn.close()
            del self.cxn
            self.cxn = None

    def _query(self, query):           
        query = query.encode('utf-8')
        res = self.cxn.query(query)
        return res

    def begin_storing(self, session):
        self._openContainer(session)
        return None

    def commit_storing(self, session):
        self._closeContainer(session)
        return None

    def generate_id(self, session):
        self._openContainer(session)
        # Find greatest current id
        if (self.currentId == -1 or session.environment == 'apache'):
            query = "SELECT identifier FROM %s ORDER BY identifier DESC LIMIT 1;" % self.table
            res = self._query(query)
            try:
                id = int(res.dictresult()[0]['identifier']) +1
            except:
                id = 0
            self.currentId = id
            return id
        else:
            self.currentId += 1
            return self.currentId

    def store_data(self, session, id, data, metadata={}):        
        self._openContainer(session)
        id = str(id)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if (self.idNormalizer != None):
            id = self.idNormalizer.process_string(session, id)
        data = data.replace(nonTextToken, '\\\\000\\\\001')

        query = "INSERT INTO %s (identifier, timeCreated) VALUES ('%s', '%s');" % (self.table, id, now)
        try:
            self._query(query)
        except:
            # already exists
            pass

        try:
            ndata = pg.escape_bytea(data)
        except:
            # insufficient PyGreSQL version
            ndata = data.replace("'", "\\'")

        if metadata:
            extra = []
            for (n,v) in metadata.iteritems():
                if type(v) in (int, long):
                    extra.append('%s = %s' % (n,v))
                else:
                    extra.append("%s = '%s'" % (n,v))
            extraq = ', '.join(extra)
            query = "UPDATE %s SET data = '%s', %s, timeModified = '%s' WHERE identifier = '%s';" % (self.table, ndata, extraq, now, id)
        else:
            query = "UPDATE %s SET data = '%s', timeModified = '%s' WHERE  identifier = '%s';" % (self.table, ndata, now, id)

        try:
            self._query(query)
        except:
            # Uhhh...
            raise
        return None


    def fetch_data(self, session, id):
        self._openContainer(session)
        sid = str(id)
        if (self.idNormalizer != None):
            sid = self.idNormalizer.process_string(session, sid)
        query = "SELECT data FROM %s WHERE identifier = '%s';" % (self.table, sid)
        res = self._query(query)
        try:
	        data = res.dictresult()[0]['data']
        except IndexError:
	    	raise ObjectDoesNotExistException(id)
        
        try:
            ndata = pg.unescape_bytea(data)
        except:
            # insufficient PyGreSQL version
            ndata = data.replace("\\'", "'")
            
        ndata = ndata.replace('\\000\\001', nonTextToken)
        ndata = ndata.replace('\\012', '\n')
        return ndata

    def delete_data(self, session, id):
        self._openContainer(session)
        sid = str(id)
        if (self.idNormalizer != None):
            sid = self.idNormalizer.process_string(session, str(id))
        query = "DELETE FROM %s WHERE identifier = '%s';" % (self.table, sid)
        self._query(query)
        return None

    def fetch_metadata(self, session, id, mType):
        if (self.idNormalizer != None):
            id = self.idNormalizer.process_string(session, id)
        elif type(id) == unicode:
            id = id.encode('utf-8')
        else:
            id = str(id)
        self._openContainer(session)
        query = "SELECT %s FROM %s WHERE identifier = %s;" % (mType, self.table, id)
        res = self._query(query)
        try:
            data = res.dictresult()[0][mtype]
        except:
            return None
        return data

    def store_metadata(self, session, key, mType, value):
        if (self.idNormalizer != None):
            id = self.idNormalizer.process_string(session, id)
        elif type(id) == unicode:
            id = id.encode('utf-8')
        else:
            id = str(id)
        self._openContainer(session)
        query = "UPDATE %s SET %s = %r WHERE identifier = '%s';" % (self.table, mType, value, id)
        try:
            self._query(query)
        except:
            return None
        return value

    def clear(self, session):
        self._openContainer(session)
        query = "DELETE FROM %s" % (self.table)
        self._query(query)    
    
    def clean(self, session):
        # here is where sql is nice...
        self._openContainer(session)
        nowStr = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time()))
        query = "DELETE FROM %s WHERE expires < '%s';" % (self.table, nowStr)
        self._query(query)

    def get_dbSize(self, session):
        query = "SELECT count(identifier) AS count FROM %s;" % (self.table)
        res = self._query(query)
        return res.dictresult()[0]['count']


    # NOT API
    def link(self, session, relation, *args, **kw):
        # create a new row in the named relation
        fields = []
        values = []
        for obj in args:
            fields.append(obj.recordStore)
            oid = obj.id
            if (self.idNormalizer):
                oid = self.idNormalizer.process_string(session, oid)                
            values.append(oid)
        for (name, value) in kw.iteritems():
            fields.append(name)
            values.append(value)

        fstr = ""
        valstr = ""
        for x in range(len(fields)):
            fstr += (fields[x] + ", ")
            valstr += ("%r, " % values[x])

        query = "INSERT INTO %s_%s (%s) VALUES (%s);" % (self.table, relation, fstr[:-2], valstr[:-2])
        self._query(query)


    # Also NOT API
    def unlink(self, session, relation, *args, **kw):
        cond = ""
        for obj in args:
            oid = obj.id
            if (self.idNormalizer):
                oid = self.idNormalizer.process_string(session, oid)                
            cond += ("%s = %r, " % (obj.recordStore, oid))
        for (name, value) in kw.iteritems():
            cond += ("%s = %r, " % (name, value))
        query = "DELETE FROM %s_%s WHERE %s;" % (self.table, relation, cond[:-2])
        self._query(query)


    # STILL NOT API
    def get_links(self, session, relation, *args, **kw):
        cond = ""
        for obj in args:
            oid = obj.id
            if (self.idNormalizer):
                oid = self.idNormalizer.process_string(session, oid)                
            cond += ("%s = %r, " % (obj.recordStore, oid))
        for (name, value) in kw.iteritems():
            cond += ("%s = %r, " % (name, value))           
        query = "SELECT * FROM %s_%s WHERE %s;" % (self.table, relation, cond[:-2])
        res = self._query(query)

        links = []
        reln = self.relations[relation]
        for row in res.getresult():
            link = []
            linkHash = {}
            for i in range(len(row)):
                name = reln[i-1][0]
                if (reln[i-1][2]):
                    link.append(SimpleResultSetItem(session, row[i], name))
                else:
                    linkHash[name] = row[i]

            links.append((link, linkHash))
            return links


class PostgresRecordStore(PostgresStore, SimpleRecordStore):
    def __init__(self, session, node, parent):
        SimpleRecordStore.__init__(self, session, node, parent)
        PostgresStore.__init__(self, session, node, parent)

    def __iter__(self):
        # Return an iterator object to iter through
        return PostgresRecordIter(self.session, self)


class PostgresDocumentStore(PostgresStore, SimpleDocumentStore):
    def __init__(self, session, node, parent):
        SimpleDocumentStore.__init__(self, session, node, parent)
        PostgresStore.__init__(self, session, node, parent)
        
    def __iter__(self):
        # Return an iterator object to iter through
        return PostgresDocumentIter(self.session, self)



class PostgresResultSetStore(PostgresStore, SimpleResultSetStore):
    _possibleSettings = {
                         'overwriteOkay' : {'docs': 'Can resultSets in this store be overwritten by a resultSet with the same identifier. NB if the item membership or order of a resultSet change, then the resultSet is fundamentally altered and should be assigned a new identifier. A stored resultSet should NEVER be overwritten by one that has different items or ordering!. 1 = Yes, 0 = No (default).', 'type': int, 'options' : "0|1"}
                        }
    
    def __init__(self, session, node, parent):
        SimpleResultSetStore.__init__(self, session, node, parent)
        PostgresStore.__init__(self, session, node, parent)

    def _verifyDatabases(self, session):
        # Custom resultSetStore table
        try:
            #self.cxn = pg.connect(self.database)
            self._openContainer(session)
        except pg.InternalError as e:
            raise ConfigFileException("Cannot connect to Postgres: %r" % e.args)

        try:
            query = "SELECT identifier FROM %s LIMIT 1" % self.table
            res = self._query(query)
        except pg.ProgrammingError as e:
            # no table for self, initialise
            query = """
            CREATE TABLE %s (identifier VARCHAR PRIMARY KEY,
            data BYTEA,
            size INT,
            class VARCHAR,
            timeCreated TIMESTAMP,
            timeAccessed TIMESTAMP,
            expires TIMESTAMP);
            """ % self.table
            self._query(query)
            # rs.id, rs.serialise(), digest, len(rs), rs.__class__, now, expireTime
            # NB: rs can't be modified

        # And check additional relations
        for (name, fields) in self.relations.iteritems():
            try:
                query = "SELECT identifier FROM %s_%s LIMIT 1" % (self.table,name)
                res = self._query(query)
            except pg.ProgrammingError as e:
                # No table for relation, initialise
                query = "CREATE TABLE %s_%s (identifier SERIAL PRIMARY KEY, " % (self.table, name)
                for f in fields:
                    query += ("%s %s" % (f[0], f[1]))
                    if f[2]:
                        # Foreign Key
                        query += (" REFERENCES %s (identifier)" % f[2])
                    query += ", "
                query = query[:-2] + ");"                        
                res = self._query(query)


    def store_data(self, session, id, data, size=0):        
        # should call store_resultSet
        raise NotImplementedError

    def create_resultSet(self, session, rset):
        id = self.generate_id(session)
        rset.id = id
        rset.retryOnFail = 1
        self.store_resultSet(session, rset)
        return id

    def store_resultSet(self, session, rset):
        self._openContainer(session)
        now = time.time()
        nowStr = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))        
        if (rset.expires):
            expires = now + rset.expires
        else:
            expires = now + self.get_default(session, 'expires', 600)
        rset.timeExpires = expires
        expiresStr = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(expires))
        id = rset.id
        if (self.idNormalizer != None):
            id = self.idNormalizer.process_string(session, id)

        # Serialise and store
        srlz = rset.serialize(session)
        cl = str(rset.__class__)
        data = srlz.replace('\x00', '\\\\000')
        try:
            ndata = pg.escape_bytea(data)
        except:
            # insufficient PyGreSQL version - do the best we can
            ndata = data.replace("'", "\\'")
            
        query = "INSERT INTO %s (identifier, data, size, class, timeCreated, timeAccessed, expires) VALUES ('%s', E'%s', %s, '%s', '%s', '%s', '%s')" % (self.table, id, ndata, len(rset), cl, nowStr, nowStr, expiresStr)
        try:
            self._query(query)
        except pg.ProgrammingError as e:
            # already exists, retry for overwrite, create
            if self.get_setting(session, 'overwriteOkay', 0):
                query = "UPDATE %s SET data = E'%s', size = %s, class = '%s', timeAccessed = '%s', expires = '%s' WHERE identifier = '%s';" % (self.table, ndata, len(rset), cl, nowStr, expiresStr, id)
                self._query(query)
            elif hasattr(rset, 'retryOnFail'):
                # generate new id, re-store
                id = self.generate_id(session)
                if (self.idNormalizer != None):
                    id = self.idNormalizer.process_string(session, id)
                query = "INSERT INTO %s (identifier, data, size, class, timeCreated, timeAccessed, expires) VALUES ('%s', E'%s', %s, '%s', '%s', '%s', '%s')" % (self.table, id, ndata, len(rset), cl, nowStr, nowStr, expiresStr)
                try:
                    self._query(query)
                except pg.ProgrammingError:
                    raise ValueError(ndata)
                    
            else:
                raise ObjectAlreadyExistsException(self.id + '/' + id)
        return rset

    def fetch_resultSet(self, session, id):
        self._openContainer(session)

        sid = str(id)
        if (self.idNormalizer != None):
            sid = self.idNormalizer.process_string(session, sid)
        query = "SELECT class, data FROM %s WHERE identifier = '%s';" % (self.table, sid)
        res = self._query(query)
        try:
            rdict = res.dictresult()[0]
        except IndexError:
            raise ObjectDoesNotExistException('%s/%s' % (self.id, sid))

        data = rdict['data']
        try:
            ndata = pg.unescape_bytea(data)
        except:
            # insufficient PyGreSQL version
            ndata = data.replace("\\'", "'")
            
        ndata = ndata.replace('\\000', '\x00')
        ndata = ndata.replace('\\012', '\n')
        # data is res.dictresult()
        cl = rdict['class']
        rset = dynamic.buildObject(session, cl, [[]])
        rset.deserialize(session, ndata)
        rset.id = id
        
        # Update expires 
        now = time.time()
        nowStr = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))        
        expires = now + self.get_default(session, 'expires', 600)
        rset.timeExpires = expires
        expiresStr = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(expires))

        query = "UPDATE %s SET timeAccessed = '%s', expires = '%s' WHERE identifier = '%s';" % (self.table, nowStr, expiresStr, sid)
        self._query(query)
        return rset


    def delete_resultSet(self, session, id):
        self._openContainer(session)
        sid = str(id)
        if (self.idNormalizer != None):
            sid = self.idNormalizer.process_string(session, sid)
        query = "DELETE FROM %s WHERE identifier = '%s';" % (self.table, sid)
        self._query(query)


class PostgresObjectStore(PostgresRecordStore, SimpleObjectStore):
    """An interface to PosgreSQL storage mechanism for configured Cheshire3 objects."""
    
    def __iter__(self):
        # Return an iterator object to iter through
        return PostgresObjectIter(self.session, self)
    

# -- non proximity, just store occurences of type per record
# CREATE TABLE parent.id + self.id + index.id (identifier SERIAL PRIMARY KEY, term VARCHAR, occurences INT, recordId VARCHAR, stem VARCHAR, pos VARCHAR);

# -- proximity. Store each token, not each type, per record
# CREATE TABLE parent.id + self.id + index.id (identifier SERIAL PRIMARY KEY, term VARCHAR, field VARCHAR, recordId VARCHAR, stem VARCHAR, pos VARCHAR);

# -- then check adjacency via identifier comparison (plus field/recordId)

# -- recordId = recordStore.id / record.id
# -- so then can do easy intersection/union on them

# CREATE INDEX parent.id+self.id+index.id+termIndex on aboveTable (term);
# BEGIN
# INSERT INTO aboveTable (...) VALUES (...);
# COMMIT
# CLUSTER aboveIndex on aboveTable;

class PostgresIndexStore(IndexStore, PostgresStore):
    database = ""
    transaction = 0

    _possibleSettings = {'termProcess' : {'docs' : "Position of the process map to use for processing terms in the query.", 'type': int}}
    _possiblePaths = {'databaseName' : {'docs' : 'Name of the database in which to store the indexes.  Table names are assigned automatically.'}
                      , 'protocolMap' : {'docs' : 'ProtocolMap identifier for the indexes?'}}

    def __init__(self, session, config, parent):
        IndexStore.__init__(self, session, config, parent)
        # Open connection
        self.database = self.get_path(session, 'databaseName', 'cheshire3')
        # multiple tables, one per index 
        self.transaction = 0

    def _generate_tableName(self, session, index):
        base = self.parent.id + "__" + self.id + "__" + index.id
        return base.replace('-', '_').lower()

    def contains_index(self, session, index):
        self._openContainer(session)
        table = self._generate_tableName(session, index)
        query = "SELECT relname FROM pg_stat_user_tables WHERE relname = '%s'" % table;
        res = self._query(query)
        return len(res.dictresult()) == 1

    def create_index(self, session, index):
        self._openContainer(session)
        table = self._generate_tableName(session, index)
        query = "CREATE TABLE %s (identifier SERIAL PRIMARY KEY, term VARCHAR, occurences INT, recordId VARCHAR, stem VARCHAR, pos VARCHAR)" % table
        query2 = "CREATE INDEX %s ON %s (term)" % (table + "_INDEX", table)
        self._openContainer(session)
        self._query(query)
        self._query(query2)

    def begin_indexing(self, session, index):
        self._openContainer(session)
        if not self.transaction:
            self._query('BEGIN')
            self.transaction = 1

    def commit_indexing(self, session, index):
        if self.transaction:
            self._query('COMMIT')
            self.transaction = 0
        table = self._generate_tableName(session, index)
        termIdx = table + "_INDEX"
        self._query('CLUSTER %s ON %s' % (termIdx, table))

    def store_terms(self, session, index, termhash, record):
        # write directly to db, as sort comes as CLUSTER later
        table = self._generate_tableName(session, index)
        queryTmpl = "INSERT INTO %s (term, occurences, recordId) VALUES ('%%s', %%s, '%r')" % (table, record)

        for t in termhash.values():
            term = t['text'].replace("'", "''")
            query = queryTmpl % (term, t['occurences'])
            self._query(query)

    def delete_terms(self, session, index, termHash, record):
        table = self._generate_tableName(session, index)
        query = "DELETE FROM %s WHERE recordId = '%r'" % (table, record)
        self._query(query)


    def fetch_term(self, session, index, term, prox=True):
        # should return info to create result set
        # --> [(rec, occs), ...]
        table = self._generate_tableName(session, index)
        term = term.replace("'", "\\'");
        query = "SELECT recordId, occurences FROM %s WHERE term='%s'" % (table, term)
        res = self._query(query)
        dr = res.dictresult()
        totalRecs = len(dr)
        occq = "SELECT SUM(occurences) as sum FROM %s WHERE term='%s'" % (table, term)
        res = self._query(occq)
        totalOccs = res.dictresult()[0]['sum']
        return {'totalRecs' : totalRecs, 'records' : dr, 'totalOccs' : totalOccs}

    def fetch_termList(self, session, index, term, numReq=0, relation="", end="", summary=0, reverse=0):
        table = self._generate_tableName(session, index)

        if (not (numReq or relation or end)):
            numReq = 20
        if (not relation and not end):
            relation = ">="
        if type(end) == unicode:
            end = end.encode('utf-8')
        if (not relation):
            if (term > end):
                relation = "<="
            else:
                relation = ">"

        if relation in ['<', '<=']:
            order = "order by term desc "
        else:
            order = ""

        # assumes summary, atm :|
        # term, total recs, total occurences

        occq = "SELECT term, count(term), sum(occurences) FROM %s WHERE term%s'%s' group by term %sLIMIT %s" % (table, relation, term, order, numReq)
        res = self._query(occq)
        # now construct list from query result

        tlist = res.getresult()
        tlist = [(x[0], (0, x[1], x[2])) for x in tlist]
        if order:
            # re-reverse
            tlist = tlist[::-1]
        return tlist


    def _cql_to_sql(self, session, query, pm):
        if (isinstance(query, cql.SearchClause)):
            idx = pm.resolveIndex(session, query)

            if (idx != None):
                # check if 'stem' relmod

                # get the index to chunk the term
                pn = idx.get_setting(session, 'termProcess')
                if (pn == None):
                    pn = 0
                else:
                    pn = int(pn)
                process = idx.sources[pn][1]
                res = idx._processChain(session, [query.term.value], process)
                if len(res) == 1:
                    nterm = res.keys()[0]

                # check stem
                if query.relation['stem']:
                    termCol = 'stem'
                else:
                    termCol = 'term'
                table = self._generate_tableName(session, idx)
                qrval = query.relation.value
                
                if qrval == "any":
                    terms = []
                    for t in res:
                        terms.append("'" + t + "'")
                    inStr = ', '.join(terms)
                    q = "SELECT DISTINCT recordid FROM %s WHERE %s in (%s)" % (table, termCol, inStr)
                elif qrval == "all":
                    qs = []
                    for t in res:
                        qs.append("SELECT recordid FROM %s WHERE %s = '%s'" % (table, termCol, t))
                    q = " INTERSECT ".join(qs)
                elif qrval == "exact":
                    q = "SELECT recordid FROM %s WHERE %s = '%s'" % (table, termCol, nterm)
                elif qrval == "within":
                    q = "SELECT recordid FROM %s WHERE %s BETWEEN '%s' AND '%s'" % (table, termCol, res[0], nterm)
                elif qrval in ['>', '<', '>=', '<=', '<>']:
                    q = "SELECT recordid FROM %s WHERE %s %s '%s'" % (table, termCol, qrval, nterm)
                elif qrval == '=':
                    # no prox
                    raise NotImplementedError()
                else:
                    raise NotImplementedError(qrval)

                return q
            else:
                raise ObjectDoesNotExistException(query.index.toCQL())

        else:
            left = self._cql_to_sql(session, query.leftOperand, pm)
            right = self._cql_to_sql(session, query.rightOperand, pm)
            bl = query.boolean
            if bl.value == "and":
                b = 'INTERSECT'
            elif bl.value == "or":
                b = 'UNION'
            elif bl.value == 'not':
                b = 'EXCEPT'
            else:
                raise NotImplementedError()
            q = "(%s %s %s)" % (left, b, right)
            return q


    # kludgey optimisation
    def search(self, session, query, db):
        pm = db.get_path(session, 'protocolMap')
        if not pm:
            db._cacheProtocolMaps(session)
            pm = db.protocolMaps.get('http://www.loc.gov/zing/srw/')
            db.paths['protocolMap'] = pm
        query = self._cql_to_sql(session, query, pm)
        res = self._query(query)
        dr = res.dictresult()
        rset = SimpleResultSet([])
        rsilist = []
        for t in dr:
            (store, id) = t['recordid'].split('/',1)
            item = SimpleResultSetItem(session, id, store, 1, resultSet=rset)
            rsilist.append(item)
        rset.fromList(rsilist)
        return rset


class PostgresIndex(SimpleIndex):
    
    def construct_resultSet(self, session, terms, queryHash={}):
        # in: res.dictresult()

        s = SimpleResultSet(session, [])
        rsilist = []
        for t in terms['records']:
            (store, id) = t['recordid'].split('/',1)
            occs = t['occurences']
            item = SimpleResultSetItem(session, id, store, occs, resultSet=s)
            rsilist.append(item)
        s.fromList(rsilist)
        s.index = self
        if queryHash:
            s.queryTerm = queryHash['text']
            s.queryFreq = queryHash['occurences']
        s.totalRecs = terms['totalRecs']
        s.totalOccs = terms['totalOccs']
        return s
        
