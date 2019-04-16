from __future__ import unicode_literals
import collections
import heapq
import itertools
import logging
import math
import numbers
import random
import Levenshtein

from .baseline import BaselineModel, ConstrNode
from .cost import Cost
from .constructions.cognate import CognateConstructionMethods, WILDCARD
from .corpus import LexiconEncoding, CorpusEncoding, \
    AnnotatedCorpusEncoding, FixedCorpusWeight

_logger = logging.getLogger(__name__)


class CognateModel(BaselineModel):
    """Morfessor Baseline model class.

    Implements training of and segmenting with a Morfessor model. The model
    is complete agnostic to whether it is used with lists of strings (finding
    phrases in sentences) or strings of characters (finding morphs in words).

    """

    penalty = -9999.9

    def __init__(self, corpusweight=None, use_skips=False, constr_class=None):
        """Initialize a new model instance.

        Arguments:
            forcesplit_list: force segmentations on the characters in
                               the given list
            corpusweight: weight for the corpus cost
            use_skips: randomly skip frequently occurring constructions
                         to speed up training
            nosplit_re: regular expression string for preventing splitting
                          in certain contexts

        """

        self.cc = CognateConstructionMethods()

        # In analyses for each construction a ConstrNode is stored. All
        # training data has a rcount (real count) > 0. All real morphemes
        # have no split locations.
        self._analyses = {}

        # Flag to indicate the model is only useful for segmentation
        self._segment_only = False

        # Cost variables
        # self._lexicon_coding = LexiconEncoding()
        # self._corpus_coding = CorpusEncoding(self._lexicon_coding)
        # self._annot_coding = None

        #Set corpus weight updater
        # self.set_corpus_weight_updater(corpusweight)
        self._corpus_weight_updater = None

        self.cost = CognateCost(self.cc, corpusweight)

    def _recursive_split(self, construction):
        """Optimize segmentation of the construction by recursive splitting.

        Returns list of segments.

        """
        # contains cognate-morfessor specific hacks!

        # if self._use_skips and self._test_skip(construction):
        #     return self.segment(construction)
        rcount, count = self._remove(construction)
        src, trg = construction
        src_rcount = 0
        src_count = 0
        wild_src = None
        trg_rcount = 0
        trg_count = 0
        wild_trg = None
        if src != WILDCARD and trg != WILDCARD:
            # when modifying a cognate pair,
            # also modify the corresponding wildcard constructions
            wild_src = self.cc.type(src, WILDCARD)
            if wild_src in self._analyses:
                src_rcount, src_count = self._remove(wild_src)
            else:
                wild_src = None
            wild_trg = self.cc.type(WILDCARD, trg)
            if wild_trg in self._analyses:
                trg_rcount, trg_count = self._remove(wild_trg)
            else:
                wild_trg = None

        # Check all binary splits and no split
        self._modify_construction_count(construction, count)
        self._modify_construction_count(wild_src, src_count)
        self._modify_construction_count(wild_trg, trg_count)
        mincost = self.get_cost()
        self._modify_construction_count(construction, -count)
        self._modify_construction_count(wild_src, -src_count)
        self._modify_construction_count(wild_trg, -trg_count)

        best_splitloc = None

        for loc in self.cc.split_locations(construction):
            prefix, suffix = self.cc.split(construction, loc)
            self._modify_construction_count(prefix, count)
            self._modify_construction_count(suffix, count)
            if wild_src is not None:
                src_prefix, src_suffix = self.cc.split(wild_src, loc)
                self._modify_construction_count(src_prefix, src_count)
                self._modify_construction_count(src_suffix, src_count)
            if wild_trg is not None:
                trg_prefix, trg_suffix = self.cc.split(wild_trg, loc)
                self._modify_construction_count(trg_prefix, trg_count)
                self._modify_construction_count(trg_suffix, trg_count)
            cost = self.get_cost()
            self._modify_construction_count(prefix, -count)
            self._modify_construction_count(suffix, -count)
            if wild_src is not None:
                self._modify_construction_count(src_prefix, -src_count)
                self._modify_construction_count(src_suffix, -src_count)
            if wild_trg is not None:
                self._modify_construction_count(trg_prefix, -trg_count)
                self._modify_construction_count(trg_suffix, -trg_count)
            if cost <= mincost:
                mincost = cost
                best_splitloc = loc

        if best_splitloc:
            # Virtual construction
            self._analyses[construction] = ConstrNode(
                rcount, count, best_splitloc)
            prefix, suffix = self.cc.split(construction, best_splitloc)
            self._modify_construction_count(prefix, count)
            self._modify_construction_count(suffix, count)
            if wild_src is not None:
                self._analyses[wild_src] = ConstrNode(
                    src_rcount, src_count, best_splitloc)
                src_prefix, src_suffix = self.cc.split(wild_src, best_splitloc)
                self._modify_construction_count(src_prefix, src_count)
                self._modify_construction_count(src_suffix, src_count)
            if wild_trg is not None:
                self._analyses[wild_trg] = ConstrNode(
                    trg_rcount, trg_count, best_splitloc)
                trg_prefix, trg_suffix = self.cc.split(wild_trg, best_splitloc)
                self._modify_construction_count(trg_prefix, trg_count)
                self._modify_construction_count(trg_suffix, trg_count)
            lp = self._recursive_split(prefix)
            if suffix != prefix:
                return lp + self._recursive_split(suffix)
            else:
                return lp + lp
        else:
            # Real construction
            self._analyses[construction] = ConstrNode(rcount, 0, None)
            self._modify_construction_count(construction, count)
            if wild_src is not None:
                self._analyses[wild_src] = ConstrNode(src_rcount, 0, None)
                self._modify_construction_count(wild_src, src_count)
            if wild_trg is not None:
                self._analyses[wild_trg] = ConstrNode(trg_rcount, 0, None)
                self._modify_construction_count(wild_trg, trg_count)
            return [construction]

    def get_construction_count(self, construction):
        if construction.src == WILDCARD:
            return self.cost.trg_cost.counts[construction.trg]
        if construction.trg == WILDCARD:
            return self.cost.src_cost.counts[construction.src]
        # else
        return super().get_construction_count(construction)

