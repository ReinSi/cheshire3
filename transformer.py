
from configParser import C3Object
from baseObjects import Transformer
import os.path, time, utils, types
from document import StringDocument
from c3errors import ConfigFileException

from Ft.Xml.Xslt.Processor import Processor
from Ft.Xml import InputSource
from Ft.Xml.Domlette import ConvertDocument
from PyZ3950 import z3950, grs1
from PyZ3950.zmarc import MARC

from utils import verifyXPaths, saxToString
from utils import nonTextToken
from utils import elementType, flattenTexts, verifyXPaths


class FilepathTransformer(Transformer):
    """ Returns record.id as an identifier, in raw SAX events. For use as the inTransformer of a recordStore """
    def process_record(self, session, rec):
        sax = ['1 identifier {}', '3 ' + str(rec.id), '2 identifier']
        data = nonTextToken.join(sax)
        return StringDocument(data)

# Simplest transformation ...
class XmlTransformer(Transformer):
    """ Return the raw XML string of the record """
    def process_record(self,session, rec):
        return StringDocument(rec.get_xml())


# --- XSLT Transformers ---

try:
    from lxml import etree
    class LxmlXsltTransformer(Transformer):
        """ XSLT transformer using Lxml implementation. Requires LxmlRecord """

        _possiblePaths = {'xsltPath' : {'docs' : "Path to the XSLT file to use."}}

        def __init__(self, session, config, parent):
            Transformer.__init__(self, session, config, parent)
            xfrPath = self.get_path(session, "xsltPath")
            dfp = self.get_path(session, "defaultPath")
            path = os.path.join(dfp, xfrPath)
            et = etree.parse(path)
            self.txr = etree.XSLT(et)

        def process_record(self, session, rec):
            # return StringDocument
            dom = rec.get_dom()
            result = self.txr(dom)
            return StringDocument(str(result))

except:
    pass
    

class XsltTransformer(Transformer):
    """ 4Suite based XSLT transformer. """
    
    _possiblePaths = {'xsltPath' : {'docs' : "Path to the XSLT file to use."}}

    def __init__(self, session, config, parent):
	Transformer.__init__(self, session, config, parent)
	xfrPath = self.get_path(session, "xsltPath")
	dfp = self.get_path(session, "defaultPath")
	path = os.path.join(dfp, xfrPath)
	xfr = InputSource.DefaultFactory.fromStream(file(path), "file://" + path)
	processor = Processor()
	processor.appendStylesheet(xfr)
	self.processor = processor

    def process_record(self, session, rec):
        p = self.permissionHandlers.get('info:srw/operation/2/transform', None)
        if p:
            if not session.user:
                raise PermissionException("Authenticated user required to transform using %s" % self.id)
            okay = p.hasPermission(session, session.user)
            if not okay:
                raise PermissionException("Permission required to transform using %s" % self.id)
        dom = rec.get_dom()

        if not hasattr(dom, 'nodeType'):
            # Probably LXML
            raise ConfigFileException("Record given to %s is not the right class. Should probably be LxmlXsltTransformer." % self.id)

        dfp = self.get_path(session, "defaultPath")
        result = self.processor.runNode(dom, u'')
        return StringDocument(result, self.id, rec.processHistory, parent=rec.parent)


# --- Text, CSV Transformers ---

class CSVTransformer(Transformer):
    """ Create simple CSV format from indexes specified """

    def _handleConfigNode(self, session, node):
        # fields
        #   path type=index|workflow|xpathObject ref=id
        # --> ordered list of fields
        if node.localname == "fields":
            fields = []
            for child in node.childNodes:
                if child.nodeType == elementType:
                    if child.localname == "path":
                        otype = child.getAttributeNS(None, 'type')
                        if not otype in ['index', 'workflow', 'xpathObject']:
                            raise ConfigFileException("'%s' type not allowed for CSVTransformer %s (index, workflow, xpathObject)" % (otype, self.id))
                        ref = child.getAttributeNS(None, 'ref')
                        obj = self.get_object(session, ref)
                        fields.append((type, obj))
            self.fields = fields
                        

    def __init__(self, session, config, parent):
        self.fields = []
        Transformer.__init__(self, session, config, parent)
        
    def process_record(self, session, rec):
        # simple comma separated format
        data = []
        for xp in self.fields:
            try:
                data.append(saxToString(rec.process_xpath(xp)[0]))
            except IndexError:
                # Missing Value
                data.append('?')
        line = ','.join(data)
        return StringDocument(line)


