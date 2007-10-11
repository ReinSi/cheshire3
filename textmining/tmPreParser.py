
from document import StringDocument
from baseObjects import PreParser
import os, re
from utils import getFirstData, elementType, verifyXPaths
from xml.sax.saxutils import escape

from TsujiiC3 import TsujiiObject, EnjuObject, GeniaObject

class PosPreParser(PreParser):
    """ Base class for deriving Part of Speech PreParsers """
    pass

class TsujiiChunkerPreParser(PreParser):
    # Any need for this outside of preParsing??

    inh = None
    outh = None

    _possiblePaths = {'chunkerPath' : {'docs' : "Path to the directory where the chunker lives."}}

    def __init__(self, session, node, parent):
	PreParser.__init__(self, session, node, parent)
	o = os.getcwd()
        tp = self.get_path(session, 'chunkerPath')
        if tp:
            os.chdir(tp)
        else:
            os.chdir('../../code/textmining')
        (a,b) = os.popen2('./parser')
        self.inh = a
        self.outh = b
        os.chdir(o)

        
    def process_document(self, session, doc):
        # Must be raw text after passed through tagger
        txt = doc.get_raw()
        lines = txt.split('\n')
        all = []
        for l in lines:
            self.inh.write(l)
            self.inh.write("\n")
            self.inh.flush()
            tagd = self.outh.readline()                
            all.append(tagd)
        return StringDocument('\n'.join(all))


class TsujiiXMLPosPreParser(PosPreParser, TsujiiObject):

    def __init__(self, session, node, parent):
        PosPreParser.__init__(self, session, node, parent)
        TsujiiObject.__init__(self, session, node, parent)

    def process_document(self, session, doc):
        text = doc.get_raw()
        tt = self.tag(session, text, xml=1)
        ttj = '\n'.join(tt)
        ttj = "<text>" + ttj + "</text>"
        return StringDocument(ttj, self.id, doc.processHistory, 'text/xml', doc.parent)

class TsujiiTextPosPreParser(PosPreParser, TsujiiObject):

    def __init__(self, session, node, parent):
        PosPreParser.__init__(self, session, node, parent)
        TsujiiObject.__init__(self, session, node, parent)

    def process_document(self, session, doc):
        text = doc.get_raw()
        tt = self.tag(session, text, xml=0)
        tt = '\n'.join(tt)
        return StringDocument(tt, self.id, doc.processHistory, 'text/plain', doc.parent)

class EnjuTextPreParser(PosPreParser, EnjuObject):
    def __init__(self, session, node, parent):
        PosPreParser.__init__(self, session, node, parent)
        EnjuObject.__init__(self, session, node, parent)

    def process_document(self, session, doc):
        text = doc.get_raw()
        tt = self.tag(session, text)
        tt= '\n'.join(tt)
        return StringDocument("<text>%s</text>" % tt)


class GeniaTextPreParser(PreParser):
    """ Take the full output from Genia and reconstruct the document, maybe with stems ('useStem') and/or PoS tags ('pos') """

    _possibleSettings = {'useStem' : {'docs' : "Should the document reconstruction use the stem (1) or the original word (0, default)", 'type' : int, 'options' : "0|1"},
                         'pos' : {'docs' : "Should the PoS tag be added back to the word in the form word/POS (1) or not (0, default)", 'type' : int, 'options' : "0|1"}
                         }

    def __init__(self, session, config, parent):
        PreParser.__init__(self, session, config, parent)
        self.stem = self.get_setting(session, 'useStem', 0)
        self.pos = self.get_setting(session, 'pos', 0)
        self.puncre = re.compile('[ ]([.,;:?!][ \n])')

    def process_document(self, session, doc):
        data = doc.get_raw()
        lines = data.split('\n')
        words = []
        for l in lines:
            if l == '\n':
                words.append(l)
            else:
                (word, stem, pos, other) = l[:-1].split('\t')
                if self.stem:
                    w = stem
                else:
                    w = word
                if self.pos:
                    w = "%s/%s" % (w, pos)
                words.append(w)
        txt = ' '.join(words)
        txt = self.puncRe.sub('\\1', txt)
        return StringDocument(txt)

    
class GeniaVerbSpanPreParser(GeniaTextPreParser):
    # Take in unparsed genia and return spans between verb forms

    _possibleSettings = {'requireNoun' : {'docs' : "Should the sections of text be required to have at least one noun (1) or not (0, default)", 'type' : int, 'options' : "0|1"}
                         }
    def __init__(self, session, config, parent):
        GeniaTextPreParser.__init__(self, session, config, parent)
        self.requireNoun = self.get_setting(session, 'requireNoun', 0)
        self.minimumWords = self.get_setting(session, 'minimumWords', 1)

    def process_document(self, session, doc):
        minWds = self.minimumWords
        data = doc.get_raw()
        data = data.decode('utf-8')
        lines = data.split('\n')

        chunks = []        
        words = []
        nounOkay = 0
        for l in lines:
            try:
                (word, stem, pos, rest) = l.split('\t', 3)
            except:
                # ignore whitespace lines
                continue

            if pos[0] in ['V', '.']:
                if not self.requireNoun or nounOkay:
                    chunks.append(words)
                words = []
                nounOkay = 0
            else:
                if self.stem:
                    w = stem
                else:
                    w = word
                if self.pos:
                    w = "%s/%s" % (w, pos)
                words.append(w)
                if pos[0] == "N":
                    nounOkay = 1

        if not self.requireNoun or nounOkay:
            chunks.append(words)

        # serialise a bit into xml
        xml = [u"""<document id="%s" docStore="%s">\n""" % (doc.id, doc.documentStore)]
        for c in chunks:
            if len(c) >= minWds:
                try:
                    chunk = u' '.join(c)
                except:
                    print c
                    raise
                chunk = escape(chunk)
                xml.append(u"<chunk>%s</chunk>\n" % (chunk))
        xml.append(u'</document>\n\n')
        return StringDocument(u''.join(xml))
        


        
        