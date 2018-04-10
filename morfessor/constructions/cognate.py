import collections

class Wildcard(object):
    def __repr__(self):
        return ''

    def __len__(self):
        return 1

    def __eq__(self, other):
        return isinstance(other, Wildcard)

WILDCARD = Wildcard()

class CognateConstructionMethods(object):
    type = collections.namedtuple("CognateConstruction", ['src', 'trg'])

    @staticmethod
    def force_split_locations(construction):
        return []

    @staticmethod
    def split_locations(construction, start=None, stop=None):
        """
        Return all possible split-locations between start and end. Start and end will not be returned.
        """
        start = (0,0) if start is None else start
        end = (len(construction.src), len(construction.trg)) if stop is None else stop

        for gi in range(start[0] + 1, end[0]):
            for pi in range(start[1] + 1, end[1]):
                yield (gi, pi)

    @classmethod
    def _sub_slice(cls, field, start=None, stop=None):
        if field == WILDCARD:
            return WILDCARD
        start = 0 if start is None else start
        stop = len(field) if stop is None else stop
        return field[start:stop]

    @classmethod
    def split(cls, construction, loc):
        assert 0 < loc[0] < len(construction.src)
        assert 0 < loc[1] < len(construction.trg)
        return (cls.type(cls._sub_slice(construction.src, stop=loc[0]),
                         cls._sub_slice(construction.trg, stop=loc[1])),
                cls.type(cls._sub_slice(construction.src, start=loc[0]),
                         cls._sub_slice(construction.trg, start=loc[1])))

    @classmethod
    def splitn(cls, construction, locs):
        if len(locs) > 0 and not hasattr(locs[0], '__iter__'):
            for p in cls.split(construction, locs):
                yield p
            return

        prev = (0,0)
        for l in locs:
            assert prev[0] < l[0] < len(construction.src)
            assert prev[1] < l[1] < len(construction.trg)
            yield cls.type(cls._sub_slice(construction.src, prev[0], l[0]),
                           cls._sub_slice(construction.trg, prev[1], l[1]))
            prev = l
        yield cls.type(cls._sub_slice(construction.src, start=prev[0]),
                       cls._sub_slice(construction.trg, start=prev[1]))

    @staticmethod
    def parts_to_splitlocs(parts):
        cur_len = [0, 0]
        for p in parts[:-1]:
            if p.src != WILDCARD:
                cur_len[0] += len(p.src)
            if p.trg != WILDCARD:
                cur_len[1] += len(p.trg)
            yield tuple(cur_len)

    @classmethod
    def slice(cls, construction, start=None, stop=None):
        start = (0,0) if start is None else start
        stop = (len(construction.src), len(construction.trg)) if stop is None else stop
        return cls.type(cls._sub_slice(construction.src, start[0], stop[0]),
                        cls._sub_slice(construction.trg, start[1], stop[1]))

    @classmethod
    def from_string(cls, string):
        src, trg = string.split('/', 1)
        if len(src) == 0:
            src = WILDCARD
        if len(trg) == 0:
            trg = WILDCARD
        return cls.type(src, trg)

    @staticmethod
    def to_string(construction):
        return u"{}/{}".format(construction.src, construction.trg)

    @staticmethod
    def corpus_key(construction):
        return construction

    @staticmethod
    def lex_key(construction):
        return construction

    @staticmethod
    def atoms(construction):
        return construction