class CognateCost(object):
    def __init__(self, contr_class, corpusweight=1.0):
        self.src_cost = Cost(contr_class, corpusweight=corpusweight)
        self.trg_cost = Cost(contr_class, corpusweight=corpusweight)
        self.edit_cost = Cost(contr_class, corpusweight=1.0)
        self.edit_weight = 1.0

        self.cc = contr_class
        self._corpus_weight_updater = None

        #Set corpus weight updater
        self.set_corpus_weight_updater(corpusweight)

    def set_corpus_weight_updater(self, corpus_weight):
        if corpus_weight is None:
            self._corpus_weight_updater = FixedCorpusWeight(1.0)
        elif isinstance(corpus_weight, numbers.Number):
            self._corpus_weight_updater = FixedCorpusWeight(corpus_weight)
        else:
            self._corpus_weight_updater = corpus_weight

        self._corpus_weight_updater.update(self, 0)

    def set_corpus_coding_weight(self, weight):
        self.src_cost.set_corpus_coding_weight(weight)
        self.trg_cost.set_corpus_coding_weight(weight)
        #self.edit_cost.set_corpus_coding_weight(weight)

    def set_edit_weight(self, weight):
        self.edit_weight = weight

    def cost(self):
        return self.src_cost.cost() + self.trg_cost.cost() + \
            self.edit_weight * self.edit_cost.cost()

    def update(self, construction, delta):
        if delta == 0:
            return

        src, trg = self.cc.lex_key(construction)
        if src != WILDCARD:
            self.src_cost.update(src, delta)
        if trg != WILDCARD:
            self.trg_cost.update(trg, delta)
        if src != WILDCARD and trg != WILDCARD:
            for edit in edits(src, trg):
                self.edit_cost.update(edit, delta)

    def update_boundaries(self, compound, delta):
        src, trg = self.cc.corpus_key(compound)
        if src != WILDCARD:
            self.src_cost.update_boundaries(src, delta)
        if trg != WILDCARD:
            self.trg_cost.update_boundaries(trg, delta)
        if src != WILDCARD and trg != WILDCARD:
            for edit in edits(src, trg):
                self.edit_cost.update_boundaries(edit, delta)

    def coding_length(self, construction):
        pass

    def tokens(self):
        return self.src_cost.tokens() + self.trg_cost.tokens()

    def compound_tokens(self):
        return self.src_cost.compound_tokens() + \
            self.trg_cost.compound_tokens()

    def types(self):
        return self.src_cost.types() + self.trg_cost.types()

    def all_tokens(self):
        return self.src_cost.all_tokens() + self.trg_cost.all_tokens()

    def newbound_cost(self, count):
        return self.src_cost.newbound_cost(count) + \
            self.trg_cost.newbound_cost(count)

    def bad_likelihood(self, compound, addcount):
        src, trg = self.cc.corpus_key(compound)
        cost = 0
        if src != WILDCARD:
            cost += self.src_cost.bad_likelihood(src, addcount)
        if trg != WILDCARD:
            cost += self.trg_cost.bad_likelihood(trg, addcount)
        return cost

    def get_coding_cost(self, compound):
        src, trg = self.cc.lex_key(compound)
        return self.src_cost.get_coding_cost(src) + \
            self.trg_cost.get_coding_cost(trg)