# --- GRS1 Transformers for Z39.50 ---

class GRS1Transformer(Transformer):
    """ Create representation of the XML tree in Z39.50's GRS1 format """

    def initState(self):
        self.top = None
        self.nodeStack = []

    def startElement(self, name, attribs):
        node = z3950.TaggedElement()
        node.tagType = 3
        node.tagValue = ('string', name)
        node.content = ('subtree', [])

        for a in attribs:
            # Elements with Metadata
            anode = z3950.TaggedElement()
            md = z3950.ElementMetaData()
            anode.tagType = 3
            anode.tagValue = ('string', a)
            md.message = 'attribute'
            anode.metaData = md
            anode.content = ('octets', attribs[a])
            node.content[1].append(anode)

        if (self.nodeStack):
            self.nodeStack[-1].content[1].append(node)
        else:
            self.top = node
        self.nodeStack.append(node)

        
    def endElement(self, foo):
        if (self.nodeStack[-1].content[1] == []):
            self.nodeStack[-1].content = ('elementEmpty', None)
        self.nodeStack.pop()

    def characters(self, text, zero, length):
        if (self.nodeStack):
            if (text.isspace()):
                text = " "
            # pre-encode to utf8 to avoid charset/encoding headaches
            # eg these are now octets, not unicode
            text = text.encode('utf8')
            node = z3950.TaggedElement()
            node.tagType = 2
            node.tagValue = ('numeric', 19)
            node.content = ('octets', text)
            self.nodeStack[-1].content[1].append(node)


    def process_record(self, session, rec):
        p = self.permissionHandlers.get('info:srw/operation/2/transform', None)
        if p:
            if not session.user:
                raise PermissionException("Authenticated user required to transform using %s" % self.id)
            okay = p.hasPermission(session, session.user)
            if not okay:
                raise PermissionException("Permission required to transform using %s" % self.id)
        self.initState()
        rec.saxify(self)
        return StringDocument(self.top, self.id, rec.processHistory, parent=rec.parent)


class GrsMapTransformer(Transformer):
    """ Create a particular GRS1 instance, based on a configured map of XPath to GRS1 element. """

    def _handleConfigNode(self,session, node):
        if (node.localName == "transform"):
            self.tagset = node.getAttributeNS(None, 'tagset')
            maps = []
            for child in node.childNodes:
                if (child.nodeType == elementType and child.localName == "map"):
                    map = []
                    for xpchild in child.childNodes:
                        if (xpchild.nodeType == elementType and xpchild.localName == "xpath"):
                            map.append(flattenTexts(xpchild))
                    if map[0][0] != "#":
                        vxp = verifyXPaths([map[0]])
                    else:
                        # special case to process
                        vxp = [map[0]]
                    maps.append([vxp[0], map[1]])
            self.maps = maps

    def __init__(self, session, config, parent):
        self.maps = []
        self.tagset = ""
        Transformer.__init__(self, session, config, parent)
    
    def _resolveData(self, session, rec, xpath):
        if xpath[0] != '#': 
                data = rec.process_xpath(xpath)
                try: data = ' '.join(data)
                except TypeError:
                    # data isn't sequence, maybe a string or integer
                    pass
                try:
                    data = data.encode('utf-8')
                except:
                    data = str(data)
        elif xpath == '#RELEVANCE#':
            data = rec.resultSetItem.scaledWeight
        elif xpath == '#RAWRELEVANCE#':
            data = rec.resultSetItem.weight
        elif xpath == '#DOCID#':
            data = rec.id
        elif xpath == '#RECORDSTORE#':
            data = rec.recordStore
        elif xpath == '#PROXINFO#':
            data = repr(rec.resultSetItem.proxInfo)
        elif xpath[:8] == '#PARENT#':
            # Get parent docid out of record
            try: 
                parent = rec.process_xpath('/c3:component/@parent', {'c3':'http://www.cheshire3.org/'})[0]
            except IndexError:
                # probably no namespaces
                parent = rec.process_xpath('/c3component/@parent')[0]
            parentStore, parentId = parent.split('/', 1)

            xtrapath = xpath[8:]
            if xtrapath:
                # actually get parent record to get stuff out of
                # TODO: not sure the best way to do this yet :(
                parentRec = self.parent.get_object(session, parentStore).fetch_record(session, parentId)
                # strip leading slash from xtra path data
                # N.B. double slash needed to root xpath to doc node (e.g. #PARENT#//root/somenode)
                if parentRec:
                    xtrapath = xtrapath[1:]
                    data = self._resolveData(session, parentRec, xtrapath)
            else:
                # by default just return id of parent record
                data = parentId
        return data

    
    def process_record(self, session, rec):
        elems = []
        for m in self.maps:
            (xpath, tagPath) = m
            node = z3950.TaggedElement()            
            data = self._resolveData(session, rec, xpath)
            node.content = ('string', str(data))
            node.tagType = 2
            node.tagValue = ('numeric', int(tagPath))
            elems.append(node)
        return StringDocument(elems, self.id, rec.processHistory, parent=rec.parent)


        
