#!/usr/bin/env python3

import collections
import sys
import math

srcs_file = sys.argv[1]
trgs_file = sys.argv[2]
cogs_file = sys.argv[3]

out_file = sys.argv[4]

srcs_unscaled = collections.Counter()
trgs_unscaled = collections.Counter()

cogs = []
cogs_selected = []
cogs_src = set()
cogs_trg = set()

count_threshold = 2

with open(srcs_file, 'r') as fobj:
    for line in fobj:
        count, word = line.strip().split('\t')
        count = int(count)
        srcs_unscaled[word] = count

with open(trgs_file, 'r') as fobj:
    for line in fobj:
        count, word = line.strip().split('\t')
        count = int(count)
        trgs_unscaled[word] = count

src_sum = sum(srcs_unscaled.values())
trg_sum = sum(trgs_unscaled.values())
if src_sum < trg_sum:
    mult = trg_sum / src_sum
    srcs = collections.Counter({
        word: int(count * mult)
        for word, count in srcs_unscaled.items()})
    trgs = trgs_unscaled
else:
    mult = src_sum / trg_sum
    trgs = collections.Counter({
        word: int(count * mult)
        for word, count in trgs_unscaled.items()})
    srcs = srcs_unscaled
print(src_sum, trg_sum, mult)

with open(cogs_file, 'r') as fobj:
    for line in fobj:
        dist, src, trg = line.strip().split('\t')
        dist = int(dist)
        count = int(math.ceil((srcs[src] + trgs[trg]) / 2))
        cogs.append((dist, -count, src, trg))

cogs.sort()

for dist, ncount, src, trg in cogs:
    if src in cogs_src or trg in cogs_trg:
        # already found a better match
        continue
    cogs_selected.append((-ncount, src, trg))
    cogs_src.add(src)
    cogs_trg.add(trg)

with open(out_file, 'w') as out_fobj:
    for (word, count) in srcs.most_common():
        if word in cogs_src:
            # don't output
            continue
        if srcs_unscaled[word] >= count_threshold:
            out_fobj.write('{}\t{}\t{}\n'.format(count, word, ''))
    for (word, count) in trgs.most_common():
        if word in cogs_trg:
            # don't output
            continue
        if trgs_unscaled[word] >= count_threshold:
            out_fobj.write('{}\t{}\t{}\n'.format(count, '', word))
    for count, src, trg in cogs_selected:
        count = max(count, 1)   # in case cognate not in count file
        # cognates not thresholded
        out_fobj.write('{}\t{}\t{}\n'.format(count, src, trg))
