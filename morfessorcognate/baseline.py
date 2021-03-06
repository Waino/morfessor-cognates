from __future__ import unicode_literals
import collections
import heapq
import itertools
import logging
import math
import numbers
import random

from .cost import Cost
from .constructions.base import BaseConstructionMethods
from .corpus import LexiconEncoding, CorpusEncoding, \
    AnnotatedCorpusEncoding, FixedCorpusWeight
from .utils import _progress, tail
from .exception import MorfessorException, SegmentOnlyModelException

_logger = logging.getLogger(__name__)


# rcount = root count (from corpus)
# count = total count of the node
# splitloc = integer or tuple. Location(s) of the possible splits for virtual
#            constructions; empty tuple or 0 if real construction
ConstrNode = collections.namedtuple('ConstrNode',
                                    ['rcount', 'count', 'splitloc'])


class BaselineModel(object):
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

        self.cc = constr_class if constr_class is not None else BaseConstructionMethods()

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

        self.cost = Cost(self.cc, corpusweight)

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

    @property
    def tokens(self):
        """Return the number of construction tokens."""
        return self.cost.tokens()

    @property
    def types(self):
        """Return the number of construction types."""
        return self.cost.types() - 1  # do not include boundary

    def _check_segment_only(self):
        if self._segment_only:
            raise SegmentOnlyModelException()

    def _epoch_checks(self):
        """Apply per epoch checks"""
        # self._check_integrity()

    def _epoch_update(self, epoch_num):
        """Do model updates that are necessary between training epochs.

        The argument is the number of training epochs finished.

        In practice, this does two things:
        - If random skipping is in use, reset construction counters.
        - If semi-supervised learning is in use and there are alternative
          analyses in the annotated data, select the annotations that are
          most likely given the model parameters. If not hand-set, update
          the weight of the annotated corpus.

        This method should also be run prior to training (with the
        epoch number argument as 0).

        """
        forced_epochs = 0
        if self._corpus_weight_updater is not None:
            if self._corpus_weight_updater.update(self, epoch_num):
                forced_epochs += 2

        # if self._use_skips:
        #     self._counter = collections.Counter()
        # if self._supervised:
        #     self._update_annotation_choices()
        #     self._annot_coding.update_weight()

        return forced_epochs

    def _update_annotation_choices(self):
        """Update the selection of alternative analyses in annotations.

        For semi-supervised models, select the most likely alternative
        analyses included in the annotations of the compounds.

        """
        if not self._supervised:
            return

        # Collect constructions from the most probable segmentations
        # and add missing compounds also to the unannotated data
        constructions = collections.Counter()
        for compound, alternatives in self.annotations.items():
            if not compound in self._analyses:
                self._add_compound(compound, 1)

            analysis, cost = self._best_analysis(alternatives)
            for m in analysis:
                constructions[m] += self._analyses[compound].rcount

        # Apply the selected constructions in annotated corpus coding
        self._annot_coding.set_constructions(constructions)
        for constr in constructions.keys():
            count = self.get_construction_count(constr)
            self._annot_coding.set_count(constr, count)

    def _best_analysis(self, choices):
        """Select the best analysis out of the given choices."""
        bestcost = None
        bestanalysis = None
        for analysis in choices:
            cost = 0.0
            for constr in analysis:
                count = self.get_construction_count(constr)
                if count > 0:
                    cost += (math.log(self.cost.tokens()) -
                             math.log(count))
                else:
                    cost -= self.penalty  # penalty is negative
            if bestcost is None or cost < bestcost:
                bestcost = cost
                bestanalysis = analysis
        return bestanalysis, bestcost

    def _add_compound(self, compound, c):
        """Add compound with count c to data."""
        self.cost.update_boundaries(compound, c)
        self._modify_construction_count(compound, c)
        oldrc = self._analyses[compound].rcount
        self._analyses[compound] = \
            self._analyses[compound]._replace(rcount=oldrc + c)

    def _remove(self, construction):
        """Remove construction from model."""
        rcount, count, splitloc = self._analyses[construction]
        self._modify_construction_count(construction, -count)
        return rcount, count

    def _clear_compound_analysis(self, compound):
        """Clear analysis of a compound from model"""
        pass

    def _set_compound_analysis(self, compound, parts):
        """Set analysis of compound to according to given segmentation.

        Arguments:
            compound: compound to split
            parts: desired constructions of the compound

        """
        parts = list(parts)
        if len(parts) == 1:
            rcount, count = self._remove(compound)
            self._analyses[compound] = ConstrNode(rcount, 0, tuple())
            self._modify_construction_count(compound, count)
        else:
            rcount, count = self._remove(compound)

            splitloc = tuple(self.cc.parts_to_splitlocs(parts))
            self._analyses[compound] = ConstrNode(rcount, count, splitloc)
            for constr in parts:
                self._modify_construction_count(constr, count)

    def get_construction_count(self, construction):
        """Return (real) count of the construction."""
        if (construction in self._analyses and
            not self._analyses[construction].splitloc):
            count = self._analyses[construction].count
            if count <= 0:
                raise MorfessorException("Construction count of '%s' is %s"
                                         % (construction, count))
            return count
        return 0

    def _test_skip(self, construction):
        """Return true if construction should be skipped."""
        if construction in self._counter:
            t = self._counter[construction]
            if random.random() > 1.0 / max(1, t):
                return True
        self._counter[construction] += 1
        return False

    def _viterbi_optimize(self, compound, addcount=0, maxlen=30):
        """Optimize segmentation of the compound using the Viterbi algorithm.

        Arguments:
          compound: compound to optimize
          addcount: constant for additive smoothing of Viterbi probs
          maxlen: maximum length for a construction

        Returns list of segments.

        """
        if self._use_skips and self._test_skip(compound):
            return self.segment(compound)

        # Use Viterbi algorithm to optimize the subsegments
        constructions = []
        for part in self.cc.splitn(compound, self.cc.force_split_locations(compound)):
            constructions.extend(self.viterbi_segment(part, addcount=addcount,
                                                  maxlen=maxlen)[0])
        self._set_compound_analysis(compound, constructions)
        return constructions

    def _recursive_optimize(self, compound):
        """Optimize segmentation of the compound using recursive splitting.

        Returns list of segments.

        """
        # if self._use_skips and self._test_skip(compound):
        #     return self.segment(compound)
        # Collect forced subsegments

        parts = list(self.cc.splitn(compound, self.cc.force_split_locations(compound)))
        if len(parts) == 1:
            # just one part
            return self._recursive_split(compound)
        self._set_compound_analysis(compound, parts)
        # Use recursive algorithm to optimize the subsegments
        constructions = []
        for part in parts:
            constructions += self._recursive_split(part)
        return constructions

    def _recursive_split(self, construction):
        """Optimize segmentation of the construction by recursive splitting.

        Returns list of segments.

        """
        # if self._use_skips and self._test_skip(construction):
        #     return self.segment(construction)
        rcount, count = self._remove(construction)

        # Check all binary splits and no split
        self._modify_construction_count(construction, count)
        mincost = self.get_cost()
        self._modify_construction_count(construction, -count)

        best_splitloc = None

        for loc in self.cc.split_locations(construction):
            prefix, suffix = self.cc.split(construction, loc)
            self._modify_construction_count(prefix, count)
            self._modify_construction_count(suffix, count)
            cost = self.get_cost()
            self._modify_construction_count(prefix, -count)
            self._modify_construction_count(suffix, -count)
            if cost <= mincost:
                mincost = cost
                best_splitloc = loc

        if best_splitloc:
            # Virtual construction
            self._analyses[construction] = ConstrNode(rcount, count, best_splitloc)
            prefix, suffix = self.cc.split(construction, best_splitloc)
            self._modify_construction_count(prefix, count)
            self._modify_construction_count(suffix, count)
            lp = self._recursive_split(prefix)
            if suffix != prefix:
                return lp + self._recursive_split(suffix)
            else:
                return lp + lp
        else:
            # Real construction
            self._analyses[construction] = ConstrNode(rcount, 0, None)
            self._modify_construction_count(construction, count)
            return [construction]

    def _modify_construction_count(self, construction, dcount):
        """Modify the count of construction by dcount.

        For virtual constructions, recurses to child nodes in the
        tree. For real constructions, adds/removes construction
        to/from the lexicon whenever necessary.

        """
        if dcount == 0 or construction is None:
            return
        if construction in self._analyses:
            rcount, count, splitloc = self._analyses[construction]
        else:
            rcount, count, splitloc = 0, 0, None
        newcount = count + dcount
        # observe that this comparison will not work correctly if counts
        # are floats rather than ints
        if newcount == 0:
            if construction in self._analyses:
                del self._analyses[construction]
        else:
            self._analyses[construction] = ConstrNode(rcount, newcount,
                                                      splitloc)
        if splitloc:
            # Virtual construction
            for child in self.cc.splitn(construction, splitloc):
                self._modify_construction_count(child, dcount)
        else:
            self.cost.update(construction, newcount-count)
            # Real construction

    def get_compounds(self):
        """Return the compound types stored by the model."""
        self._check_segment_only()
        return [w for w, node in self._analyses.items()
                if node.rcount > 0]

    def get_constructions(self):
        """Return a list of the present constructions and their counts."""
        return sorted((c, node.count) for c, node in self._analyses.items()
                      if not node.splitloc)

    def get_cost(self):
        """Return current model encoding cost."""
        return self.cost.cost()
        cost = self.cost.cost()
        if self._supervised:
            return cost + self._annot_coding.get_cost()
        else:
            return cost

    def get_segmentations(self):
        """Retrieve segmentations for all compounds encoded by the model."""
        self._check_segment_only()
        for w in sorted(self._analyses.keys()):
            c = self._analyses[w].rcount
            if c > 0:
                yield c, w, self.segment(w)

    def load_data(self, data):
        """Load data to initialize the model for batch training.

        Arguments:
            data: iterator of DataPoint tuples

        Adds the compounds in the corpus to the model lexicon. Returns
        the total cost.

        """
        self._check_segment_only()
        for dp in data:
            self._add_compound(dp.compound, dp.count)
            self._clear_compound_analysis(dp.compound)
            self._set_compound_analysis(dp.compound, self.cc.splitn(dp.compound, dp.splitlocs))
        return self.get_cost()

    # FIXME: refactor?
    def load_segmentations(self, segmentations):
        self._check_segment_only()
        for count, compound, constructions in segmentations:
            splitlocs = tuple(self.cc.parts_to_splitlocs(constructions))
            self._add_compound(compound, count)
            self._clear_compound_analysis(compound)
            self._set_compound_analysis(compound, self.cc.splitn(compound, splitlocs))
        return self.get_cost()

    def segment(self, compound):
        """Segment the compound by looking it up in the model analyses.

        Raises KeyError if compound is not present in the training
        data. For segmenting new words, use viterbi_segment(compound).

        """
        self._check_segment_only()
        _, _, splitloc = self._analyses[compound]
        constructions = []
        if splitloc:
            for part in self.cc.splitn(compound, splitloc):
                constructions += self.segment(part)
        else:
            constructions.append(compound)

        return constructions

    def train_batch(self, algorithm='recursive', algorithm_params=(),
                    finish_threshold=0.005, max_epochs=None):
        """Train the model in batch fashion.

        The model is trained with the data already loaded into the model (by
        using an existing model or calling one of the load\_ methods).

        In each iteration (epoch) all compounds in the training data are
        optimized once, in a random order. If applicable, corpus weight,
        annotation cost, and random split counters are recalculated after
        each iteration.

        Arguments:
            algorithm: string in ('recursive', 'viterbi', 'flatten') 
                         that indicates the splitting algorithm used.
            algorithm_params: parameters passed to the splitting algorithm.
            finish_threshold: the stopping threshold. Training stops when
                                the improvement of the last iteration is
                                smaller then finish_threshold * #boundaries
            max_epochs: maximum number of epochs to train

        """
        epochs = 0
        forced_epochs = max(1, self._epoch_update(epochs))
        newcost = self.get_cost()
        compounds = list(self.get_compounds())
        _logger.info("Compounds in training data: %s types / %s tokens" %
                     (len(compounds), self.cost.compound_tokens()))

        if algorithm == 'flatten':
            _logger.info("Flattening analysis tree")
            for compound in _progress(compounds):
                parts = self.segment(compound)
                self._clear_compound_analysis(compound)
                self._set_compound_analysis(compound, parts)
            _logger.info("Done.")
            return 1, self.get_cost()

        _logger.info("Starting batch training")
        _logger.info("Epochs: %s\tCost: %s" % (epochs, newcost))

        while True:
            # One epoch
            random.shuffle(compounds)

            for w in _progress(compounds):
                if algorithm == 'recursive':
                    segments = self._recursive_optimize(w, *algorithm_params)
                elif algorithm == 'viterbi':
                    segments = self._viterbi_optimize(w, *algorithm_params)
                else:
                    raise MorfessorException("unknown algorithm '%s'" %
                                             algorithm)
                _logger.debug("#%s -> %s" %
                              (w, " + ".join(self.cc.to_string(s) for s in segments)))
            epochs += 1

            _logger.debug("Cost before epoch update: %s" % self.get_cost())
            forced_epochs = max(forced_epochs, self._epoch_update(epochs))
            oldcost = newcost
            newcost = self.get_cost()

            self._epoch_checks()

            _logger.info("Epochs: %s\tCost: %s" % (epochs, newcost))
            if (forced_epochs == 0 and
                    newcost >= oldcost - finish_threshold *
                    self.cost.compound_tokens()):
                break
            if forced_epochs > 0:
                forced_epochs -= 1
            if max_epochs is not None and epochs >= max_epochs:
                _logger.info("Max number of epochs reached, stop training")
                break
        _logger.info("Done.")
        return epochs, newcost

    def train_online(self, data, count_modifier=None, epoch_interval=10000,
                     algorithm='recursive', algorithm_params=(),
                     init_rand_split=None, max_epochs=None):
        """Train the model in online fashion.

        The model is trained with the data provided in the data argument.
        As example the data could come from a generator linked to standard in
        for live monitoring of the splitting.

        All compounds from data are only optimized once. After online
        training, batch training could be used for further optimization.

        Epochs are defined as a fixed number of compounds. After each epoch (
        like in batch training), the annotation cost, and random split counters
        are recalculated if applicable.

        Arguments:
            data: iterator of (_, compound_atoms) tuples. The first
                    argument is ignored, as every occurence of the
                    compound is taken with count 1
            count_modifier: function for adjusting the counts of each
                              compound
            epoch_interval: number of compounds to process before starting
                              a new epoch
            algorithm: string in ('recursive', 'viterbi') that indicates
                         the splitting algorithm used.
            algorithm_params: parameters passed to the splitting algorithm.
            init_rand_split: probability for random splitting a compound to
                               at any point for initializing the model. None
                               or 0 means no random splitting.
            max_epochs: maximum number of epochs to train

        """
        self._check_segment_only()
        if count_modifier is not None:
            counts = {}

        _logger.info("Starting online training")

        epochs = 0
        i = 0
        more_tokens = True
        while more_tokens:
            self._epoch_update(epochs)
            newcost = self.get_cost()
            _logger.info("Tokens processed: %s\tCost: %s" % (i, newcost))

            for _ in _progress(range(epoch_interval)):
                try:
                    dp = next(data)
                except StopIteration:
                    more_tokens = False
                    break

                self._add_compound(dp.compound, dp.count)
                self._clear_compound_analysis(dp.compound)
                self._set_compound_analysis(dp.compound, self.cc.splitn(dp.compound, dp.splitlocs))

                if algorithm == 'recursive':
                    segments = self._recursive_optimize(dp.compound, *algorithm_params)
                elif algorithm == 'viterbi':
                    segments = self._viterbi_optimize(dp.compound, *algorithm_params)
                else:
                    raise MorfessorException("unknown algorithm '%s'" %
                                             algorithm)
                _logger.debug("#%s: %s -> %s" %
                              (i, dp.compound, " + ".join(self.cc.to_string(s) for s in segments)))
                i += 1

            epochs += 1
            if max_epochs is not None and epochs >= max_epochs:
                _logger.info("Max number of epochs reached, stop training")
                break

        self._epoch_update(epochs)
        newcost = self.get_cost()
        _logger.info("Tokens processed: %s\tCost: %s" % (i, newcost))
        return epochs, newcost

    def viterbi_segment(self, compound, addcount=1.0, maxlen=30,
                        allow_longer_unk_splits=False):
        """Find optimal segmentation using the Viterbi algorithm.

        Arguments:
          compound: compound to be segmented
          addcount: constant for additive smoothing (0 = no smoothing)
          maxlen: maximum length for the constructions

        If additive smoothing is applied, new complex construction types can
        be selected during the search. Without smoothing, only new
        single-atom constructions can be selected.

        Returns the most probable segmentation and its log-probability.

        """
        #clen = len(compound)
        # indices = range(1, clen+1) if allowed_boundaries is None \
        #           else allowed_boundaries+[clen]

        grid = {None: (0.0, None)}
        tokens = self.cost.all_tokens() + addcount
        logtokens = math.log(tokens) if tokens > 0 else 0

        newboundcost = self.cost.newbound_cost(addcount) if addcount > 0 else 0

        badlikelihood = self.cost.bad_likelihood(compound,addcount)

        for t in itertools.chain(self.cc.split_locations(compound), [None]):
            # Select the best path to current node.
            # Note that we can come from any node in history.
            bestpath = None
            bestcost = None

            for pt in tail(maxlen, itertools.chain([None], self.cc.split_locations(compound, stop=t))):
                if grid[pt][0] is None:
                    continue
                cost = grid[pt][0]
                construction = self.cc.slice(compound, pt, t)
                count = self.get_construction_count(construction)
                if count > 0:
                    cost += (logtokens - math.log(count + addcount))
                elif addcount > 0:
                    if self.cost.tokens() == 0:
                        cost += (addcount * math.log(addcount) +
                                 newboundcost + self.cost.get_coding_cost(construction))
                    else:
                        cost += (logtokens - math.log(addcount) +
                                 newboundcost + self.cost.get_coding_cost(construction))

                elif self.cc.is_atom(construction):
                    cost += badlikelihood
                elif allow_longer_unk_splits:
                    # Some splits are forbidden, so longer unknown
                    # constructions have to be allowed
                    cost += len(self.cc.corpus_key(construction)) * badlikelihood
                else:
                    continue
                #_logger.debug("cost(%s)=%.2f", construction, cost)
                if bestcost is None or cost < bestcost:
                    bestcost = cost
                    bestpath = pt
            grid[t] = (bestcost, bestpath)

        splitlocs = []

        cost, path = grid[None]
        while path is not None:
            splitlocs.append(path)
            path = grid[path][1]

        constructions = list(self.cc.splitn(compound, list(reversed(splitlocs))))

        # Add boundary cost
        cost += (math.log(self.cost.tokens() +
                          self.cost.compound_tokens()) -
                 math.log(self.cost.compound_tokens()))
        return constructions, cost

    #TODO project lambda
    def forward_logprob(self, compound):
        """Find log-probability of a compound using the forward algorithm.

        Arguments:
          compound: compound to process

        Returns the (negative) log-probability of the compound. If the
        probability is zero, returns a number that is larger than the
        value defined by the penalty attribute of the model object.

        """
        clen = len(compound)
        grid = [0.0]
        if self._corpus_coding.tokens + self._corpus_coding.boundaries > 0:
            logtokens = math.log(self._corpus_coding.tokens +
                                 self._corpus_coding.boundaries)
        else:
            logtokens = 0
        # Forward main loop
        for t in range(1, clen + 1):
            # Sum probabilities from all paths to the current node.
            # Note that we can come from any node in history.
            psum = 0.0
            for pt in range(0, t):
                cost = grid[pt]
                construction = compound[pt:t]
                count = self.get_construction_count(construction)
                if count > 0:
                    cost += (logtokens - math.log(count))
                else:
                    continue
                psum += math.exp(-cost)
            if psum > 0:
                grid.append(-math.log(psum))
            else:
                grid.append(-self.penalty)
        cost = grid[-1]
        # Add boundary cost
        cost += (math.log(self._corpus_coding.tokens +
                          self._corpus_coding.boundaries) -
                 math.log(self._corpus_coding.boundaries))
        return cost

    # TODO project lambda
    def viterbi_nbest(self, compound, n, addcount=1.0, maxlen=30):
        """Find top-n optimal segmentations using the Viterbi algorithm.

        Arguments:
          compound: compound to be segmented
          n: how many segmentations to return
          addcount: constant for additive smoothing (0 = no smoothing)
          maxlen: maximum length for the constructions

        If additive smoothing is applied, new complex construction types can
        be selected during the search. Without smoothing, only new
        single-atom constructions can be selected.

        Returns the n most probable segmentations and their
        log-probabilities.

        """
        clen = len(compound)
        grid = [[(0.0, None, None)]]
        if self._corpus_coding.tokens + self._corpus_coding.boundaries + \
                addcount > 0:
            logtokens = math.log(self._corpus_coding.tokens +
                                 self._corpus_coding.boundaries + addcount)
        else:
            logtokens = 0
        if addcount > 0:
            newboundcost = (self._lexicon_coding.boundaries + addcount) * \
                           math.log(self._lexicon_coding.boundaries + addcount)
            if self._lexicon_coding.boundaries > 0:
                newboundcost -= self._lexicon_coding.boundaries * \
                                math.log(self._lexicon_coding.boundaries)
            newboundcost /= self._corpus_coding.weight
        else:
            newboundcost = 0
        badlikelihood = 1.0 + clen * logtokens + newboundcost + \
                        self._lexicon_coding.get_codelength(compound) / \
                        self._corpus_coding.weight
        # Viterbi main loop
        for t in range(1, clen + 1):
            # Select the best path to current node.
            # Note that we can come from any node in history.
            bestn = []
            if self.nosplit_re and t < clen and \
                    self.nosplit_re.match(compound[(t-1):(t+1)]):
                grid.append([(-clen*badlikelihood, t-1, -1)])
                continue
            for pt in range(max(0, t - maxlen), t):
                for k in range(len(grid[pt])):
                    if grid[pt][k][0] is None:
                        continue
                    cost = grid[pt][k][0]
                    construction = compound[pt:t]
                    count = self.get_construction_count(construction)
                    if count > 0:
                        cost += (logtokens - math.log(count + addcount))
                    elif addcount > 0:
                        if self._corpus_coding.tokens == 0:
                            cost -= (addcount * math.log(addcount) +
                                     newboundcost +
                                     self._lexicon_coding.get_codelength(
                                         construction)
                                     / self._corpus_coding.weight)
                        else:
                            cost -= (logtokens - math.log(addcount) +
                                     newboundcost +
                                     self._lexicon_coding.get_codelength(
                                         construction)
                                     / self._corpus_coding.weight)
                    elif len(construction) == 1:
                        cost -= badlikelihood
                    elif self.nosplit_re:
                        # Some splits are forbidden, so longer unknown
                        # constructions have to be allowed
                        cost -= len(construction) * badlikelihood
                    else:
                        continue
                    if len(bestn) < n:
                        heapq.heappush(bestn, (cost, pt, k))
                    else:
                        heapq.heappushpop(bestn, (cost, pt, k))
            grid.append(bestn)
        results = []
        for k in range(len(grid[-1])):
            constructions = []
            cost, path, ki = grid[-1][k]
            lt = clen + 1
            while path is not None:
                t = path
                constructions.append(compound[t:lt])
                path = grid[t][ki][1]
                ki = grid[t][ki][2]
                lt = t
            constructions.reverse()
            # Add boundary cost
            cost -= (math.log(self._corpus_coding.tokens +
                              self._corpus_coding.boundaries) -
                     math.log(self._corpus_coding.boundaries))
            results.append((-cost, constructions))
        return [(constr, cost) for cost, constr in sorted(results)]

    def get_corpus_coding_weight(self):
        return self.cost._corpus_coding.weight

    def set_corpus_coding_weight(self, weight):
        self._check_segment_only()
        self.cost.set_corpus_coding_weight(weight)

    def make_segment_only(self):
        """Reduce the size of this model by removing all non-morphs from the
        analyses. After calling this method it is not possible anymore to call
        any other method that would change the state of the model. Anyway
        doing so would throw an exception.

        """
        self._num_compounds = len(self.get_compounds())
        self._segment_only = True

        self._analyses = {k: v for (k, v) in self._analyses.items()
                          if not v.splitloc}

    def clear_segmentation(self):
        for compound in self.get_compounds():
            self._clear_compound_analysis(compound)
            self._set_compound_analysis(compound, [compound])

    def get_params(self):
        """Returns a dict of hyperparameters."""
        params = {'corpusweight': self.get_corpus_coding_weight()}
        #if self._supervised:
        #    params['annotationweight'] = self._annot_coding.weight
        params['forcesplit'] = ''.join(sorted(self.cc._force_splits))
        if self.cc._nosplit:
            params['nosplit'] = self.cc._nosplit.pattern
        return params

# count = count of the node
# splitloc = integer or tuple. Location(s) of the possible splits for virtual
#            constructions; empty tuple or 0 if real construction
SimpleConstrNode = collections.namedtuple('ConstrNode', ['count', 'splitloc'])
