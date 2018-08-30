#!/usr/bin/env python
# -*- coding: utf-8 -*-

import collections
import sys

DELIM = '￨'
FIVEDOT = '\u2059' # 5-dot punctuation

model_in = sys.argv[1]
src_map = sys.argv[2]
trg_map = sys.argv[3]
linked_morphs = sys.argv[4]

with open(model_in, 'r') as infobj, \
     open(src_map, 'w') as srcfobj, \
     open(trg_map, 'w') as trgfobj:
    links = collections.Counter()
    for line in infobj:
        line = line.strip()
        count, seg = line.split(' ', 1)
        # remove end epsilons
        seg = seg.replace(FIVEDOT, '')
        pairs = seg.split(' + ')
        pairs = [tuple(pair.split(DELIM)) for pair in pairs]
        for pair in pairs:
            src, trg = pair
            if len(src) > 0 and len(trg) > 0:
                links[pair] += 1
        srcs, trgs = zip(*pairs)
        # strip out empty morphs (non-cognate or removed epsilon)
        srcs = [x for x in srcs if x != '']
        trgs = [x for x in trgs if x != '']
        if len(srcs) > 0:
            srcfobj.write(' '.join(srcs))
            srcfobj.write('\n')
        if len(trgs) > 0:
            trgfobj.write(' '.join(trgs))
            trgfobj.write('\n')

seen_src = set()
seen_trg = set()
with open(linked_morphs, 'w') as linkfobj:
    for pair, count in links.most_common():
        src, trg = pair
        if src in seen_src or trg in seen_trg:
            continue
        linkfobj.write('{}\t{}\n'.format(src, trg))
        seen_src.add(src)
        seen_trg.add(trg)