def remove_equal(edits):
    for edit in edits:
        if edit[0] == 'equal':
            continue
        yield edit


def merge_consecutive_edits(edits):
    pop, pib, pie, pjb, pje = None, None, None, None, None
    for op, ib, ie, jb, je in edits:
        if ib == pie and jb == pje:
            pop = 'replace'
            pie = ie
            pje = je
            continue
        else:
            if pop is not None:
                yield (pop, pib, pie, pjb, pje)
            pop, pib, pie, pjb, pje = op, ib, ie, jb, je
    if pop is not None:
        yield (pop, pib, pie, pjb, pje)


def lengthening(src, trg, edits):
    """Represent lengthening sounds as longer replacements,
    rather than insertions/deletions"""
    for edit in edits:
        op, ib, ie, jb, je = edit
        if min(ie - ib, je - jb) > 0:
            # only extend if one side is empty
            yield op, ib, ie, jb, je
            continue
        use_trg = (ie - ib == 0)

        if ib > 0 and jb > 0:
            # try to expand left
            if use_trg:
                cursor = trg[jb]
            else:
                cursor = src[ib]
            if src[ib - 1] == cursor and trg[jb - 1] == cursor:
                ib -= 1
                jb -= 1
                op = 'replace'
        if ie < len(src) - 1 and je < len(trg) - 1:
            # try to expand right
            if use_trg:
                cursor = trg[je - 1]
            else:
                cursor = src[ie - 1]
            if src[ie] == cursor and trg[je] == cursor:
                ie += 1
                je += 1
                op = 'replace'
        yield op, ib, ie, jb, je


def edits(src, trg):
    edits = Levenshtein.opcodes(src, trg)
    edits = remove_equal(edits)
    edits = merge_consecutive_edits(edits)
    edits = lengthening(src, trg, edits)
    for op, ib, ie, jb, je in edits:
        if op == 'equal':
            continue
        if op == 'delete':
            yield '/'.join((src[ib:ie], ''))
        elif op == 'insert':
            yield '/'.join(('', trg[jb:je]))
        elif op == 'replace':
            yield '/'.join((src[ib:ie], trg[jb:je]))
        else:
            raise Exception(op)