class XmlRecordStoreTransformer(Transformer):
    """ Wrap the data with the record's metadata. For use as inTransformer of a recordStore. Not recommended. """

    # Transform a record, return 'string' to dump to database.
    # (String might be a struct in other implementations)

    def process_record(self, session, rec):

        vars = {'id' : rec.id, 'baseUri': rec.baseUri, 'schema' : rec.schema,
                'schemaType' : rec.schemaType, 'status' : rec.status,
                'size': rec.wordCount}
        if session == None or session.user == None:
            vars['user'] = 'admin'
        else:
            vars['user'] = session.user.username

        vars['now'] = time.strftime("%Y-%m-%d %H:%M:%S")

        if (rec.recordStore <> None and rec.id <> None):
            history = rec.history
            histlist = []

            if (history):
                history.append((vars['user'], vars['now'], 'modified'))
                for h in history:
                    histlist.append('<c3:modification type="%s"><c3:date>%s</c3:date><c3:agent>%s</c3:agent></c3:modification>' % (h[2], h[1], h[0]))
                histlist.append('<c3:modification type="modify"><c3:date>%(now)s</c3:date><c3:agent>%(user)s</c3:agent></c3:modification>' % (vars))
                histtxt = "\n".join(histlist)
            else:
                histtxt = '<c3:modification type="create"><c3:date>%(now)s</c3:date><c3:agent>%(user)s</c3:agent></c3:modification>' % (vars)
            
            rightslist = []
            for r in rec.rights:
                rightslist.append('<c3:%(userType)s role="%(role)s">%(user)</c3:%(userType)s>' % ({'userType' : r[1], 'role' : r[2], 'user': r[0]}))
            rightstxt = '\n'.join(rightslist)

            saxList = rec.get_sax()
	    saxList.append('9 ' + repr(rec.elementHash))
	    sax = nonTextToken.join(saxList)

        else:
            histtxt = '<c3:modification type="create"><c3:date>%(now)s</c3:date><c3:agent>%(user)s</c3:agent></c3:modification>' % (vars)
            rightstxt = '<c3:agent role="editor">%(user)s</c3:agent>' % (vars)
            sax = ""

        ph = []
        for item in rec.processHistory:
            ph.append('<c3:object>%s</c3:object>' % (item))

        if (rec.parent[0]):
            parent = "<c3:type>%s</c3:type><c3:store>%s</c3:store><c3:id>%d</c3:id>" % rec.parent
        else:
            parent = ""

        vars['parent'] = parent
        vars['processHistory'] =  ''.join(ph)
        vars['rights'] = rightstxt
        vars['history'] = histtxt
        vars['sax'] = sax
            
        xml = u"""<c3:record xmlns:c3="http://www.cheshire3.org/schemas/record/1.0/">
        <c3:id>%(id)s</c3:id>
        <c3:status>%(status)s</c3:status>
        <c3:baseUri>%(baseUri)s</c3:baseUri>
        <c3:schema>%(schema)s</c3:schema>
        <c3:schemaType>%(schemaType)s</c3:schemaType>
        <c3:size>%(size)d</c3:size>
        <c3:parent>%(parent)s</c3:parent>
        <c3:technicalRights>
          %(rights)s
        </c3:technicalRights>
        <c3:history>
          %(history)s
        </c3:history>
        <c3:processHistory>
        %(processHistory)s
        </c3:processHistory>
        <c3:saxEvents>%(sax)s</c3:saxEvents>
        </c3:record>
        """ % (vars)

        return StringDocument(xml)
              
              
